#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
提示词模板加载
从 prompts/ 目录加载 .st 模板文件，供 Claude Code 使用
"""

from typing import Dict, Any
from pathlib import Path

# 注意：此模块在 resume_matcher/ 子目录中，prompts/ 在项目根目录
_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


def load_prompt(template_name: str) -> str:
    """从 prompts 目录加载提示词模板文件"""
    prompt_path = _PROMPTS_DIR / f"{template_name}.st"
    try:
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        raise FileNotFoundError(f"提示词模板文件未找到: {prompt_path}")


def get_resume_parse_prompt(resume_text: str) -> str:
    """获取简历解析提示词（供 Claude 使用）"""
    template = load_prompt("resume_parse")
    return template.format(resume_text=resume_text)


def get_job_analysis_prompt(job: Dict[str, Any]) -> str:
    """获取岗位分析提示词（供 Claude 使用）"""
    template = load_prompt("job_analysis")
    job_detail = job.get('岗位要求和职责', '')[:500]
    return template.format(
        company=job.get('公司', ''),
        position=job.get('职位', ''),
        salary=job.get('薪资', ''),
        experience=job.get('经验', ''),
        education=job.get('学历', ''),
        skills=job.get('技能标签', ''),
        scale=job.get('规模', ''),
        job_detail=job_detail
    )


def get_match_analysis_prompt(resume_info: str, job_requirements: str) -> str:
    """获取匹配分析提示词（供 Claude 使用）"""
    template = load_prompt("match_analysis")
    return template.format(
        resume_info=resume_info,
        job_requirements=job_requirements
    )


def get_optimize_prompt(
    resume_text: str, company: str, position: str,
    salary: str, requirements: str, match_score: int,
    missing_items: str, optimization_points: str
) -> str:
    """获取简历优化提示词（供 Claude 使用）"""
    template = load_prompt("resume_optimize")
    return template.format(
        resume_text=resume_text,
        company=company,
        position=position,
        salary=salary,
        requirements=requirements,
        match_score=match_score,
        missing_items=missing_items,
        optimization_points=optimization_points
    )
