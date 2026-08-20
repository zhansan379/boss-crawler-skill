#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""岗位匹配（match）评估的唯一 CLI 入口。

对比「gold（独立金标准）」与 规则 / 深度 / 混合 三条预测路径，算四族指标并出报告。

流程：gold 来源 → 岗位 → 规则打分（恒跑）→ 深度/混合（可选）→ 四族指标 → 聚合+建议 → 报告。

三条预测路径：
  - rule（规则，恒跑）：scoring.score_job_advanced()（0-115 原生，比较前 /115*100 归一）。
  - deep（深度，可选）：读既有 deep_results.json（--deep-results，无 LLM 调用）或在线逐岗
    chat_json；deep/LLM 只产 missing_items、不产 matched_skills —— 固有不对称，报告写明。
  - blended（混合）：int(0.4*(rule/115*100) + 0.6*deep)，分类以 deep 为准（同 merge_deep_results）。

gold 三来源：
  --gold-fixtures    手工 fixture（离线、回归角点，自带岗位）
  --gold-ai N        AI 造岗内嵌 gold（触网，跨三档，落 gold_manifest.json 供离线重放）
  --judge-gold       对真实岗位做独立 LLM judge（触网，需 --jobs-* 供岗位，落 gold_manifest.json）

退出码：0 全好（部分缺 gold 仍 0 仅标注）；1 致命（无 profile/无岗位/无 gold/deep 缺结果）；
2 旗冲突（--offline + 需触网的 gold 来源且无 manifest 可重放）；3 部分失败（个别岗评分失败、
缺 deep rank、缺 gold，报告仍写）。

产物：{run_dir}/eval_matcher/{report.html, eval.json, gold_manifest.json}。
"""

import os
import sys
import json
import argparse
from concurrent.futures import ThreadPoolExecutor

# 本文件位于 scripts/eval/matcher/，上溯两级拿到 scripts/ 根，才能 import
# llm / scoring / deep_analyze / eval.materials / eval.matcher 等。
_SCRIPTS = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
for _p in (_SCRIPTS,
           os.path.join(_SCRIPTS, 'stages'),
           os.path.join(_SCRIPTS, 'verify'),
           os.path.join(_SCRIPTS, 'resume_matcher'),
           os.path.join(_SCRIPTS, 'eval', 'materials')):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def reconfigure_stdout():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:                                  # noqa: BLE001
            pass


DEEP_WEIGHT = 0.6
QUICK_WEIGHT = 0.4
RULE_MAX = 115.0


# ==================== profile / 岗位 / gold ====================

def load_profile(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def _profile_skills(profile):
    out = []
    for cat in ('programming', 'frameworks', 'tools', 'other'):
        out.extend((profile.get('skills') or {}).get(cat) or [])
    if not out:
        out = profile.get('keywords') or []
    return [str(s).strip() for s in out if str(s).strip()] or None


def _profile_keywords(profile):
    kw = profile.get('keywords') or []
    return [str(s).strip() for s in kw if str(s).strip()] or [
        'AI', 'RAG', 'Agent', 'LLM', '大模型', '后端开发', 'Python', 'Java', '全栈']


def resolve_gold(run_dir, profile, args, out_dir):
    """按 args 决定 gold 样本（每样本自带 job），并写 gold_manifest.json。"""
    from eval.matcher.matcher_gold import (
        load_hand_gold, build_gold_ai, judge_gold_jobs, write_manifest, load_manifest)

    manifest = os.path.join(out_dir, 'gold_manifest.json')

    if args.gold_fixtures:
        samples = load_hand_gold()
        return samples, 'hand'

    if args.gold_ai:
        if args.offline:
            if os.path.exists(manifest):
                return load_manifest(manifest), 'ai(manifest 离线重放)'
            print('❌ --offline 与 --gold-ai 冲突：AI 造岗需要触网，且无既有 gold_manifest.json 可重放。',
                  file=sys.stderr)
            sys.exit(2)
        from llm import resolve, ConfigError
        try:
            cfg = resolve(stage='gen_gold')
        except ConfigError as exc:
            print('❌ 配置错误：\n%s' % exc, file=sys.stderr)
            sys.exit(1)
        samples = build_gold_ai(args.gold_ai, profile, cfg, run_dir, args.gold_ai_spec or '')
        write_manifest(manifest, samples)
        print('✅ AI 造岗（内嵌 gold）%d 个 → %s' % (len(samples), manifest))
        return samples, 'ai'

    if args.judge_gold:
        if args.offline:
            if os.path.exists(manifest):
                return load_manifest(manifest), 'judge(manifest 离线重放)'
            print('❌ --offline 与 --judge-gold 冲突：LLM judge 需要触网，且无既有 gold_manifest.json 可重放。',
                  file=sys.stderr)
            sys.exit(2)
        jobs = resolve_jobs(run_dir, profile, args, offline=False)
        from stages.deep_analyze import build_resume_info
        from llm import resolve, ConfigError
        try:
            cfg = resolve(stage='judge_gold')
        except ConfigError as exc:
            print('❌ 配置错误：\n%s' % exc, file=sys.stderr)
            sys.exit(1)
        samples, _ = judge_gold_jobs(profile, jobs, cfg, run_dir)
        write_manifest(manifest, samples)
        print('✅ LLM judge %d 个真实岗位 → %s' % (len(samples), manifest))
        return samples, 'judge'

    return [], ''


def resolve_jobs(run_dir, profile, args, offline):
    """供 --judge-gold 的岗位来源（复用 materials.gen_test_jobs，不写 qualified_jobs.json）。"""
    from eval.materials.gen_test_jobs import load_jobs_csv, load_jobs_existing, build_jobs_ai
    if args.jobs_csv:
        return load_jobs_csv(args.jobs_csv)
    if args.jobs_ai:
        # 在线造岗当真实岗位用（gold 仍由 judge 独立评出）
        from llm import resolve, ConfigError
        from eval.materials.gen_test_jobs import build_jobs_ai as _bjai
        from stages.deep_analyze import build_resume_info
        try:
            cfg = resolve(stage='gen_test_jobs')
        except ConfigError as exc:
            print('❌ 配置错误：\n%s' % exc, file=sys.stderr)
            sys.exit(1)
        return _bjai(build_resume_info(profile), args.jobs_ai, cfg, run_dir, args.jobs_ai_spec or '')
    if args.jobs_existing:
        try:
            return load_jobs_existing(run_dir)
        except FileNotFoundError:
            return None
    return None


# ==================== 规则预测 ====================

def rule_predict(job, profile):
    """单岗规则打分（守卫复用 classify_jobs_advanced 的取值，避开 None 传播 TypeError）。

    生产批量入口 classify_jobs_advanced 会算出核心语言/AI 能力/技能权重一并传入；
    评估路径必须同样传，否则技术栈锁死在这里静默失效，fixture 与线上行为不一致。
    """
    from resume_matcher.scoring import (score_job_advanced, _core_languages,
                                        _candidate_has_ai, _normalize_skill,
                                        SKILL_CATEGORY_WEIGHTS)
    skills = _profile_skills(profile)
    salary = profile.get('salary_expectation') or {}
    sal_min = salary.get('min') or 8
    sal_max = salary.get('max') or 10
    exp = float(profile.get('experience', {}).get('total_years', 0) or 0)
    degree = profile.get('education', {}).get('degree', '') or ''
    skill_weights = {}
    for cat, w in SKILL_CATEGORY_WEIGHTS.items():
        for s in (profile.get('skills') or {}).get(cat) or []:
            skill_weights.setdefault(_normalize_skill(str(s)), w)
    result = score_job_advanced(
        job, resume_skills=skills, resume_keywords=_profile_keywords(profile),
        salary_min=sal_min, salary_max=sal_max,
        user_experience_years=exp, user_degree=degree,
        core_languages=_core_languages(profile),
        has_ai_capability=_candidate_has_ai(profile),
        skill_weights=skill_weights)
    return result


# ==================== 深度 / 混合预测 ====================

def load_deep_results(path):
    """读 deep_results.json 形状（{"results":[{rank,score,category,reason,missing_items}]}）。"""
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    records = {}
    src = data.get('results') if isinstance(data, dict) else (data or [])
    for r in src:
        rank = r.get('rank')
        if rank is not None:
            records[int(rank)] = r
    return records


def deep_predict_online(jobs, profile, cfg, run_dir, workers=4):
    """内存跑一遍深度预测（不写文件）。返回 {rank: record}。误触网在 offline 由守卫拦。"""
    from stages.deep_analyze import build_resume_info, build_job_requirements, normalize_result
    from resume_matcher.prompts import get_match_analysis_prompt
    from llm import chat_json
    resume_info = build_resume_info(profile)
    records = {}

    def work(i):
        job = jobs[i - 1]
        candidate = {'rank': i, 'job': job}
        prompt = get_match_analysis_prompt(resume_info, build_job_requirements(candidate))
        raw = chat_json(prompt, stage='deep', run_dir=run_dir, cfg=cfg)
        return i, normalize_result(raw, candidate)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, rec in ex.map(work, range(1, len(jobs) + 1)):
            records[i] = rec
    return records


# ==================== 预测变体 → 四族评估 ====================

def _blend(rule_result, deep_record, rule_category):
    rule100 = min(100.0, float(rule_result['match_score']) / RULE_MAX * 100.0)
    if deep_record is None:
        return {'category': rule_category, 'score100': round(rule100)}
    deep_score = deep_record.get('score') or deep_record.get('overall_match') or deep_record.get('deep_score') or rule100
    return {'category': rule_category, 'score100': int(
        QUICK_WEIGHT * rule100 + DEEP_WEIGHT * float(deep_score))}


def build_variants(samples, rule_results, deep_records):
    """把 gold 样本 + rule/deep 合成 rule/deep/blended 三套预测（每套带 gold，供评估）。

    rule 恒有；deep/blended 只在有对应 deep-rank 的岗上产出（缺 deep 就缺席 → 该岗不进
    深度变体的评估池，report 按更小 n 算数）。
    """
    variants = {'rule': [], 'deep': [], 'blended': []}
    for i, sample in enumerate(samples, 1):
        rule = rule_results.get(i) or {}
        gold = sample.get('gold') or {}
        base = {'index': i, 'link': sample.get('link', ''), 'gold': gold}
        rc = rule.get('application_category') or 'need_optimization'
        rule100 = min(100.0, float(rule.get('match_score', 50)) / RULE_MAX * 100.0)
        variants['rule'].append({**base,
                                 'category': rc, 'score100': round(rule100),
                                 'matched_skills': rule.get('matched_skills') or [],
                                 'missing_skills': rule.get('missing_skills') or []})
        deep = deep_records.get(i)
        if deep is None:
            continue                                # 本岗无深度 → deep/blended 缺席
        from resume_matcher.deep_analysis import _normalize_category
        dc = _normalize_category(str(deep.get('category') or '')) or rc
        dm = deep.get('missing_items') or deep.get('missing_skills') or []
        variants['deep'].append({**base, 'category': dc,
                                 'score100': deep.get('score', 50),
                                 'matched_skills': None, 'missing_skills': dm})
        bl = _blend(rule, deep, dc)
        variants['blended'].append({**base, 'category': bl['category'],
                                    'score100': bl['score100'],
                                    'matched_skills': None, 'missing_skills': dm})
    return variants


def run_variants(samples, rule_results, deep_records, args):
    """按 --mode 选出要评估的变体，逐变体跑 evaluate_matching。"""
    from eval.matcher.matcher_metrics import evaluate_matching
    variants = build_variants(samples, rule_results, deep_records)
    choice = args.mode  # quick | deep | both
    result = {}
    if choice in ('quick', 'both'):
        result['rule'] = evaluate_matching(samples, variants['rule'],
                                           k=args.k, relevance=args.relevance,
                                           normalize=not args.no_normalize)
    if choice in ('deep', 'both') and variants['deep']:
        result['deep'] = evaluate_matching(samples, variants['deep'],
                                           k=args.k, relevance=args.relevance,
                                           normalize=not args.no_normalize)
        result['blended'] = evaluate_matching(samples, variants['blended'],
                                              k=args.k, relevance=args.relevance,
                                              normalize=not args.no_normalize)
    return result, variants


# ==================== 报告落盘 ====================

def build_resume_view(profile):
    """简历概览（报告顶部区块）：技能/经验/学历/期望薪资，纯展示。"""
    p = profile or {}
    skills = _profile_skills(p) or []
    sal = (p.get('salary_expectation') or {})
    return {
        'skills': skills,
        'experience': (p.get('experience') or {}).get('total_years'),
        'degree': (p.get('education') or {}).get('degree', ''),
        'salary_min': sal.get('min'),
        'salary_max': sal.get('max'),
    }


def write_report(out_dir, recommend_result, jobs_view, meta, gold_sources, resume_view=None):
    from eval.matcher.matcher_report_html import render_html, write_eval_json
    os.makedirs(out_dir, exist_ok=True)
    html_path = os.path.join(out_dir, 'report.html')
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(render_html(recommend_result, jobs_view, meta, gold_sources, resume_view))
    json_path = write_eval_json(os.path.join(out_dir, 'eval.json'),
                                recommend_result, jobs_view, meta)
    return html_path, json_path


def build_jobs_view(samples, rule_results, deep_records, variants, mode):
    """逐岗行视图：gold + rule/deep/blended 的 category/score 供肉眼核对。"""
    view = []
    for i, sample in enumerate(samples, 1):
        rule = rule_results.get(i) or {}
        gold = sample.get('gold') or {}
        job = sample.get('job', {})
        row = {
            'index': i,
            'company': job.get('公司', ''),
            'position': job.get('职位', ''),
            'link': sample.get('link', ''),
            # 完整岗位信息，供详情展开
            'job': {'公司': job.get('公司', ''), '职位': job.get('职位', ''),
                    '薪资': job.get('薪资', ''), '经验': job.get('经验', ''),
                    '学历': job.get('学历', ''), '技能标签': job.get('技能标签', ''),
                    '岗位要求和职责': job.get('岗位要求和职责', '')},
            'gold': {'category': gold.get('category'), 'score': gold.get('score'),
                     'reason': gold.get('reason', ''),
                     'matched_skills': gold.get('matched_skills') or [],
                     'missing_skills': gold.get('missing_skills') or []},
            'rule': {'category': rule.get('application_category'),
                     'score100': round(min(100, float(rule.get('match_score', 0)) / RULE_MAX * 100)),
                     'score_native': rule.get('match_score'),
                     'match_reasons': rule.get('match_reasons') or [],
                     'category_reason': rule.get('application_category_reason', ''),
                     'salary_score': rule.get('salary_score'),
                     'experience_score': rule.get('experience_score'),
                     'degree_score': rule.get('degree_score'),
                     'skills_score': rule.get('skills_score'),
                     'position_score': rule.get('position_score'),
                     'ai_bonus': rule.get('ai_bonus')},
            'deep': {'category': None, 'score': None},
            'blended': {'category': None, 'score': None},
            'matched_rule': rule.get('matched_skills') or [],
            'missing_rule': rule.get('missing_skills') or [],
            'gold_matched': gold.get('matched_skills') or [],
            'gold_missing': gold.get('missing_skills') or [],
        }
        deep = deep_records.get(i)
        if deep is not None and mode in ('deep', 'both'):
            from resume_matcher.deep_analysis import _normalize_category
            dc = _normalize_category(str(deep.get('category') or '')) \
                or rule.get('application_category')
            row['deep'] = {'category': dc, 'score': deep.get('score'),
                           'reason': deep.get('reason', ''),
                           'missing_items': deep.get('missing_items') or []}
            bl = _blend(rule, deep, dc)
            row['blended'] = {'category': bl['category'], 'score100': bl['score100']}
        view.append(row)
    return view


# ==================== CLI ====================

def main():
    reconfigure_stdout()
    ap = argparse.ArgumentParser(
        description='匹配环节评估：gold vs 规则/深度/混合 四族指标（分类/误差/排序/技能）',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='三变体（--mode）：\n'
               '  quick   仅规则分\n'
               '  deep    深度分（+混合，需 --deep-results 或联网）\n'
               '  both    规则 + 深度 + 混合（默认）\n'
               'gold 三来源：--gold-fixtures（离线回归）/ --gold-ai N（AI 造岗内嵌）/\n'
               '          --judge-gold（对 {{--jobs-csv|--jobs-ai|--jobs-existing}} 的真实岗位 LLM judge）\n'
               '离线确定性：规则纯函数恒离线；AI/judge 触网并落 gold_manifest.json，--offline 重放。')
    ap.add_argument('run_dir', help='工作运行目录')
    ap.add_argument('--profile', help='简历 profile.json 路径（--gold-ai / --judge-gold 必需；'
                                      '--gold-fixtures 禁止，评分用数据集内置简历）')
    g = ap.add_mutually_exclusive_group()
    g.add_argument('--gold-fixtures', action='store_true', help='用代码内手工 fixture 当 gold')
    g.add_argument('--gold-ai', type=int, metavar='N', help='AI 造 N 个岗位并内嵌 gold（触网）')
    g.add_argument('--judge-gold', action='store_true', help='对真实岗位做 LLM judge 当 gold（触网）')
    ap.add_argument('--gold-ai-spec', default='', help='AI 造岗（含 gold）的多样性补充说明')
    j = ap.add_mutually_exclusive_group()
    j.add_argument('--jobs-csv', help='真实岗位 CSV（--judge-gold 用）')
    j.add_argument('--jobs-ai', type=int, metavar='N', help='AI 造真实岗位（--judge-gold 用，触网）')
    j.add_argument('--jobs-existing', action='store_true', help='复用 run_dir 既有 qualified_jobs.json')
    ap.add_argument('--mode', choices=['quick', 'deep', 'both'], default='both')
    ap.add_argument('--deep-results', help='既有 deep_results.json（无 LLM 调用，按 rank 回填）')
    ap.add_argument('--blend-weight', type=float, default=DEEP_WEIGHT, help='deep 权重（默认 0.6）')
    ap.add_argument('--k', type=int, help='nDCG@k（默认全程）')
    ap.add_argument('--relevance', choices=['category', 'score'], default='category',
                    help='排序相关性口径（category=序数稳健 / score=连续更灵敏）')
    ap.add_argument('--no-normalize', action='store_true', help='技能比较不折叠别名/大小写')
    ap.add_argument('--offline', action='store_true', help='全程不触网（AI/judge gold 需 manifest 重放）')
    ap.add_argument('--out-dir', help='报告输出目录（默认 <run_dir>/eval_matcher/）')
    ap.add_argument('--workers', '-w', type=int, default=4)
    ap.add_argument('--llm-recommend', action='store_true', help='额外调模型做综合点评（避开 offline）')
    ap.add_argument('--model'); ap.add_argument('--base-url'); ap.add_argument('--api-key')
    args = ap.parse_args()

    run_dir = os.path.abspath(args.run_dir)
    os.makedirs(run_dir, exist_ok=True)
    out_dir = os.path.abspath(args.out_dir) if args.out_dir else os.path.join(run_dir, 'eval_matcher')

    if args.offline and args.llm_recommend:
        print('❌ --offline 与 --llm-recommend 冲突：点评要调模型。', file=sys.stderr)
        return 2
    if args.gold_ai is None and not args.gold_fixtures and not args.judge_gold:
        print('❌ 需要一种 gold 来源：--gold-fixtures / --gold-ai N / --judge-gold。', file=sys.stderr)
        return 1
    if args.judge_gold and not (args.jobs_csv or args.jobs_ai or args.jobs_existing):
        print('❌ --judge-gold 需要一个岗位来源：--jobs-csv / --jobs-ai / --jobs-existing。', file=sys.stderr)
        return 1
    # --gold-fixtures 是「内置简历 + 内置数据集」密封对：评分禁止外部自定义简历，否则 gold
    # 标注（针对内置简历校准）与运行时简历错位，测出的准度失真。
    if args.gold_fixtures and args.profile:
        print('❌ --gold-fixtures 模式只允许使用内置简历，请勿提供 --profile '
              '（评分会自动采用数据集内联的内置简历）。', file=sys.stderr)
        return 1
    if not args.gold_fixtures and not args.profile:
        print('❌ --gold-ai / --judge-gold 需要 --profile 简历路径。', file=sys.stderr)
        return 1

    # 1) gold（及样本自带的岗位）
    #    profile：--gold-fixtures 下由数据集内联（稍后从样本提取）；否则读用户 --profile。
    profile = None
    if not args.gold_fixtures:
        try:
            profile = load_profile(args.profile)
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            print('❌ profile 读取失败：%s' % exc, file=sys.stderr)
            return 1
    try:
        samples, gold_sources = resolve_gold(run_dir, profile, args, out_dir)
    except Exception as exc:                                   # noqa: BLE001
        print('❌ gold 来源失败：%s' % exc, file=sys.stderr)
        return 1
    if not samples:
        print('❌ 没有任何 gold 样本。', file=sys.stderr)
        return 1
    if args.gold_fixtures:
        # 评分一律采用数据集内置简历（所有样本内联同一份）。
        profile = next((s.get('profile') for s in samples if s.get('profile')), None)
        if profile is None:
            print('❌ 内置数据集缺少内联简历，--gold-fixtures 无法运行。', file=sys.stderr)
            return 1
    if args.jobs_csv and args.gold_fixtures:
        print('ℹ --jobs-csv 对 --gold-fixtures 不生效（fixture 自带岗位），忽略。')

    if args.offline:
        from eval.materials.stubs import offline_guard
        import llm as _llm
        # 所有触网调用（gold-ai / judge-gold / jobs-ai / deep 在线）都走 `from llm import
        # chat_json`（函数内 import，读 sys.modules['llm']），只 patch llm 模块的三个引用
        # 即可拦全部；且 llm 必有这三个属性，offline_guard 不会 AttributeError。
        with offline_guard(_llm):
            return _run(run_dir, out_dir, profile, samples, args, gold_sources)
    return _run(run_dir, out_dir, profile, samples, args, gold_sources)


def _run(run_dir, out_dir, profile, samples, args, gold_sources):
    partial_errors = []

    # 2) 规则打分（恒跑，纯函数）
    rule_results, fail_count = {}, 0
    for i, sample in enumerate(samples, 1):
        try:
            rule_results[i] = rule_predict(sample.get('job') or {}, profile)
        except Exception as exc:                               # noqa: BLE001
            rule_results[i] = None
            fail_count += 1
            partial_errors.append({'index': i, 'link': sample.get('link'),
                                   'where': 'rule', 'error': str(exc)})
    if rule_results and not any(rule_results.values()):
        print('❌ 规则打分全部失败。', file=sys.stderr)
        return 1
    if fail_count:
        print('⚠ %d 个岗位规则打分失败（计入报告 errors）。' % fail_count)

    # 3) 深度 / 混合
    deep_records = {}
    if args.mode in ('deep', 'both'):
        if args.deep_results:
            try:
                deep_records = load_deep_results(args.deep_results)
            except (FileNotFoundError, json.JSONDecodeError) as exc:
                print('❌ --deep-results 读取失败：%s' % exc, file=sys.stderr)
                return 1
        elif not args.offline:
            from llm import resolve, ConfigError
            try:
                cfg = resolve(stage='deep')
            except ConfigError as exc:
                print('❌ 配置错误：\n%s' % exc, file=sys.stderr)
                return 1
            deep_records = deep_predict_online([s.get('job') or {} for s in samples],
                                               profile, cfg, run_dir, args.workers)
        else:
            print('⚠ offline 且无 --deep-results：跳过 deep/blended 变体（仅规则）。')

    missing_ranks = [i for i in range(1, len(samples) + 1) if i not in deep_records]
    if (args.mode in ('deep', 'both')) and not args.deep_results and not args.offline and missing_ranks:
        pass   # 在线内存跑本应全覆盖；缺失只是极少数异常
    if args.deep_results and missing_ranks and args.mode in ('deep', 'both'):
        partial_errors.append({'where': 'deep',
                               'error': 'deep_results 缺 rank %s（这些岗无 deep/blended 变体）'
                                        % missing_ranks})
        if not deep_records:
            print('❌ --deep-results 没有与岗位对齐的任何 rank。', file=sys.stderr)
            return 3

    # 4) 四族指标
    results, variants = run_variants(samples, rule_results, deep_records, args)
    # 缺 deep rank 的岗在 variants['deep']/['blended'] 里本来就被跳过
    payload = [{'name': name, 'result': r,
                'note': {'rule': '规则分 (0-115→100)',
                         'deep': '深度分·只比 missing（无 matched）',
                         'blended': '0.4·规则+0.6·深度'}.get(name, '')}
               for name, r in results.items()]

    # 5) 聚合 + 建议 (+LLM 点评)
    from eval.matcher.matcher_recommend import recommend, recommend_llm
    llm_comment = ''
    if args.llm_recommend and not args.offline:
        from llm import resolve, ConfigError
        try:
            cfg = resolve(stage='eval_matcher_recommend')
            from eval.matcher.matcher_recommend import aggregate
            llm_comment = recommend_llm(aggregate(payload), cfg, run_dir)
        except (ConfigError, Exception) as exc:              # noqa: BLE001
            print('⚠ LLM 点评跳过: %s' % exc)
    rec = recommend(payload, llm_comment=llm_comment)

    _mode_word = {'rule': '只用规则分', 'deep': '只用AI分', 'both': '规则+AI分'}
    _rel_word = {'category': '按分档比对', 'score': '按具体分数比对'}
    _profile_label = '内置简历(fixtures)' if args.gold_fixtures \
        else os.path.basename(args.profile or 'profile.json')
    meta = '%s · 简历 %s · %s · %s' % (
        os.path.basename(run_dir or ''), _profile_label,
        _mode_word.get(args.mode, args.mode),
        _rel_word.get(args.relevance, args.relevance))

    jobs_view = build_jobs_view(samples, rule_results, deep_records,
                                {'rule': None, 'deep': None, 'blended': None}, args.mode)
    try:
        html_path, json_path = write_report(out_dir, rec, jobs_view, meta, gold_sources,
                                            resume_view=build_resume_view(profile))
    except Exception as exc:                                   # noqa: BLE001
        print('❌ 报告写出失败：%s' % exc, file=sys.stderr)
        return 1

    agg = rec.get('aggregate', {}).get('overall', {})
    print('\n📊 匹配评估完成：%d 岗 · %d 个变体' % (len(samples), len(payload)))
    print('   平均分类准确率 %.0f%% · MAE %.1f · bias %+.1f · nDCG %.2f'
          % (agg.get('avg_accuracy', 0) * 100, agg.get('avg_mae', 0),
             agg.get('avg_bias', 0), agg.get('avg_ndcg', 0)))
    print('   qualified F1 %.2f · need_opt F1 %.2f · cannot_apply F1 %.2f'
          % (agg.get('avg_qualified_f1', 0), agg.get('avg_need_optimization_f1', 0),
             agg.get('avg_cannot_apply_f1', 0)))
    print('   建议 %d 条' % len(rec.get('suggestions', [])))
    print('   📄 %s' % html_path)
    if partial_errors:
        print('⚠ 部分失败 %d 处' % len(partial_errors))
        for e in partial_errors[:8]:
            print('   · [%s] %s' % (e.get('where', '?'), e.get('error')))
        return 3
    return 0


if __name__ == '__main__':
    sys.exit(main())