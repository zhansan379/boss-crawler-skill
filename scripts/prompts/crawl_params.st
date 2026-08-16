你是求职参数配置专家。根据下面的简历结构化信息，推断 BOSS 直聘的爬取与匹配参数。

今天的日期：{today}

简历信息：
{resume}

请只输出一个 JSON 对象，字段如下（不确定的字段填 null，**不要编造**）：
{{
  "keywords": ["搜索关键词，{kw_budget} 个以内"],
  "cities": ["目标城市中文名"],
  "mode": "custom 或 list（关键词搜索用 custom）",
  "count": 每个关键词每个城市最多爬多少条（整数，20-50 之间比较合适）,
  "match_mode": "quick 或 deep",
  "top_n": deep 模式下深度分析前几个候选（整数，10-20）,
  "min_count": 认为「爬到的岗位够用」的最低条数（整数，约 10）,
  "degree": ["学历筛选"],
  "experience": ["经验筛选"],
  "salary": ["薪资档位"],
  "job_type": ["工作类型"],
  "reasoning": "两三句话说明关键词和筛选是怎么定的，其中必须写清 job_type 是怎么定的"
}}

硬性约束：
1. keywords 必须是**不同的概念**，不是同义词。「AI应用开发」和「大模型应用开发」算同一个，
   只保留一个。宁可少给，也不要用同义词占满预算。
2. cities 优先用简历里的 basic_info.expected_city；没有就用 basic_info.city。
3. 以下四个字段只能从给定枚举里选，写别的值等于筛不出岗位：
   - salary: {salary}
   - experience: {experience}
   - degree: {degree}
   - job_type: {job_type}
4. salary 取比期望**略低**的档位以扩大覆盖面（期望 25-35K → "20-50K"）。
5. degree 向下兼容：硕士 → ["本科","硕士"]；本科 → ["本科"]。
6. **先用今天的日期算出「现在还在不在校」，再定 experience 和 job_type。**
   拿 {today} 跟 education.graduation_year（毕业年份）比，不要只看简历里有没有出现
   「实习」两个字 —— 简历是往届写法还是在校写法，光看措辞分不出来：
   - 毕业年份 > 今年 → 在校 → experience ["在校生"]、job_type ["实习"]
   - 毕业年份 == 今年 → 再比月份（国内毕业季按 6 月算）：今天在 7 月之前算在校，
     取 ["在校生"] + ["实习"]；7 月及以后算已毕业，取 ["应届生"] + ["全职"]
   - 毕业年份 < 今年但不满一年 → experience ["应届生"]、job_type ["全职"]
   - 毕业年份 < 今年且已超过一年 → 按 experience.total_years 选档位，可带上低一档
     （如 5 年 → ["3-5年","5-10年"]），job_type ["全职"]
   - 简历明确表达了在找实习（basic_info.expected_position 里带「实习」、
     basic_info.availability.duration 给了可实习时长）→ 不看日期，一律 ["实习"]。
     求职意图优先于日期推算。
   - 只有毕业年份缺失**且**看不出实习意图时，job_type 才按 ["全职"] 兜底。job_type
     不要填 null：null 等于不筛，实习岗和全职岗会混在同一批结果里回来，后面的匹配
     打分分不开这两种岗。experience 实在无从判断可以填 null。
7. 不要输出 scale（公司规模）—— 简历里没有任何信息能推出这一项。
