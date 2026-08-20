#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""gold（金标准）数据模型 + 三来源加载。

gold 是「真正该不该投」的独立 oracle，**绝不由 run decide_application_category 得到**
（否则评估循环无意义）。每样本 = { link, job, gold:{category, score, matched_skills,
missing_skills, reason, source} }。gold.score 恒为 0-100，与 deep/blended 同一刻度。

三来源：
  A. 手工 fixture（HAND_FIXTURES，代码内、可离线断言）—— 覆盖回归角点。
  B. AI 造岗内嵌 gold（--gold-ai N，触网）—— 经 gen_gold.st，跨三档、刻意构造硬门槛。
  C. LLM judge（--judge-gold，触网）—— 真实岗位复用 deep_analyze.build_resume_info /
     build_job_requirements + 新 judge_gold.st（措辞为 oracle，不给规则/深度分，防回声）。

技能口径：matched/missing 两侧都经 scoring._normalize_skill + SKILL_SYNONYMS（别名不算错）。
gold.missing 缺失时优先取 job['技能标签'] 分割（技能-only 字段）；仅当标签为空才回退
verify_no_fabrication._baseline_keys(jd 文本)（启发式，报告标注）—— 两套归一
（_normalize_skill / _baseline_keys）不可互换（后者含非技能词）。

不 import llm，不做网络调用在顶层：chat 依赖函数内 lazy import，offline 守卫（materials.stubs）
才能把调用点换成必抛。
"""

import os
import sys
import json
import re

# scripts/eval/matcher/ 上溯两级 → scripts/ 根
_SCRIPTS = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
for _p in (_SCRIPTS, os.path.join(_SCRIPTS, 'verify'),
           os.path.join(_SCRIPTS, 'resume_matcher'),
           os.path.join(_SCRIPTS, 'stages')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_JOB_FIELDS = ('link', '公司', '职位', '薪资', '经验', '学历', '技能标签', '岗位要求和职责')

_CATEGORIES = ('qualified', 'need_optimization', 'cannot_apply')


# ==================== 手工 fixture（回归角点） ====================

# 一份精简但真实的简历 profile（各 fixture 内联引用同一份）。
# 技能类目对齐 classify_jobs_advanced 的 [programming/frameworks/tools/other]。
_FIXTURE_PROFILE = {
    'basic_info': {'name': '张三', 'status': '离职-随时到岗', 'city': '北京',
                   'availability': {'can_start': '随时', 'days_per_week': '5天'}},
    'education': {'school': '某大学', 'degree': '本科', 'major': '计算机'},
    'experience': {'total_years': 3,
                   'companies': [{'name': '某科技', 'position': '后端', 'duration': '2023-2025',
                                  'highlights': ['订单查询 P99 从 800ms 降到 120ms']}]},
    'skills': {'programming': ['Python'], 'frameworks': ['FastAPI', 'Django'],
               'tools': ['Redis', 'MySQL', 'k8s', 'es'], 'other': ['RAG']},
    'projects': [{'name': '智能问答系统', 'tech_stack': 'Python/FAISS',
                  'highlights': ['召回率升 30%']}],
    'keywords': ['Python', 'FastAPI', 'RAG', '后端开发', 'AI'],
    'salary_expectation': {'min': 25, 'max': 35},
    'awards': [], 'publications': [],
}


def _fixture_sample(link, company, position, salary, exp, degree, tags, jd, gold):
    """把一条 fixture 拼成样本 dict（job 走 _JOB_FIELDS 白名单 + gold 内联）。"""
    job = {'link': link, '公司': company, '职位': position, '薪资': salary, '经验': exp,
           '学历': degree, '技能标签': tags, '岗位要求和职责': jd}
    gold = dict(gold)
    gold.setdefault('source', 'hand')
    return {'link': link, 'job': job, 'gold': gold, 'profile': dict(_FIXTURE_PROFILE)}


# 覆盖角点：
#   A1-A3 三档边界：技能命中4 / 经验差3（硬门槛）/ 薪资差8K（硬门槛 > 8 才是硬性）。
#   A4 面议：薪资不可解析 → 不得判 qualified（与 scoring 语义一致）。
#   A5 别名：k8s⇄kubernetes、es⇄elasticsearch —— 命中要靠别名归一。
#   A6 学历硬门槛不达标（JD 硕士 vs 简历本科）。
#   A7 空技能/缺 JD：只能依赖 reason 与 missing 推导，不得崩。
#   A8 简历技能完全没进 JD（漏投倾向：gold=need_optimization，规则若凭别名误判 qualified 才是错）。
HAND_FIXTURES = [
    # A1 qualified：技能 4 项命中 + 薪资匹配 + 学历经验满足
    _fixture_sample(
        'https://jobs.eval.local/match/1', '借呗科技', 'Python后端工程师', '25-35K', '1-3年', '本科',
        'Python,FastAPI,Redis,MySQL,Django',
        '负责 Python 后端与 FastAPI 服务开发，用 Redis 做缓存、MySQL 做存储。',
        {'category': 'qualified', 'score': 82,
         'matched_skills': ['Python', 'FastAPI', 'Redis', 'MySQL'],
         'missing_skills': [], 'reason': '核心技能基本命中，薪资在期望区间，学历经验满足'}),
    # A2 need_optimization：经验差 2 年（<3 未触发硬门槛），技能差 1 项
    _fixture_sample(
        'https://jobs.eval.local/match/2', '元象网络', '资深Python开发', '20-30K', '3-5年', '本科',
        'Python,Django,Redis',
        '要求 3-5 年 Python 开发经验，负责核心业务服务。',
        {'category': 'need_optimization', 'score': 66,
         'matched_skills': ['Python', 'Django', 'Redis'],
         'missing_skills': ['高并发架构'],
         'reason': '技能命中但经验差 2 年，优化简历后可投'}),
    # A3 cannot_apply：薪资差 >8K（10-15K vs 期望 25-35），无硬学历/经验问题
    _fixture_sample(
        'https://jobs.eval.local/match/3', '小微创业', 'Python开发', '10-15K', '1-3年', '本科',
        'Python,Flask',
        '负责内部工具开发，Flask 为主。',
        {'category': 'cannot_apply', 'score': 38,
         'matched_skills': ['Python'], 'missing_skills': ['Flask'],
         'reason': '薪资差距过大（>8K），命中硬门槛'}),
    # A4 面议：薪资不可解析 → 不得判 qualified（薪资未被验证）
    _fixture_sample(
        'https://jobs.eval.local/match/4', '神秘公司', 'AI工程师', '面议', '3-5年', '本科',
        'Python,RAG,LLM',
        '负责 Agent 与大模型应用开发，薪资面议。',
        {'category': 'need_optimization', 'score': 60,
         'matched_skills': ['Python', 'RAG', 'LLM'],
         'missing_skills': [],
         'reason': '技能契合但薪资面议无法验证，需确认后投'}),
    # A5 别名：k8s ⇄ Kubernetes、es ⇄ Elasticsearch，命中靠别名归一
    _fixture_sample(
        'https://jobs.eval.local/match/5', '云算科技', '平台工程师', '26-36K', '2-4年', '本科',
        'Kubernetes,Elasticsearch,Go',
        '用 k8s 部署微服务，用 ES 做检索平台。',
        {'category': 'need_optimization', 'score': 72,
         'matched_skills': ['Kubernetes', 'Elasticsearch'],
         'missing_skills': ['Go'],
         'reason': 'k8s/es 别名命中但仅 2 项达标技能，缺 Golang'}),
    # A5 别名：简历写 k8s/es（别名形），JD 用 Kubernetes/Elasticsearch（规范形）。命中靠
# _normalize_skill 折叠；但命中仅 2 项（< 达标 4）→ should be need_optimization，不是 qualified。
# A6 学历硬门槛：JD 硕士，简历本科 → cannot_apply
    _fixture_sample(
        'https://jobs.eval.local/match/6', '前沿研究院', '算法工程师', '30-40K', '3-5年', '硕士',
        'Python,机器学习,大模型',
        '深入 ML 与大模型，要求硕士及以上学历。',
        {'category': 'cannot_apply', 'score': 42,
         'matched_skills': ['Python'], 'missing_skills': ['机器学习', '大模型'],
         'reason': '学历硬性不达标（JD 硕士 vs 简历本科）'}),
    # A7 缺 JD 全文/技能标签空：不崩，missing 走 reason 与规则兜底
    _fixture_sample(
        'https://jobs.eval.local/match/7', '未知岗', '开发岗', '25-35K', '1-3年', '本科',
        '', '',
        {'category': 'need_optimization', 'score': 55,
         'matched_skills': [], 'missing_skills': [],
         'reason': 'JD 信息不足，无法确认技能与要求'}),
    # A8 简历技能没进 JD：gold=need_optimization（不该凭猜 qualified）
    _fixture_sample(
        'https://jobs.eval.local/match/8', '异域厂', '硬件工程师', '25-35K', '3-5年', '本科',
        '嵌入式,C语言,FPGA',
        '负责嵌入式与 FPGA 开发。',
        {'category': 'need_optimization', 'score': 48,
         'matched_skills': [], 'missing_skills': ['嵌入式', 'C语言', 'FPGA'],
         'reason': '技术栈完全不在简历内，但无硬门槛，方向跨得太远'}),
]


def load_hand_gold():
    """A 来源：手工 fixture → 样本列表。来源恒为 'hand'，可离线断言。"""
    return [dict(f) for f in HAND_FIXTURES]


# ==================== 技能归一 ====================

_SKILL_SPLIT = re.compile(r'[,，、;；|\s]+')


def normalize_skill(name):
    from resume_matcher.scoring import _normalize_skill
    return _normalize_skill(name)


def _profile_skills(profile):
    """扁平化简历技能（对齐 classify_jobs_advanced 的取法 + 空键防 None 传播）。"""
    out = []
    for cat in ('programming', 'frameworks', 'tools', 'other'):
        out.extend((profile.get('skills') or {}).get(cat) or [])
    if not out:
        out = profile.get('keywords') or []
    return [str(s).strip() for s in out if str(s).strip()]


def _jobs_tags(job):
    tags = (job.get('技能标签') or '').strip()
    if tags:
        return [t for t in _SKILL_SPLIT.split(tags) if t]
    return []


def _fold(skills):
    return {normalize_skill(s) for s in skills}


# ==================== gold 清洗与缺失回退 ====================

def _clean_gold(raw, job, source):
    """把任意 dict 规整成 gold。category/score 都要能落到枚举与 0-100。"""
    from resume_matcher.deep_analysis import _normalize_category
    if not isinstance(raw, dict):
        raw = {}
    category = _normalize_category(raw.get('category') or '') or 'need_optimization'
    if category not in _CATEGORIES:
        category = 'need_optimization'
    try:
        score = int(float(raw.get('score', 50)))
    except (TypeError, ValueError):
        score = 50
    score = max(0, min(100, score))
    matched = [str(v) for v in (raw.get('matched_skills') or []) if v]
    missing = [str(v) for v in (raw.get('missing_skills') or []) if v]
    # 缺失回退：missing 优先取 技能标签 分割；标签为空才回退 _baseline_keys(jd)（启发式）。
    if not missing:
        missing = _jobs_tags(job)
    if not missing:
        jd = '、'.join(str(job.get(k)) for k in ('岗位要求和职责', '职位', '公司') if job.get(k))
        if jd.strip():
            from verify_no_fabrication import _baseline_keys
            missing = list(_baseline_keys(jd))
    gold = {'category': category, 'score': score,
            'matched_skills': [str(s) for s in matched],
            'missing_skills': [str(s) for s in missing],
            'reason': str(raw.get('reason') or '').strip(),
            'source': source}
    return gold


def _stack_gold(job, gold):
    """job（白名单）+ gold → 样本 dict（含 link）。"""
    return {'link': job.get('link', ''), 'job': dict(job), 'gold': gold}


# ==================== B 来源：AI 造岗内嵌 gold ====================

def build_gold_ai(count, profile, cfg, run_dir, spec=''):
    """B 来源：调模型造 count 个跨三档岗位，每岗内嵌 gold（source='ai'）。"""

    def _summary():
        from stages.deep_analyze import build_resume_info
        return build_resume_info(profile)

    from eval.materials.gen_test_jobs import _clean_job
    from llm import chat_json, LLMError

    from eval.matcher import matcher_prompts as _mp
    prompt = _mp.build_gen_gold_prompt(_summary(), count, spec)
    data = chat_json(prompt, stage='gen_gold', run_dir=run_dir, cfg=cfg)
    if isinstance(data, dict):
        data = data.get('jobs') or data.get('data') or []
    if not isinstance(data, list):
        raise LLMError('AI 造岗（含 gold）没有返回岗位数组')
    samples = []
    for i, raw in enumerate(data, 1):
        job = _clean_job(raw, i)
        gold = _clean_gold(raw.get('gold') if isinstance(raw, dict) else None, job, 'ai')
        samples.append(_stack_gold(job, gold))
    if not samples:
        raise LLMError('AI 造岗（含 gold）没有解析出任何岗位')
    return samples


# ==================== C 来源：LLM judge 真实岗位 ====================

def judge_gold_jobs(profile, jobs, cfg, run_dir):
    """C 来源：对真实岗位做独立 LLM gold 评审（source='judge'）。

    missing 取深度判定的 missing_items；matched 用「简历技能 ∩ 岗位技能标签」推导
    （深度侧不产 matched，这是固有不对称）。每条逐岗请求，失败计入 errors 不整批崩。
    """
    from stages.deep_analyze import build_resume_info, build_job_requirements, normalize_result
    from resume_matcher.deep_analysis import _normalize_category
    from llm import chat_json, LLMError

    from eval.matcher import matcher_prompts as _mp
    resume_summary = build_resume_info(profile)
    profile_skills = _fold(_profile_skills(profile))
    samples, errors = [], []
    for i, job in enumerate(jobs, 1):
        try:
            candidate = {'rank': i, 'job': job}
            prompt = _mp.build_judge_gold_prompt(resume_summary, build_job_requirements(candidate))
            data = chat_json(prompt, stage='judge_gold', run_dir=run_dir, cfg=cfg)
            record = normalize_result(data, candidate)
            category = _normalize_category(record.get('category') or '') or 'need_optimization'
            matched = sorted(profile_skills & _fold(_jobs_tags(job)))
            gold = {'category': category, 'score': record.get('score', 50),
                    'matched_skills': [str(s) for s in matched],
                    'missing_skills': record.get('missing_items') or [],
                    'reason': record.get('reason') or '', 'source': 'judge'}
            samples.append(_stack_gold(job, gold))
        except (LLMError, Exception) as exc:                       # noqa: BLE001
            errors.append({'index': i, 'link': job.get('link'), 'error': str(exc)})
    return samples, errors


# ==================== gold 持久化（offline 重放） ====================

def write_manifest(path, samples):
    """AI/judge 在线跑完的 gold 落盘，供 --offline 确定性重放（离线绝不重生）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {'_run': 'gold-manifest', 'samples': samples}
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path


def load_manifest(path):
    """回放 gold 清单。samples 里已含 gold.source，直接返回。"""
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return data.get('samples') or []