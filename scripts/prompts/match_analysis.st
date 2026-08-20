你是一个资深的简历与职位匹配专家。请根据以下【简历摘要】和【职位JD】，完成两项任务：

1. 给出匹配度评分（0-100），评分参考维度：薪资重叠度、经验年限、学历硬门槛、技能重合度。
2. **直接给出投递建议分类**，必须严格从以下三类中选择：
   - `"qualified"`: 技能重合度高（命中≥4项核心技能）、薪资在期望范围内、学历经验完全满足。
   - `"need_optimization"`: 核心技能缺2-3项但可短期补齐（如工具链）、薪资略有差距（≤3K）、或经验差1年内。
   - `"cannot_apply"`: 学历硬性不达标（如要求硕士但你是本科）、经验差≥3年、薪资期望差距过大（>8K）。

简历信息:
{resume_info}

岗位要求:
{job_requirements}

注意：
- 硬门槛优先：若JD要求"硕士"而简历为"本科"，请直接归类为 `"cannot_apply"`，即使其他维度满分。同理，经验差≥3年或薪资差距>8K 也直接判 `"cannot_apply"`。
- 核心技术栈优先（最重要的方向判定）：`简历摘要`里的 `## 核心技术栈`与项目中使用最多的语言 是候选的主业语言，权重最高。
  对每个岗位，先判断它的核心编程语言，再交叉判定：
  · 岗位要求的核心语言 ∈ 候选主业语言 → 正常评估；核心语言/框架命中远比通用工具、次要标签值钱。
  · 岗位要求的核心语言 ∉ 候选主业语言，直接归类为 `"cannot_apply"`，`score` 压低（≤40），
    理由注明"技术栈不匹配(核心X vs 岗位Y)"。
- 薪资为"面议"或无法解析时，最高只能评到 `"need_optimization"`，理由中注明"薪资面议待确认"——未经验证的薪资不应触发自动投递。
- `category` 只能是 `qualified` / `need_optimization` / `cannot_apply` 三个英文枚举值之一，不要输出中文标签。
- 输出必须为合法JSON格式，不要包含任何解释文字或 Markdown 代码块标记。

输出格式:
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
    "salary_match": {{
        "score": 0-100的匹配分数,
        "match": true/false,
        "reason": "简短原因"
    }},
    "score": 0-100总体匹配度,
    "category": "qualified/need_optimization/cannot_apply",
    "reason": "分类的简短理由",
    "missing_items": ["缺失项列表"],
    "optimization_points": ["可优化点列表"]
}}

输出示例:
{{
    "education_match": {{"score": 100, "match": true, "reason": "JD要求本科，简历本科满足"}},
    "experience_match": {{"score": 85, "match": true, "reason": "JD要求3年，简历2.5年，差半年"}},
    "skills_match": {{"score": 75, "matched": ["Python", "Docker"], "missing": ["Kubernetes", "Istio"], "reason": "核心语言匹配，缺容器编排"}},
    "salary_match": {{"score": 90, "match": true, "reason": "18-25K 与期望 20-28K 重叠"}},
    "score": 78,
    "category": "need_optimization",
    "reason": "技能缺Kubernetes和Istio，经验差半年",
    "missing_items": ["Kubernetes", "Istio"],
    "optimization_points": ["补充K8s部署实战经验", "量化项目性能优化数据"]
}}
