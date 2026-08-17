请分析以下简历与目标岗位，产出「简历调整计划」。这是简历优化流程的**第一阶段**：只做分析与规划，**不要输出最终简历**（完整简历在第二阶段按本计划输出）。

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
    "chapter_plan": [
        {{"chapter": "专业技能", "order": 1, "keep": true}},
        {{"chapter": "开源经历", "order": 4, "keep": true}}
    ],
    "optimization_suggestions": {{
        "must_add": [
            {{"section": "建议添加的位置", "content": "具体内容建议"}}
        ],
        "should_adjust": [
            {{"section": "建议调整的部分", "suggestion": "调整建议"}}
        ],
        "keywords_to_emphasize": ["需强调的关键词"],
        "format_suggestions": ["格式优化建议"]
    }}
}}

注意：
1. 不要编造虚假经历，只建议用户补充真实信息；优化应聚焦于突出已有优势。
2. 【最重要】chapter_plan 必须列出**原简历实有的全部二级章节（## 标题）**，每条 keep:true，一条都不得省略。尤其「荣誉奖项」「开源经历」是无工作时长的应届生最有力的差异化背书，即使与目标岗位相关度靠后也必须保留。相关度只决定 order 先后，不决定去留。
3. 不要新造章节（原简历没有的，就别列进 chapter_plan）。原简历缺、但岗位强烈要求的内容，写进 optimization_suggestions.must_add 提示用户补充真实信息，而不是在计划里造章节。
4. 二级章节的合法取值（其余不要用）：个人简介 / 专业技能 / 工作经历 / 实习经历 / 项目经历 / 教育背景 / 荣誉奖项 / 开源经历