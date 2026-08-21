#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""术语三分类的 **LLM 语义分类器**：让模型看「原简历 + 优化简历 + JD」逐词打标签。

为什么要单独放一个文件：`metrics.py` 刻意不 import llm（其余五维的离线纯度不破）。
本模块是唯一 import llm 术语分类点——评估主路径的术语落桶交给这里，metrics 只消费结果。

输入每岗三样：base_text（原简历）、opt_resume（优化后）、jd（该岗要求）。
输出结构化术语列表 + 一段人话分析，落盘到磁盘缓存（键 = 三样拼接的哈希），
跑过一回之后 `--offline` / 再跑直接命中，不重复烧钱（参照 matcher 的 gold_manifest 思路）。
"""

import os
import re
import sys
import json
import hashlib

# 上溯两级拿 scripts/ 根（其内有 eval/__init__.py，故能 import eval.materials.* 与 llm）
_SCRIPTS = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

_BUCKETS = ('retained', 'jd_driven', 'unfounded')
_MODE = 'llm'


def _cache_path(run_dir):
    return os.path.join(run_dir, 'eval', 'term_classify_cache.json')


def _job_key(base_text, opt_resume, jd):
    """缓存键：三样输入拼接的 sha1。任一变化即换新键，规避过期缓存。"""
    blob = '%s\x1f%s\x1f%s' % (base_text, opt_resume, jd)
    return hashlib.sha1(blob.encode('utf-8')).hexdigest()


def load_cache(run_dir):
    """读整份缓存 {key: terms_dict}。缺文件回空 dict。"""
    path = _cache_path(run_dir)
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_cache(run_dir, cache):
    path = _cache_path(run_dir)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


# ==================== prompt ====================

def _build_prompt(base_text, opt_resume, jd):
    from eval.materials.prompts import load_eval_prompt
    prompt = load_eval_prompt('eval_terms')
    return (prompt.replace('RESUME_BASE', base_text)
                  .replace('RESUME_JD', jd)
                  .replace('RESUME_OPT', opt_resume))


def _docarify(raw):
    """把模型返回的任意形态归一成 {term:[…], analysis:str}。

    chat_json 可能给 [term…] / {terms:[…]} 两种，都归一成 {'terms':[…], 'analysis':…}。
    单个 term 可能是 str（没有 bucket/reason）或 dict —— 字符串的兜底成 retained，
    这样模型漏标也不至于把防线拆干净（少报总比乱报安全）。
    """
    if isinstance(raw, dict):
        terms = raw.get('terms') or raw.get('data') or []
        analysis = str(raw.get('analysis') or '')
    elif isinstance(raw, list):
        terms, analysis = raw, ''
    else:
        terms, analysis = [], ''
    cleaned = []
    for t in terms if isinstance(terms, list) else []:
        if isinstance(t, str):
            cleaned.append({'term': t, 'bucket': 'retained', 'reason': ''})
        elif isinstance(t, dict):
            bucket = t.get('bucket')
            if bucket not in _BUCKETS:
                bucket = 'retained'          # 未知/漏标 -> 保守保留
            cleaned.append({'term': str(t.get('term') or '').strip(),
                            'bucket': bucket,
                            'reason': str(t.get('reason') or '').strip(),
                            'context': str(t.get('context') or '').strip()})
    return cleaned, analysis


def _to_terms_dict(terms, analysis):
    """词表 -> 与 rule 版同构的 terms dict（n_retained/n_jd_driven/n_unfounded + 百分比）。

    recommendation/report 共用同一套字段，两种 source 都喂得动。
    """
    def bucket(b):
        return sum(1 for t in terms if t['bucket'] == b)

    n_opt = max(len(terms), 1)
    retained = bucket('retained')
    jd_driven = bucket('jd_driven')
    unfounded = bucket('unfounded')
    unfounded_list = [{'term': t['term'], 'context': t.get('context') or ''}
                      for t in terms if t['bucket'] == 'unfounded' and t['term']]
    new_total = (unfounded + jd_driven) or 1
    return {
        'n_opt': len(terms),
        'n_retained': retained,
        'n_jd_driven': jd_driven,
        'n_unfounded': unfounded,
        'retention_pct': retained / n_opt if len(terms) else 0,
        'jd_driven_pct': jd_driven / n_opt if len(terms) else 0,
        'hallucination_pct': unfounded / n_opt if len(terms) else 0,
        'fabrication_within_new': unfounded / new_total if len(terms) else 0,
        'unfounded': unfounded_list,
        'terms': terms,          # 逐词明细（bucket/reason/context）
        'analysis': analysis,    # 人话总结
        'n_dropped': 0,
        'mode': _MODE,
    }


# ==================== 主入口 ====================

def _load_opt_resume(resume_path):
    """读 resume_#_*.json 的 optimized_resume。"""
    with open(resume_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return (data or {}).get('optimized_resume') or ''


def classify_job(base_text, opt_resume, jd, cfg=None, run_dir=None, use_cache=True):
    """单岗分类。命中缓存直接回；否则 chat_json 一次并落缓存。

    返回 terms dict（见 _to_terms_dict）。失败（LLM 配置缺失/网络/解析）返回
    {'mode':'llm','error':…,'terms':[],…}，由调用方决定放行（报告里标 error）。
    """
    from llm import chat_json, LLMError

    key = _job_key(base_text, opt_resume, jd)
    cache = load_cache(run_dir) if use_cache and run_dir else {}

    # 分类 inputs 本身为空（无优化稿）时没有可分类对象，直接空桶
    if not (opt_resume or '').strip():
        d = _to_terms_dict([], '')
        d['n_opt'] = 0
        return d

    if key in cache:
        hit = cache[key]
        hit.setdefault('mode', _MODE)
        return hit

    prompt = _build_prompt(base_text, opt_resume, jd)
    try:
        raw = chat_json(prompt, stage='eval_terms', run_dir=run_dir, cfg=cfg)
        terms, analysis = _docarify(raw)
        d = _to_terms_dict(terms, analysis)
    except (LLMError, Exception) as exc:                             # noqa: BLE001
        d = _to_terms_dict([], '')
        d['error'] = 'LLM 分类失败：%s' % exc
        return d

    if use_cache and run_dir:
        cache[key] = d
        save_cache(run_dir, cache)
    return d


def classify_all(jobs, base_text, opt_by_index, cfg=None, run_dir=None,
                 offline=False, force=False):
    """批量逐岗分类。返回 {岗号: terms dict}。

    - offline:   只读缓存，不触网；该岗无缓存就给 {'mode':'rule'}（调用方据此走规则兜底）。
    - force:     忽略缓存强跑（联网）。
    任何一岗失败都不致命——给该岗一个空桶 + error 标记，报告照样出。
    """
    out = {}
    cache = load_cache(run_dir) if run_dir else {}
    for i, job in enumerate(jobs, 1):
        opt_resume = opt_by_index.get(i) or ''
        jd = '、'.join(str(v) for v in
                       (job.get('技能标签'), job.get('岗位要求和职责'), job.get('职位'),
                        job.get('公司')) if v)
        key = _job_key(base_text, opt_resume, jd)
        if offline:
            if key in cache:
                d = cache[key]
                d.setdefault('mode', _MODE)
                out[i] = d
            else:
                out[i] = {'mode': 'rule'}        # 无缓存 -> 调用方走规则兜底
            continue
        if not force and key in cache:
            out[i] = cache[key]
            continue
        out[i] = classify_job(base_text, opt_resume, jd, cfg=cfg, run_dir=run_dir)
    return out