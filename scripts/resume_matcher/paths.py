#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
run_dir 目录结构的**唯一真源**：所有脚本都从这里取产物路径，不各自拼。

按「有用/无用」把跑一遍流水线的产物分进四个桶：

    run_dir/
    ├── state/         机器状态（续跑/回溯用，人不直接看）
    │   ├── profile.json / resume_text.txt / profile_validation.json
    │   ├── crawl_params.json / crawl_summary.json / scored_jobs.json
    │   ├── qualified_jobs.json / match_analysis.json / verify_report.json / apply_log.json
    ├── materials/     LLM 源（机器源，花钱的，再渲染靠它）
    │   ├── greeting_#_<公司>.txt
    │   └── resume_#_<公司>.json
    ├── deliver/       最终交付（人看的）
    │   ├── matching_report.html
    │   └── #N-<公司>-<岗位>/<姓名>-<岗位>.png + 岗位信息+招呼语.md
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


def match_analysis_path(run_dir):
    """按 link 存的裁定结论（merge_deep_results 在写 qualified_jobs 时一并产出）。

    qualified_jobs.json 按设计只含原始爬取字段（投递阶段按 link 回查 CSV），匹配
    结论单独落在这里，供 write_application_md 生成 岗位信息+招呼语.md 时按 link 连回来。
    放 state/（随 run 存在、删 run 即删）而不是 intermediate/（文档明言可整删），
    否则 岗位信息+招呼语.md 在中间产物被清理后就没匹配分析了。
    """
    return os.path.join(state_dir(run_dir), 'match_analysis.json')


def verify_report_path(run_dir):
    return os.path.join(state_dir(run_dir), 'verify_report.json')


def verify_known_path(run_dir):
    """verify 阶段由 LLM 确认、累计进缓存的技术词（run-dir 级，删 run 即删）。

    与 verify_report.json 的区别：这是「学习结果」不是「单次检查结论」。LLM 判定一个
    写法（缩写/同义词/中英等价）在本轮简历里有依据后，它的归一形存进这里；下次同一轮
    目录再跑 verify 时直接命中、不再调模型。只认本轮 baseline，换简历/换目录就重判，
    避免把「A 简历有依据」误当「永远有依据」导致的假阴性泄漏。
    """
    return os.path.join(state_dir(run_dir), 'verify_known.json')


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