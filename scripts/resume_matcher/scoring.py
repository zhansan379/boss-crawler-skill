#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
评分与岗位分类模块

包含：
- 独立规则匹配函数（SKILL.md Phase 5a 直接引用）
- 新版 6 维度评分 (score_job_advanced)
- 4 级分层分类 (classify_jobs_advanced)
- tiers → JobClassification 适配器
"""

import re
from typing import List, Dict, Any, Optional, Tuple

from .config import (
    ResumeProfile, JobRequirements, MatchResult,
    DifficultyPrediction, JobClassification,
    DIFFICULTY_WEIGHTS,
)
from .utils import (
    parse_education_level, parse_experience_years,
    parse_salary_range, parse_company_size, edu_level_to_str,
)


# ==================== 技能别名映射 & AI关键词 ====================

# 技能同义词 → 规范名（Fix 4: 支持多种写法归一化）
SKILL_SYNONYMS: Dict[str, str] = {}
_SKILL_CANONICAL_MAP: Dict[str, str] = {}

def _build_skill_aliases():
    """构建技能别名双向映射"""
    _raw = {
        'kubernetes':     ['k8s', 'k3s', 'kubernetes'],
        'golang':         ['go', 'golang'],
        'postgresql':     ['postgresql', 'postgres', 'pgsql', 'postgre'],
        'elasticsearch':  ['elasticsearch', 'es', 'elastic'],
        'javascript':     ['javascript', 'js', 'ecmascript'],
        'typescript':     ['typescript', 'ts'],
        'react':          ['react', 'reactjs', 'react.js'],
        'vue':            ['vue', 'vuejs', 'vue.js'],
        'node.js':        ['node.js', 'nodejs', 'node'],
        'tensorflow':     ['tensorflow', 'tf'],
        'pytorch':        ['pytorch', 'torch'],
        'machine-learning': ['ml', 'machine-learning', '机器学习', '深度学习'],
        'nlp':            ['nlp', '自然语言处理'],
        'cv':             ['cv', '计算机视觉'],
        'llm':            ['llm', '大模型', '大语言模型', 'large-language-model'],
        'mongodb':        ['mongodb', 'mongo'],
        'ci/cd':          ['ci/cd', 'cicd', 'ci cd', 'ci_cd'],
        'docker':         ['docker', 'container'],
        'redis':          ['redis'],
        'aws':            ['aws', 'amazon-web-services'],
        'generative-ai':  ['genai', 'aigc', '生成式ai', '生成式人工智能'],
    }
    for canonical, variants in _raw.items():
        for v in variants:
            SKILL_SYNONYMS[v.lower()] = canonical
            _SKILL_CANONICAL_MAP[v.lower()] = canonical

_build_skill_aliases()


def _normalize_skill(name: str) -> str:
    """将技能名归一到规范形式（处理同义词/别名，Fix 4）"""
    return SKILL_SYNONYMS.get(name.lower().strip(), name.lower().strip())


def _skill_in_text(skill: str, text: str) -> bool:
    """
    检查技能名是否以完整词/短语形式出现在文本中（Fix 4: 词边界匹配）。

    短技能名（≤3字符）使用正则词边界避免误匹配（如 'Go' 匹配 'GoLand'）。
    长技能名使用直接子串匹配。
    """
    skill_lower = skill.lower().strip()
    text_lower = text.lower()

    if len(skill_lower) <= 3:
        # 短技能：要求词边界，避免子串误匹配。
        # 使用大小写不敏感的字符类 [A-Za-z0-9#] 确保 'L' 也视为字母
        pattern = r'(?<![A-Za-z0-9#])' + re.escape(skill_lower) + r'(?![A-Za-z0-9#])'
        return bool(re.search(pattern, text_lower))
    else:
        return skill_lower in text_lower


# AI 核心能力关键词（Fix 5: 扩展列表，梯度计分）
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


# ==================== 独立规则匹配函数 ====================

def analyze_job_requirements_quick(job: Dict[str, Any]) -> JobRequirements:
    """快速解析岗位要求（纯规则，不调用LLM）"""
    edu = parse_education_level(job.get('学历', ''))
    exp_years = parse_experience_years(job.get('经验', ''))
    salary = parse_salary_range(job.get('薪资', ''))
    company_size = parse_company_size(job.get('公司', ''), job.get('规模', ''))

    # 解析技能
    skills_str = job.get('技能标签', '')
    skills = [s.strip() for s in skills_str.split() if s.strip()] if skills_str else []

    return JobRequirements(
        required_education=edu_level_to_str(edu),
        required_experience_years=exp_years,
        required_skills=skills[:5],
        preferred_skills=skills[5:],
        other_requirements=[],
        salary_range=salary,
        company_size=company_size,
        key_keywords=skills[:10]
    )


def calculate_education_match(profile: ResumeProfile, requirements: JobRequirements) -> Dict[str, Any]:
    """计算学历匹配度"""
    user_edu = parse_education_level(profile.education.get('degree', ''))
    req_edu = parse_education_level(requirements.required_education)

    if req_edu == 0:  # 不限
        return {'score': 100, 'match': True, 'reason': '学历不限'}

    if user_edu >= req_edu:
        return {'score': 100, 'match': True, 'reason': '学历符合'}
    elif user_edu == req_edu - 1:
        return {'score': 50, 'match': False, 'reason': '学历略低'}
    else:
        return {'score': 0, 'match': False, 'reason': '学历不满足'}


def calculate_experience_match(profile: ResumeProfile, requirements: JobRequirements) -> Dict[str, Any]:
    """计算经验匹配度"""
    user_years = int(profile.experience.get('total_years', 0) or 0)
    req_years = requirements.required_experience_years

    if req_years == 0:
        return {'score': 100, 'match': True, 'reason': '经验不限'}

    if user_years >= req_years:
        score = min(100, 80 + (user_years - req_years) * 5)
        return {'score': score, 'match': True, 'reason': '经验符合'}
    elif user_years >= req_years - 1:
        score = int(user_years / req_years * 80)
        return {'score': score, 'match': True, 'reason': '经验接近'}
    else:
        score = int(user_years / req_years * 50)
        return {'score': max(0, score), 'match': False, 'reason': '经验不足'}


def calculate_skills_match(profile: ResumeProfile, requirements: JobRequirements) -> Dict[str, Any]:
    """计算技能匹配度（模糊匹配）"""
    user_skills = set()
    for category in ['programming', 'frameworks', 'tools', 'other']:
        if profile.skills.get(category):
            user_skills.update(s.lower() for s in profile.skills[category])

    required = requirements.required_skills
    preferred = requirements.preferred_skills

    # 匹配必备技能
    matched_required = []
    missing_required = []
    for skill in required:
        skill_lower = skill.lower()
        if any(skill_lower in us or us in skill_lower for us in user_skills):
            matched_required.append(skill)
        else:
            missing_required.append(skill)

    # 匹配加分技能
    matched_preferred = []
    for skill in preferred:
        skill_lower = skill.lower()
        if any(skill_lower in us or us in skill_lower for us in user_skills):
            matched_preferred.append(skill)

    # 计算分数
    req_score = (len(matched_required) / len(required) * 70) if required else 70
    pref_score = (len(matched_preferred) / len(preferred) * 30) if preferred else 30
    total_score = int(req_score + pref_score)

    return {
        'score': total_score,
        'match': len(missing_required) <= len(required) * 0.3 if required else True,
        'matched': matched_required + matched_preferred,
        'missing': missing_required,
        'reason': f'匹配{len(matched_required + matched_preferred)}项，缺失{len(missing_required)}项'
    }


def predict_difficulty(
    profile: ResumeProfile,
    job: Dict[str, Any],
    match: MatchResult,
    requirements: Optional[JobRequirements] = None
) -> DifficultyPrediction:
    """投递难度预测"""
    if requirements is None:
        requirements = analyze_job_requirements_quick(job)

    edu_score = match.education_match.get('score', 50)
    exp_score = match.experience_match.get('score', 50)
    skill_score = match.skills_match.get('score', 50)

    # 公司规模得分
    if requirements.company_size == '大厂':
        size_score = 60
    elif requirements.company_size == '中厂':
        size_score = 80
    else:
        size_score = 90

    # 薪资匹配得分
    user_salary = profile.salary_expectation
    job_salary = requirements.salary_range
    if user_salary.get('min') and job_salary.get('min'):
        if user_salary['min'] <= job_salary.get('max', 0) and user_salary.get('max', 0) >= job_salary.get('min', 0):
            salary_score = 100
        else:
            salary_score = 50
    else:
        salary_score = 80

    total = int(
        edu_score * DIFFICULTY_WEIGHTS['education'] +
        exp_score * DIFFICULTY_WEIGHTS['experience'] +
        skill_score * DIFFICULTY_WEIGHTS['skills'] +
        size_score * DIFFICULTY_WEIGHTS['company_size'] +
        salary_score * DIFFICULTY_WEIGHTS['salary']
    )

    if total >= 70:
        level = '易'
    elif total >= 50:
        level = '中'
    else:
        level = '难'

    success_rate = min(90, max(10, total))

    return DifficultyPrediction(
        difficulty_level=level,
        success_rate=success_rate,
        factors={
            'education': edu_score,
            'experience': exp_score,
            'skills': skill_score,
            'company_size': size_score,
            'salary': salary_score
        },
        total_score=total
    )


# ==================== 增强评分系统（6维度精准匹配） ====================

def score_job_advanced(
    job: Dict[str, Any],
    resume_skills: List[str] = None,
    resume_keywords: List[str] = None,
    salary_min: int = 8,
    salary_max: int = 10,
    user_experience_years: float = 0,
) -> Dict[str, Any]:
    """
    增强版岗位评分（6维度精细打分，区分度 0-115 分）

    评分维度:
        1. 薪资匹配 (0-20): JD薪资 vs 期望薪资（Fix 1: 使用实际参数）
        2. 经验匹配 (0-20): JD经验要求 vs 用户实际年限（Fix 6）
        3. 学历匹配 (0-15): 本科/大专 vs 硕士要求
        4. 技能重合 (0-30): 简历技能在JD中词边界匹配 + 别名归一化（Fix 4）
        5. 职位相关 (0-20): 岗位名含目标关键词
        6. AI核心优势 (0-10): JD含AI关键词梯度计分（Fix 5）

    Args:
        job: 岗位字典，需含 '职位', '薪资', '经验', '学历', '技能标签', '岗位要求和职责'
        resume_skills: 求职者技能列表
        resume_keywords: 目标职位关键词
        salary_min: 期望最低薪资(K) -- 实际参与计算（Fix 1）
        salary_max: 期望最高薪资(K) -- 实际参与计算（Fix 1）
        user_experience_years: 用户实际工作年限，0表示未提供（Fix 6）

    Returns:
        {
            'match_score': int,        # 总分 (0-115)
            'salary_score': int,
            'experience_score': int,
            'degree_score': int,
            'skills_score': int,
            'position_score': int,
            'ai_bonus': int,
            'match_reasons': [str],    # 匹配理由
            'matched_skills': [str],   # 命中的技能
            'missing_skills': [str],   # 用户缺失的JD要求技能（Fix 7）
            'difficulty': str,         # Easy/Medium/Hard
            'success_rate': int,       # 预估获得面试邀请概率 15-90%（Fix 3）
            'optimization_points': [str],
        }
    """
    if resume_skills is None:
        resume_skills = []
    if resume_keywords is None:
        resume_keywords = ['AI', 'RAG', 'Agent', 'LLM', '大模型', '后端开发', 'Python', 'Java', '全栈']

    score = 0
    reasons = []
    jd_text = (job.get('岗位要求和职责', '') + ' ' + job.get('技能标签', '')).lower()
    pos_name = job.get('职位', '').lower()

    # ── 维度1: 薪资匹配 (0-20) ── Fix 1: 使用实际期望薪资
    salary_str = job.get('薪资', '')
    sal_low = sal_high = 0
    try:
        parts = salary_str.replace('K', '').replace('·', ' ').split()[0].split('-')
        sal_low = int(parts[0])
        sal_high = int(parts[1]) if len(parts) > 1 else sal_low
    except (ValueError, IndexError):
        sal_low = sal_high = 0

    salary_score = 0
    if sal_low == 0 and sal_high == 0:
        # 薪资不可解析
        salary_score = 10
        reasons.append('薪资未知')
    elif sal_low <= salary_max and sal_high >= salary_min:
        # 范围重叠：JD薪资与期望区间相交
        # 计算重叠程度，更接近期望 → 更高分
        overlap_low = max(sal_low, salary_min)
        overlap_high = min(sal_high, salary_max)
        if overlap_high - overlap_low >= 2:
            salary_score = 20
            reasons.append(f'薪资匹配({sal_low}-{sal_high}K vs 期望{salary_min}-{salary_max}K)')
        else:
            salary_score = 16
            reasons.append(f'薪资基本匹配({sal_low}-{sal_high}K)')
    elif salary_min > sal_high:
        # JD薪资低于用户最低期望
        gap = salary_min - sal_high
        if gap <= 3:
            salary_score = 12
            reasons.append(f'薪资略低于期望({sal_low}-{sal_high}K)')
        else:
            salary_score = 5
            reasons.append(f'薪资低于期望({sal_low}-{sal_high}K)')
    elif salary_max < sal_low:
        # JD最低薪资超过用户最高期望（可冲刺的高薪岗位）
        gap = sal_low - salary_max
        if gap <= 3:
            salary_score = 14
            reasons.append(f'薪资略高({sal_low}-{sal_high}K，可冲刺)')
        elif gap <= 8:
            salary_score = 10
            reasons.append(f'薪资偏高({sal_low}-{sal_high}K，可冲刺)')
        else:
            salary_score = 5
            reasons.append(f'薪资远超期望({sal_low}-{sal_high}K，竞争激烈)')
    else:
        salary_score = 10
        reasons.append(f'薪资({sal_low}-{sal_high}K)')
    score += salary_score

    # ── 维度2: 经验匹配 (0-20) ── Fix 6: 使用用户实际年限
    exp = job.get('经验', '')
    experience_score = 0
    jd_exp_years = parse_experience_years(exp)

    # 应届/经验不限：适合所有人
    if any(t in exp for t in ['应届', '经验不限', '1年以内', '在校', '在校生']):
        experience_score = 20
        reasons.append('经验要求匹配(应届/经验不限)')
    elif jd_exp_years > 0 and user_experience_years > 0:
        # JD 和用户都有数值型经验 → 直接比较
        if user_experience_years >= jd_exp_years:
            experience_score = 20
            reasons.append(f'经验符合(要求{jd_exp_years}年, 您{user_experience_years}年)')
        elif user_experience_years >= jd_exp_years - 1:
            experience_score = 12
            reasons.append(f'经验接近(要求{jd_exp_years}年, 您{user_experience_years}年)')
        elif user_experience_years >= jd_exp_years - 2:
            experience_score = 6
            reasons.append(f'经验略低(要求{jd_exp_years}年, 您{user_experience_years}年)')
        else:
            experience_score = 2
            reasons.append(f'经验不足(要求{jd_exp_years}年, 您{user_experience_years}年)')
    elif jd_exp_years > 0:
        # JD有数值要求但用户未提供年限 → 基于JD关键词估计
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
        # JD经验字段无法解析 → 基于关键词兜底
        if '3-5年' in exp:
            experience_score = 3
            reasons.append('经验要求3-5年(偏高)')
        elif '5-10年' in exp or '10年以上' in exp:
            experience_score = 1
            reasons.append(f'经验要求高({exp})')
        else:
            experience_score = 8
            reasons.append(f'经验要求({exp})')
    score += experience_score

    # ── 维度3: 学历匹配 (0-15) ──
    deg = job.get('学历', '')
    degree_score = 0
    if '本科' in deg or '大专' in deg or not deg:
        degree_score = 15
        reasons.append('学历匹配')
    elif '硕士' in deg:
        reasons.append('学历要求硕士(硬门槛)')
    else:
        degree_score = 5
    score += degree_score

    # ── 维度4: 技能重合 (0-30) ── Fix 4: 词边界匹配 + 别名归一化
    matched = []
    matched_norm = set()  # 去重用
    for skill in resume_skills:
        skill_norm = _normalize_skill(skill)
        if skill_norm in matched_norm:
            continue
        # 检查技能本身（词边界）
        if _skill_in_text(skill, jd_text):
            matched.append(skill)
            matched_norm.add(skill_norm)
        else:
            # 检查同义变体
            for variant in _SKILL_CANONICAL_MAP.get(skill_norm, [skill]):
                if variant != skill_norm and _skill_in_text(variant, jd_text):
                    matched.append(skill)
                    matched_norm.add(skill_norm)
                    break
    skills_score = min(30, len(matched) * 5)
    score += skills_score
    reasons.append(f'技能命中:{len(matched)}项')

    # ── 维度5: 职位相关度 (0-20) ──
    pos_rel = sum(5 for kw in resume_keywords if kw.lower() in pos_name.lower())
    position_score = min(20, pos_rel)
    score += position_score
    if pos_rel > 0:
        reasons.append(f'职位相关:+{position_score}')

    # ── 维度6: AI核心优势 (0-10) ── Fix 5: 梯度计分
    ai_bonus = 0
    ai_hits = [k for k in AI_KEYWORDS if k in jd_text]
    # 去重：不同关键词可能指向同一概念
    ai_unique = set(_normalize_skill(k) if k not in AI_KEYWORDS else k for k in ai_hits)
    ai_count = len(ai_unique)
    if ai_count >= 5:
        ai_bonus = 10
    elif ai_count >= 3:
        ai_bonus = 7
    elif ai_count >= 1:
        ai_bonus = 4
    if ai_bonus > 0:
        score += ai_bonus
        reasons.append(f'AI/RAG/Agent匹配:+{ai_bonus} ({ai_count}项)')

    # ── 缺失技能 ── Fix 7: JD要求但用户不具备的技能
    jd_skills_str = job.get('技能标签', '')
    jd_tag_skills = [s.strip() for s in jd_skills_str.split() if s.strip()] if jd_skills_str else []
    # 用户技能归一化集合
    user_skill_norms = set(_normalize_skill(s) for s in resume_skills)
    missing = []
    seen_missing = set()
    for jd_skill in jd_tag_skills:
        jd_norm = _normalize_skill(jd_skill)
        if jd_norm in seen_missing:
            continue
        seen_missing.add(jd_norm)
        # 检查用户是否有匹配技能
        is_matched = jd_norm in user_skill_norms
        if not is_matched:
            # 检查同义变体
            jd_variants = _SKILL_CANONICAL_MAP.get(jd_norm, [jd_norm])
            for user_norm in user_skill_norms:
                if user_norm in jd_variants:
                    is_matched = True
                    break
        if not is_matched:
            missing.append(jd_skill)
        if len(missing) >= 5:
            break
    # 兜底：如果JD没有标签技能，尝试从描述中提取关键技能
    if not missing and not jd_tag_skills:
        common_skills = ['python', 'java', 'go', 'docker', 'kubernetes', 'mysql',
                         'redis', 'linux', 'git', 'react', 'vue', 'spring']
        missing = [s for s in common_skills[:5] if _skill_in_text(s, jd_text) and _normalize_skill(s) not in user_skill_norms][:5]

    # ── 难度 & 成功率 ── Fix 2: 公司规模调整
    # 基础成功率 = 匹配分数的分段线性映射（含义：预估获得面试邀请的概率）
    if score >= 100:
        difficulty = 'Easy'
        success_rate = 65 + min(25, (score - 100))
    elif score >= 85:
        difficulty = 'Medium'
        success_rate = 40 + (score - 85)
    else:
        difficulty = 'Hard'
        success_rate = max(15, 15 + (score - 55))

    # 公司规模修正
    company = job.get('公司', '')
    scale = job.get('规模', '')
    company_size = parse_company_size(company, scale)
    if company_size == '大厂':
        success_rate = max(10, success_rate - 12)
        reasons.append('大厂(竞争激烈,成功率下调)')
    elif company_size == '中厂':
        success_rate = max(10, success_rate - 3)
    else:
        success_rate = min(90, success_rate + 3)

    # ── 优化建议 ──
    optimization_points = []
    if score >= 85:
        optimization_points = [
            f'强调{job.get("职位", "")}相关项目经验',
            '突出RAG/Agent实战能力',
            '量化项目成果数据'
        ]
    else:
        optimization_points = [
            f'补充{missing[0] if missing else "相关"}技能',
            '强化项目描述与岗位关联'
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
        'success_rate': success_rate,
        'optimization_points': optimization_points,
        'parsed_salary_low': sal_low,
        'parsed_salary_high': sal_high,
    }


def compute_difficulty_success_rate(
    match_score: int,
    company_size: str = '',
) -> Tuple[str, int]:
    """
    根据匹配分数计算难度等级和成功率（Fix 2: 含公司规模调整）

    success_rate 含义：预估获得面试邀请的概率 (estimated probability of
    receiving an interview invitation)，百分比值 10-90%（Fix 3）。

    Args:
        match_score: 综合匹配分数
        company_size: 公司规模类别 ('大厂'/'中厂'/'小厂')，用于调整成功率。
                      空字符串表示不调整。

    Returns:
        (difficulty: str, success_rate: int)
    """
    if match_score >= 100:
        difficulty = 'Easy'
        success_rate = 65 + min(25, (match_score - 100))
    elif match_score >= 85:
        difficulty = 'Medium'
        success_rate = 40 + (match_score - 85)
    else:
        difficulty = 'Hard'
        success_rate = max(15, 15 + (match_score - 55))

    # 公司规模修正（Fix 2）
    if company_size == '大厂':
        success_rate = max(10, success_rate - 12)
    elif company_size == '中厂':
        success_rate = max(10, success_rate - 3)
    elif company_size == '小厂':
        success_rate = min(90, success_rate + 3)

    return difficulty, success_rate


def classify_jobs_advanced(
    profile: ResumeProfile,
    jobs: List[Dict[str, Any]],
    tier_thresholds: Tuple[int, int] = (100, 85)
) -> Tuple[List[Dict], List[Dict], List[Dict], List[Dict]]:
    """
    增强版批量岗位分类（6维度评分）

    Args:
        profile: 简历解析结果
        jobs: 岗位列表
        tier_thresholds: (tier1_min, tier2_min) 阈值，默认 >=100 为 Tier1，>=85 为 Tier2

    Returns:
        (tier1, tier2, tier3, tier4) 四个层级的岗位列表(按分数降序)
    """
    # 从 profile 提取技能和关键词
    all_skills = []
    for cat in ['programming', 'frameworks', 'tools', 'other']:
        all_skills.extend(profile.skills.get(cat, []))
    if not all_skills:
        all_skills = profile.keywords or []

    resume_keywords = profile.keywords or [
        'AI', 'RAG', 'Agent', 'LLM', '大模型', '后端开发', 'Python', 'Java', '全栈'
    ]

    salary = profile.salary_expectation
    sal_min = salary.get('min', 8) if salary else 8
    sal_max = salary.get('max', 10) if salary else 10

    # 提取用户实际工作年限（Fix 6）
    user_experience_years = float(profile.experience.get('total_years', 0) or 0)

    print(f"\n正在增强分析 {len(jobs)} 个岗位(6维度评分)...")

    tier1_min, tier2_min = tier_thresholds

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
        )

        job_result = {
            **{k: v for k, v in job.items()},  # 保留原始字段
            **result,  # 合并评分结果
        }

        ms = result['match_score']
        if ms >= tier1_min:
            tier1.append(job_result)
        elif ms >= tier2_min:
            tier2.append(job_result)
        elif ms >= 70:
            tier3.append(job_result)
        else:
            tier4.append(job_result)

    # 按分数降序
    for tier in [tier1, tier2, tier3, tier4]:
        tier.sort(key=lambda x: x['match_score'], reverse=True)

    print(f"分析完成！Tier1: {len(tier1)}, Tier2: {len(tier2)}, "
          f"Tier3: {len(tier3)}, Tier4: {len(tier4)}")

    return tier1, tier2, tier3, tier4


# ==================== 适配器 ====================

def tiers_to_classification(
    tier1: List[Dict],
    tier2: List[Dict],
    tier3: List[Dict]
) -> JobClassification:
    """
    将 classify_jobs_advanced 的 4 层输出映射回 JobClassification 三桶格式

    tier1 → qualified (高匹配)
    tier2 → need_optimization (可优化)
    tier3 → cannot_apply (不匹配)
    tier4 丢弃
    """
    def _map_job(job: Dict) -> Dict[str, Any]:
        link = job.get('link', '')
        job_id = job.get('job_id', '')
        if not job_id and link:
            # 从链接中提取唯一 ID
            m = re.search(r'([A-Za-z0-9_-]{7,})(?:\.html|$)', link)
            if m:
                job_id = 'job_' + m.group(1)

        score = job.get('match_score', 50)
        company_size = parse_company_size(
            job.get('公司', job.get('company', '')),
            job.get('规模', job.get('scale', ''))
        )
        difficulty, success_rate = compute_difficulty_success_rate(score, company_size)

        return {
            'job_id': job_id or f"job_{abs(hash(link)):x}"[:12],
            'link': link,
            'company': job.get('公司', job.get('company', '')),
            'position': job.get('职位', job.get('position', '')),
            'city': job.get('城市', job.get('city', '')),
            'salary': job.get('薪资', job.get('salary', '')),
            'experience': job.get('经验', job.get('experience', '')),
            'education': job.get('学历', job.get('education', '')),
            'skills': job.get('技能标签', job.get('skill_tags', '')),
            'skill_tags': job.get('技能标签', job.get('skill_tags', '')),
            'welfare_tags': job.get('福利标签', job.get('welfare_tags', '')),
            'source_file': job.get('source_file', ''),
            'company_info': (job.get('公司信息', job.get('company_info', '')) or '')[:500],
            'scale': job.get('规模', job.get('scale', '')),
            'jd': (job.get('岗位要求和职责', job.get('jd', '')) or '')[:1000],
            'job_detail': (job.get('岗位要求和职责', job.get('job_detail', '')) or '')[:200],
            'company_size': company_size,
            'match_score': score,
            'difficulty': difficulty,
            'success_rate': success_rate,
            'classification_reason': '; '.join(job.get('match_reasons', [])[:4]),
            'missing_items': job.get('missing_skills', [])[:5],
            'optimization_points': job.get('optimization_points', []),
        }

    return JobClassification(
        cannot_apply=[_map_job(j) for j in tier3],
        need_optimization=[_map_job(j) for j in tier2],
        qualified=[_map_job(j) for j in tier1],
    )
