#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""匹配评估的内置 fixture 数据集（简历 + 手工 gold 样本）。

--gold-fixtures 用的是这套密封数据：主评分链路只允许「内置简历 + 对应数据集」，
不接收外部自定义简历（见 evaluate_matcher CLI 校验），避免用用户简历去答另一份
简历出好的题。
"""
from .hand import PROFILE, FIXTURES, build_fixtures

__all__ = ['PROFILE', 'FIXTURES', 'build_fixtures']