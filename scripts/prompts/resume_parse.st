请解析以下简历内容，提取结构化信息。保持真实性，不编造内容。尽可能提取所有细节，包括量化指标、技术架构细节、获奖情况等。

简历内容:
{resume_text}

请以JSON格式返回以下信息（不要包含任何解释文字，直接返回JSON）:
{{
    "basic_info": {{
        "name": "姓名",
        "phone": "电话（如有）",
        "email": "邮箱（如有）",
        "gender": "性别（男/女）",
        "age": "年龄（数字）",
        "expected_city": "期望城市",
        "expected_position": "期望职位"
    }},
    "education": {{
        "school": "最高学历学校名称",
        "degree": "学历等级（博士/硕士/本科/大专/高中）",
        "major": "专业",
        "start_year": "入学年份",
        "graduation_year": "毕业年份"
    }},
    "experience": {{
        "total_years": "工作总年限（数字）",
        "companies": [
            {{
                "name": "公司名",
                "position": "职位",
                "duration": "起止时间（如 2025.07-2025.10）",
                "type": "类型（实习/全职/兼职）",
                "description": "工作概述（一句话总结）",
                "highlights": ["工作要点1（保留原始量化指标和具体细节）", "工作要点2", ...]
            }}
        ]
    }},
    "awards": [
        {{
            "name": "奖项名称",
            "level": "级别（国家级/省级/市级/校级）",
            "rank": "奖项等级（一等奖/二等奖/三等奖/金奖等）",
            "year": "获奖年份"
        }}
    ],
    "publications": [
        {{
            "title": "论文/期刊标题",
            "journal": "发表期刊/会议名称（如有）",
            "year": "发表年份"
        }}
    ],
    "social_links": {{
        "github": "GitHub地址（如有）",
        "blog": "博客/CSDN地址（如有）",
        "other": ["其他链接（如有）"]
    }},
    "skills": {{
        "programming": ["编程语言列表"],
        "frameworks": ["框架列表"],
        "tools": ["工具列表"],
        "other": ["其他技能"],
        "summary": "技能描述段落（简历中关于专业技能的文字描述，保留原文）"
    }},
    "projects": [
        {{
            "name": "项目名",
            "description": "简短概述",
            "highlights": ["项目要点1（保留原始量化指标、架构细节、性能数据）", "项目要点2", ...],
            "tech_stack": ["技术栈"],
            "url": "项目链接（如有）",
            "duration": "项目时间（如 2025.01-2025.05）"
        }}
    ],
    "salary_expectation": {{
        "min": "最低期望薪资（数字，单位K）",
        "max": "最高期望薪资（数字，单位K）"
    }},
    "keywords": ["简历核心关键词列表"]
}}

重要提示：
- **highlights 字段**：不要合并要点。保留原始简历中的每条独立要点，保留所有量化指标（百分比、秒数、数量等）和具体技术名词。
- **awards**：捕获所有获奖信息，包括竞赛、奖学金等。
- **publications**：捕获所有学术发表、期刊论文。
- **social_links**：提取所有个人主页链接。
- **skills.summary**：保留简历中关于技能描述的原文段落，不要拆成标签。
- 缺失信息填 null 或空数组 []。请准确提取，不要编造。