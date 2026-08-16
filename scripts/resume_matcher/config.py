#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
常量定义与数据类
"""

import os
import time
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field

# 常量定义
ENCODING = 'utf-8-sig'
_SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUTPUT_DIR = os.path.join(_SKILL_ROOT, 'assets')


def create_run_dir(base_dir: Optional[str] = None) -> str:
    """
    在 base_dir 下创建带时间戳的运行子目录，避免多次运行互相覆盖。

    目录命名: ./resume_output/2026-08-09_14-30-00/
    同时写入 LATEST.txt 指针文件，记录最近一次运行的目录名。

    Args:
        base_dir: 基础输出目录，默认为 OUTPUT_DIR

    Returns:
        带时间戳的运行目录绝对路径
    """
    if base_dir is None:
        base_dir = OUTPUT_DIR
    timestamp = time.strftime('%Y-%m-%d_%H-%M-%S')
    run_dir = os.path.join(base_dir, timestamp)
    os.makedirs(run_dir, exist_ok=True)

    # 四个桶：state/materials/deliver/intermediate。先建好让目录结构自明；
    # 各写方写前仍会 os.makedirs 兜底（--run-dir 指向已有目录时桶可能不存在）。
    import paths
    for _sub in (paths.state_dir(run_dir), paths.materials_dir(run_dir),
                 paths.deliver_dir(run_dir), paths.intermediate_dir(run_dir)):
        os.makedirs(_sub, exist_ok=True)

    # 写入 latest 指针
    latest_file = os.path.join(base_dir, 'LATEST.txt')
    with open(latest_file, 'w', encoding='utf-8') as f:
        f.write(timestamp)

    return os.path.abspath(run_dir)


def get_latest_run_dir(base_dir: Optional[str] = None) -> Optional[str]:
    """
    读取 LATEST.txt 获取最近一次运行的目录路径。

    Args:
        base_dir: 基础输出目录，默认为 OUTPUT_DIR

    Returns:
        最近运行目录的路径，若无记录则返回 None
    """
    if base_dir is None:
        base_dir = OUTPUT_DIR
    latest_file = os.path.join(base_dir, 'LATEST.txt')
    if not os.path.exists(latest_file):
        return None
    with open(latest_file, 'r', encoding='utf-8') as f:
        timestamp = f.read().strip()
    run_dir = os.path.join(base_dir, timestamp)
    if os.path.isdir(run_dir):
        return os.path.abspath(run_dir)
    return None

# CSV字段（与 boss_crawler/config.py 的 CSV_FIELDS 必须逐字一致）
CSV_FIELDS = [
    'link', '职位', '城市', '区域', '商圈', '地址', '公司', '薪资', '经验', '学历',
    '领域', '性质', '规模', '技能标签', '福利标签', '位置', '岗位要求和职责', '公司信息',
    '已失效', '代招',
    'HR活跃度', 'HR在线', 'HR职位', 'HR姓名', 'HR公司'
]


# ==================== 数据类定义 ====================

@dataclass
class ResumeProfile:
    """简历解析结果"""
    basic_info: Dict[str, Any] = field(default_factory=dict)
    education: Dict[str, Any] = field(default_factory=dict)
    experience: Dict[str, Any] = field(default_factory=dict)
    skills: Dict[str, Any] = field(default_factory=dict)
    projects: List[Dict[str, Any]] = field(default_factory=list)
    awards: List[Dict[str, Any]] = field(default_factory=list)
    publications: List[Dict[str, Any]] = field(default_factory=list)
    social_links: Dict[str, Any] = field(default_factory=dict)
    salary_expectation: Dict[str, Any] = field(default_factory=dict)
    keywords: List[str] = field(default_factory=list)
    raw_text: str = ""


@dataclass
class JobClassification:
    """岗位分类结果"""
    cannot_apply: List[Dict[str, Any]] = field(default_factory=list)
    need_optimization: List[Dict[str, Any]] = field(default_factory=list)
    qualified: List[Dict[str, Any]] = field(default_factory=list)
