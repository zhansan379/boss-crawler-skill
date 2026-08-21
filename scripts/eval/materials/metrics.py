#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""质量评估的**纯函数层**：五维指标，输入文本/集合，输出结构化数字与明细。

刻意不 import llm，也刻意不触网 —— 评估只有「读已生成产物 + 算数」两件事，
任何维度都不需要再问模型（可选 `--llm-recommend` 的综合点评单独住在 recommend.py）。

复用的口径（与既有校验链路严格一致，避免两套标准打架）：
- **术语级幻觉判定**：verify_no_fabrication 的 `_tokens`/`_baseline_keys`/`_norm`/`_context`。
  同一套「白名单式」判定：原文/ profile 里出现过的词一律放过，没出现过一律报告。
- **章节保真**：gen_materials 的 `_extract_chapters`/`_norm_chapter`（它在 scripts/stages/，
  依赖 llm，不能在这里 import，故按同源复制 —— 一旦那两处改要同步改）。
- **招呼语前 15 字客套判定**：auto_apply 的 preview/has_wasted_preview（同样同源复制）。

评估不对「优化简历」判断质量，只对**会真正发出去的产物**（optimized_resume、greeting）
做事实性/完整性衡量。optimization_suggestions 刻意排除 —— 给简历提它缺的技能是那
字段的本职工作，查它就是制造幻觉误报（同 verify 的口径）。
"""

import re
import difflib

# sys.path：verify_no_fabrication 在 scripts/verify/ 下，且它依赖 scripts/ 里的
# resume_matcher 包，两处都加进来才能 import。
import os
import sys

# scripts/eval/materials/ 上溯两级 → scripts/ 根
_SCRIPTS = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
for _p in (_SCRIPTS, os.path.join(_SCRIPTS, 'verify')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from verify_no_fabrication import (                        # noqa: E402  (sys.path 之后)
    _tokens, _baseline_keys, _context,
)


# ==================== 同源复制的判定基元 ====================
# 与 scripts/stages/gen_materials.py 的 _extract_chapters/_norm_chapter、
# scripts/resume_matcher/auto_apply.py 的 preview/has_wasted_preview 逐字一致。
# 一旦原处改动，这里要同步 —— 评估必须和生成走同一个「章节规范名」与「前15字口径」。

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
    name = (name or '').strip()
    if not name:
        return ''
    for canon, aliases in _CHAPTER_ALIASES.items():
        for a in aliases:
            if name == a or a in name:
                return canon
    return name


def _extract_chapters(md_text):
    """Markdown 的二级章节（## 开头，排除 ###），归一到规范键（同 gen_materials）。"""
    out = []
    for line in (md_text or '').splitlines():
        s = line.strip()
        if s.startswith('## ') and not s.startswith('### '):
            key = _norm_chapter(s[3:].strip())
            if key and key not in out:
                out.append(key)
    return out


def missing_chapters(base_text, opt_text):
    """原简历实有、优化后缺失的章节（= 删除）。空 base 视为无法校验，全通过。"""
    src = _extract_chapters(base_text)
    if not src:
        return []
    present = _extract_chapters(opt_text)
    return [c for c in src if c not in present]


PREVIEW_LEN = 15
_WASTED_OPENERS = ('您好', '你好', '我是', '我对', '还招', '虽然', '尊敬的', '贵公司', '贵司')


def preview(text):
    """HR 在消息列表里只看得见前 15 个字（同 auto_apply）。"""
    return (text or '').strip().replace('\n', '')[:PREVIEW_LEN]


def has_wasted_preview(text):
    """前 15 字被客套话占掉（同 auto_apply）。"""
    return preview(text).startswith(_WASTED_OPENERS)


# ==================== 1) 字符级 diff ====================

_NUM = re.compile(r'\d+(?:\.\d+)?%?')


def normalize_chars(s):
    """比较用归一化：去掉一切空白。加粗/标题符号留在里面（div 口径看标 3.1）。"""
    return re.sub(r'\s+', '', (s or ''))


def char_diff(base_text, opt_text):
    """优化后相对原简历的字符级删除/新增/覆盖。

    用 difflib.SequenceMatcher 的 opcode：delete/replace 的 a 段算删除，
    insert/replace 的 b 段算新增。分母各用各自文本长度 —— 删除比例相对原文、
    新增比例相对优化后，两者不可相加。
    """
    a, b = normalize_chars(base_text), normalize_chars(opt_text)
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    deleted = added = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ('delete', 'replace'):
            deleted += i2 - i1
        if tag in ('insert', 'replace'):
            added += j2 - j1
    da = len(a) or 1
    db = len(b) or 1
    return {
        'deleted_chars': deleted,
        'added_chars': added,
        'deleted_pct': deleted / da,
        'added_pct': added / db,
        'coverage_pct': 1 - deleted / da,     # 原文还有多少%保留
        'base_len': len(a),
        'opt_len': len(b),
    }


# ==================== 2) 术语级三分类 ====================

def term_stats(opt_text, baseline, jd_keys):
    """优化后全文的每个技术术语分到 保留 / 岗位驱动 / 无据(幻觉)。

    - baseline：原简历 + profile 里出现过的归一术语集合（白名单）。
    - jd_keys：岗位 JD 里出现过的归一集合。命中的新增术语算「岗位驱动」，是合理新增。
    - 两者都没有的 → 凭空新增，即幻觉。
    主分母 = 优化后全文术语总数 N_opt：hallucination_pct = 无据 / N_opt。
    """
    tokens = _tokens(opt_text)                 # {归一形: 原文首次写法}
    retained = jd_driven = unfounded = 0
    retained_list, jd_list, unfounded_list, terms = [], [], [], []
    for key, raw in tokens.items():
        _ct = _context(opt_text, raw, width=42)
        if key in baseline:
            retained += 1
            retained_list.append({'term': raw, 'context': _ct})
            terms.append({'term': raw, 'bucket': 'retained', 'context': _ct})
        elif key in jd_keys:
            jd_driven += 1
            jd_list.append({'term': raw, 'context': _ct})
            terms.append({'term': raw, 'bucket': 'jd_driven', 'context': _ct})
        else:
            unfounded += 1
            unfounded_list.append({'term': raw, 'context': _ct})
            terms.append({'term': raw, 'bucket': 'unfounded', 'context': _ct})
    n_opt = len(tokens)
    new_total = (unfounded + jd_driven) or 1
    return {
        'n_opt': n_opt,
        'n_retained': retained,
        'n_jd_driven': jd_driven,
        'n_unfounded': unfounded,
        'retention_pct': retained / n_opt if n_opt else 0,
        'jd_driven_pct': jd_driven / n_opt if n_opt else 0,
        'hallucination_pct': unfounded / n_opt if n_opt else 0,
        # 次口径：只看「新增」里有多少是无据（排除保留项的分母）
        'fabrication_within_new': unfounded / new_total,
        # 逐桶明细 + 统一 terms list：规则路也有 bucket，report 才能逐词着色
        'retained': retained_list,
        'jd_driven': jd_list,
        'unfounded': unfounded_list,
        'terms': terms,
    }


# ==================== 3) 招呼语质量 ====================

_PROMISE_RE = re.compile(
    r'(?:可?到岗|可实习|每周|每工作日|[周][一二三四五六日]\s*\d*\s*天|'
    r'实习\s*\d+\s*个月|\d+\s*个月实习|长期实习)')


def greeting_stats(greeting_text, baseline, jd_keys, availability_text):
    """招呼语质量：前15字 / 量化 / 术语事实性 / 到岗承诺编造。

    到岗/实习时长/每周出勤 是 HR 会据以行动的安排承诺，只能从简历复制。
    availability_text 为空 = 简历没写这几项，此时招呼语里出现「可到岗/可实习X周」
    就是编造承诺（硬伤,verify 抓不到这层）。其余维度的无据词只是「风险提示」。
    """
    text = greeting_text or ''
    tokens = _tokens(text)
    unfounded = [{'term': raw, 'context': _context(text, raw, width=30)}
                 for key, raw in tokens.items()
                 if key not in baseline and key not in jd_keys]
    quantified = bool(_NUM.search(re.sub(r'\s', '', text)))
    fabricated_commitment = (not (availability_text or '').strip()
                             and bool(_PROMISE_RE.search(text)))
    return {
        'length': len(preview(text)),
        'has_wasted_preview': has_wasted_preview(text),
        'has_quantified': quantified,
        'unfounded_terms': unfounded,
        'n_unfounded': len(unfounded),
        'fabricated_commitment': fabricated_commitment,
    }


# ==================== 4) 章节保真 ====================

def chapter_stats(base_text, opt_text):
    """原简历实有、优化后缺失的章节（= 删除内容）。"""
    missing = missing_chapters(base_text, opt_text)
    return {
        'missing_chapters': missing,
        'n_missing': len(missing),
        'n_base_chapters': len(_extract_chapters(base_text)),
        'n_opt_chapters': len(_extract_chapters(opt_text)),
    }


# ==================== 5) 客观性 / 夸大（启发，非结论） ====================
# verify 明确抓不到这层，这里也只给「⚠ 提示」：能力等级副词升级 + 新增绝对表述。

_UPGRADE_WORDS = ('精通', '熟练掌握', '深入掌握', '扎实掌握', '熟练掌握并')
_LOW_LEVEL_WORDS = ('了解', '接触', '略懂', '初步', '入门', '听说过')
_ABSOLUTE_WORDS = ('最优', '顶尖', '第一', '最好', '行业领先', '超高', '垄断')

_UPGRADE_RE = re.compile('(?:' + '|'.join(_UPGRADE_WORDS) + ')')
_ABSOLUTE_RE = re.compile('(?:' + '|'.join(_ABSOLUTE_WORDS) + ')')


def subjective_stats(base_text, opt_text, enabled=True):
    """(启发) 优化后出现 精通/熟练掌握 但原文对应位置只写了 了解/接触 -> 升级；或新增绝对用语。

    只做粗粒度计数。真正要「结论级」得靠 --llm-recommend 的综合点评。
    enabled=False 时（--no-subjective）直接给空。
    """
    if not enabled:
        return {'upgrades': [], 'absolute_added': [], 'flagged': False}
    base = base_text or ''
    opt = opt_text or ''
    # 升级：优化后有升级词，而原文同一技术词附近是低等级词。按词对粗判。
    upgrades = []
    opt_keys = _baseline_keys(opt)
    for pos, m in enumerate(_UPGRADE_RE.finditer(opt)):
        seg = opt[max(0, m.start() - 24): m.end() + 24]
        for low in _LOW_LEVEL_WORDS:
            if low in base:
                upgrades.append({'segment': seg.replace('\n', ' ').strip(),
                                 'strong': m.group()})
                break
    absolute_added = []
    for m in _ABSOLUTE_RE.finditer(opt):
        seg = opt[max(0, m.start() - 20): m.end() + 20]
        absolute_added.append({'segment': seg.replace('\n', ' ').strip(),
                               'word': m.group()})
    flagged = bool(upgrades or absolute_added)
    return {'upgrades': upgrades, 'absolute_added': absolute_added, 'flagged': flagged}


# ==================== 逐岗汇总 ====================

def evaluate_job(*, base_text, baseline, jd_keys, greeting_text,
                 optimized_resume, availability_text='', subjective=True,
                 terms_source='rule', classified_terms=None):
    """一个岗位的完整五维评估。返回可直接进 JSON 的 dict。

    terms_source / classified_terms：
      - 'rule'：用纯规则白名单（term_stats）三分类，离线、确定性（默认）。
      - 'llm'：调用方（evaluate_materials）已用 eval.materials.terms_llm 分类好
        （缓存优先），本函数直接采用 classified_terms 作 terms dict，不现场跑规则。
    除 terms 外的四维（char_diff / greeting / chapters / subjective）
    **始终离线纯算数**，两种 source 共用同一套输出字段，下游 aggregate/report 无需感知来源。
    """
    opt = optimized_resume or ''
    if terms_source == 'llm' and isinstance(classified_terms, dict):
        terms = dict(classified_terms)
        terms.setdefault('mode', 'llm')
    else:
        terms = term_stats(opt, baseline, jd_keys)       # rule 兜底
    return {
        'char_diff': char_diff(base_text, opt),
        'terms': terms,
        'greeting': greeting_stats(greeting_text or '', baseline, jd_keys,
                                   availability_text),
        'chapters': chapter_stats(base_text, opt),
        'subjective': subjective_stats(base_text, opt, enabled=subjective),
    }