# 匹配环节评估（match eval）

> `scripts/eval/matcher/` —— 度量「匹配环节对不对」的评估子系统。

`match` 阶段把每个岗位映射到候选简历并裁定投不投（`application_category`：
`qualified` / `need_optimization` / `cannot_apply`）。此前这套判定没有任何 ground-truth 参照；
本子系统引入**独立的 gold（金标准）**，把规则打分 / 深度 LLM 打分 / 混合三种预测路径
逐一与 gold 对比，算出四族指标并出报告，回答「匹配做得对不对、该往哪儿调」。

## 与 `scripts/eval/materials/` 的关系

`scripts/eval/` 是两类评估的公共命名空间：

- `materials/` — 评估「优化简历 + 招呼语」的**产出质量**（幻觉/删改/章节等），拿原简历当基线。
- `matcher/`  — 评价**匹配正确性**（本子系统），拿独立 gold 当参照。

两者产物目录不冲突：materials 写 `{run_dir}/eval/`，matcher 写 `{run_dir}/eval_matcher/`，
可跑在同一 run_dir。

## gold 三来源

每样本 = `{ link, job, gold:{category, score, matched_skills, missing_skills, reason, source} }`。
关键不变式：**gold 是独立 oracle，绝不由 `decide_application_category` 得到**，否则评估循环无意义。
`gold.score` 恒为 0-100（与 deep / blended / rule·/115*100 同刻度）。

| 来源 | 触发 | 离线？ | 说明 |
|---|---|---|---|
| A. 手工 fixture | `--gold-fixtures`（默认恒有） | ✅ | `HAND_FIXTURES` 8 条回归角点：三档边界、薪资硬门槛、学历硬门槛、别名（k8s⇄kubernetes、es⇄elasticsearch）、面议、空技能/缺 JD。 |
| B. AI 造岗内嵌 | `--gold-ai N` | ❌（落 `gold_manifest.json` 供 `--offline` 重放） | 让模型造 N 个跨三档岗位，每岗同时输出相对简历的 gold；刻意构造硬门槛。 |
| C. LLM judge | `--judge-gold` + `--jobs-csv/--jobs-ai/--jobs-existing` | ❌（同上） | 对真实岗位做独立 LLM 判分（oracle 措辞，**不给**规则/深度分，防回声）。 |

离线确定性：规则打分纯函数 → `rule` 天然离线；AI/judge 触网并落 `gold_manifest.json`，
`--offline` 时重放、绝不重生。

## 三条预测路径

| 变体 | 打分 | 说明 |
|---|---|---|
| `rule` | 规则分（恒跑） | `scoring.score_job_advanced()`，0-115 原生、比较前 `/115*100` 归一。 |
| `deep` | 深度分 | `--deep-results` 读既有 `deep_results.json`（无 LLM 调用），或在线逐岗 `chat_json`。 |
| `blended` | 混合 | `int(0.4·(rule/115*100) + 0.6·deep)`，分类以 deep 为准。 |

**固有不对称**：deep/LLM 只产 `missing_items`，不产 `matched_skills` —— 因此 `skills.matched`
仅对规则路径计算，deep/blended 只比 `missing`。报告会写清，而非静默留空。

## 四族指标（`matcher_metrics.py`，纯函数）

1. **分类** — 准确率 + 混淆矩阵 + 逐类 P/R/F1 + `classification_gap`（暴露系统性过/欠预测）。
2. **分数误差** — MAE / RMSE / 有符号 bias（正=偏高，负=偏低）+ 逐样本明细。
3. **排序质量** — nDCG / nDCG@k / Spearman；`relevance=category`（序数，稳健）或 `score`（连续，灵敏）。
4. **技能命中/缺失** — matched / missing 的 P/R/F1；别名经 `_normalize_skill` 折叠，normalize=False 则精确匹配。

## CLI

```
python scripts/eval/matcher/evaluate_matcher.py <run_dir> --profile PATH \
    [--gold-fixtures | --gold-ai N | --judge-gold] \
    [--jobs-csv F | --jobs-ai N | --jobs-existing] \
    --mode quick|deep|both (默认 both) \
    [--deep-results P][--k N][--relevance category|score][--no-normalize] \
    [--offline][--out-dir D][--workers N][--llm-recommend]
```

建议先 `--mode both` 在一份报告里看三变体：直接回答「加 LLM 是否提升分类/排序」。

**退出码**：`0` 全好（部分缺 gold 仅标注）；`1` 致命（无 profile/岗位/gold、deep 缺结果）；
`2` 旗冲突（`--offline` + 需触网的 gold 来源且无 manifest）；`3` 部分失败（个别岗评分失败/
缺 deep rank/缺 gold，报告仍写）。

## 报告 & 建议

`{run_dir}/eval_matcher/{report.html, eval.json, gold_manifest.json}`。report.html 含
逐变体 KPI 卡、阈值→具体 `(文件, 动作)` 建议表（如：accuracy 低 → 提示 `match_analysis.st`
加硬门槛逐项自检；规则 bias 强负 → 复核 `scoring.py` 的薪资/经验折算；别名漏判 → 补
`_SKILL_ALIASES`）、LLM 综合点评、逐岗核对表（错判高亮）。所有阈值都是启发读数，不是结论。

## 文件布局

```
scripts/eval/matcher/
  __init__.py            # 子包命名空间注解
  evaluate_matcher.py    # 唯一 CLI 入口（编排 来源→预测→指标→聚合→报告）
  matcher_metrics.py     # 纯函数指标层（四族，不触网）
  matcher_gold.py        # gold 数据模型 + 三来源加载 + 技能归一
  matcher_prompts.py     # 模板加载 + prompt 构建
  matcher_recommend.py   # 聚合 + 阈值→文件/代码位建议
  matcher_report_html.py # HTML + eval.json 渲染
  templates/{gen_gold, judge_gold, matcher_recommend}.st
```

## 测试

```
python -m pytest tests/test_matcher_metrics.py tests/test_eval_matcher.py \
               tests/test_gold_sources.py tests/test_matcher_recommend.py -q
```

全部离线、不触网：纯函数指标、8 条 fixture 端到端（rule 应 accuracy=1.0）、manifest
roundtrip、judge 映射、阈值建议。刻意错例/退位码/深度回填也在测试内覆盖。