#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""eval 子包自己的模板加载 + prompt 构建。

不 import llm —— 只负责把 .st 模板文件读进来 .format 成最终 prompt 字符串；
要不要真发请求由调用方（gen_test_jobs / evaluate_materials）决定。
模板目录固定在本包 templates/ 下，同步于仓库 prompts/ 的加载机制（load_prompt）。
"""

import os

_TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates')


def load_eval_prompt(name):
    """读取 eval/templates/<name>.st。"""
    path = os.path.join(_TEMPLATES_DIR, '%s.st' % name)
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


def build_gen_jobs_prompt(profile_summary, count, spec=''):
    """给 AI 造岗位的 prompt。spec 是多样的补充说明（空则不额外约束）。"""
    return load_eval_prompt('gen_test_jobs').format(
        count=count,
        resume_summary=profile_summary,
        spec=spec,
    )


def build_recommend_prompt(aggregate_json):
    """给 LLM 综合点评的 prompt（--llm-recommend）。aggregate_json 是 recommend.py
    聚合出的六维结果（已序列化成多行 JSON 字符串）。"""
    return load_eval_prompt('llm_recommend').format(
        data=aggregate_json,
    )