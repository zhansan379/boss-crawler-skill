#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
工具函数：筛选处理、文件 I/O、时间预估、参数展开
"""

import math
import os

import chardet

from .config import (
    FILTER_MAPS,
    FILTER_LABELS,
    FILTER_PARAM_NAMES,
    PER_PAGE,
    PER_PAGE_TIME,
    PER_DETAIL_TIME,
)


# ==================== 给人照着粘的命令 ====================

# scripts/ 的绝对路径。utils.py 在 scripts/boss_crawler/ 里，往上两级就是它。
_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 爬虫入口迁到 stages/ 子目录了（和 pipeline.py 的 _SCRIPT_SUBDIR 同源）；
# 别再指 scripts/ 根，那里没有 boss_post_interactive.py。
_ENTRY = os.path.join(_SCRIPTS_DIR, 'stages', 'boss_post_interactive.py')


def _quote(text):
    return '"%s"' % text if ' ' in text else text


def _render_py_cmd(full, args):
    """把 `python <脚本路径> <参数…>` 拼出来，路径按**当前工作目录**算。

    别在提示里写死路径。写死裸文件名（`python boss_post_interactive.py`）在仓库根目录
    粘就是 Errno 2 —— 脚本在 scripts/ 里；反过来写死 `scripts/…`，在 scripts/ 目录里
    粘又是错的。而这些提示恰恰是给「已经卡住了」的人看的，让人再猜一次路径最糟。

    流水线是从仓库根目录启动子进程的，所以未登录那条提示实际总在根目录下被读到 ——
    第一版写的裸文件名在那儿 100% 粘不动。

    带空格的路径和参数都会加引号（Windows 上运行目录带空格很常见）。
    """
    try:
        path = os.path.relpath(full, os.getcwd())
    except ValueError:          # Windows 上 cwd 与脚本不同盘时 relpath 直接抛
        path = full
    if len(path) > len(full):
        path = full             # cwd 在别处时 ..\..\.. 反而更长更难读，不如给绝对路径
    return ' '.join(['python', _quote(path)] + [_quote(str(a)) for a in args])


def script_cmd(script, *args):
    """拼一条 `python <scripts/里的脚本> <参数…>`，路径按**当前工作目录**算。"""
    return _render_py_cmd(os.path.join(_SCRIPTS_DIR, script), args)


def entry_cmd(*args):
    """爬虫入口（boss_post_interactive.py）的 script_cmd 快捷方式。"""
    return _render_py_cmd(_ENTRY, args)


# ==================== 参数展开 ====================

def _expand_arg(value):
    """将逗号分隔的值列表展开为 flat list"""
    if value is None:
        return []
    result = []
    for item in value:
        for part in item.split(','):
            part = part.strip()
            if part:
                result.append(part)
    return result


# ==================== 筛选处理 ====================

def resolve_filter_values(raw_values, filter_key):
    """
    将用户输入的筛选值（支持代码或标签）解析为标准代码列表。
    跳过 "不限"，对未知值给出警告。

    Args:
        raw_values: 用户输入列表，如 ['3-5年', '5-10年']
        filter_key: FILTER_MAPS 中的键，如 'experience'

    Returns:
        代码列表，如 ['105', '106']
    """
    if not raw_values:
        return []

    label_map = FILTER_MAPS.get(filter_key, {})
    result = []
    for val in raw_values:
        if val == '不限':
            continue
        code = label_map.get(val)
        if code:
            if code not in result:
                result.append(code)
        else:
            print(f"[警告] 未知的{filter_key}值: {val}")
    return result


def build_filter_query_string(filter_dict):
    """
    构建 URL 筛选参数字符串。

    Args:
        filter_dict: {'experience': ['105','106'], 'degree': ['203']}

    Returns:
        '&experience=105,106&degree=203' 或 ''
    """
    parts = []
    # 固定顺序以保证可预测的输出
    for key in ['jobType', 'salary', 'experience', 'degree', 'scale']:
        values = filter_dict.get(key, [])
        if values:
            parts.append(f'{key}={",".join(values)}')
    if parts:
        return '&' + '&'.join(parts)
    return ''


def format_filter_display(filter_dict):
    """
    将筛选参数字典格式化为人类可读的展示文本。

    Args:
        filter_dict: {'experience': ['105','106'], 'degree': ['203']}

    Returns:
        '经验要求: 3-5年,5-10年; 学历要求: 本科' 或 '不限'
    """
    displays = []
    for key in ['jobType', 'salary', 'experience', 'degree', 'scale']:
        values = filter_dict.get(key, [])
        if values:
            label_map = FILTER_LABELS.get(key, {})
            labels = [label_map.get(v, v) for v in values]
            name = FILTER_PARAM_NAMES.get(key, key)
            displays.append(f'{name}: {",".join(labels)}')
    return '; '.join(displays) if displays else '不限'


# ==================== 文件 I/O ====================

def detect_encoding(file_path):
    """检测文件编码"""
    with open(file_path, 'rb') as f:
        raw_data = f.read()
        result = chardet.detect(raw_data)
        return result['encoding']


def ensure_output_dir(path):
    """确保输出目录存在"""
    dir_path = os.path.dirname(path)
    if dir_path and not os.path.exists(dir_path):
        os.makedirs(dir_path)


# ==================== 菜单辅助 ====================

def parse_multi_selection(input_str, max_count):
    """解析多选输入（如 1,3-5,all）为索引列表"""
    input_str = input_str.strip().lower()

    if input_str == 'all':
        return list(range(1, max_count + 1))

    indices = []

    parts = input_str.split(',')
    for part in parts:
        part = part.strip()
        if '-' in part:
            try:
                start, end = part.split('-')
                start = int(start.strip())
                end = int(end.strip())
                for i in range(start, end + 1):
                    if 1 <= i <= max_count:
                        indices.append(i)
            except ValueError:
                pass
        else:
            try:
                i = int(part)
                if 1 <= i <= max_count:
                    indices.append(i)
            except ValueError:
                pass

    indices = sorted(set(indices))
    return indices


# ==================== 时间预估 ====================

def estimate_time(positions, cities, count_limit, with_detail):
    """预估爬取时间"""
    total_positions = len(positions)
    total_cities = len(cities)

    if count_limit:
        total_items = count_limit * total_positions * total_cities
    else:
        total_items = 300 * total_positions * total_cities

    list_pages = math.ceil(total_items / PER_PAGE)
    list_time = list_pages * PER_PAGE_TIME

    if with_detail:
        detail_time = total_items * PER_DETAIL_TIME
    else:
        detail_time = 0

    total_time = (list_time + detail_time) / 60

    return total_items, total_time
