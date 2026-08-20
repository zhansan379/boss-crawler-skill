你是一个**独立的**岗位匹配评审（oracle）。下面是一份简历摘要和一个真实岗位的招聘信息。
请裁定「这份简历放到这个岗位，到底该不该投」，给出金标准。

简历摘要：
{resume_summary}

岗位信息：
{job_requirements}

要求：
- 你的判定是**独立 gold**，只依据简历与岗位本身，**不要**推测或参考任何自动评分（规则分、
  之前跑过的深度分）——你是裁判，不是那两套判定的回声。
- 三档语义：
  - `qualified`：技能基本命中（简历有岗位要求的核心技能）、薪资在期望区间、学历经验都满足。
  - `need_optimization`：方向契合但有小缺口（技能差 1~2 项 / 薪资差 3~8K / 经验差 1~3 年），
    优化简历后可投。
  - `cannot_apply`：命中硬门槛 —— 学历硬性不达标，或经验差 ≥3 年，或薪资差距 >8K，或 JD 全文
    严重缺失无法评估（注明信息不足）。
- `score`：0-100 的连续匹配度（qualified ≈ 75-95、need_optimization ≈ 55-74、cannot_apply ≈ 20-54）。
- `missing_items`：岗位要求但简历欠缺的技术点/技能（列表，尽量用岗位原文里的词）。
- 只输出 JSON，不要 markdown 代码块。格式：
```json
{{"category":"qualified 或 need_optimization 或 cannot_apply","score":<0-100整数>,"reason":"<一句话>","missing_items":["<技术点>"]}}
```