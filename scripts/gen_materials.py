#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""为 qualified_jobs.json 里的每个岗位生成招呼语 + 优化简历（并发调 LLM）。

产物文件名是下游的硬契约，`check_artifacts.py` / `write_application_md.py` /
ShowCV 渲染都按它对齐：

    {run_dir}/generated/greeting_{i}_{公司}.txt    纯文本招呼语
    {run_dir}/generated/resume_{i}_{公司}.json     resume_optimize.st 的整个 JSON 对象

`{i}` 是岗位在 qualified_jobs.json 里的 1-based 序号 —— 下游全靠这个序号对齐，所以
即使某个岗位失败，其余岗位的序号也不能挪动。

用法：
    python scripts/gen_materials.py <run_dir>
    python scripts/gen_materials.py <run_dir> --greeting-mode default   # 不调模型，套模板
    python scripts/gen_materials.py <run_dir> --resume-mode skip        # 只出招呼语
    python scripts/gen_materials.py <run_dir> --only 1,3,5              # 只补这几个
    python scripts/gen_materials.py <run_dir> --force                   # 覆盖已有产物
    python scripts/gen_materials.py <run_dir> --scene 实习 --dry-run

退出码：0 = 需要的产物齐全，1 = 输入缺失/全部失败，3 = 部分失败。
"""

import os
import re
import sys
import json
import time
import argparse
import threading

import stage_timer
from resume_matcher.prompts import get_greeting_prompt, get_optimize_prompt
from resume_matcher.config import ResumeProfile
from llm import (
    ConfigError, LLMError, chat, chat_json, map_concurrent, resolve,
    strip_fence, format_usage, reconfigure_stdout,
)

GEN_DIR = 'generated'

# Windows 文件名禁用字符 + 空白：公司名里出现 "（上海）有限公司/分公司" 这类写法很常见。
# `[` `]` 不是文件名非法字符，但 write_application_md.resolve_greeting 用 glob 回找产物，
# 而 glob 会把方括号当字符集 —— 名字里带 `[` 的文件会永远匹配不上。
_BAD_CHARS = re.compile(r'[\\/:*?"<>|\[\]\r\n\t]+')


def safe_name(text, limit=24):
    """公司名 → 可用作文件名的片段。

    只做替换不做删除：`check_artifacts` 只比对 `{kind}_{i}_` 前缀，后半段仅供人眼
    辨认，所以这里的目标是「不炸文件系统」，不是「可逆」。
    """
    s = _BAD_CHARS.sub('_', str(text or '').strip())
    s = s.replace(' ', '')
    return (s[:limit] or 'unknown')


# ==================== 输入 ====================

def load_jobs(run_dir):
    path = os.path.join(run_dir, 'qualified_jobs.json')
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    if isinstance(data, dict):                      # 容忍被包一层，与 check_artifacts 同口径
        data = data.get('jobs') or data.get('data') or []
    if not isinstance(data, list):
        raise ValueError('qualified_jobs.json 不是数组')
    return data


def load_profile(run_dir):
    path = os.path.join(run_dir, 'profile.json')
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def _norm_list(value, limit=None):
    if isinstance(value, list):
        out = [str(v).strip() for v in value if v is not None and str(v).strip()]
    elif value and isinstance(value, str):
        out = [value.strip()]
    else:
        out = []
    return out[:limit] if limit else out


# build_match_index 挪到了 match_index.py，好让读取侧（read_thin.py --kind ranked）
# 用上同一份三文件合并逻辑 —— 之前它只长在写材料侧，主代理想看「分数+判定+公司+职位」
# 只能自己拼。这里 re-export，本文件其余代码照旧调用。
from match_index import build_match_index          # noqa: E402,F401  (re-export)


# ==================== 简历摘要 / 场景 ====================

def build_resume_summary(profile, limit=700):
    """给招呼语用的简历摘要：短、只含事实、不含联系方式。

    招呼语模板会把这段当作「可引用的素材库」，越短模型越不容易去引用无关内容；
    同时**不放电话邮箱** —— 招呼语里带联系方式既没用（平台内已通）又是泄露。
    """
    parts = []
    edu = profile.get('education') or {}
    line = '、'.join(str(edu[k]) for k in ('school', 'degree', 'major', 'graduation_year')
                    if edu.get(k))
    if line:
        parts.append('教育：' + line)

    exp = profile.get('experience') or {}
    years = exp.get('total_years')
    if years:
        parts.append('工作年限：%s 年' % years)
    for company in (exp.get('companies') or [])[:3]:
        if not isinstance(company, dict):
            continue
        head = '%s %s' % (company.get('name', ''), company.get('position', ''))
        highlights = [str(h) for h in (company.get('highlights') or [])[:2]]
        parts.append('经历：%s%s' % (head.strip(),
                                   '（%s）' % '；'.join(highlights) if highlights else ''))

    skills = profile.get('skills') or {}
    flat = []
    for key, value in skills.items():
        if key == 'summary':
            continue
        if isinstance(value, list):
            flat.extend(str(v) for v in value)
    if flat:
        parts.append('技能：' + '、'.join(flat[:18]))

    for project in (profile.get('projects') or [])[:3]:
        if not isinstance(project, dict):
            continue
        highlights = [str(h) for h in (project.get('highlights') or [])[:2]]
        parts.append('项目：%s%s' % (project.get('name', ''),
                                   '（%s）' % '；'.join(highlights) if highlights else ''))

    summary = '\n'.join(parts)
    return summary[:limit]


def format_availability(profile):
    """basic_info.availability → 一行文本，没写就明确说没写。

    模板对这三项有专门的「没给就别猜」分支，需要一个明确取值才生效；留空会被模型
    读成「这栏我自己想」。见 prompts.py:get_greeting_prompt 的注释。
    """
    avail = (profile.get('basic_info') or {}).get('availability')
    if not isinstance(avail, dict):
        return ''
    labels = (('can_start', '可到岗'), ('duration', '可实习时长'), ('days_per_week', '每周出勤'))
    parts = ['%s: %s' % (label, avail[key]) for key, label in labels if avail.get(key)]
    return '、'.join(parts)


def infer_scene(profile):
    """猜投递场景（社招 / 校招 / 实习）。

    只是给模板的 hint —— 模板明确写了「scene_hint 是提示不是命令，可据简历修正」，
    所以猜错的代价是模型自己纠正一次，不是产出错误。
    """
    basic = profile.get('basic_info') or {}
    status = str(basic.get('status') or '').strip()
    if '实习' in status:
        return '实习'
    exp = profile.get('experience') or {}
    try:
        years = float(exp.get('total_years') or 0)
    except (TypeError, ValueError):
        years = 0
    if years >= 1:
        return '社招'
    edu = profile.get('education') or {}
    if edu.get('graduation_year'):
        return '校招'
    return ''


def build_generic_resume_text(profile):
    """profile → 通用简历正文，作为 resume_text.txt 缺失时的优化基底。

    对应投递选项里的 `不发送`：没有原文时也要有一份可优化的
    结构化底稿，而不是把空字符串喂给优化模板（那等于请模型凭空写一份简历）。
    """
    lines = []
    edu = profile.get('education') or {}
    if edu:
        lines.append('## 教育背景')
        lines.append('、'.join('%s' % v for v in
                               (edu.get('school'), edu.get('degree'), edu.get('major'),
                                edu.get('graduation_year')) if v))

    skills = profile.get('skills') or {}
    if skills:
        lines.append('\n## 专业技能')
        for key, value in skills.items():
            if isinstance(value, list) and value:
                lines.append('- %s：%s' % (key, '、'.join(str(v) for v in value)))
            elif value and not isinstance(value, (list, dict)):
                lines.append('- %s：%s' % (key, value))

    projects = profile.get('projects') or []
    if projects:
        lines.append('\n## 项目经历')
        for project in projects:
            if not isinstance(project, dict):
                continue
            lines.append('### %s' % project.get('name', ''))
            for key in ('role', 'tech_stack', 'description'):
                if project.get(key):
                    lines.append('- %s' % project[key])
            for highlight in (project.get('highlights') or []):
                lines.append('- %s' % highlight)

    exp = profile.get('experience') or {}
    companies = exp.get('companies') or []
    if companies:
        lines.append('\n## 工作经历')
        for company in companies:
            if not isinstance(company, dict):
                continue
            lines.append('### %s / %s / %s' % (
                company.get('name', ''), company.get('position', ''),
                company.get('duration', company.get('period', ''))))
            for highlight in (company.get('highlights') or []):
                lines.append('- %s' % highlight)

    return '\n'.join(lines)


def load_resume_text(run_dir, profile, override=None):
    """优化基底：--resume-text 指定的文件 > resume_text.txt > profile 生成的通用稿。"""
    for path in (override, os.path.join(run_dir, 'resume_text.txt')):
        if path and os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                text = f.read().strip()
            if len(text) >= 50:
                return text, os.path.basename(path)
    text = build_generic_resume_text(profile)
    if len(text) < 30:
        raise LLMError('既没有 resume_text.txt，profile 也太空，无法生成优化基底')
    return text, 'profile.json（通用结构）'


# ==================== 生成 ====================

_GREETING_FIX = (
    '上面这条招呼语的前 15 个字被客套话占掉了。HR 在消息列表里只能看到前 15 个字，'
    '那是标题不是寒暄。请重写：第一句话就给出最硬的事实（到岗时间 / 年限+一个数字 / '
    '学校届别），不要以「您好」「你好」「我是」「我对」开头。只输出招呼语本文。'
)


def gen_greeting(job, profile, match, cfg, run_dir, scene, mode):
    """一个岗位的招呼语。返回 (文本, 是否重写过)。"""
    if mode == 'default':
        # 不调模型：套 auto_apply 里的规则模板（离线、免费、质量一般）
        from resume_matcher.auto_apply import generate_greeting
        return generate_greeting(profile=_to_profile_obj(profile), job=job), False

    prompt = get_greeting_prompt(
        job,
        name=(profile.get('basic_info') or {}).get('name') or '',
        resume_summary=build_resume_summary(profile),
        match_reasons='；'.join(match.get('match_reasons') or []) or '',
        availability=format_availability(profile),
        scene_hint=scene,
    )
    text = chat(prompt, stage='greeting', run_dir=run_dir, cfg=cfg).strip()

    # 模板说了「不要代码块」，但真出现时得剥掉，否则前 15 字全是 ```
    if text.startswith('```'):
        text = strip_fence(text).strip()

    # 前 15 字是整条消息唯一被看见的部分，值得为它多花一次调用
    from resume_matcher.auto_apply import has_wasted_preview
    rewritten = False
    if has_wasted_preview(text):
        messages = [{'role': 'user', 'content': prompt},
                    {'role': 'assistant', 'content': text},
                    {'role': 'user', 'content': _GREETING_FIX}]
        retry = chat(messages=messages, stage='greeting:fix-preview',
                    run_dir=run_dir, cfg=cfg).strip()
        if retry and not has_wasted_preview(retry):
            text, rewritten = retry, True

    if not text:
        raise LLMError('模型返回了空招呼语')
    return text, rewritten


def gen_resume(job, resume_text, match, cfg, run_dir):
    """一个岗位的优化简历。返回 resume_optimize.st 的整个 JSON 对象。"""
    prompt = get_optimize_prompt(
        resume_text=resume_text,
        company=job.get('公司', '') or '',
        position=job.get('职位', '') or '',
        salary=job.get('薪资', '') or '',
        requirements=(job.get('岗位要求和职责', '') or job.get('技能标签', '') or '')[:2000],
        match_score=match.get('match_score') or 0,
        missing_items='、'.join(match.get('missing_items') or []) or '（无）',
        optimization_points='、'.join(match.get('optimization_points') or []) or '（无）',
    )
    data = chat_json(prompt, stage='resume', run_dir=run_dir, cfg=cfg)
    if not isinstance(data, dict):
        raise LLMError('返回的不是 JSON 对象')

    body = data.get('optimized_resume')
    if not isinstance(body, str) or len(body.strip()) < 50:
        # render 阶段直接渲染这个字段：空的话会渲染出一张空白简历图并被投出去
        raise LLMError('optimized_resume 缺失或过短（%s）'
                       % (len(body) if isinstance(body, str) else type(body).__name__))
    if body.strip().startswith('```'):
        data['optimized_resume'] = strip_fence(body).strip()
    return data


def _to_profile_obj(profile):
    """dict → ResumeProfile（只有 --greeting-mode default 的规则模板需要）。"""
    return ResumeProfile(
        basic_info=profile.get('basic_info') or {},
        education=profile.get('education') or {},
        experience=profile.get('experience') or {},
        skills=profile.get('skills') or {},
        projects=profile.get('projects') or [],
        awards=profile.get('awards') or [],
        publications=profile.get('publications') or [],
        social_links=profile.get('social_links') or {},
        salary_expectation=profile.get('salary_expectation') or {},
        keywords=profile.get('keywords') or [],
        raw_text=profile.get('raw_text') or '',
    )


def existing_artifact(gen_dir, kind, index):
    """已落盘且非空的产物文件名，没有返回 None（口径与 check_artifacts 一致）。"""
    prefix = '%s_%d_' % (kind, index)
    if not os.path.isdir(gen_dir):
        return None
    for name in sorted(os.listdir(gen_dir)):
        if not name.startswith(prefix):
            continue
        try:
            if os.path.getsize(os.path.join(gen_dir, name)) > 0:
                return name
        except OSError:
            continue
    return None


def _write_atomic(path, text):
    """先写 .part 再改名。

    check_artifacts 用「文件存在且非空」当 barrier，边写边被看到会让一份半截的
    简历通过校验。改名在同一目录内是原子的。
    """
    tmp = path + '.part'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(text)
    os.replace(tmp, path)


# ==================== 主流程 ====================

# --only 的解析挪到了 check_artifacts.py（那边没有依赖）。这里 re-export，让
# 「生成哪些」和「核对哪些」共用同一份解析 —— 两份各自漂移过一次，序号错位很难查。
from check_artifacts import parse_only          # noqa: F401  (re-export)


def main():
    reconfigure_stdout()

    ap = argparse.ArgumentParser(
        description='为 qualified_jobs.json 的每个岗位生成招呼语与优化简历',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='产物落在 <run_dir>/generated/，文件名契约与 check_artifacts.py 一致。\n'
               '跑完可选：python scripts/render_images.py <run_dir>（简历转图片）\n')
    ap.add_argument('run_dir', help='运行目录（含 qualified_jobs.json 与 profile.json）')
    ap.add_argument('--greeting-mode', choices=('ai', 'default', 'skip'), default='ai',
                    help='ai=调模型（默认）；default=套规则模板不花钱；skip=不生成')
    ap.add_argument('--resume-mode', choices=('ai', 'skip'), default='ai',
                    help='ai=调模型优化简历（默认）；skip=不生成')
    ap.add_argument('--resume-text', help='优化基底文件，默认用 <run_dir>/resume_text.txt')
    ap.add_argument('--scene', help='投递场景提示（社招/校招/实习），默认按简历推断')
    ap.add_argument('--only', help='只处理这些 1-based 序号，如 1,3,5-7')
    ap.add_argument('--force', action='store_true', help='覆盖已有产物（默认跳过已有的）')
    ap.add_argument('--workers', '-w', type=int, help='并发数，默认取配置里的 concurrency')
    ap.add_argument('--dry-run', action='store_true', help='只打印计划，不请求、不写文件')
    ap.add_argument('--model')
    ap.add_argument('--base-url')
    ap.add_argument('--api-key')
    args = ap.parse_args()

    run_dir = os.path.abspath(args.run_dir)
    if not os.path.isdir(run_dir):
        print('❌ 运行目录不存在: %s' % run_dir)
        return 1

    try:
        jobs = load_jobs(run_dir)
    except FileNotFoundError as exc:
        print('❌ 找不到 %s' % exc)
        print('  先跑匹配：python scripts/run_matcher.py …（会自动生成 qualified_jobs.json）')
        return 1
    except ValueError as exc:
        print('❌ qualified_jobs.json 解析失败: %s' % exc)
        return 1
    if not jobs:
        print('❌ qualified_jobs.json 是空的，没有岗位需要生成材料')
        return 1

    try:
        profile = load_profile(run_dir)
    except FileNotFoundError as exc:
        print('❌ 找不到 %s' % exc)
        print('  先跑：python scripts/parse_resume.py <简历文件> --output-dir "%s"' % run_dir)
        return 1
    except ValueError as exc:
        print('❌ profile.json 解析失败: %s' % exc)
        return 1

    kinds = []
    if args.greeting_mode != 'skip':
        kinds.append('greeting')
    if args.resume_mode != 'skip':
        kinds.append('resume')
    if not kinds:
        print('❌ --greeting-mode 与 --resume-mode 都是 skip，没有要做的事')
        return 1

    try:
        only = parse_only(args.only, len(jobs))
    except ValueError as exc:
        print('❌ %s' % exc)
        return 1

    needs_llm = args.greeting_mode == 'ai' or args.resume_mode == 'ai'
    # 两个阶段各自解析配置：招呼语短、便宜，简历改写长、吃能力，配置文件里的
    # stages.greeting / stages.resume 就是为了让它们指向不同的模型。
    cfg_greeting = cfg_resume = None
    overrides = {k: v for k, v in
                 (('model', args.model), ('base_url', args.base_url), ('api_key', args.api_key))
                 if v}
    try:
        if args.greeting_mode == 'ai':
            cfg_greeting = resolve(stage='greeting', **overrides)
        if args.resume_mode == 'ai':
            cfg_resume = resolve(stage='resume', **overrides)
    except ConfigError as exc:
        print('❌ 配置错误：\n%s' % exc)
        return 1
    cfg_any = cfg_greeting or cfg_resume
    workers = args.workers or (cfg_any.concurrency if cfg_any else 4)

    resume_text, resume_source = None, ''
    if args.resume_mode == 'ai':
        try:
            resume_text, resume_source = load_resume_text(run_dir, profile, args.resume_text)
        except LLMError as exc:
            print('❌ %s' % exc)
            return 1

    scene = args.scene or infer_scene(profile)
    match_index = build_match_index(run_dir)
    gen_dir = os.path.join(run_dir, GEN_DIR)

    # ── 列出任务：(序号, 岗位, kind) ──
    tasks, skipped = [], []
    for index, job in enumerate(jobs, 1):
        if only is not None and index not in only:
            continue
        for kind in kinds:
            have = existing_artifact(gen_dir, kind, index)
            if have and not args.force:
                skipped.append(have)
                continue
            tasks.append((index, job, kind))

    print('📂 %s' % run_dir)
    print('岗位 %d 个 / 匹配信息命中 %d 个 / 场景提示 %s'
          % (len(jobs), sum(1 for j in jobs if (j.get('link') or '') in match_index),
             scene or '未判断'))
    print('招呼语 %s / 简历 %s%s' % (
        args.greeting_mode, args.resume_mode,
        '（基底：%s）' % resume_source if resume_source else ''))
    if needs_llm:
        models = []
        if cfg_greeting:
            models.append('招呼语 %s' % cfg_greeting.model)
        if cfg_resume:
            models.append('简历 %s' % cfg_resume.model)
        print('模型 %s / 并发 %d' % (' | '.join(models), workers))
    if skipped:
        print('跳过 %d 个已有产物（--force 可覆盖）' % len(skipped))
    if not match_index and args.resume_mode == 'ai':
        print('⚠ 没找到任何匹配信息（scored_jobs / job_classification / deep_results 都不在），'
              '简历优化会缺少缺失项提示')

    if not tasks:
        print('\n✅ 需要的产物都已存在，无需生成。')
        return 0

    if args.dry_run:
        print('\n--dry-run：将生成 %d 份产物' % len(tasks))
        for index, job, kind in tasks:
            ext = 'txt' if kind == 'greeting' else 'json'
            print('  %s_%d_%s.%s   ← %s / %s'
                  % (kind, index, safe_name(job.get('公司')), ext,
                     job.get('公司', '?'), job.get('职位', '?')))
        print('（未发送任何请求，未写任何文件）')
        return 0

    os.makedirs(gen_dir, exist_ok=True)
    lock = threading.Lock()
    written = []
    preview_fixed = []

    def work(task):
        index, job, kind = task
        match = match_index.get(job.get('link') or '', {})
        company = safe_name(job.get('公司'))
        if kind == 'greeting':
            text, rewritten = gen_greeting(job, profile, match, cfg_greeting, run_dir,
                                           scene, args.greeting_mode)
            path = os.path.join(gen_dir, 'greeting_%d_%s.txt' % (index, company))
            _write_atomic(path, text)
        else:
            data = gen_resume(job, resume_text, match, cfg_resume, run_dir)
            path = os.path.join(gen_dir, 'resume_%d_%s.json' % (index, company))
            _write_atomic(path, json.dumps(data, ensure_ascii=False, indent=2))
            rewritten = False
        name = os.path.basename(path)
        with lock:
            written.append(name)
            if rewritten:
                preview_fixed.append(name)
        return name

    def label(task, _i):
        index, job, kind = task
        return '%-8s #%-2d %s / %s' % (kind, index, job.get('公司', '?'), job.get('职位', '?'))

    print('\n✍ 开始生成 %d 份（并发 %d）…' % (len(tasks), workers))
    started = time.time()
    outcomes = []
    interrupted = False
    try:
        with stage_timer.stage(run_dir, 'gen_materials'):
            outcomes = map_concurrent(tasks, work, workers=workers, label=label)
    except KeyboardInterrupt:
        interrupted = True
        print('\n⚠ 已中断。已写出的产物保留在 generated/，可再跑一次补齐剩下的。')
    elapsed = time.time() - started

    failures = [o for o in outcomes if not o.ok]
    print('\n%s' % ('=' * 60))
    print('  写出 %d / 失败 %d%s（耗时 %.1fs）'
          % (len(written), len(failures), ' / 被中断' if interrupted else '', elapsed))
    print('  目录: %s' % gen_dir)
    if preview_fixed:
        print('  %d 条招呼语的前 15 字被客套话占掉，已重写：%s'
              % (len(preview_fixed), '、'.join(preview_fixed)))
    if failures:
        print('  失败的（重跑本命令即可只补这些，已有产物会自动跳过）：')
        for outcome in failures:
            index, job, kind = outcome.item
            print('    %s #%d %s — %s' % (kind, index, job.get('公司', '?'),
                                          outcome.error[:120]))

    if needs_llm:
        print('\n' + format_usage(run_dir))

    # 用真实的 check_artifacts 复核，而不是自己数写了几个文件：
    # 下游认的是那套口径（前缀 + 非空），这里就用同一个函数判定。
    print()
    try:
        from check_artifacts import check
        _, found, missing = check(run_dir, kinds, jobs=jobs)
        if missing:
            print('⚠ check_artifacts 口径下仍缺 %d 项：' % len(missing))
            for item in missing[:12]:
                print('    %s' % item)
            if len(missing) > 12:
                print('    …还有 %d 项' % (len(missing) - 12))
        else:
            print('✅ check_artifacts 口径校验通过：%d 个岗位 × %s 全部齐全'
                  % (len(jobs), '+'.join(kinds)))
    except Exception as exc:                        # noqa: BLE001 — 复核失败不改变生成结果
        print('（跳过 check_artifacts 复核：%s）' % exc)

    print('\n下一步：')
    if os.environ.get('BOSS_PIPELINE_STAGE'):
        # 由 pipeline 启动：render 阶段会自动落盘 岗位信息+招呼语.md，不用单独跑。
        print('  python scripts/pipeline.py --run-dir "%s" --from verify --all'
              % run_dir)
    else:
        # 直接跑（不经 pipeline）：render_images.py 不写 岗位信息+招呼语.md，
        # 那份要么走 pipeline.py --from materials 让 materials 自动写，要么单独跑它。
        if args.resume_mode != 'skip':
            print('  python scripts/render_images.py "%s"        # 简历 JSON → PNG' % run_dir)
        print('  python scripts/write_application_md.py "%s" --all   # 或改走 pipeline 让 materials 自动写'
              % run_dir)
    print('  python scripts/apply.py "%s" --yes                # 真投递（不加 --yes 只预演）'
          % run_dir)

    if failures or interrupted:
        return 3
    return 0


if __name__ == '__main__':
    sys.exit(main())
