"""scripts/eval/matcher/：岗位匹配（match）环节的评估子系统。

`match` 把每个岗位映射到候选简历并裁定投不投（application_category）。这里是给它
配一把「尺子」：gold（金标准）独立于规则分与深度分两条预测路径，再对分类 / 分数 /
排序 / 技能四族指标做比较，产出 HTML 报告 + 阈值→文件/代码位建议。

与 sibling 的 materials（材料质量评估）互补：那边拿原简历当基线衡量「优化简历+招呼语」
的产出质量；这边拿独立 gold 衡量「匹配裁定」对不对。两套工具可写同一 run_dir，
产物分别落在 {run_dir}/eval/{...} 与 {run_dir}/eval_matcher/{...}。
"""