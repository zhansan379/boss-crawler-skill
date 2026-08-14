#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
岗位数据加载：从 post_data/ 目录扫描和加载 CSV 文件
"""

import csv
import os
from typing import List, Dict, Any

from .config import ENCODING, OUTPUT_DIR


def _richness(row: Dict[str, Any]) -> int:
    """同一 link 有多行时，用来挑信息最全的那一行。

    影子行的 JD 是空的 —— 2026-08-14 之前，同义关键词会把同一岗位写进多个 CSV，
    而详情去重（crawl_job_details 的 detail_existing_links）只回填最先出现的那份。
    所以「保留第一条」会有一半概率留下一个没有岗位描述的岗位，规则打分直接判它不匹配。
    """
    score = 0
    if (row.get('岗位要求和职责') or '').strip():
        score += 2
    if (row.get('公司信息') or '').strip():
        score += 1
    return score


def load_job_data(csv_paths: List[str]) -> List[Dict[str, Any]]:
    """加载爬取的CSV岗位数据（按 link 去重，同 link 保留信息最全的一行）"""
    all_jobs = []

    for csv_path in csv_paths:
        if not os.path.exists(csv_path):
            print(f"警告: 文件不存在 {csv_path}")
            continue

        try:
            with open(csv_path, 'r', encoding=ENCODING) as f:
                reader = csv.DictReader(f)
                count = 0
                for row in reader:
                    row['source_file'] = csv_path
                    all_jobs.append(row)
                    count += 1

            print(f"  加载 {csv_path}: {count} 条")
        except Exception as e:
            print(f"  加载失败 {csv_path}: {e}")

    # 爬取侧已经做了整轮去重，但已落盘的旧 CSV、以及多轮运行之间的文件仍会重叠，
    # 所以加载侧保留这一道。不去重的代价是同一岗位被打分、被排进候选、被派给
    # 深度分析 subagent 好几遍。
    deduped: Dict[str, Dict[str, Any]] = {}
    no_link = []
    for row in all_jobs:
        link = (row.get('link') or '').strip()
        if not link:
            no_link.append(row)
            continue
        kept = deduped.get(link)
        if kept is None or _richness(row) > _richness(kept):
            deduped[link] = row

    result = list(deduped.values()) + no_link
    dropped = len(all_jobs) - len(result)
    if dropped:
        print(f"  [去重] {len(all_jobs)} → {len(result)} 条（丢弃 {dropped} 条重复 link）")

    return result


def list_available_job_files() -> List[Dict[str, Any]]:
    """列出可用的岗位数据文件"""
    print("\n正在扫描岗位数据文件...")

    job_files = []

    # 扫描 post_data 目录（位于 assets/ 下）
    post_data_dir = os.path.join(OUTPUT_DIR, 'post_data')
    if os.path.exists(post_data_dir):
        for root, _, files in os.walk(post_data_dir):
            for file in files:
                if file.endswith('.csv') and '_details' not in file:
                    file_path = os.path.join(root, file)
                    try:
                        with open(file_path, 'r', encoding=ENCODING) as f:
                            count = sum(1 for _ in csv.DictReader(f))
                        job_files.append({
                            'path': file_path,
                            'name': file,
                            'count': count
                        })
                    except Exception:
                        pass

    return job_files
