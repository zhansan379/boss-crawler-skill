#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
数据加载：岗位/城市 JSON 数据、CSV 去重、在线数据更新
"""

import csv
import json
import os
import re
import time

from DrissionPage import WebPage

from .config import ENCODING, ASSETS_DIR, co
from .utils import detect_encoding, ensure_output_dir


# ==================== JSON 数据加载 ====================

# update_json_data() 会开浏览器，一个进程里最多自动触发一次：
# 首用（文件根本没下载过）值得自动补，但如果补完还是读不出来，
# 那是网络或接口的问题，重试只会再开一次浏览器。
_auto_init_done = False


def _load_json_asset(file_name, _retry=True):
    """读取 assets 下的 JSON 数据文件。

    缺失或损坏时自动跑一次首用初始化（update_json_data）再读一遍。
    成功返回 dict，彻底失败返回 None —— 空结构长什么样由调用方决定。
    """
    global _auto_init_done

    file_path = os.path.join(ASSETS_DIR, file_name)
    try:
        encoding = detect_encoding(file_path)
        with open(file_path, 'r', encoding=encoding) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError) as e:
        reason = type(e).__name__
        if not _retry or _auto_init_done:
            print(f"[错误] {file_path} 读取失败（{reason}），请手动运行 --update-data 后重试")
            return None

        _auto_init_done = True
        print(f"[首用初始化] {file_name} 缺失或损坏（{reason}），自动获取一次...")
        update_json_data()
        return _load_json_asset(file_name, _retry=False)


def load_position_data():
    """加载岗位分类数据"""
    data = _load_json_asset('post_data.json')
    if data is None:
        return {}

    position_data = {}
    for category in data.get('zpData', {}).get('position', []):
        cat_name = category['name'].replace('/', '_')
        sub_categories = []
        for sub_cat in category.get('subLevelModelList', []):
            sub_name = sub_cat['name'].replace('/', '_')
            positions = []
            for pos in sub_cat.get('subLevelModelList', []):
                pos_name = pos['name'].replace('/', '_')
                positions.append({
                    'name': pos_name,
                    'code': pos['code'],
                    'path': os.path.join(ASSETS_DIR, 'post_data', cat_name, sub_name, pos_name)
                })
            sub_categories.append({
                'name': sub_name,
                'positions': positions
            })
        position_data[cat_name] = sub_categories

    return position_data


def load_city_data():
    """加载城市数据"""
    data = _load_json_asset('weizhi.json')
    if data is None:
        return {'hot': [], 'other': []}

    zpData = data.get('zpData', {})
    hot_cities = zpData.get('hotCitySites', [])

    all_cities = []
    for group in zpData.get('siteGroup', []):
        all_cities.extend(group.get('cityList', []))

    hot_codes = {c['code'] for c in hot_cities}
    other_cities = [c for c in all_cities if c['code'] not in hot_codes]

    return {
        'hot': hot_cities,
        'other': other_cities
    }


def update_json_data():
    """更新岗位和城市JSON数据"""
    urls = {
        'post_data.json': 'https://www.zhipin.com/wapi/zpCommon/data/getCityShowPosition',
        'weizhi.json': 'https://www.zhipin.com/wapi/zpgeek/common/data/city/site.json'
    }

    print("\n正在更新数据...")
    dp = WebPage(chromium_options=co)

    for file_name, url in urls.items():
        try:
            print(f"  获取 {file_name}...")
            dp.get(url)
            time.sleep(2)

            page_text = dp.html
            if page_text:
                if '<pre' in page_text:
                    match = re.search(r'<pre[^>]*>(.*?)</pre>', page_text, re.DOTALL)
                    if match:
                        page_text = match.group(1)

                data = json.loads(page_text)
                save_path = os.path.join(ASSETS_DIR, file_name)
                with open(save_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                print(f"  [OK] {save_path} 更新成功")
            else:
                print(f"  [FAIL] {file_name} 获取失败")
        except Exception as e:
            print(f"  [FAIL] {file_name} 更新失败: {e}")

    dp.quit()
    print("\n数据更新完成！")


# ==================== 名称查找 ====================

def find_positions_by_name(position_data, names):
    """
    通过名称匹配岗位，返回 [(path, code, name), ...]
    支持精确匹配和包含匹配
    """
    # 构建岗位索引: name -> (path, code, name)
    index = {}
    for cat_name, sub_categories in position_data.items():
        for sub_cat in sub_categories:
            for pos in sub_cat['positions']:
                pos_name = pos['name']
                key = pos_name.lower()
                index[key] = (pos['path'], pos['code'], pos_name)

    result = []
    not_found = []

    for name in names:
        name_lower = name.strip().lower()
        # 先尝试精确匹配
        if name_lower in index:
            result.append(index[name_lower])
            continue
        # 模糊匹配（包含匹配）
        found = False
        for key, value in index.items():
            if name_lower in key:
                result.append(value)
                found = True
                break
        if not found:
            not_found.append(name)

    if not_found:
        print(f"[警告] 以下岗位未匹配到: {', '.join(not_found)}")
        print(f"  使用 --list-positions 查看所有可用岗位")

    return result


def find_cities_by_name(city_data, names):
    """
    通过名称或代码匹配城市，返回 [(name, code), ...]
    特殊值 "all" 返回所有城市
    """
    all_cities = city_data['hot'] + city_data['other']

    # 检查是否全部选择
    if len(names) == 1 and names[0].lower() == 'all':
        return [(c['name'], c['code']) for c in all_cities]

    # 构建索引: name -> (name, code) 和 code -> (name, code)
    name_index = {}
    code_index = {}
    for c in all_cities:
        name_index[c['name'].lower()] = (c['name'], c['code'])
        code_index[str(c['code'])] = (c['name'], c['code'])

    result = []
    not_found = []

    for name in names:
        name_stripped = name.strip()
        name_lower = name_stripped.lower()

        # 先尝试名称匹配
        if name_lower in name_index:
            result.append(name_index[name_lower])
            continue
        # 尝试代码匹配
        if name_stripped in code_index:
            result.append(code_index[name_stripped])
            continue
        # 模糊匹配
        found = False
        for key, value in name_index.items():
            if name_lower in key:
                result.append(value)
                found = True
                break
        if not found:
            not_found.append(name)

    if not_found:
        print(f"[警告] 以下城市未匹配到: {', '.join(not_found)}")
        print(f"  使用 --list-cities 查看所有可用城市")

    return result


# ==================== CSV / 去重 ====================

def load_existing_links(file_path):
    """加载已有CSV中的link到集合"""
    existing = set()
    if os.path.exists(file_path):
        try:
            with open(file_path, 'r', encoding=ENCODING) as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if 'link' in row:
                        existing.add(row['link'])
        except Exception as e:
            print(f"加载已有数据失败: {e}")
    return existing


def init_csv_file(file_path):
    """初始化CSV文件（首次写入表头，旧表头自动迁移）"""
    ensure_output_dir(file_path)
    from .config import CSV_FIELDS

    if not os.path.exists(file_path):
        with open(file_path, 'w', encoding=ENCODING, newline='') as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            writer.writeheader()
        return

    # 文件已存在：爬取阶段以 'a' 追加模式写入，若表头是旧版本（列数更少），
    # 新写入的行会按当前 CSV_FIELDS 的列数排列，静默错位到旧表头下面。
    # 所以先把整个文件按新表头重写一遍，缺失的列补空串。
    try:
        with open(file_path, 'r', encoding=ENCODING) as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None:
                return  # 空文件，交给后续写入
            if list(reader.fieldnames) == CSV_FIELDS:
                return  # 表头已是最新，无需迁移
            old_fields = list(reader.fieldnames)
            rows = list(reader)
    except Exception as e:
        print(f"[警告] 读取表头失败，跳过迁移: {e}")
        return

    dropped = [c for c in old_fields if c and c not in CSV_FIELDS]
    if dropped:
        print(f"[警告] 旧表头存在未知列，迁移时将丢弃: {', '.join(dropped)}")

    try:
        with open(file_path, 'w', encoding=ENCODING, newline='') as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction='ignore')
            writer.writeheader()
            for row in rows:
                writer.writerow({k: (row.get(k) or '') for k in CSV_FIELDS})
        added = [c for c in CSV_FIELDS if c not in old_fields]
        print(f"[迁移] {os.path.basename(file_path)} 表头已更新"
              f"（{len(old_fields)} → {len(CSV_FIELDS)} 列，新增: {', '.join(added) or '无'}）")
    except Exception as e:
        print(f"[警告] 表头迁移失败: {e}")
