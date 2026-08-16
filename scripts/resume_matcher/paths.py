#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
run_dir 目录结构的**唯一真源**：所有脚本都从这里取产物路径，不各自拼。

按「有用/无用」把跑一遍流水线的产物分进四个桶：

    run_dir/
    ├── state/         机器状态（续跑/回溯用，人不直接看）
    │   ├── profile.json / resume_text.txt / profile_validation.json
    │   ├── crawl_params.json / crawl_summary.json / scored_jobs.json
    │   ├── qualified_jobs.json / verify_report.json / apply_log.json
    ├── materials/     LLM 源（机器源，花钱的，再渲染靠它）
    │   ├── greeting_#_<公司>.txt
    │   └── resume_#_<公司>.json
    ├── deliver/       最终交付（人看的）
    │   ├── matching_report.html
    │   └── <公司>-<岗位>/<姓名>-<岗位>.png + 投递.md
    └── intermediate/  跑完即无用：可整体删除
        ├── deep_candidates.json / deep_results.json
        ├── llm_usage.jsonl / run_timings.jsonl
        ├── staging/   （原 showcv_staging/）
        └── exports/   （原 showcv_exports/）

改目录结构只改这一个文件，其余脚本无需动。
"""

import os

# ==================== 四个桶 ====================


def state_dir(run_dir):
    return os.path.join(run_dir, 'state')


def materials_dir(run_dir):
    return os.path.join(run_dir, 'materials')


def deliver_dir(run_dir):
    return os.path.join(run_dir, 'deliver')


def intermediate_dir(run_dir):
    return os.path.join(run_dir, 'intermediate')


# ==================== state/ ====================


def profile_path(run_dir):
    return os.path.join(state_dir(run_dir), 'profile.json')


def resume_text_path(run_dir):
    return os.path.join(state_dir(run_dir), 'resume_text.txt')


def profile_validation_path(run_dir):
    return os.path.join(state_dir(run_dir), 'profile_validation.json')


def crawl_params_path(run_dir):
    return os.path.join(state_dir(run_dir), 'crawl_params.json')


def crawl_summary_path(run_dir):
    return os.path.join(state_dir(run_dir), 'crawl_summary.json')


def scored_jobs_path(run_dir):
    return os.path.join(state_dir(run_dir), 'scored_jobs.json')


def qualified_jobs_path(run_dir):
    return os.path.join(state_dir(run_dir), 'qualified_jobs.json')


def verify_report_path(run_dir):
    return os.path.join(state_dir(run_dir), 'verify_report.json')


def apply_log_path(run_dir):
    return os.path.join(state_dir(run_dir), 'apply_log.json')


# ==================== materials/ ====================

# 文件名契约：greeting_{i}_{公司}.txt / resume_{i}_{公司}.json
# （i 是岗位在 qualified_jobs.json 里的 1-based 序号）


def greeting_pattern(run_dir, index):
    return os.path.join(materials_dir(run_dir), 'greeting_%d_*.txt' % index)


def greeting_path(run_dir, index, company):
    return os.path.join(materials_dir(run_dir),
                        'greeting_%d_%s.txt' % (index, company))


def resume_pattern(run_dir, index):
    return os.path.join(materials_dir(run_dir), 'resume_%d_*.json' % index)


# ==================== deliver/ ====================


def matching_report_path(run_dir):
    return os.path.join(deliver_dir(run_dir), 'matching_report.html')


# ==================== intermediate/ ====================


def deep_candidates_path(run_dir):
    return os.path.join(intermediate_dir(run_dir), 'deep_candidates.json')


def job_classification_path(run_dir):
    return os.path.join(intermediate_dir(run_dir), 'job_classification.json')


def deep_results_path(run_dir):
    return os.path.join(intermediate_dir(run_dir), 'deep_results.json')


def llm_usage_path(run_dir):
    return os.path.join(intermediate_dir(run_dir), 'llm_usage.jsonl')


def timings_path(run_dir):
    return os.path.join(intermediate_dir(run_dir), 'run_timings.jsonl')


def showcv_staging_dir(run_dir):
    return os.path.join(intermediate_dir(run_dir), 'staging')


def showcv_exports_dir(run_dir):
    return os.path.join(intermediate_dir(run_dir), 'exports')