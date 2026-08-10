#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
岗位数据加载：从 post_data/ 目录扫描和加载 CSV 文件
"""

import csv
import os
from typing import List, Dict, Any

from .config import ENCODING, OUTPUT_DIR


def load_job_data(csv_paths: List[str]) -> List[Dict[str, Any]]:
    """加载爬取的CSV岗位数据"""
    all_jobs = []

    for csv_path in csv_paths:
        if not os.path.exists(csv_path):
            print(f"警告: 文件不存在 {csv_path}")
            continue

        try:
            with open(csv_path, 'r', encoding=ENCODING) as f:
                reader = csv.DictReader(f)
                for row in reader:
                    row['source_file'] = csv_path
                    all_jobs.append(row)

            count = sum(1 for r in all_jobs if r['source_file'] == csv_path)
            print(f"  加载 {csv_path}: {count} 条")
        except Exception as e:
            print(f"  加载失败 {csv_path}: {e}")

    return all_jobs


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
