#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""三种造岗位路径：AI 造多样岗位 / 已有 CSV / 复用现有 run 的 state。

输出统一为「与 gen_materials.load_jobs 消费形状一致」的 job dict 数组，字段白名单：
link, 公司, 职位, 薪资, 经验, 学历, 技能标签, 岗位要求和职责 —— 正好是
get_greeting_prompt 与 gen_resume 读的那几个。多余字段（如 AI 顺手塞的语气词）
会被丢弃，免得污染 jd_keys 的基线判断。

AI 造岗位用 chat_json；CSV 用 csv.DictReader，不拉 DrissionPage，不触爬虫。
"""

import os
import csv
import json
import hashlib
import sys

_SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

_JOB_FIELDS = ('link', '公司', '职位', '薪资', '经验', '学历', '技能标签', '岗位要求和职责')


# ==================== AI 造岗位 ====================

def _clean_job(raw, idx):
    """把模型返回的任意 dict 归一成消费形状。只留白名单字段，缺失的给保守默认。"""
    if not isinstance(raw, dict):
        raw = {}
    job = {k: ('' if raw.get(k) is None else str(raw[k]).strip()) for k in _JOB_FIELDS}
    if not job['公司']:
        job['公司'] = '示例科技'
    if not job['职位']:
        job['职位'] = 'AI工程师'
    if not job['link']:
        # 无 link 时用内容哈希造一个稳定 id，保证按 link 去重/对齐仍有效
        job['link'] = 'https://jobs.eval.local/%s' % hashlib.md5(
            ('%s|%s|%d' % (job['公司'], job['职位'], idx)).encode()).hexdigest()[:12]
    return job


def _jobs_from_ai_response(data):
    """chat_json 返回可能是 [job…] 或 {jobs:[…]} / {data:[…]}，归一成列表。"""
    if isinstance(data, dict):
        data = data.get('jobs') or data.get('data') or []
    if not isinstance(data, list):
        return []
    return [_clean_job(j, i) for i, j in enumerate(data, 1) if isinstance(j, dict) or j]


def build_jobs_ai(profile_summary, count, cfg, run_dir, spec=''):
    """调模型造 count 个多样岗位。返回 job dict 数组（可能因解析失败少于 count）。"""
    from llm import chat_json, LLMError
    from eval.prompts import build_gen_jobs_prompt

    prompt = build_gen_jobs_prompt(profile_summary, count, spec)
    data = chat_json(prompt, stage='gen_test_jobs', run_dir=run_dir, cfg=cfg)
    jobs = _jobs_from_ai_response(data)
    if not jobs:
        raise LLMError('AI 造岗位没有返回可用的岗位数组')
    return jobs


# ==================== CSV 加载 ====================

_INVALID_VALUES = ('是', 'true', '1', '已失效')


def _is_invalid(row):
    """已失效 列是三态：空/未采集 视为在招，'是'/'true'/'1' 才是失效。"""
    v = str(row.get('已失效') or '').strip().lower()
    return v in _INVALID_VALUES


def load_jobs_csv(csv_path):
    """读岗位 CSV（中文列），筛掉已失效、按 link 去重（保留信息最全行）。

    不复用 data_loader 因为它会打进 OUTPUT_DIR 的扫描逻辑；这里只读单文件。
    """
    if not os.path.exists(csv_path):
        raise FileNotFoundError(csv_path)
    rows = []
    with open(csv_path, 'r', encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            if _is_invalid(row):
                continue
            rows.append(row)
    # 按 link 去重，同 link 保留 JD 更全的那行
    seen, out = {}, []
    for row in rows:
        link = (row.get('link') or '').strip()
        if not link:
            out.append(row)             # 无 link 无法去重，照收
            continue
        prev = seen.get(link)
        jd = (row.get('岗位要求和职责') or '').strip()
        if prev is None or (jd and not (prev.get('_jd') or '')):
            seen[link] = dict(row)
            seen[link]['_jd'] = jd
    pooled = list(seen.values()) if seen else []
    for row in out:
        pooled.append(row)
    jobs = []
    for i, row in enumerate(pooled, 1):
        row.pop('_jd', None)
        jobs.append(_clean_job(row, i))
    return jobs


# ==================== 复用现有 run 的 state ====================

def load_jobs_existing(run_dir):
    """直接读既有 run_dir 的 state/qualified_jobs.json（路径同 gen_materials.load_jobs）。"""
    from resume_matcher import qualified_jobs_path
    path = qualified_jobs_path(run_dir)
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    if isinstance(data, dict):
        data = data.get('jobs') or data.get('data') or []
    return data or []