请分析简历与岗位的匹配情况。

简历信息:
{resume_info}

岗位要求:
{job_requirements}

请以JSON格式返回匹配分析结果（不要包含任何解释文字）:
{{
    "education_match": {{
        "score": 0-100的匹配分数,
        "match": true/false,
        "reason": "简短原因"
    }},
    "experience_match": {{
        "score": 0-100的匹配分数,
        "match": true/false,
        "reason": "简短原因"
    }},
    "skills_match": {{
        "score": 0-100的匹配分数,
        "matched": ["匹配的技能"],
        "missing": ["缺失的技能"],
        "reason": "简短原因"
    }},
    "overall_match": 0-100总体匹配度,
    "classification": "不可投递/可优化后投递/符合要求",
    "classification_reason": "分类原因",
    "missing_items": ["缺失项列表"],
    "optimization_points": ["可优化点列表"]
}}

分类标准:
- 不可投递: 硬性条件不满足（学历/年限明显不足）
- 可优化后投递: 硬性条件满足，但有优化空间
- 符合要求: 匹配度高，可直接投递