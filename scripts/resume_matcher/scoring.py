#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
评分与岗位分类模块

- score_job_advanced:          6 维度规则评分（0-115 分）
- decide_application_category: 硬门槛优先的投递建议分类（后端唯一裁定）
- compute_difficulty:          分数 → 难度等级
- hr_activity_rank:            HR 活跃度 → 排序键（不参与评分）
- classify_jobs_advanced:      批量评分 + 4 级分层
- tiers_to_classification:     tiers → JobClassification 适配器

投递决策以 application_category 字段对外暴露，前端只渲染、不做任何数学运算。
调整阈值只需改本文件中的常量，无需同步前端。
"""

import re
from typing import Any, Dict, List, Optional, Tuple

from .config import ResumeProfile, JobClassification
from .utils import parse_experience_years, parse_company_size


# ==================== 分层阈值 ====================

TIER1_MIN = 100   # Easy   / qualified
TIER2_MIN = 85    # Medium / need_optimization
TIER3_MIN = 70    # Hard   / cannot_apply（低于此值归入 Tier4，报告中丢弃）


# ==================== HR 活跃度（排序键，不参与评分） ====================

# 活跃度刻意不进入 score，也不影响 application_category。
# 它衡量的是「HR 会不会回」，与「人岗是否匹配」是两个正交维度 —— 混进总分会让
# 「匹配度 85%」这个数字失去单一含义，也会让 difficulty 阈值随 HR 状态漂移。
# 这里只产出一个排序键：auto_apply 用它决定投递顺序，报告用它展示。
#
# 数据是爬取瞬间的快照。bossOnline 波动极快，投递时通常已过期；
# activeTimeDesc（"刚刚活跃"/"本月活跃"）粒度粗但相对稳定，是排序的主要依据。

HR_ONLINE_RANK = 100      # 爬取瞬间在线
HR_UNRECOGNIZED_RANK = 30  # 采集到了但文案无法识别


def hr_activity_rank(job) -> Optional[int]:
    """
    把 HR 活跃度快照折算成排序键。越大越活跃。

    Returns:
        int:  0-100，越大越活跃
        None: 未采集（无 -d 的爬取拿不到活跃度）

    None 是有意义的取值而非错误：未采集时全部岗位同为 None，排序退化成纯
    match_score 排序，而不是把它们误判成「最不活跃」而集体沉底。
    """
    online = (job.get('HR在线') or job.get('hr_online') or '').strip()
    if online == '是':
        return HR_ONLINE_RANK

    desc = (job.get('HR活跃度') or job.get('hr_active_desc') or '').strip()
    if not desc:
        return None

    # 带数字的区间文案优先（"3日内活跃"），再落到固定文案
    m = re.search(r'(\d+)\s*[日天]', desc)
    if m:
        return max(65, 78 - int(m.group(1)))
    m = re.search(r'(\d+)\s*周', desc)
    if m:
        return max(45, 58 - int(m.group(1)) * 3)
    m = re.search(r'(\d+)\s*个?月', desc)
    if m:
        return max(15, 38 - int(m.group(1)) * 4)

    for token, rank in (
        ('刚刚', 90),
        ('今日', 80), ('今天', 80),
        ('本周', 60),
        ('半月', 50), ('半个月', 50),
        ('本月', 40),
        ('半年', 12),
        ('年前', 10),
    ):
        if token in desc:
            return rank

    # BOSS 改了文案：给中性值。既不当成最不活跃，也不退回 None —— 它确实采集到了。
    return HR_UNRECOGNIZED_RANK


def hr_activity_sort_key(job) -> int:
    """排序用：未采集（None）折成 -1，排在所有已采集岗位之后。"""
    rank = hr_activity_rank(job)
    return -1 if rank is None else rank


# ==================== 投递建议分类 ====================

# 分类枚举：与 JobClassification 字段名、report_data['classification'] 容器键、
# 前端 tab id 保持同一套词汇，避免前后端出现两种叫法。
CATEGORY_QUALIFIED = 'qualified'                  # 可直接投递
CATEGORY_NEED_OPTIMIZATION = 'need_optimization'  # 优化后投递
CATEGORY_CANNOT_APPLY = 'cannot_apply'            # 不可投递

# 判定阈值（集中在此，调整时前端零改动）
QUALIFIED_MIN_SKILLS = 4        # 进入 qualified 所需的最少技能命中数
NEED_OPT_MAX_SALARY_GAP = 3     # 优化后投递可容忍的薪资差距(K)
NEED_OPT_MAX_EXP_GAP = 1        # 优化后投递可容忍的经验差(年)
CANNOT_APPLY_EXP_GAP = 3        # 经验差 ≥ 此值直接判不可投递(年)
CANNOT_APPLY_SALARY_GAP = 8     # 薪资差距 > 此值直接判不可投递(K)


# ==================== 学历等级 ====================

# 学历 → 等级序数，用于硬门槛比较（JD 等级 > 简历等级 即为不达标）
_DEGREE_LEVELS: List[Tuple[str, int]] = [
    ('博士', 6), ('博士后', 6),
    ('硕士', 5), ('研究生', 5), ('mba', 5), ('emba', 5),
    ('本科', 4), ('学士', 4), ('统招本科', 4),
    ('大专', 3), ('专科', 3), ('高职', 3),
    ('中专', 2), ('高中', 2), ('技校', 2),
    ('初中', 1),
]

# 简历未写明学历时的默认假设（保持既有行为：按本科处理）
DEFAULT_RESUME_DEGREE_LEVEL = 4

# JD 学历为以下表述时视为无门槛
_DEGREE_UNLIMITED = ('不限', '无要求', '不要求', '学历不限')


def parse_degree_level(degree_str: str, default: int = 0) -> int:
    """
    学历文本 → 等级序数（博士 6 / 硕士 5 / 本科 4 / 大专 3 / 中专高中 2 / 初中 1）。

    '不限' 及空值返回 0（无门槛）；无法识别时返回 default。
    '本科及以上' 取 '本科' 的等级——「及以上」不抬高门槛。
    """
    if not degree_str:
        return 0
    s = degree_str.lower().strip()
    if any(u in s for u in _DEGREE_UNLIMITED):
        return 0
    for name, level in _DEGREE_LEVELS:
        if name in s:
            return level
    return default


# ==================== 技能别名映射 & AI 关键词 ====================

# 规范名 → 全部等价写法（首项即规范名）
_SKILL_ALIASES: Dict[str, List[str]] = {
    'kubernetes':       ['kubernetes', 'k8s', 'k3s'],
    'golang':           ['golang', 'go'],
    'postgresql':       ['postgresql', 'postgres', 'pgsql', 'postgre'],
    'elasticsearch':    ['elasticsearch', 'es', 'elastic'],
    'javascript':       ['javascript', 'js', 'ecmascript'],
    'typescript':       ['typescript', 'ts'],
    'react':            ['react', 'reactjs', 'react.js'],
    'vue':              ['vue', 'vuejs', 'vue.js'],
    'node.js':          ['node.js', 'nodejs', 'node'],
    'tensorflow':       ['tensorflow', 'tf'],
    'pytorch':          ['pytorch', 'torch'],
    'machine-learning': ['machine-learning', 'ml', '机器学习', '深度学习'],
    'nlp':              ['nlp', '自然语言处理'],
    'cv':               ['cv', '计算机视觉'],
    'llm':              ['llm', '大模型', '大语言模型', 'large-language-model'],
    'mongodb':          ['mongodb', 'mongo'],
    'ci/cd':            ['ci/cd', 'cicd', 'ci cd', 'ci_cd'],
    'docker':           ['docker', 'container'],
    'redis':            ['redis'],
    'aws':              ['aws', 'amazon-web-services'],
    'generative-ai':    ['generative-ai', 'genai', 'aigc', '生成式ai', '生成式人工智能'],
}

# 变体 → 规范名（由 _SKILL_ALIASES 反向生成）
SKILL_SYNONYMS: Dict[str, str] = {
    variant: canonical
    for canonical, variants in _SKILL_ALIASES.items()
    for variant in variants
}

# AI 核心能力关键词（命中数量梯度计分）
AI_KEYWORDS = [
    'rag', 'agent', 'langchain', 'langgraph', 'llamaindex',
    '大模型', 'llm', 'prompt', 'fine-tune', 'fine_tune', '微调',
    'embedding', '向量数据库', 'vector', 'vectordb', 'chromadb', 'pinecone',
    'nlp', '自然语言处理', 'semantic', '语义',
    '多模态', 'multimodal', 'transformer', 'diffusion',
    'chatgpt', 'gpt', 'bert', 'hugging face', 'huggingface',
    'comfyui', 'stable diffusion', 'stablediffusion',
    'mcp', 'model context protocol',
    'vllm', 'sglang', 'ollama',
]

# JD 无技能标签时，从描述中提取缺失技能的候选池
_COMMON_SKILLS = [
    'python', 'java', 'go', 'docker', 'kubernetes', 'mysql',
    'redis', 'linux', 'git', 'react', 'vue', 'spring',
]


def _normalize_skill(name: str) -> str:
    """技能名归一到规范形式（处理同义词/别名）"""
    key = name.lower().strip()
    return SKILL_SYNONYMS.get(key, key)


def _skill_variants(skill: str) -> List[str]:
    """返回技能的全部等价写法；无别名时返回归一化后的自身"""
    norm = _normalize_skill(skill)
    return _SKILL_ALIASES.get(norm, [norm])


def _skill_in_text(skill: str, text: str) -> bool:
    """
    技能名是否以完整词/短语形式出现在文本中。

    短名（≤3 字符）用正则词边界，避免 'Go' 命中 'GoLand'、'es' 命中 'test'；
    长名直接子串匹配。
    """
    skill_lower = skill.lower().strip()
    text_lower = text.lower()

    if len(skill_lower) <= 3:
        pattern = r'(?<![A-Za-z0-9#])' + re.escape(skill_lower) + r'(?![A-Za-z0-9#])'
        return bool(re.search(pattern, text_lower))
    return skill_lower in text_lower


def _skill_hit(skill: str, text: str) -> bool:
    """技能或其任一同义变体是否命中文本"""
    return any(_skill_in_text(v, text) for v in _skill_variants(skill))


# ==================== 编程语言 & 核心栈识别 ====================
# 技术栈锁死依赖「候选核心语言(programming) vs 岗位要求语言」的交叉校验。
# 语言识别只收**编程语言**，不收框架/工具；别名经 _normalize_skill 折叠
# （go→golang、js→javascript 等）+ 一张中文/拼写纠偏表。
# 语义:核心语言自己申报才作数——programming 为空即"未声明核心" → 关掉锁死，不误杀。

_STACK_MISMATCH_MAX = 75   # 锁死岗分数封顶：≥ TIER3_MIN(70) → 落 tier3 可见，不进 tier4 被丢

# 主次技能权重：programming/frameworks 是主业（打分满权），通用工具次之，其他标签最低。
SKILL_CATEGORY_WEIGHTS = {'programming': 1.0, 'frameworks': 1.0, 'tools': 0.6, 'other': 0.35}

# 中文/易混拼写 → 规范语言名（其余别名交给 SKILL_SYNONYMS / _normalize_skill）
_LANG_SPELL = {
    'c语言': 'c', 'c#': 'csharp', 'c++': 'cpp', 'cpp': 'cpp', 'csharp': 'csharp',
    'objective-c': 'objectivec', 'objectivec': 'objectivec',
    'golang': 'golang', 'typescript': 'typescript', 'javascript': 'javascript',
    'python': 'python', 'java': 'java', 'go': 'golang', 'js': 'javascript',
    'ts': 'typescript', 'vbs': 'visualbasic',
}
# 自由正文里**放心当语言词**规范名（排除 'c'/'cpp' 这类短名在正文里的误伤，
# 它们只在干净的标签 token 精确匹配时才认）。
_LANG_FREE = {
    'java', 'python', 'javascript', 'typescript', 'golang',
    'csharp', 'php', 'rust', 'swift', 'kotlin', 'ruby', 'dart',
    'scala', 'matlab', 'objectivec', 'perl', 'lua', 'haskell',
    'erlang', 'elixir', 'clojure', 'solidity', 'cobol', 'fortran',
    'powershell', 'visualbasic',
}
# 只允许精确 token 命中的短语言名（防 'c' 命中正文 'can'、'cpp' 命中英文里不相干的词）
_LANG_TAG_ONLY = {'c', 'cpp', 'shell', 'bash'}
_LANG_ALL = _LANG_FREE | _LANG_TAG_ONLY


def _profile_dict(profile) -> Dict[str, Any]:
    """ResumeProfile dataclass → dict；已是 dict 则原样返回。

    _core_languages / _candidate_has_ai 用 dict 口径读字段，批量入口拿到的是 dataclass，
    转一次再传，避免两套取值。
    """
    if isinstance(profile, dict):
        return profile
    try:
        from dataclasses import asdict
        return asdict(profile)
    except Exception:                                   # noqa: BLE001
        return getattr(profile, '__dict__', {}) if not hasattr(profile, 'get') else profile


def _canon_lang(token: str) -> Optional[str]:
    """单个 token → 规范语言名；不是语言/无法识别返回 None。"""
    t = (token or '').strip().lower()
    if not t:
        return None
    c = _LANG_SPELL.get(t)
    if c:
        return c
    c = _normalize_skill(t)   # go→golang、js→javascript、python→python …
    return c if c in _LANG_ALL else None


def _extract_langs_text(text: str) -> set:
    """正文里词边界扫规范长语言名；短名（c/cpp/shell/bash）不参与，防误伤。"""
    s = (text or '').lower()
    return {lang for lang in _LANG_FREE if _skill_in_text(lang, s)}


def _job_languages(job: Dict[str, Any], req: Dict[str, Any]) -> set:
    """岗位要求的核心语言集合。

    权威要求['技能要求']（干净 token，精确分类可认短名）→ 卡片『技能标签』 →
    兜底 JD 正文（只扫长名）。取不到则返回 ∅（岗位没点明语言 → 不触发锁死）。
    """
    found: set = set()
    src: List[str] = []
    for sk in req.get('技能要求') or []:
        if isinstance(sk, str) and sk.strip():
            src.append(sk)
    if not src:
        tags = (job.get('技能标签') or '').strip()
        src = [t for t in re.split(r'[,，、;；|\s]+', tags) if t]
    for tok in src:
        c = _canon_lang(tok)
        if c:
            found.add(c)
    if not found:
        jd = ' '.join(str(job.get(k) or '') for k in ('岗位要求和职责', '职位'))
        found |= _extract_langs_text(jd)
    return found


def _core_languages(profile: Dict[str, Any]) -> set:
    """候选核心语言：profile.skills['programming'] 归一；空则扫 keywords 兜底；仍空返回 ∅。"""
    profile = profile or {}
    skills = profile.get('skills') or {}
    p = skills.get('programming') or (profile.get('keywords') or [])
    out: set = set()
    for s in p or []:
        c = _canon_lang(str(s))
        if c:
            out.add(c)
    return out


def _candidate_has_ai(profile: Dict[str, Any]) -> bool:
    """候选是否具备 AI 能力：技能/关键词/项目里出现 AI 词（复用 AI_KEYWORDS）。"""
    profile = profile or {}
    parts: List[str] = []
    skills = profile.get('skills') or {}
    for cat in ('programming', 'frameworks', 'tools', 'other'):
        parts.extend(skills.get(cat) or [])
    parts.extend(profile.get('keywords') or [])
    for proj in profile.get('projects') or []:
        parts.append(str(proj.get('description') or ''))
        parts.extend(proj.get('highlights') or [])
        parts.extend(proj.get('tech_stack') or [])
    blob = ' '.join(str(x) for x in parts).lower()
    return any(k.lower() in blob for k in AI_KEYWORDS)


# 折算基准：每月平均工作日（用于「元/天」类日薪换算）
_WORKDAYS_PER_MONTH = 21.75

# 单位 → 「千元」倍率
_SALARY_UNITS = {'万': 10.0, '千': 1.0, 'k': 1.0, '元': 0.001, None: 1.0}


def _parse_salary(salary_str: str) -> Tuple[int, int]:
    """
    解析 BOSS 薪资字符串，统一折算为「月薪千元(K)」区间；无法解析返回 (0, 0)。

    支持:
        '15-25K'        → (15, 25)      '20K'          → (20, 20)
        '15-25K·13薪'   → (15, 25)      忽略「几薪」后缀
        '1-2万'         → (10, 20)      '1.5万'        → (15, 15)
        '8千-1.2万'     → (8, 12)       区间两端单位可不同
        '200-300元/天'  → (4, 7)        按 21.75 个工作日折算
        '年薪20-30万'   → (17, 25)      按 12 个月折算
        '面议' / ''     → (0, 0)
    """
    if not salary_str:
        return 0, 0

    s = salary_str.lower().replace('ｋ', 'k')
    s = re.sub(r'[·•․,，、\s]*\d+\s*薪', '', s)   # 去掉 '·13薪' 这类年终倍数后缀

    # 计薪周期 → 该金额覆盖的月数
    if '年' in s:
        months = 12.0
    elif '天' in s or '日' in s:
        months = 1 / _WORKDAYS_PER_MONTH
    elif '时' in s:
        months = 1 / (_WORKDAYS_PER_MONTH * 8)
    else:
        months = 1.0

    # 拆区间两端，分别取「数值 + 单位」
    pairs = []
    for part in re.split(r'[-~～至]', s, maxsplit=1):
        m = re.search(r'(\d+(?:\.\d+)?)\s*(万|千|k|元)?', part)
        if m:
            pairs.append((float(m.group(1)), m.group(2)))
    if not pairs:
        return 0, 0

    # 单位缺省时沿用另一端的单位（'15-25K' 左端无单位；'8千-1.2万' 两端不同）
    fallback = next((u for _, u in pairs if u), None)
    values = [v * _SALARY_UNITS[u or fallback] / months for v, u in pairs]

    low = round(min(values))
    high = round(max(values))

    # 兜底：折算结果越界视为解析失败（>1000K/月 基本是单位判断错了）
    if high <= 0 or high > 1000:
        return 0, 0
    return max(low, 1), high



# ==================== 难度等级 ====================

def compute_difficulty(match_score: int) -> str:
    """按匹配分数返回难度等级 Easy / Medium / Hard"""
    if match_score >= TIER1_MIN:
        return 'Easy'
    if match_score >= TIER2_MIN:
        return 'Medium'
    return 'Hard'


# ==================== 投递建议分类 ====================

def decide_application_category(
    matched_skill_count: int,
    salary_gap: int,
    exp_gap: Optional[float],
    degree_ok: bool,
    salary_known: bool = True,
) -> Tuple[str, str]:
    """
    裁定投递建议分类。

    分类语义（三类互斥且穷尽）:
        cannot_apply      ⟺ 命中硬门槛。仅三种情形：学历硬性不达标、
                            经验差 ≥ CANNOT_APPLY_EXP_GAP 年、
                            薪资差距 > CANNOT_APPLY_SALARY_GAP K。
        qualified         ⟺ 未命中硬门槛，且技能命中 ≥ QUALIFIED_MIN_SKILLS、
                            薪资在期望区间内、学历经验完全满足。
        need_optimization ⟺ 其余（未命中硬门槛但未达 qualified）——差距可补齐。

    注意「经验差 1~3 年」「薪资差 3~8K」这类中间带归 need_optimization：
    未命中硬门槛就不应告知用户「不可投递」。NEED_OPT_MAX_* 常量只用于
    措辞（略有差距 / 差距较大），不参与分类判定。

    Args:
        matched_skill_count: 简历技能在 JD 中的命中数
        salary_gap: 薪资差距(K)，区间重叠时为 0，否则为到期望区间的距离（始终 ≥ 0）
        exp_gap: JD 要求年限 - 用户实际年限；None 表示无法判断（用户未提供年限）
        degree_ok: JD 学历要求是否被简历满足
        salary_known: 薪资是否成功解析（面议/无法解析为 False）

    Returns:
        (category, reason) —— category 为三枚举之一，reason 为简短中文理由
    """
    # ── 硬门槛 1: 学历 ──
    if not degree_ok:
        return CATEGORY_CANNOT_APPLY, '学历硬性不达标'

    # ── 硬门槛 2: 经验差 ≥ 3 年 ──
    if exp_gap is not None and exp_gap >= CANNOT_APPLY_EXP_GAP:
        return CATEGORY_CANNOT_APPLY, f'经验差{exp_gap:g}年（≥{CANNOT_APPLY_EXP_GAP}年）'

    # ── 硬门槛 3: 薪资期望差距过大 ──
    if salary_known and salary_gap > CANNOT_APPLY_SALARY_GAP:
        return CATEGORY_CANNOT_APPLY, f'薪资期望差距{salary_gap}K（>{CANNOT_APPLY_SALARY_GAP}K）'

    # ── 可直接投递: 技能命中足够 + 薪资在期望范围内 + 学历经验完全满足 ──
    # 经验差未知时不得进入 qualified：无从确认「经验完全满足」
    # 薪资面议时同样不得进入：未经验证的薪资不应触发自动投递
    exp_fully_met = exp_gap is not None and exp_gap <= 0
    if (matched_skill_count >= QUALIFIED_MIN_SKILLS
            and salary_known and salary_gap == 0
            and exp_fully_met):
        return CATEGORY_QUALIFIED, f'技能命中{matched_skill_count}项，薪资匹配，学历经验满足'

    # ── 其余一律优化后投递: 未命中硬门槛，差距可补齐 ──
    details = [f'技能命中{matched_skill_count}项']
    if matched_skill_count < QUALIFIED_MIN_SKILLS:
        details.append(f'缺{QUALIFIED_MIN_SKILLS - matched_skill_count}项达标技能')
    if not salary_known:
        details.append('薪资面议待确认')
    elif salary_gap:
        details.append(f'薪资差{salary_gap}K'
                       + ('' if salary_gap <= NEED_OPT_MAX_SALARY_GAP else '（差距较大）'))
    if exp_gap is None:
        details.append('年限待确认')
    elif exp_gap > 0:
        details.append(f'经验差{exp_gap:g}年'
                       + ('' if exp_gap <= NEED_OPT_MAX_EXP_GAP else '（差距较大）'))
    return CATEGORY_NEED_OPTIMIZATION, '，'.join(details)


# ==================== 6 维度评分 ====================

def score_job_advanced(
    job: Dict[str, Any],
    resume_skills: List[str] = None,
    resume_keywords: List[str] = None,
    salary_min: int = 8,
    salary_max: int = 10,
    user_experience_years: float = 0,
    user_degree: str = '',
    core_languages: set = None,
    has_ai_capability: Optional[bool] = None,
    skill_weights: Dict[str, float] = None,
) -> Dict[str, Any]:
    """
    6 维度规则评分（总分 0-115）+ 投递建议分类

    评分维度:
        1. 薪资匹配 (0-20): JD 薪资区间 vs 期望区间
        2. 经验匹配 (0-20): JD 要求年限 vs 用户实际年限
        3. 学历匹配 (0-15): JD 学历等级 vs 简历学历等级，不达标为硬门槛（0 分）
        4. 技能重合 (0-30): 简历技能在 JD 中词边界匹配 + 别名归一化，每项 5 分
        5. 职位相关 (0-20): 职位名含目标关键词，每个 5 分
        6. AI 核心优势 (0-10): JD 含 AI 关键词，按去重后命中数梯度计分

    分数只决定 difficulty（展示用）；是否投递由 application_category 独立裁定，
    见 decide_application_category —— 硬门槛优先，不受总分影响。

    Args:
        job: 岗位字典，需含 '职位' '薪资' '经验' '学历' '技能标签' '岗位要求和职责'
        resume_skills: 求职者技能列表
        resume_keywords: 目标职位关键词
        salary_min / salary_max: 期望薪资区间(K)
        user_experience_years: 用户实际工作年限，0 表示未提供
        user_degree: 用户学历文本（如 '本科'），空值按 DEFAULT_RESUME_DEGREE_LEVEL 处理
        core_languages: 候选核心语言集合（由 profile 算出）；None=未声明核心 → 跳过技术栈锁死。
            （旧调用/老测试不传则行为不变。）
        has_ai_capability: 候选是否具备 AI 能力。False → 不给 AI 加分；
            True → Python·AI/Agent 岗豁免技术栈锁死。None → 不影响（旧行为）。
        skill_weights: 规范技能名 → 匹配权重；给出时按主次加权（仅影响分数排序，不改 category）。

    Returns:
        含 match_score、各维度分项、match_reasons、matched_skills、missing_skills、
        difficulty、application_category、application_category_reason、
        optimization_points 的字典
    """
    if resume_skills is None:
        resume_skills = []
    if resume_keywords is None:
        resume_keywords = ['AI', 'RAG', 'Agent', 'LLM', '大模型', '后端开发', 'Python', 'Java', '全栈']

    reasons: List[str] = []
    jd_text = (job.get('岗位要求和职责', '') + ' ' + job.get('技能标签', '')).lower()
    pos_name = job.get('职位', '').lower()

    # ── 权威要求覆盖 ──
    # 判定以岗位要求文本为准（requirements.enrich 把缓存挂到 job['_jd_req']）。
    # 卡片标签 / 搜索筛选条件不一定准，只有 JD 文本明确给的值才覆盖；JD 缺失或未提及时
    # 回退卡片字段（即原行为）。"不限" / "面议" 是明确信息，不算空，必须保留。
    req = job.get('_jd_req') or {}
    sal_src = req.get('薪资范围') or job.get('薪资', '')
    exp_src = req.get('经验要求') or job.get('经验', '')
    deg_src = req.get('学历要求') or job.get('学历', '')
    req_skills = req.get('技能要求') or []

    # ── 维度1: 薪资匹配 (0-20) ──
    sal_low, sal_high = _parse_salary(sal_src)
    salary_known = bool(sal_high)
    salary_gap = 0   # 到期望区间的距离(K)，区间重叠为 0

    if not salary_known:
        salary_score = 10
        reasons.append('薪资未知')
    elif sal_low <= salary_max and sal_high >= salary_min:
        # 区间重叠：重叠越宽越接近期望
        overlap = min(sal_high, salary_max) - max(sal_low, salary_min)
        if overlap >= 2:
            salary_score = 20
            reasons.append(f'薪资匹配({sal_low}-{sal_high}K vs 期望{salary_min}-{salary_max}K)')
        else:
            salary_score = 16
            reasons.append(f'薪资基本匹配({sal_low}-{sal_high}K)')
    elif sal_high < salary_min:
        # JD 低于最低期望
        salary_gap = salary_min - sal_high
        if salary_gap <= 3:
            salary_score = 12
            reasons.append(f'薪资略低于期望({sal_low}-{sal_high}K)')
        else:
            salary_score = 5
            reasons.append(f'薪资低于期望({sal_low}-{sal_high}K)')
    else:
        # JD 最低薪资超过最高期望：可冲刺的高薪岗
        salary_gap = sal_low - salary_max
        if salary_gap <= 3:
            salary_score = 14
            reasons.append(f'薪资略高({sal_low}-{sal_high}K，可冲刺)')
        elif salary_gap <= 8:
            salary_score = 10
            reasons.append(f'薪资偏高({sal_low}-{sal_high}K，可冲刺)')
        else:
            salary_score = 5
            reasons.append(f'薪资远超期望({sal_low}-{sal_high}K，竞争激烈)')

    # ── 维度2: 经验匹配 (0-20) ──
    exp = exp_src
    jd_exp_years = parse_experience_years(exp)
    exp_gap: Optional[float] = None   # None = 无法判断（用户或 JD 年限缺失）

    if any(t in exp for t in ['应届', '经验不限', '1年以内', '在校', '在校生']):
        experience_score = 20
        exp_gap = 0.0   # JD 无年限门槛，视为完全满足
        reasons.append('经验要求匹配(应届/经验不限)')
    elif jd_exp_years > 0 and user_experience_years > 0:
        # 双方都有数值年限 → 直接比较
        exp_gap = jd_exp_years - user_experience_years
        gap = exp_gap
        if gap <= 0:
            experience_score = 20
            label = '经验符合'
        elif gap <= 1:
            experience_score = 12
            label = '经验接近'
        elif gap <= 2:
            experience_score = 6
            label = '经验略低'
        else:
            experience_score = 2
            label = '经验不足'
        reasons.append(f'{label}(要求{jd_exp_years}年, 您{user_experience_years}年)')
    elif jd_exp_years > 0:
        # JD 有数值要求但用户未提供年限 → 按 JD 门槛估计
        if jd_exp_years <= 2:
            experience_score = 10
            reasons.append(f'经验要求低({exp}, 可尝试)')
        elif jd_exp_years <= 4:
            experience_score = 5
            reasons.append(f'经验要求中等({exp})')
        else:
            experience_score = 2
            reasons.append(f'经验要求高({exp})')
    else:
        # JD 经验字段无法解析 → 关键词兜底
        if '3-5年' in exp:
            experience_score = 3
            reasons.append('经验要求3-5年(偏高)')
        elif '5-10年' in exp or '10年以上' in exp:
            experience_score = 1
            reasons.append(f'经验要求高({exp})')
        else:
            experience_score = 8
            reasons.append(f'经验要求({exp})')

    # ── 维度3: 学历匹配 (0-15) ──
    # 等级比较：JD 要求高于简历学历即为硬门槛（0 分 + 后续判不可投递）
    deg = deg_src
    jd_degree_level = parse_degree_level(deg)
    resume_degree_level = parse_degree_level(user_degree, default=DEFAULT_RESUME_DEGREE_LEVEL)
    if not resume_degree_level:
        resume_degree_level = DEFAULT_RESUME_DEGREE_LEVEL
    degree_ok = jd_degree_level <= resume_degree_level

    if not jd_degree_level:
        degree_score = 15
        reasons.append('学历匹配(不限)')
    elif degree_ok:
        degree_score = 15
        reasons.append(f'学历匹配({deg})')
    else:
        degree_score = 0
        reasons.append(f'学历要求{deg}(硬门槛，简历不达标)')

    # ── 维度4: 技能重合 (0-30) ──
    matched: List[str] = []
    matched_norms = set()
    for skill in resume_skills:
        norm = _normalize_skill(skill)
        if norm in matched_norms:
            continue
        if _skill_hit(skill, jd_text):
            matched.append(skill)
            matched_norms.add(norm)
    if skill_weights:
        # 按主次技能加权：核心语言/框架满权，通用工具打折、其他标签最低。
        # 只改分数排序，不改 category（聚类仍用 len(matched)）。
        skills_score = min(30, round(sum(skill_weights.get(norm, 1.0) for norm in matched_norms)))
    else:
        skills_score = min(30, len(matched) * 5)
    reasons.append(f'技能命中:{len(matched)}项')

    # ── 维度5: 职位相关度 (0-20) ──
    position_score = min(20, sum(5 for kw in resume_keywords if kw.lower() in pos_name))
    if position_score:
        reasons.append(f'职位相关:+{position_score}')

    # ── 维度6: AI 核心优势 (0-10) ──
    # 归一化去重：'llm' 与 '大模型' 指向同一概念，只计 1 项。
    # 门控：AI 加分只在候选人自己具备 AI 能力时给——不允许"JD 堆 AI 词就白拿分"。
    ai_count = len({_normalize_skill(k) for k in AI_KEYWORDS if k in jd_text})
    if ai_count >= 5:
        ai_bonus = 10
    elif ai_count >= 3:
        ai_bonus = 7
    elif ai_count >= 1:
        ai_bonus = 4
    else:
        ai_bonus = 0
    if has_ai_capability is False:
        ai_bonus = 0
    if ai_bonus:
        reasons.append(f'AI/RAG/Agent匹配:+{ai_bonus} ({ai_count}项)')

    score = (salary_score + experience_score + degree_score
             + skills_score + position_score + ai_bonus)

    # ── 缺失技能: JD 要求但用户不具备 ──
    # 两侧都经 _normalize_skill 归一，故比较规范名即可覆盖别名
    user_norms = {_normalize_skill(s) for s in resume_skills}
    # 缺失技能先看岗位要求抽出的权威技能；没有则回退卡片技能标签（原行为）
    jd_tag_skills = req_skills or (job.get('技能标签', '') or '').split()
    missing: List[str] = []
    seen_norms = set()
    for jd_skill in jd_tag_skills:
        norm = _normalize_skill(jd_skill)
        if norm in seen_norms:
            continue
        seen_norms.add(norm)
        if norm not in user_norms:
            missing.append(jd_skill)
            if len(missing) >= 5:
                break

    # 兜底：JD 无技能标签时从描述中提取
    if not jd_tag_skills:
        missing = [s for s in _COMMON_SKILLS
                   if _skill_in_text(s, jd_text) and _normalize_skill(s) not in user_norms][:5]

    difficulty = compute_difficulty(score)

    # ── 投递建议分类（硬门槛优先，与总分解耦）──
    application_category, category_reason = decide_application_category(
        matched_skill_count=len(matched),
        salary_gap=salary_gap,
        exp_gap=exp_gap,
        degree_ok=degree_ok,
        salary_known=salary_known,
    )

    # ── 技术栈锁死 ──
    # 岗位要求语言不在候选核心语言里，且不触发「次要 AI 兴趣」→ 压为不可投 + 分数封顶，
    # 让这类纯非核心栈的岗沉到 tier3（报告可见理由，auto-apply 永不触碰）。
    # Python·AI/Agent 岗（JD 有 AI 词 + 候选自己具备 AI 能力）豁免——命中候选列的次要兴趣。
    stack_mismatch = False
    job_langs: set = set()
    if core_languages:
        job_langs = _job_languages(job, req)
        ai_interest = ai_count >= 1 and has_ai_capability is True
        stack_mismatch = bool(job_langs) and job_langs.isdisjoint(core_languages) and not ai_interest
    if stack_mismatch:
        stack_reason = '技术栈不匹配(核心%s vs 岗位%s)' % (
            '/'.join(sorted(core_languages)), '/'.join(sorted(job_langs)))
        score = min(score, _STACK_MISMATCH_MAX)
        difficulty = 'Hard'
        application_category = CATEGORY_CANNOT_APPLY
        category_reason = stack_reason
        reasons.append(stack_reason)

    # ── 优化建议 ──
    if score >= TIER2_MIN:
        optimization_points = [
            f'强调{job.get("职位", "")}相关项目经验',
            '突出RAG/Agent实战能力',
            '量化项目成果数据',
        ]
    else:
        optimization_points = [
            f'补充{missing[0] if missing else "相关"}技能',
            '强化项目描述与岗位关联',
        ]

    return {
        'match_score': score,
        'salary_score': salary_score,
        'experience_score': experience_score,
        'degree_score': degree_score,
        'skills_score': skills_score,
        'position_score': position_score,
        'ai_bonus': ai_bonus,
        'match_reasons': reasons,
        'matched_skills': matched,
        'missing_skills': missing,
        'difficulty': difficulty,
        'application_category': application_category,
        'application_category_reason': category_reason,
        'optimization_points': optimization_points,
        'parsed_salary_low': sal_low,
        'parsed_salary_high': sal_high,
    }


# ==================== 批量分类 ====================

def classify_jobs_advanced(
    profile: ResumeProfile,
    jobs: List[Dict[str, Any]],
    tier_thresholds: Tuple[int, int] = (TIER1_MIN, TIER2_MIN)
) -> Tuple[List[Dict], List[Dict], List[Dict], List[Dict]]:
    """
    批量 6 维度评分并分为 4 层

    分层由 application_category 驱动（而非分数），保证「岗位所在桶」与
    「岗位的 application_category 字段」永不矛盾：

        tier1 = qualified
        tier2 = need_optimization
        tier3 = cannot_apply 且 score ≥ TIER3_MIN
        tier4 = cannot_apply 且 score < TIER3_MIN（报告中丢弃）

    Args:
        profile: 简历解析结果
        jobs: 岗位列表
        tier_thresholds: 保留参数，仅用于兼容旧调用签名（分层已改为分类驱动）

    Returns:
        (tier1, tier2, tier3, tier4) 四层岗位列表，各层按分数降序
    """
    all_skills: List[str] = []
    for cat in ['programming', 'frameworks', 'tools', 'other']:
        # `or []`：某类技能为空时解析产出的是 null 而非缺键，get 的默认值不生效，
        # extend(None) 会 TypeError（同本文件 676 行的坑）
        all_skills.extend((profile.skills or {}).get(cat) or [])
    if not all_skills:
        all_skills = profile.keywords or []

    resume_keywords = profile.keywords or [
        'AI', 'RAG', 'Agent', 'LLM', '大模型', '后端开发', 'Python', 'Java', '全栈'
    ]

    # 技术栈锁死所需：核心语言 + 是否具备 AI 能力，一次算好所有岗位共用。
    core_languages = _core_languages(_profile_dict(profile))
    has_ai_capability = _candidate_has_ai(_profile_dict(profile))
    # 主次技能权重：按类目标签逐技能打权重，供评分按主次加权。
    skill_weights: Dict[str, float] = {}
    for cat, w in SKILL_CATEGORY_WEIGHTS.items():
        for s in (profile.skills or {}).get(cat) or []:
            skill_weights.setdefault(_normalize_skill(str(s)), w)

    salary = profile.salary_expectation or {}
    # `or` 而不是 get 的第二参数：简历没写期望薪资时，解析产出的是
    # {"min": null, "max": null}——键存在、值是 None，默认值形同虚设，
    # 于是 None 一路传到 score_job_advanced 的 `sal_low <= salary_max` 崩成
    # TypeError，整批评分挂掉（2026-08-13 实测）。
    sal_min = salary.get('min') or 8
    sal_max = salary.get('max') or 10
    user_experience_years = float(profile.experience.get('total_years', 0) or 0)
    user_degree = profile.education.get('degree', '') or ''

    print(f"\n正在增强分析 {len(jobs)} 个岗位(6维度评分)...")

    tier1, tier2, tier3, tier4 = [], [], [], []

    for i, job in enumerate(jobs):
        if (i + 1) % 50 == 0:
            print(f"  进度: {i+1}/{len(jobs)}")

        result = score_job_advanced(
            job,
            resume_skills=all_skills,
            resume_keywords=resume_keywords,
            salary_min=sal_min,
            salary_max=sal_max,
            user_experience_years=user_experience_years,
            user_degree=user_degree,
            core_languages=core_languages,
            has_ai_capability=has_ai_capability,
            skill_weights=skill_weights,
        )
        job_result = {**job, **result}

        category = result['application_category']
        if category == CATEGORY_QUALIFIED:
            tier1.append(job_result)
        elif category == CATEGORY_NEED_OPTIMIZATION:
            tier2.append(job_result)
        elif result['match_score'] >= TIER3_MIN:
            tier3.append(job_result)
        else:
            tier4.append(job_result)

    # 报告是给人看的，主序仍是匹配分；活跃度只做同分裁决
    for tier in (tier1, tier2, tier3, tier4):
        tier.sort(key=lambda x: (x['match_score'], hr_activity_sort_key(x)), reverse=True)

    print(f"分析完成！Tier1: {len(tier1)}, Tier2: {len(tier2)}, "
          f"Tier3: {len(tier3)}, Tier4: {len(tier4)}")

    return tier1, tier2, tier3, tier4


# ==================== 岗位视图（唯一的字段映射处） ====================

# 岗位从 CSV（中文列名）走到前端（ASCII 键）的路上要被重新构造一次。
# 这里曾经有三份各自独立的白名单：scoring.tiers_to_classification、
# report.generate_bauhaus_json、deep_analysis 的 job_result。三者服务不同路径
# （快速模式 HTML / 快速模式 JSON / 深度模式），加一个字段必须同步改三处，
# 漏一处那条路径就静默丢字段 —— 没有报错，只是前端拿到空值。
# HR 活跃度就这么丢过一次：JSON 正常，HTML 每张卡都显示「未采集」，
# 而数据其实已经采到了。比不显示更糟的是显示成没有。
#
# 所以现在只有这一处枚举岗位字段。新增字段只加在这里。
def build_job_view(
    job: Dict[str, Any],
    fallback_category: str = '',
    *,
    company_info_len: int = 500,
    jd_len: int = 1000,
    overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    岗位 dict → 报告/前端统一视图。

    读取时对「中文列名（来自 CSV）」和「ASCII 键（已归一化过的 job）」都做兜底，
    所以同一个函数能吃原始 CSV 行，也能吃已经映射过一轮的 job。

    Args:
        job: 岗位 dict，中文或 ASCII 键均可
        fallback_category: job 自身没有 application_category 时的兜底值
        company_info_len / jd_len: 截断长度。JSON 产出比 HTML 更紧，故可调。
        overrides: 调用方已算好的字段（如深度模式的 blended 分数），最后覆盖上去

    Returns:
        扁平 dict，键为前端读取的 ASCII 名
    """
    link = job.get('link', '') or ''
    job_id = job.get('job_id', '') or ''
    if not job_id and link:
        m = re.search(r'([A-Za-z0-9_-]{7,})(?:\.html|$)', link)
        if m:
            job_id = 'job_' + m.group(1)

    score = job.get('match_score', 50)
    company = job.get('公司', job.get('company', '')) or ''
    scale = job.get('规模', job.get('scale', '')) or ''
    skill_tags = job.get('技能标签', job.get('skill_tags', '')) or ''
    jd = (job.get('岗位要求和职责', job.get('jd', '')) or '')
    company_info = (job.get('公司信息', job.get('company_info', '')) or '')

    view = {
        'job_id': job_id or f"job_{abs(hash(link)):x}"[:12],
        'link': link or '#',
        'company': company,
        'position': job.get('职位', job.get('position', '')) or '',
        'city': job.get('城市', job.get('city', '')) or '',
        'salary': job.get('薪资', job.get('salary', '')) or '',
        'experience': job.get('经验', job.get('experience', '')) or '',
        'education': job.get('学历', job.get('degree', job.get('education', ''))) or '',
        'skills': skill_tags,
        'skill_tags': skill_tags,
        'welfare_tags': job.get('福利标签', job.get('welfare_tags', '')) or '',
        'source_file': job.get('source_file', '') or '',
        'company_info': company_info[:company_info_len],
        'scale': scale,
        'jd': jd[:jd_len],
        'job_detail': jd[:200],
        'company_size': parse_company_size(company, scale),
        'match_score': score,
        'difficulty': compute_difficulty(score),
        'application_category': job.get('application_category', fallback_category),
        'application_category_reason': job.get('application_category_reason', '') or '',
        'classification_reason': '; '.join(job.get('match_reasons', [])[:4]),
        'missing_items': job.get('missing_skills', [])[:5],
        'optimization_points': job.get('optimization_points', []),
        # HR 活跃度快照（爬取瞬间）。rank 为 None 表示「未采集」——爬取时没加 -d，
        # 详情 API 没被调用过。这与「不活跃」是两件事，前端必须分开显示。
        'hr_active_desc': job.get('HR活跃度', job.get('hr_active_desc', '')) or '',
        'hr_online': job.get('HR在线', job.get('hr_online', '')) or '',
        'hr_title': job.get('HR职位', job.get('hr_title', '')) or '',
        'hr_activity_rank': hr_activity_rank(job),
        # 深度模式专有，快速模式留空以保证两种模式字段集一致（前端无需判空）
        'highlight': job.get('highlight', '') or '',
        'risk': job.get('risk', '') or '',
        'is_deep': bool(job.get('is_deep', False)),
    }

    if overrides:
        view.update(overrides)
    return view


# 供回归测试和调用方校验：视图的完整字段集合。
JOB_VIEW_FIELDS = frozenset(build_job_view({}).keys())


# ==================== 适配器 ====================

def tiers_to_classification(
    tier1: List[Dict],
    tier2: List[Dict],
    tier3: List[Dict]
) -> JobClassification:
    """
    将 4 层输出映射回 JobClassification 三桶格式

    tier1 → qualified（高匹配）
    tier2 → need_optimization（可优化）
    tier3 → cannot_apply（不匹配）
    tier4 丢弃
    """
    return JobClassification(
        cannot_apply=[build_job_view(j, CATEGORY_CANNOT_APPLY) for j in tier3],
        need_optimization=[build_job_view(j, CATEGORY_NEED_OPTIMIZATION) for j in tier2],
        qualified=[build_job_view(j, CATEGORY_QUALIFIED) for j in tier1],
    )
