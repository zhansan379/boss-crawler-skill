#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""scripts/eval/：招呼语生成 + 简历优化 质量评估工具。

让用户上传真实简历 + 一组多样岗位，按 skill **原本逻辑**（复用 gen_materials 的
gen_greeting / gen_resume）批量生成招呼语与优化简历，再对产物做多角度质量评估
（幻觉 / 删除 / 新增 / 章节保真 / 招呼语质量 / 客观性），输出 HTML 报告 + JSON，
并给出提示词优化逻辑建议。

与既有投递链路的关系：**纯新增、只读复用**。不改动 SKILL.md、阶段脚本或 prompts
模板；生成走 gen_materials 的原本逻辑，幻觉检测走 verify_no_fabrication 的纯函数，
评估只读产出物进行分析。
"""