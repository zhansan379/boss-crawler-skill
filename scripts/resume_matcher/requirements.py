#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
从岗位要求文本（CSV 的 `岗位要求和职责`，即详情页 detail.jd）抽取权威的
学历/经验/薪资/技能要求。

搜索筛选条件、列表卡片标签都不一定准——真正说了算的是 BOSS 详情页的岗位要求。
本模块用 LLM 把这段文本字段化成权威要求，匹配/投递判定以它为准；JD 缺失或抽取
失败时，评分才回退卡片标签（向后兼容，行为不变）。

产物缓存到 assets/job_requirements.json，按 link 索引，跨运行共享、幂等可续跑：
岗位要求是岗位自身的事实，不绑定某一轮 run_dir，所以不像 deep_results 那样按目录存。

顶层的解释性 import 刻意只在 stdlib：llm.client 顶部会 `from resume_matcher import
llm_usage_path`，若本模块在 resume_matcher 包初始化途中被 import，模块级引入会撞出
循环导入。所有依赖都放进函数内懒加载。
"""

import json
import os
import tempfile
from typing import Any, Dict, List, Optional

# 记账/配置阶段名（assets/llm_config.json 可用 stages.requirements 单独换模型）
STAGE = 'requirements'

# 契约字段：模型输出里的键。学历/经验/薪资是评分 parser 能直接吃的文本，技能是列表。
# 空串/空列表 = 未提及（评分回退卡片），"不限"/"面议" = 明确的放宽（属于信息，不回退）。
_ALLOWED_KEYS = ('学历要求', '经验要求', '薪资范围', '技能要求')

_EMPTY = {'学历要求': '', '经验要求': '', '薪资范围': '', '技能要求': []}


def cache_path() -> str:
    """权威要求缓存文件（assets/job_requirements.json）。"""
    from resume_matcher import OUTPUT_DIR
    return os.path.join(OUTPUT_DIR, 'job_requirements.json')


def get_requirements_prompt(jd_text: str) -> str:
    from resume_matcher.prompts import load_prompt
    return load_prompt('extract_requirements').format(jd=jd_text or '')


def _clean_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ''


def _clean_skills(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(s).strip() for s in value if isinstance(s, str) and s.strip()][:8]
    if isinstance(value, str):
        parts = [p.strip() for p in value.replace('，', ',').replace('、', ',').split(',') if p.strip()]
        return parts[:8]
    return []


def normalize_requirements(data: Any) -> Dict[str, Any]:
    """校验并规整一条模型输出，返回字段化空值起底的 dict。

    形状本就在 prompt 里定死；这里把类型收紧（技能必须列表、其余必须字符串），
    让评分只面对它认识的类型。模型给了残缺/非 dict 时返回全空——评分侧对空串
    的回退分支天然能处理，不需要在这里抛错。
    """
    out: Dict[str, Any] = dict(_EMPTY)
    if not isinstance(data, dict):
        return out
    for key, value in data.items():
        if key not in _ALLOWED_KEYS:
            continue
        if key == '技能要求':
            out[key] = _clean_skills(value)
        else:
            out[key] = _clean_text(value)
    return out


def extract_job_requirements(jd_text: str, *, run_dir: Optional[str] = None,
                             cfg=None) -> Dict[str, Any]:
    """单条 JD 文本的权威要求抽取。

    JD 为空直接返回全空（没得抽）。LLM 失败让异常向上抛——由批量调用方
    （map_concurrent）兜住按条失败，单条失败不影响整批。
    """
    if not (jd_text or '').strip():
        return dict(_EMPTY)
    from llm.client import chat_json
    data = chat_json(get_requirements_prompt(jd_text),
                     stage=STAGE, run_dir=run_dir, cfg=cfg)
    return normalize_requirements(data)


def load_all() -> Dict[str, Dict[str, Any]]:
    """读缓存 {link: requirements}。文件不存在/损坏返回空 dict。"""
    from resume_matcher.config import ENCODING
    path = cache_path()
    if not os.path.exists(path):
        return {}
    try:
        # ENCODING(utf-8-sig) 会带 BOM，读取必须用同一编码把 BOM 剥掉，
        # 否则 json.loads 看到 ﻿ 直接解析失败、静默返回空。
        with open(path, 'r', encoding=ENCODING) as f:
            data = json.load(f)
    except (ValueError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): v for k, v in data.items() if isinstance(v, dict)}


def save_all(mapping: Dict[str, Dict[str, Any]]) -> None:
    """写缓存，原子替换（先写临时文件再 rename，避免读到半个文件）。"""
    from resume_matcher.config import ENCODING
    path = cache_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding=ENCODING) as f:
            json.dump(mapping, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def enrich(jobs: List[Dict[str, Any]],
           cached: Optional[Dict[str, Dict[str, Any]]] = None,
           *, quiet: bool = False) -> int:
    """把缓存的权威要求挂到各 job 的 `_jd_req`，返回命中缓存的岗位数。

    在加载岗位 CSV 之后、打分之前调用（run_matcher 的 quick / deep_prepare 都走这里）。
    没有该 link 缓存或缓存损坏的岗位保持原样，打分照旧用卡片字段——向后兼容。
    """
    mapping = load_all() if cached is None else cached
    hit = 0
    for job in jobs:
        link = (job.get('link') or '').strip()
        if link and mapping.get(link):
            job['_jd_req'] = mapping[link]
            hit += 1
    if hit and not quiet:
        print(f'  [要求] 已用岗位要求(权威抽取)覆盖 {hit} 个岗位的学历/经验/薪资/技能判定'
              f'（未覆盖的回退卡片标签）')
    return hit