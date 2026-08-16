#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""deep_candidates.json → deep_results.json（逐岗并发调 LLM）。

流水线的 deep 阶段：`run_matcher.py --mode deep` 预筛出候选后由本脚本逐岗分析。
并发单位天然就是「一个岗位一次请求」，彼此不共享上下文，所以直接按 concurrency 铺开。

产出格式与 `merge_deep_results` 期望的完全一致（靠 rank 回填），所以合并那步一行不改：
    python scripts/run_matcher.py --mode deep --merge --output-dir <run_dir>

用法：
    python scripts/deep_analyze.py <run_dir>
    python scripts/deep_analyze.py <run_dir> --workers 6 --model deepseek-reasoner
    python scripts/deep_analyze.py <run_dir> --resume          # 续跑，跳过已有 rank
    python scripts/deep_analyze.py <run_dir> --limit 3         # 先拿 3 个试水
    python scripts/deep_analyze.py <run_dir> --dry-run         # 只打印将发什么，不花钱

退出码：0 = 全部成功，1 = 读不到输入/全部失败，3 = 部分失败（deep_results.json 已写）。
"""

import os
import sys
import json
import time
import argparse
import threading

import stage_timer
from resume_matcher.prompts import get_match_analysis_prompt
from resume_matcher import deep_candidates_path, deep_results_path
from llm import (
    ConfigError, LLMError, chat_json, map_concurrent, resolve,
    format_usage, reconfigure_stdout,
)

_VALID_CATEGORIES = ('qualified', 'need_optimization', 'cannot_apply')

# match_analysis.st 不产 highlight / risk，但 HTML 报告的深度模式会展示这两栏
# （merge 里是 deep.get('highlight', '')）。附言写在调用侧而不是塞进模板：模板描述的是
# 评分契约（merge 按那些字段回填），这两栏只是报告的展示件，混进去会让契约边界变模糊。
_ADDENDUM = """

额外要求（本次调用附加）：
在同一个 JSON 对象里再补两个字段：
- "highlight": 一句话，这个岗位对该候选人最值得投的地方（不超过 40 字）
- "risk": 一句话，投这个岗位最大的风险或不确定性；确实没有就给空字符串
"""


def build_resume_info(profile):
    """把 profile 拼成给模型看的简历摘要。

    刻意**不带**规则侧的评分和 matched/missing 技能：merge 会把规则分按 40% 权重
    掺进来，如果这里再把规则结论喂给模型，模型的分就成了规则分的回声，40/60 的
    混合也就没有意义了。两侧要各自独立地看同一份原始信息。
    """
    parts = []

    basic = profile.get('basic_info') or {}
    if basic:
        fields = [('姓名', 'name'), ('城市', 'city'), ('期望城市', 'expected_city'),
                  ('电话', 'phone'), ('邮箱', 'email'), ('求职状态', 'status')]
        line = '、'.join('%s: %s' % (label, basic[key])
                        for label, key in fields if basic.get(key))
        if line:
            parts.append('## 基本信息\n' + line)

    edu = profile.get('education') or {}
    if edu:
        parts.append('## 教育\n' + '、'.join(
            '%s: %s' % (k, v) for k, v in edu.items() if v))

    exp = profile.get('experience') or {}
    if exp:
        lines = []
        years = exp.get('total_years') or exp.get('years')
        if years is not None:
            lines.append('总年限: %s' % years)
        for company in (exp.get('companies') or []):
            if not isinstance(company, dict):
                continue
            lines.append('- %s / %s / %s' % (
                company.get('name', '?'), company.get('position', '?'),
                company.get('duration', company.get('period', '?'))))
            for point in (company.get('highlights') or [])[:4]:
                lines.append('  · %s' % point)
        if lines:
            parts.append('## 工作/实习经历\n' + '\n'.join(lines))

    skills = profile.get('skills') or {}
    if skills:
        lines = []
        for category, value in skills.items():
            if isinstance(value, list) and value:
                lines.append('- %s: %s' % (category, '、'.join(str(v) for v in value)))
            elif value and not isinstance(value, (list, dict)):
                lines.append('- %s: %s' % (category, value))
        if lines:
            parts.append('## 技能\n' + '\n'.join(lines))

    projects = profile.get('projects') or []
    if projects:
        lines = []
        for project in projects[:8]:
            if not isinstance(project, dict):
                continue
            lines.append('- %s（%s）' % (project.get('name', '?'),
                                        project.get('role', project.get('tech_stack', '')) or ''))
            desc = project.get('description') or ''
            if desc:
                lines.append('  %s' % desc[:200])
            for point in (project.get('highlights') or [])[:4]:
                lines.append('  · %s' % point)
        if lines:
            parts.append('## 项目\n' + '\n'.join(lines))

    salary = profile.get('salary_expectation') or {}
    if salary:
        parts.append('## 薪资期望\n' + '、'.join(
            '%s: %s' % (k, v) for k, v in salary.items() if v))

    keywords = profile.get('keywords') or []
    if keywords:
        parts.append('## 关键词\n' + '、'.join(str(k) for k in keywords))

    awards = profile.get('awards') or []
    if awards:
        names = [a.get('name', str(a)) if isinstance(a, dict) else str(a) for a in awards[:6]]
        parts.append('## 奖项\n' + '、'.join(names))

    if not parts:
        # profile 全空时不要发一份空简历过去问「匹配度多少」—— 那只会拿到编出来的分
        raise LLMError('profile 里没有任何可用信息，先检查 profile.json')
    return '\n\n'.join(parts)


def build_job_requirements(candidate, jd_limit=2500):
    """把候选岗位拼成给模型看的 JD 块。"""
    job = candidate.get('job') or {}
    head = [('公司', '公司'), ('职位', '职位'), ('城市', '城市'), ('区域', '区域'),
            ('薪资', '薪资'), ('经验要求', '经验'), ('学历要求', '学历'),
            ('行业', '领域'), ('公司规模', '规模'), ('融资阶段', '性质')]
    lines = ['%s: %s' % (label, job[key]) for label, key in head if job.get(key)]

    result = ['## 岗位基本信息\n' + '\n'.join(lines)]
    if job.get('技能标签'):
        result.append('## 技能标签\n%s' % job['技能标签'])
    jd = (job.get('岗位要求和职责') or '').strip()
    if jd:
        result.append('## 岗位要求和职责\n%s' % jd[:jd_limit])
    else:
        # 没有 JD 全文（爬取时没带 -d）时必须说清楚，否则模型会按标签硬编一个理由
        result.append('## 岗位要求和职责\n（未采集到 JD 全文，只能依据上面的标签判断，'
                      '请在 reason 里注明信息不足）')
    company_info = (job.get('公司信息') or '').strip()
    if company_info:
        result.append('## 公司信息\n%s' % company_info[:600])
    return '\n\n'.join(result)


def normalize_result(data, candidate):
    """校验并规整一条模型输出，返回 merge_deep_results 能吃的记录。

    merge 侧对键名很宽容（score/overall_match/deep_score 都认，category 还兼容中文
    标签），但**宽容不等于可以乱写**：在这里就把形状收紧，出问题时报在这一条上，
    而不是留到 merge 阶段变成一个静默走了规则兜底的岗位。
    """
    if not isinstance(data, dict):
        raise LLMError('返回的不是 JSON 对象，而是 %s' % type(data).__name__)

    score = data.get('score', data.get('overall_match', data.get('deep_score')))
    try:
        score = int(round(float(score)))
    except (TypeError, ValueError):
        raise LLMError('score 不是数字: %r' % (score,))
    score = max(0, min(100, score))

    category = str(data.get('category') or data.get('classification') or '').strip().lower()
    if category not in _VALID_CATEGORIES:
        # 交给 merge 的 _normalize_category 去兜（它认中文标签和 direct_apply 别名），
        # 但要留一条告警，因为这说明提示词的枚举约束没生效。
        if not category:
            raise LLMError('缺 category 字段')

    def _list(key):
        value = data.get(key)
        if isinstance(value, list):
            return [str(v) for v in value if v is not None and str(v).strip()]
        if value and isinstance(value, str):
            return [value]
        return []

    record = {
        'rank': candidate.get('rank'),
        'score': score,
        'category': category,
        'reason': str(data.get('reason') or data.get('classification_reason') or '').strip(),
        'missing_items': _list('missing_items'),
        'optimization_points': _list('optimization_points'),
        'highlight': str(data.get('highlight') or '').strip(),
        'risk': str(data.get('risk') or '').strip(),
    }
    # 四个维度的细分打分不参与 merge，但留在文件里便于事后核对模型为什么给这个分
    for key in ('education_match', 'experience_match', 'skills_match', 'salary_match'):
        if isinstance(data.get(key), dict):
            record[key] = data[key]
    return record


def load_candidates(run_dir):
    path = deep_candidates_path(run_dir)
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    candidates = data.get('candidates') or []
    profile = data.get('profile') or {}
    return profile, candidates


def load_existing(run_dir):
    """已有的 deep_results.json → {rank: record}，用于 --resume 续跑。"""
    path = deep_results_path(run_dir)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (ValueError, OSError):
        return {}
    results = data.get('results') if isinstance(data, dict) else data
    if not isinstance(results, list):
        return {}
    return {r.get('rank'): r for r in results
            if isinstance(r, dict) and r.get('rank') is not None}


def write_results(run_dir, records, model):
    """按 rank 排序写出 deep_results.json。"""
    ordered = sorted(records, key=lambda r: (r.get('rank') is None, r.get('rank')))
    output = {
        'version': '1.0',
        'analyzed_by': 'openai-compatible:%s' % model,
        'analyzed_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'results': ordered,
    }
    path = deep_results_path(run_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    return path


def main():
    reconfigure_stdout()

    ap = argparse.ArgumentParser(
        description='深度模式阶段 2：逐岗位调 LLM 分析，产出 deep_results.json',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='跑完接阶段 3:\n'
               '  python scripts/run_matcher.py --mode deep --merge --output-dir <run_dir>\n')
    ap.add_argument('run_dir', help='运行目录（含 deep_candidates.json）')
    ap.add_argument('--workers', '-w', type=int, help='并发数，默认取配置里的 concurrency')
    ap.add_argument('--limit', type=int, help='只分析前 N 个候选（试水用）')
    ap.add_argument('--resume', action='store_true', help='跳过 deep_results.json 里已有的 rank')
    ap.add_argument('--dry-run', action='store_true', help='只打印将发送的内容与调用次数，不请求')
    ap.add_argument('--jd-limit', type=int, default=2500, help='JD 截断长度（默认 2500 字）')
    ap.add_argument('--model', help='本次使用的模型，覆盖配置')
    ap.add_argument('--base-url')
    ap.add_argument('--api-key')
    args = ap.parse_args()

    run_dir = os.path.abspath(args.run_dir)
    if not os.path.isdir(run_dir):
        print('❌ 运行目录不存在: %s' % run_dir)
        return 1

    try:
        profile, candidates = load_candidates(run_dir)
    except FileNotFoundError as exc:
        print('❌ 找不到 %s' % exc)
        print('  先跑阶段 1: python scripts/run_matcher.py --mode deep '
              '--profile "%s/profile.json" --top 15 --output-dir "%s"' % (run_dir, run_dir))
        return 1
    except ValueError as exc:
        print('❌ deep_candidates.json 解析失败: %s' % exc)
        return 1

    if not candidates:
        print('❌ deep_candidates.json 里没有候选岗位')
        return 1

    cfg_overrides = {k: v for k, v in
                     (('model', args.model), ('base_url', args.base_url), ('api_key', args.api_key))
                     if v}
    try:
        cfg = resolve(stage='deep', **cfg_overrides)
    except ConfigError as exc:
        print('❌ 配置错误：\n%s' % exc)
        return 1
    workers = args.workers or cfg.concurrency

    try:
        resume_info = build_resume_info(profile)
    except LLMError as exc:
        print('❌ %s' % exc)
        return 1

    # ── 挑出要跑的候选 ──
    todo = candidates[:args.limit] if args.limit else list(candidates)
    existing = load_existing(run_dir) if args.resume else {}
    skipped = []
    if existing:
        keep = []
        for candidate in todo:
            if candidate.get('rank') in existing:
                skipped.append(candidate.get('rank'))
            else:
                keep.append(candidate)
        todo = keep

    print('📂 %s' % run_dir)
    print('候选 %d 个%s%s' % (
        len(candidates),
        '，--limit 取前 %d' % args.limit if args.limit else '',
        '，--resume 跳过 %d 个已有结果' % len(skipped) if skipped else ''))
    print('模型 %s / 并发 %d / JD 截断 %d 字' % (cfg.model, workers, args.jd_limit))

    if not todo:
        print('\n✅ 没有需要分析的候选（都已有结果）。直接跑阶段 3 即可。')
        return 0

    if args.dry_run:
        sample = get_match_analysis_prompt(
            resume_info, build_job_requirements(todo[0], args.jd_limit)) + _ADDENDUM
        print('\n--dry-run：将发起 %d 次请求，每次约 %d 字符（首条实测）。'
              % (len(todo), len(sample)))
        print('第一条的提示词前 800 字：\n' + '-' * 60)
        print(sample[:800])
        print('-' * 60)
        print('（未发送任何请求）')
        return 0

    # 边跑边收：Ctrl-C 或中途异常时也要把已完成的结果落盘，否则 --resume 无从续起。
    done_records = []
    lock = threading.Lock()

    def analyze(candidate):
        prompt = get_match_analysis_prompt(
            resume_info, build_job_requirements(candidate, args.jd_limit)) + _ADDENDUM
        raw = chat_json(prompt, stage='deep', run_dir=run_dir, cfg=cfg)
        record = normalize_result(raw, candidate)
        with lock:
            done_records.append(record)
        return record

    def label(candidate, _index):
        job = candidate.get('job') or {}
        return 'rank %-2s %s / %s' % (candidate.get('rank'),
                                      job.get('公司', '?'), job.get('职位', '?'))

    print('\n🧠 开始分析（并发 %d）…' % workers)
    interrupted = False
    outcomes = []
    started = time.time()
    try:
        # 埋点：CLI 路径的耗时同样进 run_timings.jsonl，`stage_timer.py report` 一起看
        with stage_timer.stage(run_dir, 'deep_analyze'):
            outcomes = map_concurrent(todo, analyze, workers=workers, label=label)
    except KeyboardInterrupt:
        interrupted = True
        print('\n⚠ 已中断。正在保存已完成的结果 …')
    elapsed = time.time() - started

    # ── 汇总 ──
    ok_records = list(done_records)
    failures = [o for o in outcomes if not o.ok]

    merged = dict(existing)
    for record in ok_records:
        merged[record.get('rank')] = record

    if not merged:
        print('\n❌ 一条都没成功，不写 deep_results.json。')
        _print_failures(failures)
        print('\n' + format_usage(run_dir))
        return 1

    path = write_results(run_dir, list(merged.values()), cfg.model)

    print('\n%s' % ('=' * 60))
    print('  成功 %d / 失败 %d%s%s（耗时 %.1fs）' % (
        len(ok_records), len(failures),
        ' / 沿用已有 %d' % len(skipped) if skipped else '',
        ' / 被中断' if interrupted else '', elapsed))
    print('  deep_results.json: %s（共 %d 条）' % (path, len(merged)))
    _print_failures(failures)

    # category 越界的告警：merge 能兜，但兜住意味着提示词的枚举约束没生效
    odd = [r for r in ok_records if r.get('category') not in _VALID_CATEGORIES]
    if odd:
        print('  ⚠ %d 条的 category 不在三个枚举内（merge 会按中文标签兜底）：%s'
              % (len(odd), '、'.join(sorted({r.get('category', '') for r in odd}))))

    print('\n' + format_usage(run_dir))
    print('\n下一步（阶段 3，合并规则分与模型分并出报告）：')
    print('  python scripts/run_matcher.py --mode deep --merge --output-dir "%s"' % run_dir)

    if failures or interrupted:
        print('\n失败的可以续跑（跳过已成功的）：')
        print('  python scripts/deep_analyze.py "%s" --resume' % run_dir)
        return 3
    return 0


def _print_failures(failures):
    """失败不阻断整批：merge 侧对未分析的候选本来就会沿用规则分类。"""
    if not failures:
        return
    print('  失败的候选（merge 时将沿用规则侧分类，不会丢岗位）：')
    for outcome in failures:
        job = (outcome.item or {}).get('job') or {}
        print('    rank %-2s %s / %s — %s' % (
            (outcome.item or {}).get('rank'), job.get('公司', '?'),
            job.get('职位', '?'), outcome.error[:120]))


if __name__ == '__main__':
    sys.exit(main())
