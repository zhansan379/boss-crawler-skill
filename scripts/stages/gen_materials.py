#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""为 qualified_jobs.json 里的每个岗位生成招呼语 + 优化简历（并发调 LLM）。

产物文件名是下游的硬契约，`check_artifacts.py` / `write_application_md.py` /
ShowCV 渲染都按它对齐：

    {run_dir}/materials/greeting_{i}_{公司}.txt    纯文本招呼语
    {run_dir}/materials/resume_{i}_{公司}.json     resume_optimize.st 的整个 JSON 对象

`{i}` 是岗位在 qualified_jobs.json 里的 1-based 序号 —— 下游全靠这个序号对齐，所以
即使某个岗位失败，其余岗位的序号也不能挪动。

用法：
    python scripts/stages/gen_materials.py <run_dir>
    python scripts/stages/gen_materials.py <run_dir> --greeting-mode default   # 不调模型，套模板
    python scripts/stages/gen_materials.py <run_dir> --resume-mode skip        # 只出招呼语
    python scripts/stages/gen_materials.py <run_dir> --resume-mode plan        # 只出「调整计划」待用户逐点确认
    python scripts/stages/gen_materials.py <run_dir> --resume-mode apply       # 读已存计划+决策，只出已批准岗位的简历
    python scripts/stages/gen_materials.py <run_dir> --only 1,3,5              # 只补这几个
    python scripts/stages/gen_materials.py <run_dir> --force                   # 覆盖已有产物

同意闸门：`--resume-mode plan` 写 materials/plan_{i}_{公司}.json，用户在
SKILL.md「计划征询」停点按点确认后，用 scripts/stages/set_plan_decisions.py 写
materials/decision_{i}.json，再 `--resume-mode apply` 只对已批准岗位出完整简历。

退出码：0 = 需要的产物齐全，1 = 输入缺失/全部失败，3 = 部分失败。
"""

import os
import re
import sys
import json
import time
import argparse
import threading

_SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _SCRIPTS)

import stage_timer
from resume_matcher.prompts import (
    get_greeting_prompt, get_optimize_plan_prompt, get_optimize_apply_prompt,
)
from resume_matcher.config import ResumeProfile
from resume_matcher import qualified_jobs_path, profile_path, resume_text_path
from llm import (
    ConfigError, LLMError, chat, chat_json, map_concurrent, resolve,
    strip_fence, format_usage, reconfigure_stdout,
)

GEN_DIR = 'materials'

# load_resume_text 在没有原文（resume_text.txt 缺失/不足 50 字）时，返回这个 source 标记
# 表示基底退化成 profile 重述的通用稿。它是最容易被忽略的降级信号（和正常那行长得一样），
# 主流程靠它决定要不要拦截 / 亮警告。
GENERIC_BASE_SOURCE = 'profile.json（通用结构）'

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
    path = qualified_jobs_path(run_dir)
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
    path = profile_path(run_dir)
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

    awards = profile.get('awards') or []
    publications = profile.get('publications') or []
    if awards or publications:
        lines.append('\n## 荣誉奖项')
        seen_awards = set()
        for award in awards:
            if isinstance(award, dict):
                award = _format_award(award)
            if not award or award in seen_awards:
                # 一行原文拆成多条奖项时，每条的 raw 都是那一行 —— 只输出一次
                continue
            seen_awards.add(award)
            lines.append('- %s' % award)
        for pub in publications:
            if isinstance(pub, dict):
                # 解析产出的键是 journal，venue 是早期结构，两个都认
                pub = '、'.join(str(v) for v in
                                (pub.get('title'),
                                 pub.get('journal') or pub.get('venue'),
                                 pub.get('year')) if v)
            if pub:
                lines.append('- %s' % pub)

    return '\n'.join(lines)


def _format_award(award):
    """一条奖项渲染成一行。

    有 raw（原文整行）就直接用它 —— 参赛作品名与届次天然都在里面，重拼一遍只会漏。
    没有 raw 才按字段拼，且必须带上 rank：丢了「二等奖」的奖项行等于没写奖。
    """
    raw = (award.get('raw') or '').strip()
    if raw:
        return raw
    line = '、'.join(str(v) for v in (award.get('year'), award.get('name'),
                                     award.get('level'), award.get('rank')) if v)
    works = [str(w) for w in (award.get('works') or []) if w]
    if works:
        line += ' · 参赛作品%s' % '、'.join(works)
    detail = award.get('detail')
    if detail:
        line += '（%s）' % detail
    return line


def load_resume_text(run_dir, profile, override=None):
    """优化基底：--resume-text 指定的文件 > resume_text.txt > profile 生成的通用稿。

    注意：resume_text.txt 位于 run_dir/state/（见 paths.py 的目录结构真源），
    用 resume_text_path() 定位，不要自己拼 run_dir 根目录路径。
    """
    for path in (override, resume_text_path(run_dir)):
        if path and os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                text = f.read().strip()
            if len(text) >= 50:
                return text, os.path.basename(path)
    text = build_generic_resume_text(profile)
    if len(text) < 30:
        raise LLMError('既没有 resume_text.txt，profile 也太空，无法生成优化基底')
    return text, GENERIC_BASE_SOURCE


# ==================== 生成 ====================

_GREETING_FIX = (
    '上面这条招呼语的前 15 个字被客套话占掉了。HR 在消息列表里只能看到前 15 个字，'
    '那是标题不是寒暄。请重写：第一句话就给出最硬的事实（到岗时间 / 年限+一个数字 / '
    '学校届别），不要以「您好」「你好」「我是」「我对」开头。只输出招呼语本文。'
)


def gen_greeting(job, profile, match, cfg, run_dir, mode, resume=''):
    """一个岗位的招呼语。返回 (文本, 是否重写过)。"""
    if mode == 'default':
        # 不调模型：套 auto_apply 里的规则模板（离线、免费、质量一般）
        from resume_matcher.auto_apply import generate_greeting
        return generate_greeting(profile=_to_profile_obj(profile), job=job), False

    # 直接喂完整简历原文，不拆字段 —— 姓名、到岗信息、经验年限都由模型从原文自取。
    # availability（basic_info.availability 的结构化到岗文本）单独走 get_greeting_prompt
    # 的**动态组装**：有值才拼到岗段，空则整段不出现，不给模型脑补到岗承诺的诱因。
    prompt = get_greeting_prompt(
        job,
        resume=resume,
        match_reasons='；'.join(match.get('match_reasons') or []) or '',
        availability=format_availability(profile),
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


# 两阶段（①调整计划 / ②照单输出）各自的重试次数（首先尝试之外的次数）。
# 两处都靠「章节覆盖度」做便宜校验，缺章就在原阶段重试，不走到下一阶段。
_PLAN_RETRY = 2
_APPLY_RETRY = 2


# 章节名规范化：原简历(md) 与模型输出、chapter_plan 之间叫法可能不同
# （如 txt 里「项目」「技能」，模板规范名「项目经历」「专业技能」），比对前归一到规范键。
_CHAPTER_ALIASES = {
    '个人简介': ('个人简介',),
    '专业技能': ('专业技能', '技能'),
    '工作经历': ('工作经历',),
    '实习经历': ('实习经历',),
    '项目经历': ('项目经历', '项目'),
    '教育背景': ('教育背景', '教育'),
    '荣誉奖项': ('荣誉奖项', '荣誉', '奖项'),
    '开源经历': ('开源经历', '开源'),
}


def _norm_chapter(name):
    """章节名 → 规范键。认不到就原样返回（保守：不误判为别的章节）。"""
    name = (name or '').strip()
    if not name:
        return ''
    for canon, aliases in _CHAPTER_ALIASES.items():
        for a in aliases:
            if name == a or a in name:
                return canon
    return name


def _extract_chapters(md_text):
    """从 Markdown 提取二级章节名（## 开头，排除 ###），归一到规范键集合。"""
    out = []
    for line in (md_text or '').splitlines():
        s = line.strip()
        if s.startswith('## ') and not s.startswith('### '):
            key = _norm_chapter(s[3:].strip())
            if key and key not in out:
                out.append(key)
    return out


def _plan_chapters(plan):
    """调整计划里 keep 的章节（规范键，去重）—— 是阶段②的硬命令来源。"""
    out = []
    for c in (plan or {}).get('chapter_plan') or []:
        if not isinstance(c, dict):
            continue
        name = str(c.get('chapter') or '').strip()
        if c.get('keep') and name:
            key = _norm_chapter(name)
            if key and key not in out:
                out.append(key)
    return out


def _missing_chapters(src, present):
    """"src（必带规范章节）里没出现在 present 中的。空 src 视为全通过/无法校验。"""
    if not src:
        return []
    return [c for c in src if c not in present]


def _build_chapter_list(plan):
    """调整计划 → 「必须输出的章节清单」文本，作为阶段②的硬命令。"""
    keep = [c for c in (plan or {}).get('chapter_plan') or []
            if isinstance(c, dict) and c.get('keep')]
    keep.sort(key=lambda c: c.get('order') if isinstance(c.get('order'), (int, float)) else 999)
    return '\n'.join('%d. %s' % (i, str(c.get('chapter') or '').strip())
                     for i, c in enumerate(keep, 1))


def gen_plan(job, resume_text, match, cfg, run_dir):
    """阶段①：只生成「简历调整计划」，不写正文、不落盘。

    供计划征询停点先给用户过目、按点确认。返回 `{chapter_plan, optimization_suggestions}` 等
    的 dict；校验与重试语义与合并路径完全一致 —— chapter_plan 必须覆盖原简历全部实有章节。
    """
    jd = (job.get('岗位要求和职责', '') or job.get('技能标签', '') or '')[:2000]
    match_score = match.get('match_score') or 0
    missing_items = '、'.join(match.get('missing_items') or []) or '（无）'
    optimization_points = '、'.join(match.get('optimization_points') or []) or '（无）'

    plan_prompt = get_optimize_plan_prompt(
        resume_text=resume_text,
        company=job.get('公司', '') or '',
        position=job.get('职位', '') or '',
        salary=job.get('薪资', '') or '',
        requirements=jd,
        match_score=match_score,
        missing_items=missing_items,
        optimization_points=optimization_points,
    )
    src_chapters = _extract_chapters(resume_text)
    plan = None
    for attempt in range(_PLAN_RETRY + 1):
        cand = chat_json(plan_prompt, stage='resume:plan', run_dir=run_dir, cfg=cfg)
        if not isinstance(cand, dict):
            if attempt == _PLAN_RETRY:
                raise LLMError('阶段①（调整计划）多次调用返回的不是 JSON 对象')
            continue
        missing = _missing_chapters(src_chapters, _plan_chapters(cand))
        if not missing:
            plan = cand
            break
        if attempt == _PLAN_RETRY:
            raise LLMError('阶段①（调整计划）重试 %d 次仍遗漏实有章节: %s'
                           % (_PLAN_RETRY, '、'.join(missing)))
    if plan is None:
        raise LLMError('阶段①（调整计划）无法生成')
    return plan


def apply_plan(job, resume_text, cfg, run_dir, plan, suggestions=None):
    """阶段②：照「章节清单 + 已批准的 suggestions」输出完整简历。

    suggestions 是唯一真正驱动正文改写的来源 —— 计划征询停点把它过滤/改写成用户批准后的
    子集（含用户亲手补的 must_add 真内容）再传进来，被拒绝的条目绝不会到达阶段②。
    None 时退回计划自带的全量 suggestions（兼容无闸门的合并路径与 eval harness）。
    """
    if suggestions is None:
        suggestions = plan.get('optimization_suggestions') or {}
    src_chapters = _extract_chapters(resume_text)
    apply_prompt = get_optimize_apply_prompt(
        resume_text=resume_text,
        company=job.get('公司', '') or '',
        position=job.get('职位', '') or '',
        chapter_list=_build_chapter_list(plan),
        optimization_suggestions=json.dumps(suggestions, ensure_ascii=False, indent=2),
    )
    data = None
    for attempt in range(_APPLY_RETRY + 1):
        cand = chat_json(apply_prompt, stage='resume', run_dir=run_dir, cfg=cfg)
        if isinstance(cand, dict):
            body = cand.get('optimized_resume')
            if isinstance(body, str) and len(body.strip()) >= 50:
                if _missing_chapters(src_chapters, _extract_chapters(body)):
                    # 缺章 = 会随长图出门的定稿缺内容，宁可在本岗位失败也不放行
                    if attempt == _APPLY_RETRY:
                        raise LLMError('阶段②（完整简历）重试 %d 次仍遗漏实有章节，'
                                       '已放弃该岗位以免缺段随图出门' % _APPLY_RETRY)
                    continue
                data = cand
                break
        if attempt == _APPLY_RETRY:
            raise LLMError('阶段②（完整简历）多次输出缺失/过短，已放弃该岗位')
    if not isinstance(data, dict):
        raise LLMError('阶段②（完整简历）无法生成')

    body = data['optimized_resume']
    if body.strip().startswith('```'):
        data['optimized_resume'] = strip_fence(body).strip()

    # 组装最终产物，保持下游契约：optimization_suggestions = 实际喂给阶段②的那份（批准/过滤后）
    data['optimization_suggestions'] = suggestions
    if not isinstance(data.get('key_changes'), list):
        data['key_changes'] = plan.get('key_changes') or []
    return data


def gen_resume(job, resume_text, match, cfg, run_dir):
    """一个岗位的优化简历 —— 两阶段合并执行（默认/无闸门路径）。

    组合 gen_plan + apply_plan（suggestions 用计划自带的全量），即原有的原子语义。
    eval harness（evaluate_materials._gm）真调用本函数，签名与产物结构保持不变。
    """
    plan = gen_plan(job, resume_text, match, cfg, run_dir)
    return apply_plan(job, resume_text, cfg, run_dir, plan)


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


# ==================== 同意闸门：计划 / 决策文件 ====================

# 计划征询停点（SKILL.md「计划征询」）：
#   · --resume-mode plan   → 只跑阶段①，写 materials/plan_{i}_{公司}.json
#   · --resume-mode apply  → 读 materials/plan_{i}_{公司}.json + decision_{i}.json，
#                            只对用户已批准的岗位跑阶段②，写 resume_{i}_{公司}.json
# decision_{i}.json 由 scripts/stages/set_plan_decisions.py 原子落盘（主循环绝不手编），
# 其结构见该脚本 docstring。{i} 仍是 qualified_jobs.json 里的 1-based 序号。
DECISION_FILE = 'decision_%d.json'


def plan_file_path(gen_dir, index, company):
    return os.path.join(gen_dir, 'plan_%d_%s.json' % (index, company))


def decision_path(gen_dir, index):
    return os.path.join(gen_dir, DECISION_FILE % index)


def _artifact_prefix(kind):
    """kind → 产物文件名前缀。greeting/plan/resume 各自独立；apply 的产物落成 resume。"""
    return {'greeting': 'greeting', 'plan': 'plan', 'resume': 'resume',
            'apply': 'resume'}.get(kind, kind)


def load_decision(run_dir, index):
    """读 materials/decision_{i}.json；不存在/坏/非对象都返回 None（= 尚未征询或被拒）。"""
    path = decision_path(os.path.join(run_dir, GEN_DIR), index)
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (ValueError, OSError):
        return None
    return data if isinstance(data, dict) else None


def apply_from_decision(job, resume_text, cfg, run_dir, index, company):
    """应用阶段：按已存计划 + 用户决策调用阶段②，返回最终 3 键产物 dict。

    决策未批准（reject / 尚未落盘）时不生成简历 —— 返回 None，调用方不回写文件，
    该岗位走回退（自定义图片 / 原简历），招呼语不受影响。
    """
    decision = load_decision(run_dir, index)
    if not decision or not decision.get('approved'):
        return None
    p_path = plan_file_path(os.path.join(run_dir, GEN_DIR), index, company)
    if not os.path.exists(p_path):
        raise LLMError('应用阶段缺已存计划文件：%s（先跑 --resume-mode plan）' % p_path)
    with open(p_path, 'r', encoding='utf-8') as f:
        plan = json.load(f)
    suggestions = decision.get('suggestions')
    return apply_plan(job, resume_text, cfg, run_dir, plan,
                      suggestions if isinstance(suggestions, dict) else None)


# ==================== 主流程 ====================

# --only 的解析挪到了 check_artifacts.py（那边没有依赖）。这里 re-export，让
# 「生成哪些」和「核对哪些」共用同一份解析 —— 两份各自漂移过一次，序号错位很难查。
from check_artifacts import parse_only          # noqa: F401  (re-export)


def main():
    reconfigure_stdout()

    ap = argparse.ArgumentParser(
        description='为 qualified_jobs.json 的每个岗位生成招呼语与优化简历',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='产物落在 <run_dir>/materials/，文件名契约与 check_artifacts.py 一致。\n'
               '跑完可选：python scripts/deliver/render_images.py <run_dir>（简历转图片）\n')
    ap.add_argument('run_dir', help='运行目录（含 qualified_jobs.json 与 profile.json）')
    ap.add_argument('--greeting-mode', choices=('ai', 'default', 'skip'), default='ai',
                    help='ai=调模型（默认）；default=套规则模板不花钱；skip=不生成')
    ap.add_argument('--resume-mode', choices=('ai', 'plan', 'apply', 'skip'), default='ai',
                    help='ai=两阶段合并出完整简历（默认）；plan=只出调整计划待用户确认；'
                         'apply=读已存计划+决策只出已批准岗位；skip=不生成')
    ap.add_argument('--resume-text', help='优化基底文件，默认用 <run_dir>/state/resume_text.txt')
    ap.add_argument('--allow-generic-base', action='store_true',
                    help='state/resume_text.txt 缺失时，允许用 profile 重述的通用稿作 AI 优化基底（默认拒绝）')
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
        print('  先跑匹配：python scripts/stages/run_matcher.py …（会自动生成 qualified_jobs.json）')
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
        print('  先跑：python scripts/stages/parse_resume.py <简历文件> --output-dir "%s"' % run_dir)
        return 1
    except ValueError as exc:
        print('❌ profile.json 解析失败: %s' % exc)
        return 1

    kinds = []
    if args.greeting_mode != 'skip':
        kinds.append('greeting')
    if args.resume_mode in ('ai', 'plan', 'apply'):
        # ai=int：合并两阶段（plan→apply 一气呵成）；plan/apply 是同意闸门的两半。
        kinds.append({'ai': 'resume', 'plan': 'plan', 'apply': 'apply'}[args.resume_mode])
    if not kinds:
        print('❌ --greeting-mode 与 --resume-mode 都是 skip，没有要做的事')
        return 1

    try:
        only = parse_only(args.only, len(jobs))
    except ValueError as exc:
        print('❌ %s' % exc)
        return 1

    needs_llm = args.greeting_mode == 'ai' or args.resume_mode in ('ai', 'plan', 'apply')
    # 两个阶段各自解析配置：招呼语短、便宜，简历改写长、吃能力，配置文件里的
    # stages.greeting / stages.resume 就是为了让它们指向不同的模型。
    cfg_greeting = cfg_resume = None
    overrides = {k: v for k, v in
                 (('model', args.model), ('base_url', args.base_url), ('api_key', args.api_key))
                 if v}
    try:
        if args.greeting_mode == 'ai':
            cfg_greeting = resolve(stage='greeting', **overrides)
        if args.resume_mode in ('ai', 'plan', 'apply'):
            cfg_resume = resolve(stage='resume', **overrides)
    except ConfigError as exc:
        print('❌ 配置错误：\n%s' % exc)
        return 1
    cfg_any = cfg_greeting or cfg_resume
    workers = args.workers or (cfg_any.concurrency if cfg_any else 4)

    resume_text, resume_source = None, ''
    if args.resume_mode in ('ai', 'plan', 'apply'):
        try:
            resume_text, resume_source = load_resume_text(run_dir, profile, args.resume_text)
        except LLMError as exc:
            print('❌ %s' % exc)
            return 1
        # AI 优化把简历改出个人风格，源头绝不能从原始简历悄悄换成 profile 通用稿——
        # 那正是「最优化的简历没以原文为基底」的静默降级，命令行看不出区别。
        # 默认拒绝；要用通用稿必须显式认一次。
        if resume_source == GENERIC_BASE_SOURCE and not args.allow_generic_base:
            print('❌ 没有可用的原始简历文本（state/resume_text.txt 缺失或不足 50 字）。')
            print('  AI 简历优化的基底不能用 profile 重述的通用稿兜底。')
            print('  处理：重跑 parse 补一份原文，或用 --resume-text 指定原文文件；')
            print('        确要接受通用稿基底就加 --allow-generic-base。')
            return 1

    # 招呼语直接以完整简历原文为输入（不拆字段）。resume_mode=ai 时复用已加载的
    # resume_text；否则为 greeting 独立加载一次 —— 加载不到就传空串，模板兜底。
    greeting_resume = resume_text or ''
    if not greeting_resume:
        try:
            greeting_resume, _ = load_resume_text(run_dir, profile, args.resume_text)
        except LLMError:
            greeting_resume = ''
    match_index = build_match_index(run_dir)
    gen_dir = os.path.join(run_dir, GEN_DIR)

    # ── 列出任务：(序号, 岗位, kind) ──
    tasks, skipped = [], []
    for index, job in enumerate(jobs, 1):
        if only is not None and index not in only:
            continue
        for kind in kinds:
            have = existing_artifact(gen_dir, _artifact_prefix(kind), index)
            if have and not args.force:
                skipped.append(have)
                continue
            tasks.append((index, job, kind))

    print('📂 %s' % run_dir)
    print('岗位 %d 个 / 匹配信息命中 %d 个'
          % (len(jobs), sum(1 for j in jobs if (j.get('link') or '') in match_index)))
    print('招呼语 %s / 简历 %s%s' % (
        args.greeting_mode, args.resume_mode,
        '（基底：%s）' % resume_source if resume_source else ''))
    if resume_source == GENERIC_BASE_SOURCE:
        # 走到这说明 --allow-generic-base 被显式接受了；仍要明牌提醒：出门的是重述稿不是原文
        print('⚠ 简历优化基底是 profile 重述的通用稿（非简历原文，已 --allow-generic-base 放行）。')
        print('  投递/渲染前请核对优化稿与简历事实一致。')
    if needs_llm:
        models = []
        if cfg_greeting:
            models.append('招呼语 %s' % cfg_greeting.model)
        if cfg_resume:
            models.append('简历 %s' % cfg_resume.model)
        print('模型 %s / 并发 %d' % (' | '.join(models), workers))
    if skipped:
        print('跳过 %d 个已有产物（--force 可覆盖）' % len(skipped))
    if not match_index and args.resume_mode in ('ai', 'plan', 'apply'):
        print('⚠ 没找到任何匹配信息（scored_jobs / job_classification / deep_results 都不在），'
              '简历优化会缺少缺失项提示')

    if not tasks:
        print('\n✅ 需要的产物都已存在，无需生成。')
        return 0

    if args.dry_run:
        print('\n--dry-run：将生成 %d 份产物' % len(tasks))
        for index, job, kind in tasks:
            out_prefix = _artifact_prefix(kind)
            ext = 'txt' if kind == 'greeting' else 'json'
            print('  %s_%d_%s.%s   ← %s / %s'
                  % (out_prefix, index, safe_name(job.get('公司')), ext,
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
                                           args.greeting_mode, greeting_resume)
            path = os.path.join(gen_dir, 'greeting_%d_%s.txt' % (index, company))
            _write_atomic(path, text)
        elif kind == 'plan':
            # 计划征询的第一半：只出调整计划，留给用户逐点确认。
            plan = gen_plan(job, resume_text, match, cfg_resume, run_dir)
            path = plan_file_path(gen_dir, index, company)
            _write_atomic(path, json.dumps(plan, ensure_ascii=False, indent=2))
            rewritten = False
        elif kind == 'apply':
            # 计划征询的第二半：只对用户的已批准岗位照计划出完整简历。未批准返回 None，
            # 不回写文件 —— 该岗位回退到自定义图片/原简历，招呼语不受影响。
            data = apply_from_decision(job, resume_text, cfg_resume, run_dir, index, company)
            if data is None:
                return None
            path = os.path.join(gen_dir, 'resume_%d_%s.json' % (index, company))
            _write_atomic(path, json.dumps(data, ensure_ascii=False, indent=2))
            rewritten = False
        else:  # 'resume'：ai 合并两阶段（默认/无闸门）
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
        print('\n⚠ 已中断。已写出的产物保留在 materials/，可再跑一次补齐剩下的。')
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
        # kind 是内部名，check_artifacts 认的是产物前缀：'apply' 落盘成 resume_。
        check_kinds = sorted({_artifact_prefix(k) for k in kinds})
        if args.resume_mode == 'apply':
            # 应用阶段只对「已批准」岗位出简历，被拒岗位故意没有 resume —— 核对时只查
            # 有决策且 approved 的岗位，否则一次成功的 apply 会被误报成缺一批。
            approved = {i for i in range(1, len(jobs) + 1)
                        if (lambda d: d and d.get('approved'))(load_decision(run_dir, i))}
            if only is not None:
                approved &= only
            verify_only = approved or None
        else:
            verify_only = only
        _, found, missing = check(run_dir, check_kinds, jobs=jobs, only=verify_only)
        if missing:
            print('⚠ check_artifacts 口径下仍缺 %d 项：' % len(missing))
            for item in missing[:12]:
                print('    %s' % item)
            if len(missing) > 12:
                print('    …还有 %d 项' % (len(missing) - 12))
        else:
            print('✅ check_artifacts 口径校验通过：%d 个岗位 × %s 全部齐全'
                  % (len(jobs or []), '+'.join(check_kinds)))
    except Exception as exc:                        # noqa: BLE001 — 复核失败不改变生成结果
        print('（跳过 check_artifacts 复核：%s）' % exc)

    print('\n下一步：')
    if os.environ.get('BOSS_PIPELINE_STAGE'):
        # 由 pipeline 启动：materials 阶段会自动落盘 岗位信息+招呼语.md，不用单独跑。
        print('  python scripts/pipeline.py --run-dir "%s" --from verify --to render'
              % run_dir)
    else:
        # 直接跑（不经 pipeline）：render_images.py 不写 岗位信息+招呼语.md，
        # 那份要么走 pipeline.py --from materials 让 materials 自动写，要么单独跑它。
        if args.resume_mode != 'skip':
            print('  python scripts/deliver/render_images.py "%s"        # 简历 JSON → PNG' % run_dir)
        print('  python scripts/deliver/write_application_md.py "%s" --all   # 或改走 pipeline 让 materials 自动写'
              % run_dir)
    print('  python scripts/deliver/apply.py "%s" --yes                # 真投递（不加 --yes 只预演）'
          % run_dir)

    if failures or interrupted:
        return 3
    return 0


if __name__ == '__main__':
    sys.exit(main())
