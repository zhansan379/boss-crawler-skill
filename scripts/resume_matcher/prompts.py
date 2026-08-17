#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
提示词模板加载
从 prompts/ 目录加载 .st 模板文件
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
    """获取简历解析提示词"""
    template = load_prompt("resume_parse")
    return template.format(resume_text=resume_text)


def get_match_analysis_prompt(resume_info: str, job_requirements: str) -> str:
    """获取匹配分析提示词"""
    template = load_prompt("match_analysis")
    return template.format(
        resume_info=resume_info,
        job_requirements=job_requirements
    )


def get_greeting_prompt(
    job: Dict[str, Any],
    name: str = '',
    resume_summary: str = '',
    match_reasons: str = '',
    availability: str = '',
    scene_hint: str = '',
) -> str:
    """获取招呼语提示词（gen_materials.py 的 greeting 阶段在用）

    规则本体在 prompts/greeting.st，不要在调用处复述 —— 这套口径（前 15 字预览框、
    校招不许提出勤、到岗信息不许编）以 greeting.st 为唯一来源。

    availability 为空时传「简历未提供」而不是空串：模板里那条「没给就别猜日期」的
    指令需要一个明确的取值才生效，留空会被模型读成「这栏我自己想」。
    """
    template = load_prompt("greeting")
    return template.format(
        company=job.get('公司', '') or '',
        position=job.get('职位', '') or '',
        salary=job.get('薪资', '') or '',
        experience_req=job.get('经验', '') or '',
        education_req=job.get('学历', '') or '',
        skills_tags=job.get('技能标签', '') or '',
        jd=(job.get('岗位要求和职责', '') or '')[:1500],
        name=name or '',
        resume_summary=resume_summary or '',
        match_reasons=match_reasons or '（无）',
        availability=availability or '简历未提供',
        scene_hint=scene_hint or '未判断',
    )


def get_crawl_params_prompt(
    resume: str, kw_budget: int, salary: str, experience: str,
    degree: str, job_type: str, today: str
) -> str:
    """获取爬取参数推断提示词（infer_params.py 在用）

    只在用户没给全筛选条件时才真的发出去：SKILL.md 里参数优先由 AskUserQuestion
    确认，确认过的值以 --salary/--experience 等形式传进来，模型只补没确认的那几个。

    四个枚举字符串必须由调用方从爬虫的 FILTER_LABELS 传进来，不要写死在模板里 ——
    爬虫拿到不认识的筛选值不报错，只会安静地少筛一批，那种失败看起来跟成功一样。

    today 传今天的日期（YYYY-MM-DD）。模板靠它跟简历里的毕业年份对比来判断在校/已
    毕业，从而定 job_type；不传日期时模型只能靠简历措辞猜，实习和全职会混着出。
    """
    template = load_prompt("crawl_params")
    return template.format(
        resume=resume,
        kw_budget=kw_budget,
        salary=salary,
        experience=experience,
        degree=degree,
        job_type=job_type,
        today=today,
    )


def get_optimize_prompt(
    resume_text: str, company: str, position: str,
    salary: str, requirements: str, match_score: int,
    missing_items: str, optimization_points: str
) -> str:
    """获取简历优化提示词

    NOTE: 已拆为 resume_optimize_plan.st + resume_optimize_apply.st（两阶段：
    ①出调整计划、②照单输出）。本函数保留仅为兼容旧调用，gen_materials.py 已改用
    get_optimize_plan_prompt / get_optimize_apply_prompt。
    """
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


def get_optimize_plan_prompt(
    resume_text: str, company: str, position: str,
    salary: str, requirements: str, match_score: int,
    missing_items: str, optimization_points: str
) -> str:
    """第一阶段：简历调整计划（只分析，不重排正文）。

    产出 chapter_plan + optimization_suggestions。chapter_plan 是「原简历实有章节的
    保留 + 排序」清单，作为第二阶段的硬锚点；gen_materials 会拿它跟原简历章节做
    便宜校验，缺章就重跑本阶段。规则本体在 prompts/resume_optimize_plan.st。
    """
    template = load_prompt("resume_optimize_plan")
    return template.format(
        resume_text=resume_text,
        company=company,
        position=position,
        salary=salary,
        requirements=requirements,
        match_score=match_score,
        missing_items=missing_items,
        optimization_points=optimization_points,
    )


def get_optimize_apply_prompt(
    resume_text: str, company: str, position: str,
    chapter_list: str, optimization_suggestions: str
) -> str:
    """第二阶段：按调整计划输出完整简历（照单执行）。

    chapter_list 是第①阶段校验过的「必须输出的章节清单」，作为硬命令而非软性建议；
    optimization_suggestions 序列化成多行 JSON 一并注入。规则在 resume_optimize_apply.st。
    """
    template = load_prompt("resume_optimize_apply")
    return template.format(
        resume_text=resume_text,
        company=company,
        position=position,
        chapter_list=chapter_list,
        optimization_suggestions=optimization_suggestions,
    )
