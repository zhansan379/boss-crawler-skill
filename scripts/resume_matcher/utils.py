#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
工具函数：打印、目录、学历/经验/薪资解析
"""

import os
import re
from typing import Dict

from .config import OUTPUT_DIR


def print_header(title: str):
    """打印标题头"""
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)


def print_section(title: str):
    """打印小节标题"""
    print(f"\n【{title}】")
    print("-" * 50)


def ensure_output_dir():
    """确保输出目录存在"""
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)


def parse_education_level(edu_str: str) -> int:
    """解析学历等级"""
    edu_str = edu_str.lower() if edu_str else ""
    if '博士' in edu_str:
        return 4
    elif '硕士' in edu_str:
        return 3
    elif '本科' in edu_str or '学士' in edu_str:
        return 2
    elif '大专' in edu_str or '专科' in edu_str:
        return 1
    else:
        return 0


def parse_experience_years(exp_str: str) -> int:
    """解析经验年限"""
    if not exp_str:
        return 0

    # 匹配 "X年" 或 "X-Y年"
    match = re.search(r'(\d+)-?(\d+)?年', exp_str)
    if match:
        if match.group(2):
            return int(match.group(2))  # 取上限
        return int(match.group(1))

    # 匹配数字
    match = re.search(r'(\d+)', exp_str)
    if match:
        return int(match.group(1))

    return 0


def parse_salary_range(salary_str: str) -> Dict[str, int]:
    """解析薪资范围"""
    if not salary_str:
        return {'min': 0, 'max': 0}

    salary_str = salary_str.replace('k', 'K')
    match = re.search(r'(\d+)-(\d+)K', salary_str)
    if match:
        return {'min': int(match.group(1)), 'max': int(match.group(2))}

    match = re.search(r'(\d+)K', salary_str)
    if match:
        return {'min': int(match.group(1)), 'max': int(match.group(1))}

    return {'min': 0, 'max': 0}


def parse_company_size(company_info: str, scale_info: str) -> str:
    """解析公司规模"""
    known_big_companies = [
        '字节跳动', '阿里巴巴', '腾讯', '百度', '美团', '京东', '小米', '华为',
        '网易', '滴滴', '拼多多', '快手', '爱奇艺', '哔哩哔哩', '蚂蚁集团',
        '微软', '谷歌', '苹果', '亚马逊', 'Facebook', 'Meta'
    ]

    for big in known_big_companies:
        if big in company_info:
            return '大厂'

    if scale_info:
        if any(x in scale_info for x in ['10000', '1000-9999', '5000', '万人']):
            return '大厂'
        elif any(x in scale_info for x in ['500-999', '100-499', '1000人']):
            return '中厂'
        else:
            return '小厂'

    return '中厂'


def edu_level_to_str(level: int) -> str:
    """学历等级转字符串"""
    return {4: '博士', 3: '硕士', 2: '本科', 1: '大专', 0: '不限'}.get(level, '不限')
