#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
报告生成模块：HTML 可视化报告 + JSON 分类结果输出
"""

import os
import json
import time
from typing import List, Dict, Any
from pathlib import Path

from .config import ResumeProfile, JobClassification, OUTPUT_DIR
from .utils import ensure_output_dir
from .scoring import (
    compute_difficulty,
    hr_activity_rank,
    build_job_view,
    CATEGORY_QUALIFIED,
    CATEGORY_NEED_OPTIMIZATION,
    CATEGORY_CANNOT_APPLY,
)

# 模板文件路径
_TEMPLATE_DIR = Path(__file__).parent / "templates"


def _load_report_template() -> str:
    """从文件加载 HTML 报告模板"""
    template_path = _TEMPLATE_DIR / "report.html"
    with open(template_path, "r", encoding="utf-8") as f:
        return f.read()


def generate_html_report(
    profile: ResumeProfile,
    classification: JobClassification,
    output_dir: str = OUTPUT_DIR,
    analysis_mode: str = 'quick',
) -> str:
    """
    生成 Bauhaus 风格 HTML 可视化报告

    数据嵌入为 JSON，由客户端 JavaScript 渲染。
    返回 HTML 文件路径。

    Args:
        analysis_mode: 'quick'（纯规则）或 'deep'（Claude LLM 深度分析），
                       影响前端是否显示优化建议按钮
    """
    ensure_output_dir()

    # ── 构建 resume_info ──
    all_skills = []
    for cat in ['programming', 'frameworks', 'tools', 'other']:
        all_skills.extend(profile.skills.get(cat, []))

    school = profile.education.get('school', '')
    degree = profile.education.get('degree', '')
    major = profile.education.get('major', '')
    grad_year = profile.education.get('graduation_year', '')
    edu_parts = [school, degree, major]
    if grad_year:
        edu_parts.append(f'{grad_year}届')
    education_str = ' · '.join(p for p in edu_parts if p) or '未知'

    salary = profile.salary_expectation
    resume_info = {
        'name': profile.basic_info.get('name', '未提取'),
        'education': education_str,
        'skills': all_skills,
        'salary_expectation': {
            'min': salary.get('min') or '?' if salary else '?',
            'max': salary.get('max') or '?' if salary else '?'
        },
        'target_city': profile.basic_info.get('city', '杭州'),
        'target_position': profile.basic_info.get('target_position', '后端开发'),
    }

    # ── 构建 statistics ──
    q_jobs = classification.qualified
    n_jobs = classification.need_optimization
    c_jobs = classification.cannot_apply
    total = len(q_jobs) + len(n_jobs) + len(c_jobs)

    statistics = {
        'total_jobs': total,
        'cannot_apply_count': len(c_jobs),
        'need_optimization_count': len(n_jobs),
        'qualified_count': len(q_jobs),
    }

    # ── 确保每个 job 的 difficulty / application_category 由后端给定 ──
    def _ensure_job_meta(job: Dict, idx: int, category: str) -> Dict:
        if not job.get('job_id'):
            link = job.get('link', '')
            if link:
                import re
                m = re.search(r'([A-Za-z0-9_-]{7,})(?:\.html|$)', link)
                if m:
                    job['job_id'] = 'job_' + m.group(1)
            if not job.get('job_id'):
                job['job_id'] = f'job_{idx:04d}'
        # 始终从 match_score 重新计算 difficulty
        score = job.get('match_score', 50)
        job['difficulty'] = compute_difficulty(score)
        # 投递分类：缺失时按所在桶回填，保证前端永不拿到空值
        if not job.get('application_category'):
            job['application_category'] = category
        job.setdefault('application_category_reason', '')
        # HR 活跃度：CSV 里是中文列名，归一化成前端读的 ASCII 键。
        # hr_activity_rank 为 None 表示未采集（无 -d），前端据此显示「未采集」，
        # 而不是把它伪装成「不活跃」。
        job['hr_active_desc'] = job.get('HR活跃度', job.get('hr_active_desc', '')) or ''
        job['hr_online'] = job.get('HR在线', job.get('hr_online', '')) or ''
        job['hr_title'] = job.get('HR职位', job.get('hr_title', '')) or ''
        job['hr_activity_rank'] = hr_activity_rank(job)
        return job

    # ── 构建完整数据 ──
    report_data = {
        'resume_info': resume_info,
        'statistics': statistics,
        'classification': {
            'cannot_apply': [_ensure_job_meta(j, i, CATEGORY_CANNOT_APPLY)
                             for i, j in enumerate(c_jobs)],
            'need_optimization': [_ensure_job_meta(j, i, CATEGORY_NEED_OPTIMIZATION)
                                  for i, j in enumerate(n_jobs)],
            'qualified': [_ensure_job_meta(j, i, CATEGORY_QUALIFIED)
                          for i, j in enumerate(q_jobs)],
        },
        'crawl_params': {},
        'generated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'analysis_mode': analysis_mode,
    }

    # ── 加载模板并嵌入数据 ──
    template = _load_report_template()
    html = template.replace('___REPORT_DATA___', json.dumps(report_data, ensure_ascii=False))

    output_path = os.path.join(output_dir, 'matching_report.html')
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)

    print(f"\nHTML报告已生成: {output_path}")
    return output_path


def generate_bauhaus_json(
    profile: ResumeProfile,
    tier1: List[Dict],
    tier2: List[Dict],
    tier3: List[Dict],
    tier4: List[Dict] = None,
    crawl_params: Dict[str, Any] = None,
    output_dir: str = OUTPUT_DIR,
    analysis_mode: str = 'quick',
) -> str:
    """
    生成 Bauhaus 风格 HTML 报告所需的 JSON 数据文件

    输出格式:
        resume_info: {name, education, skills, salary_expectation, target_city, target_position}
        statistics: {total_jobs, cannot_apply_count, need_optimization_count, qualified_count}
        classification: {cannot_apply, need_optimization, qualified}
        crawl_params: {keywords, city, experience, degree, salary_range}
        generated_at: ISO时间戳

    Args:
        profile: 简历解析结果
        tier1: 高匹配岗位 (展示为 qualified)
        tier2: 可优化岗位 (展示为 need_optimization)
        tier3: 待提升岗位 (展示为 cannot_apply)
        tier4: 低匹配岗位 (不纳入报告，可选)
        crawl_params: 爬取参数 {keywords, city, ...}
        output_dir: 输出目录

    Returns:
        JSON 文件路径
    """
    from datetime import datetime

    ensure_output_dir()

    # ── 构建 resume_info ──
    all_skills = []
    for cat in ['programming', 'frameworks', 'tools', 'other']:
        all_skills.extend(profile.skills.get(cat, []))

    resume_info = {
        'name': profile.basic_info.get('name', '未提取'),
        'education': (f"{profile.education.get('school', '')} · "
                      f"{profile.education.get('degree', '')} · "
                      f"{profile.education.get('major', '')} · "
                      f"{profile.education.get('graduation_year', '')}届").strip(' ·'),
        'skills': all_skills,
        'salary_expectation': {
            # 与本文件 73-74 行同样的兜底：salary_expectation 整个可能是 null，
            # 值也可能是 null，两层都要挡，否则报告生成整个崩掉
            'min': (profile.salary_expectation or {}).get('min') or '?',
            'max': (profile.salary_expectation or {}).get('max') or '?'
        },
        'target_city': '杭州',
        'target_position': '后端开发 / AI应用开发'
    }

    # ── 映射岗位到报告格式 ──
    # 字段枚举统一在 scoring.build_job_view，不要在这里重建 dict（见该函数注释）。
    # JSON 产出比 HTML 紧，只调截断长度。
    def _map_to_report(job: Dict, category: str) -> Dict:
        return build_job_view(job, category, company_info_len=300, jd_len=500)

    # ── 构建输出 ──
    output = {
        'resume_info': resume_info,
        'statistics': {
            'total_jobs': len(tier1) + len(tier2) + len(tier3) + len(tier4 or []),
            'cannot_apply_count': len(tier3),
            'need_optimization_count': len(tier2),
            'qualified_count': len(tier1),
        },
        'classification': {
            'cannot_apply': [_map_to_report(j, CATEGORY_CANNOT_APPLY) for j in tier3],
            'need_optimization': [_map_to_report(j, CATEGORY_NEED_OPTIMIZATION) for j in tier2],
            'qualified': [_map_to_report(j, CATEGORY_QUALIFIED) for j in tier1],
        },
        'crawl_params': crawl_params or {},
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'generated_by': 'resume_matcher.report (classify_jobs_advanced)',
        'analysis_mode': analysis_mode,
    }

    output_path = os.path.join(output_dir, 'job_classification.json')
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\nBauhaus JSON 已保存到: {output_path}")
    return output_path
