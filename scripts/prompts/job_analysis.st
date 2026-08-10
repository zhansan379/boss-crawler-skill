请分析以下岗位的要求，提取关键信息。

岗位信息:
- 公司: {company}
- 职位: {position}
- 薪资: {salary}
- 经验要求: {experience}
- 学历要求: {education}
- 技能标签: {skills}
- 公司规模: {scale}
- 岗位详情: {job_detail}

请以JSON格式返回（不要包含任何解释文字）:
{{
    "required_education": "学历要求等级（博士/硕士/本科/大专/不限）",
    "required_experience_years": "经验年限要求（数字，如3表示3年）",
    "required_skills": ["必备技能列表"],
    "preferred_skills": ["加分技能列表"],
    "other_requirements": ["其他要求"],
    "salary_range": {{
        "min": "最低薪资（数字，单位K）",
        "max": "最高薪资（数字，单位K）"
    }},
    "company_size": "公司规模判断（大厂/中厂/小厂）",
    "key_keywords": ["岗位核心关键词"]
}}

请根据岗位详情准确区分硬性要求和加分项。