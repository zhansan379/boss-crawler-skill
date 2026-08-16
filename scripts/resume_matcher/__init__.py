#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
简历分析与岗位匹配系统

LLM 调用统一走 scripts/llm/（OpenAI 兼容端点），本包只管：
数据结构、文件解析、CSV 加载、规则匹配、6 维度评分、HTML 报告、自动投递。
"""

# ── 数据类
from .config import (
    ResumeProfile,
    JobClassification,
    CSV_FIELDS,
    ENCODING,
    OUTPUT_DIR,
    create_run_dir,
    get_latest_run_dir,
)

# ── 简历解析
from .parsers import parse_resume_file, parse_pdf, parse_docx, parse_plain_text

# ── 提示词
from .prompts import (
    load_prompt,
    get_resume_parse_prompt,
    get_match_analysis_prompt,
    get_optimize_prompt,
)

# ── 数据加载
from .data_loader import load_job_data, list_available_job_files

# ── 评分与分类
from .scoring import (
    score_job_advanced,
    classify_jobs_advanced,
    tiers_to_classification,
    build_job_view,
    JOB_VIEW_FIELDS,
    compute_difficulty,
    decide_application_category,
    parse_degree_level,
    hr_activity_rank,
    hr_activity_sort_key,
    CATEGORY_QUALIFIED,
    CATEGORY_NEED_OPTIMIZATION,
    CATEGORY_CANNOT_APPLY,
)

# ── 报告生成
from .report import generate_html_report, generate_bauhaus_json

# ── 自动投递
from .auto_apply import auto_apply_jobs, generate_greeting

# ── 深度分析
from .deep_analysis import (
    save_deep_candidates,
    merge_deep_results,
    serialize_profile,
    deserialize_profile,
)

# ── 工具函数
from .utils import (
    print_header,
    print_section,
    ensure_output_dir,
    parse_experience_years,
    parse_company_size,
)


__all__ = [
    # Data classes
    "ResumeProfile", "JobClassification",
    "CSV_FIELDS", "ENCODING", "OUTPUT_DIR",
    "create_run_dir", "get_latest_run_dir",
    # Parsers
    "parse_resume_file", "parse_pdf", "parse_docx", "parse_plain_text",
    # Prompts
    "load_prompt", "get_resume_parse_prompt",
    "get_match_analysis_prompt", "get_optimize_prompt",
    # Data loader
    "load_job_data", "list_available_job_files",
    # Scoring
    "score_job_advanced", "classify_jobs_advanced",
    "tiers_to_classification", "compute_difficulty",
    "decide_application_category", "parse_degree_level",
    "hr_activity_rank", "hr_activity_sort_key",
    "build_job_view", "JOB_VIEW_FIELDS",
    "CATEGORY_QUALIFIED", "CATEGORY_NEED_OPTIMIZATION", "CATEGORY_CANNOT_APPLY",
    # Report
    "generate_html_report", "generate_bauhaus_json",
    # Auto-apply
    "auto_apply_jobs", "generate_greeting",
    # Deep analysis
    "save_deep_candidates", "merge_deep_results",
    "serialize_profile", "deserialize_profile",
    # Utils
    "print_header", "print_section", "ensure_output_dir",
    "parse_experience_years", "parse_company_size",
]
