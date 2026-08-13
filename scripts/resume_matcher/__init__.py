#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
简历分析与岗位匹配系统（Claude Code 驱动版）

已移除 infra_ai 依赖，LLM 分析由 Claude Code 自身完成。
保留：数据结构、文件解析、CSV加载、规则匹配、6维度评分、HTML报告、自动投递。
"""

# ── 数据类
from .config import (
    ResumeProfile,
    JobRequirements,
    MatchResult,
    DifficultyPrediction,
    JobClassification,
    CSV_FIELDS,
    DIFFICULTY_WEIGHTS,
    ENCODING,
    OUTPUT_DIR,
    create_run_dir,
    get_latest_run_dir,
)

# ── 简历解析
from .parsers import parse_resume_file, parse_pdf, parse_docx

# ── 提示词
from .prompts import (
    load_prompt,
    get_resume_parse_prompt,
    get_job_analysis_prompt,
    get_match_analysis_prompt,
    get_optimize_prompt,
)

# ── 数据加载
from .data_loader import load_job_data, list_available_job_files

# ── 评分与分类
from .scoring import (
    analyze_job_requirements_quick,
    calculate_education_match,
    calculate_experience_match,
    calculate_skills_match,
    predict_difficulty,
    score_job_advanced,
    classify_jobs_advanced,
    tiers_to_classification,
    compute_difficulty,
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
    parse_education_level,
    parse_experience_years,
    parse_salary_range,
    parse_company_size,
    edu_level_to_str,
)


__all__ = [
    # Data classes
    "ResumeProfile", "JobRequirements", "MatchResult",
    "DifficultyPrediction", "JobClassification",
    "CSV_FIELDS", "DIFFICULTY_WEIGHTS", "ENCODING", "OUTPUT_DIR",
    "create_run_dir", "get_latest_run_dir",
    # Parsers
    "parse_resume_file", "parse_pdf", "parse_docx",
    # Prompts
    "load_prompt", "get_resume_parse_prompt", "get_job_analysis_prompt",
    "get_match_analysis_prompt", "get_optimize_prompt",
    # Data loader
    "load_job_data", "list_available_job_files",
    # Scoring
    "analyze_job_requirements_quick", "calculate_education_match",
    "calculate_experience_match", "calculate_skills_match",
    "predict_difficulty", "score_job_advanced", "classify_jobs_advanced",
    "tiers_to_classification",
    "compute_difficulty",
    # Report
    "generate_html_report", "generate_bauhaus_json",
    # Auto-apply
    "auto_apply_jobs", "generate_greeting",
    # Deep analysis
    "save_deep_candidates", "merge_deep_results",
    "serialize_profile", "deserialize_profile",
    # Utils
    "print_header", "print_section", "ensure_output_dir",
    "parse_education_level", "parse_experience_years",
    "parse_salary_range", "parse_company_size", "edu_level_to_str",
]
