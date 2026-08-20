#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""matcher 子包自己的模板加载 + prompt 构建（镜像 materials/prompts.py）。

不 import llm —— 只把本包 templates/*.st 读进来 .format 成最终 prompt 字符串；
要不要真发请求由调用方（matcher_gold / evaluate_matcher）决定。
"""

import os

_TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates')


def load_matcher_prompt(name):
    """读取 eval/matcher/templates/<name>.st。"""
    path = os.path.join(_TEMPLATES_DIR, '%s.st' % name)
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


def build_gen_gold_prompt(resume_summary, count, spec=''):
    """AI 造岗 + 内嵌 gold 的 prompt。spec 是多样性的补充说明（空则不额外约束）。"""
    return load_matcher_prompt('gen_gold').format(
        count=count, resume_summary=resume_summary, spec=spec)


def build_judge_gold_prompt(resume_summary, job_requirements):
    """LLM 判分真实岗位的 prompt（oracle 措辞）。"""
    return load_matcher_prompt('judge_gold').format(
        resume_summary=resume_summary, job_requirements=job_requirements)


def build_recommend_prompt(aggregate_json):
    """规则建议之外调模型做综合点评的 prompt（--llm-recommend）。"""
    return load_matcher_prompt('matcher_recommend').format(data=aggregate_json)