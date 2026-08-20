#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""评估结果的规则聚合 + 提示词优化建议。

`aggregate(evaluations)` 把逐岗六维结果汇总成一组平均值/命中计数（纯函数，可单测）。
`prompt_suggestions(aggregate)` 按阈值分档映射到**具体 prompt 文件 / 代码位**——
每一档都指向「改哪里、往哪个方向」，而不是泛泛说「减点幻觉」。

顶层的 `recommend(evaluations, llm_cfg=None, run_dir='')` 把两者打包，也可选调模型
做一次综合点评（--llm-recommend，住在 eval.materials.prompts.build_recommend_prompt）。

所有数值都是启发阈值，不是法官：超过阈值只是「值得看一眼」的信号，报告里也这么标注。
"""

import json

# ==================== 阈值（启发，非结论） ====================

_HALLUCINATION_HIGH = 0.05      # 优化后全文无据术语 > 5%：白名单校验没兜住
_JD_DRIVEN_LOW = 0.06           # 岗位驱动新增 < 6%：优化简历没在往 JD 上靠
_RETENTION_LOW = 0.80           # 原简历术语保留 < 80%：删了一堆原文术语
_ADDED_BLOCK_HIGH = 0.15        # 新增段落里「无据词/新数字」块占比较高
_COVERAGE_LOW = 0.80            # 原文字符保留 < 80%：删得偏多
_WASTED_PREVIEW_TARGET = 1      # 有 ≥1 条招呼语前 15 字被客套话占掉
_FABRICATION_TARGET = 1         # 有 ≥1 条招呼语编造到岗/出勤承诺
_CHARSET_RETRY_HINT = "（两阶段各带 _APPLY_RETRY/_PLAN_RETRY 自检，缺章会自动重试）"


def aggregate(evaluations):
    """逐岗六维结果 → 汇总字典。evaluations 为空返回全零结构。"""
    n = len(evaluations)
    if n == 0:
        return {'n_jobs': 0}

    def avg(key):
        vals = [e.get(key) for e in evaluations if isinstance(e.get(key), (int, float))]
        return sum(vals) / len(vals) if vals else 0.0

    terms = [e.get('terms') if isinstance(e.get('terms'), dict) else {} for e in evaluations]
    cd = [e.get('char_diff') if isinstance(e.get('char_diff'), dict) else {} for e in evaluations]
    ab = [e.get('added_blocks') if isinstance(e.get('added_blocks'), dict) else {} for e in evaluations]
    gr = [e.get('greeting') if isinstance(e.get('greeting'), dict) else {} for e in evaluations]
    chap = [e.get('chapters') if isinstance(e.get('chapters'), dict) else {} for e in evaluations]
    subj = [e.get('subjective') if isinstance(e.get('subjective'), dict) else {} for e in evaluations]

    def avg_field(dicts, key):
        vals = [d.get(key) for d in dicts if isinstance(d.get(key), (int, float))]
        return sum(vals) / len(vals) if vals else 0.0

    return {
        'n_jobs': n,
        'char_diff': {
            'avg_deleted_pct': avg_field(cd, 'deleted_pct'),
            'avg_added_pct': avg_field(cd, 'added_pct'),
            'avg_coverage_pct': avg_field(cd, 'coverage_pct'),
        },
        'terms': {
            'avg_hallucination_pct': avg_field(terms, 'hallucination_pct'),
            'avg_jd_driven_pct': avg_field(terms, 'jd_driven_pct'),
            'avg_retention_pct': avg_field(terms, 'retention_pct'),
            'avg_fabrication_within_new': avg_field(terms, 'fabrication_within_new'),
            'total_unfounded': sum(d.get('n_unfounded') or 0 for d in terms),
        },
        'added_blocks': {
            'avg_block_fabrication_pct': avg_field(ab, 'added_block_fabrication_pct'),
            'total_blocks_with_number': sum(d.get('added_blocks_with_new_number') or 0 for d in ab),
        },
        'greeting': {
            'n_wasted_preview': sum(1 for d in gr if d.get('has_wasted_preview')),
            'n_fabricated_commitment': sum(1 for d in gr if d.get('fabricated_commitment')),
            'n_quantified': sum(1 for d in gr if d.get('has_quantified')),
            'n_with_unfounded': sum(1 for d in gr if d.get('n_unfounded')),
        },
        'chapters': {
            'n_jobs_missing': sum(1 for d in chap if d.get('n_missing')),
            'total_missing': sum(d.get('n_missing') or 0 for d in chap),
        },
        'subjective': {
            'n_jobs_flagged': sum(1 for d in subj if d.get('flagged')),
        },
    }


# ==================== 提示词建议（映射到具体文件/代码位） ====================

def prompt_suggestions(agg):
    """按聚合结果生成建议列表。每项 = {file, level, issue, action, evidence}。

    file 指向仓库里真正要改的模板/脚本名；code 定位到具体代码位（如有）。
    evidence 引用触发它的聚合值，让人知道为什么给这条。
    """
    out = []
    t = agg.get('terms', {})
    cd = agg.get('char_diff', {})
    ab = agg.get('added_blocks', {})
    gr = agg.get('greeting', {})
    ch = agg.get('chapters', {})

    def add(file, level, issue, action, evidence=''):
        out.append({'file': file, 'level': level, 'issue': issue,
                    'action': action, 'evidence': evidence})

    if t.get('avg_hallucination_pct', 0) > _HALLUCINATION_HIGH:
        add('prompts/resume_optimize_apply.st', 'high',
            '优化简历出现原文与 JD 都没有的技术词（凭空造技能）',
            'apply 阶段照单输出后，加一道硬校验：对 optimized_resume 跑 _tokens，'
            '凡不在 baseline∪jd_keys 里的词过滤/重试，类比 gen_materials 里'
            '_missing_chapters 的 _APPLY_RETRY 位（缺词重试，重试尽仍在丢弃）',
            '平均无据术语占比 %.1f%%' % (t['avg_hallucination_pct'] * 100))

    if t.get('avg_retention_pct', 1) < _RETENTION_LOW:
        add('scripts/stages/gen_materials.py', 'medium',
            '原简历术语保留率偏低，优化简历删了较多原文技术词',
            '计划①的 chapter_plan 只保章节不保词；可在 apply 提示里加一句'
            '「保留原简历全部关键技术词」，并把保留率做成计划①的校验项',
            '平均保留率 %.0f%%' % (t['avg_retention_pct'] * 100))

    if t.get('avg_jd_driven_pct', 0) < _JD_DRIVEN_LOW and 'avg_jd_driven_pct' in t:
        add('prompts/resume_optimize_plan.st', 'low',
            '岗位驱动新增占比偏低，优化简历没在往 JD 上靠',
            'plan 阶段让模型显式列出该岗位要求的核心技能，作为 apply 的补充素材源',
            '平均岗位驱动新增 %.1f%%' % (t['avg_jd_driven_pct'] * 100))

    if cd.get('avg_coverage_pct', 1) < _COVERAGE_LOW:
        add('prompts/resume_optimize_apply.st', 'medium',
            '原文字符保留率偏低，删除内容偏多',
            'apply 提示强调「只做结构性调整与措辞升级，不删既有事实」，'
            '保留率的字符级做进阶段②校验',
            '平均原文字符保留 %.0f%%' % (cd['avg_coverage_pct'] * 100))

    if ab.get('avg_block_fabrication_pct', 0) > _ADDED_BLOCK_HIGH:
        add('prompts/resume_optimize_apply.st', 'medium',
            '新增段落里出现原文没有的量化数字/新词（编造成果迹象，启发而非结论）',
            '提示里规定：量化成果只能搬原简历/JD 出现过的数字，不允许新造「提升30%」'
            '之类；生成后按「数字必须曾在原文/JD 出现过」校验并重试',
            '平均新增块编造率 %.0f%%' % (ab['avg_block_fabrication_pct'] * 100))

    if gr.get('n_wasted_preview'):
        add('scripts/prompts/greeting.st', 'high',
            '有招呼语的前 15 字被客套话占掉（HR 只见前 15 字）',
            '出现率低说明 _GREETING_FIX 重试已兜底大部分；仍漏的加宽 _WASTED_OPENERS '
            '命中词，或提高 greeting 阶段重试次数',
            '%d 条命中' % gr['n_wasted_preview'])

    if gr.get('n_fabricated_commitment'):
        add('scripts/prompts/greeting.st', 'high',
            '招呼语编造了简历没有写的到岗/实习时长/每周出勤承诺',
            '提示强化「availability 未提供时不得出现可到岗/可实习X周/周X天」，'
            '并加一道规则校验：简历 format_availability 为空却含承诺词即重写',
            '%d 条命中' % gr['n_fabricated_commitment'])

    if gr.get('n_with_unfounded') and not gr.get('n_wasted_preview'):
        add('scripts/prompts/greeting.st', 'low',
            '招呼语出现无据技术词（多为点名 JD 词，风险提示非硬错）',
            '可接受；若想收敛，让招呼语只引用 resume_summary 里的词，不自由扩展',
            '%d 条含无据词' % gr['n_with_unfounded'])

    if ch.get('n_jobs_missing'):
        add('scripts/stages/gen_materials.py', 'high',
            '优化简历丢了原简历实有章节（删除内容）%s' % _CHARSET_RETRY_HINT,
            '章节保真已有自检但仍有漏——把这些缺失章节加入计划①的 chapter_plan 硬清单，'
            '缺失的章节名明确列出',
            '%d 个岗位共缺 %d 章' % (ch['n_jobs_missing'], ch['total_missing']))

    s = agg.get('subjective', {})
    if s.get('n_jobs_flagged'):
        add('prompts/resume_optimize_apply.st', 'low',
            '能力副词升级/绝对化用语（启发，非结论）',
            'plan/apply 各加一句「语气和能力等级与原文一致：原文写了解勿改成精通」',
            '%d 个岗位被标记' % s['n_jobs_flagged'])

    # 按 severity 排序：high 优先
    order = {'high': 0, 'medium': 1, 'low': 2}
    out.sort(key=lambda s: order.get(s['level'], 3))
    return out


# ==================== 顶层打包（可选 LLM 点评） ====================

def recommend(evaluations, llm_cfg=None, llm_comment=None):
    """聚合 + 规则建议；llm_comment 若已由外部算好（LLM 点评文本）则直接带上。"""
    agg = aggregate(evaluations)
    suggestions = prompt_suggestions(agg)
    return {
        'aggregate': agg,
        'suggestions': suggestions,
        'llm_comment': llm_comment or '',
    }


def recommend_llm(agg, cfg, run_dir):
    """在规则建议之外再调一次模型做综合点评（--llm-recommend）。失败不致命。"""
    from llm import chat, LLMError
    from eval.materials.prompts import build_recommend_prompt
    prompt = build_recommend_prompt(json.dumps(agg, ensure_ascii=False, indent=2))
    try:
        return chat(prompt, stage='eval_recommend', run_dir=run_dir, cfg=cfg).strip()
    except (LLMError, Exception) as exc:                     # noqa: BLE001
        return '（LLM 点评失败，仅保留规则建议：%s）' % exc