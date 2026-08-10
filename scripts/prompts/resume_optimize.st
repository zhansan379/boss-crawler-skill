请针对以下岗位要求，生成简历优化建议。

原简历:
{resume_text}

目标岗位:
- 公司: {company}
- 职位: {position}
- 薪资: {salary}
- 要求: {requirements}

匹配分析:
- 匹配度: {match_score}
- 缺失项: {missing_items}
- 可优化点: {optimization_points}

请以JSON格式返回（不要包含任何解释文字）:
{{
    "optimization_suggestions": {{
        "must_add": [
            {{"section": "建议添加的位置", "content": "具体内容建议"}}
        ],
        "should_adjust": [
            {{"section": "建议调整的部分", "suggestion": "调整建议"}}
        ],
        "keywords_to_emphasize": ["需强调的关键词"],
        "format_suggestions": ["格式优化建议"]
    }},
    "optimized_resume": "优化后的完整简历（Markdown格式，保持真实性，不编造虚假经历）",
    "key_changes": ["主要修改点列表"]
}}

注意：
1. 不要编造虚假经历，只建议用户补充真实信息
2. 优化应聚焦于突出已有优势
3. 使用STAR法则组织项目描述
4. 不需要返回个人信息