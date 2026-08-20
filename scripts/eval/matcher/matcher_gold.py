#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""gold（金标准）数据模型 + 三来源加载。

gold 是「真正该不该投」的独立 oracle，**绝不由 run decide_application_category 得到**
（否则评估循环无意义）。每样本 = { link, job, gold:{category, score, matched_skills,
missing_skills, reason, source} }。gold.score 恒为 0-100，与 deep/blended 同一刻度。

三来源：
  A. 手工 fixture（fixtures/hand.py，代码内、可离线断言）—— 覆盖回归角点，与内置简历密封成对。
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

_CATEGORIES = ('qualified', 'need_optimization', 'cannot_apply')


# ==================== A 来源：手工 fixture 数据集 ====================

from eval.matcher.fixtures import build_fixtures as _build_fixtures
from eval.matcher.fixtures import PROFILE as _FIXTURE_PROFILE


def load_hand_gold():
    """A 来源：内置手工 fixture 数据集 → 样本列表（来源恒 'hand'，可离线断言）。

    数据集（简历 + 岗位 + gold）密封在 fixtures/，评分只允许用其内联简历，
    禁止外部自定义（见 evaluate_matcher CLI 校验）。
    """
    return _build_fixtures()


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