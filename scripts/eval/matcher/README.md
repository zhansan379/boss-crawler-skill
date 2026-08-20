# 匹配环节评估（match eval）

> `scripts/eval/matcher/` —— 检验「这个人该不该投这份岗位」判得准不准的一套工具。

`match` 阶段会把每个岗位跟你的简历比对，最后给出一个结论：**投**（`qualified`）／**优化一下再投**（`need_optimization`）／**别投**（`cannot_apply`）。

以前这套判断从没跟「标准答案」对过账——好不好全凭感觉。现在往里塞了一套 **gold（金标准）**：就是人工（或模型）先把每个岗位的正确答案给出来，再拿三种打分方式跟它对比，算出几个数告诉你「现在判得准不准、偏乐观还是偏保守、该往哪儿改」。

## 大白话：这套东西回答什么问题

```
简历：会 Python、会 K8s、3 年经验…
岗位 A：招 Python，要求 3 年经验           → 该投 ✓
岗位 B：招 Java，要求 5 年经验，要硕士    → 别投 ✗
```

gold 出手，先把 A、B 的正确答案标好（投／别投 / 优化，85 分，缺「硕士学历」…）。然后工具把现在系统判的结果跟它比：

1. **判对了多少**（分类）
2. **分数差了多少**（误差）
3. **"该投的排前面没"**（排序）
4. **技能找漏了没**（一个技能没识别到，就白亏一岗）

底下四项指标 = 这四个问题的量化答案。看一遍报告你就能说出「加深度 LLM 到底值不值」。

## 和 `scripts/eval/materials/` 的关系

`scripts/eval/` 管两类评估：

- `materials/` —— 检查「优化后的简历 + 招呼语」**写得好不好**（有没有乱改、漏章节），拿原简历当基准。
- `matcher/`（本目录）—— 检查「匹配环节**判断得对不对**」，拿独立的 gold 当基准。

两边互不干扰：materials 写 `{run_dir}/eval/`，matcher 写 `{run_dir}/eval_matcher/`，能同一个 run_dir 一起跑。

## gold 是从哪来的（三选一）

gold 就是「标准答案」。关键铁律：**gold 绝不能让系统自己打出来**，否则就是自己考自己，不算数。

| 来源 | 命令 | 要联网吗？ | 说人话 |
|---|---|---|---|
| A. 手工 fixture | `--gold-fixtures`（默认就有） | ❌ | 代码里写死了 8 道「送分/刁钻」题：三档边界、薪资门槛、学历门槛、别名（k8s⇄kubernetes）、面议、空技能等。跑一遍就知道规则打分有没有退化。 |
| B. AI 造岗 | `--gold-ai N` | ✅（会存 manifest 供离线重放） | 让模型编 N 个岗位，顺便把每个的标准答案也写上。常用来构造很难的硬门槛案例。 |
| C. LLM 判官 | `--judge-gold` + 选岗位 | ✅（同上） | 对**真实岗位**请模型当判官打分。注意：只给它简历和 JD，**不给**系统自己的打分，防止它抄答案。 |

离线下不撒谎：规则分是纯算数的，天生能离线跑；B/C 联网一次落盘 `gold_manifest.json`，之后 `--offline` 就照着重放，不会再乱花钱调模型。

## 三种打分方式（`--mode`）

| 变体 | 怎么打分的 | 说人话 |
|---|---|---|
| `rule` | 规则分（一定跑） | 就是现在跑匹配的那套规则 `scoring.score_job_advanced()`，原生 0-115 分，对比前折算成百分制。 |
| `deep` | 深度分 | 让 LLM 逐岗打分。既有 `deep_results.json` 可直接读（不调模型），也能在线现算。 |
| `blended` | 混合 | `0.4×规则分 + 0.6×深度分`，要不要投以深度分为准。 |

**有个天生的不对称**：深度/LLM 只会列出「缺什么技能」，列不出「命中什么技能」——所以「技能命中」这项只有规则路径有数，深度/混合路径只比「技能缺失」。报告里会写清楚，不会假装没这回事。

## 四项指标，用大白话逐条讲（`matcher_metrics.py`，纯算数）

### 1. 分类 —— 该投/别投，判对没
- **准确率（accuracy）**：一共 N 个岗位，判对几个。最直观。
- **混淆矩阵**：把「该投的判成别投了」「别投的判成该投了」…… 每种错法记一笔，看错在哪一类。
- **逐类 P（精确率）／R（召回）／F1**：分三类分别算。比如「该投的」里系统真投对的比例、有没有漏掉该投的。
- **classification_gap（裁定向差）**：每类数一下「该有几条 vs 系统判了几条」。多投了 → 偏乐观；少投了 → 偏保守。这是**光看准确率看不出来的倾向性**，特别值得看。

### 2. 分数误差 —— 分打偏了没
- **MAE（平均绝对误差）**：每岗分数差多少，取平均。比如平均差 12 分。
- **RMSE（均方根误差）**：也是误差，但对差得离谱的岗惩罚更重（大误差开平方后更显眼）。
- **bias（偏差）**：带方向的误差。**正的 = 系统性给高分**（过于乐观），**负的 = 系统性打低分**（过于悲观）。

### 3. 排序质量 —— 「最该投的排最前」没
- **nDCG**：按系统打分把岗位从高到低排，看「排前面的岗位是否真的更该投」。完美排序 = 1.0，越接近越好。
- **nDCG@k**：只看前 k 名排得对不对（你大概只会投前几个）。
- **Spearman**：统计上「系统排名」和「gold 排名」的相关程度，-1（完全反着排）到 +1（完全一致）。
- 排序的「好」有两种口径可以选：`category`（只分「该投/优化/别投」三档，稳）或 `score`（用具体分数，更灵敏）。

### 4. 技能命中/缺失 —— 技能露没露
- 把系统识别出的「命中技能」「缺失技能」跟 gold 的对比，算 P／R／F1。
- 引擎会先折叠别名（`k8s`≡`kubernetes`、`es`≡`elasticsearch` 不算错），想精确逐字比就加 `--no-normalize`。
- **drop reason**：深度的「缺什么」如果漏判，岗位明明要 K8s 系统却说「你缺 Git」，那这就是它输给咋回事——看这块就懂了。

> 说白了：**1 看判得对不对，2 看分准不准，3 看该投的靠前没，4 看技能漏没漏。** 四项合起来，一份报告就把匹配环节扒干净了。

## 怎么跑（CLI）

```
python scripts/eval/matcher/evaluate_matcher.py <run_dir> --profile PATH \
    [--gold-fixtures | --gold-ai N | --judge-gold] \
    [--jobs-csv F | --jobs-ai N | --jobs-existing] \
    --mode quick|deep|both（默认 both） \
    [--deep-results P][--k N][--relevance category|score][--no-normalize] \
    [--offline][--out-dir D][--workers N][--llm-recommend]
```

**第一次上手，先跑这行**（全离线、不烧钱，几分钟出报告）：

```bash
python scripts/eval/matcher/evaluate_matcher.py <run_dir> \
    --profile <path>/profile.json \
    --gold-fixtures --mode both --offline
```

- `<run_dir>` 就是你跑爬虫的那个工作目录（改了会有输出，不会覆盖原数据）。
- `--profile` 指向你的 `profile.json`，必填。
- 想连深度分一起看，就用默认的 `--mode both`：一份报告里三变体齐全，直接回答「加 LLM 值不值」。

**要不要联网，取决于你选哪条 gold 路径：**
- `--gold-fixtures` → 全程离线，零成本。
- `--gold-ai` / `--judge-gold` → 第一次要联网（要调模型造岗/当判官），跑完落盘 `gold_manifest.json`；之后再补一个 `--offline` 就能离线重放。

**常见组合：**

```bash
# 纯规则回归（不联网，最省）
python scripts/eval/matcher/evaluate_matcher.py <run_dir> --profile P \
    --gold-fixtures --mode quick --offline

# 已有深度结果文件，不用再调模型
python scripts/eval/matcher/evaluate_matcher.py <run_dir> --profile P \
    --gold-fixtures --mode deep --deep-results <path>/deep_results.json
```

**三条 gold 路线各来一条完整示例（联网全量版 + LLM 综合点评）。**

上面那些默认 `--offline` 是「白嫖存档、只看结果」；这版反过来**全程联网**，把该花的模型钱都花到位：
既走 gold（A 内置免联网，B/C 联网造），又让 `--mode both` 里的深度分在线逐岗调 LLM，最后再
`--llm-recommend` 请模型做一句综合点评。适合第一次认真评估、想看完整深度分 + 点评时用。

```bash
# A. 手工 fixture —— 自带内置简历，不许填 --profile；联网只花在深度打分 + 点评
python scripts/eval/matcher/evaluate_matcher.py <run_dir> \
    --gold-fixtures --mode both --llm-recommend

# B. AI 造岗 —— 联网造 8 个岗位 + 内嵌 gold；深度打分 + 点评也联网
python scripts/eval/matcher/evaluate_matcher.py <run_dir> \
    --profile <path>/profile.json \
    --gold-ai 8 --mode both --llm-recommend

# C. 真实岗位判官 —— 联网 judge；岗位从 CSV/真实集来；深度打分 + 点评也联网
#    （岗位三选一：--jobs-csv F / --jobs-ai N / --jobs-existing）
python scripts/eval/matcher/evaluate_matcher.py <run_dir> \
    --profile <path>/profile.json \
    --judge-gold --jobs-csv <path>/jobs.csv --mode both --llm-recommend
```

B/C 这版是**联网造 gold**，跑完会落盘 `{run_dir}/eval_matcher/gold_manifest.json`；之后想白嫖存档，
把命令换成 `--offline` 并去掉 `--llm-recommend` 即可。注意：`--offline` 会把深度打分一起拦掉，
此时 `--mode both` 若没配 `--deep-results`，深度分会被跳过、只剩规则分（见下）。

**退出码扫一眼就知道咋回事：**
- `0` 全好（个别岗缺 gold 也只会标一句，照样出报告）
- `1` 致命（没简历 / 没岗位 / 没 gold / deep 结果对不上）
- `2` 旗子打架（要联网的 gold 路径撞上 `--offline`，又没有 manifest 可重放）
- `3` 部分失败（个别岗评分挂了 / 缺 deep 排名 / 缺 gold，但报告照常写）

## 报告里能看到啥

产物在 `{run_dir}/eval_matcher/`：`report.html`、`eval.json`、`gold_manifest.json`。HTML 报告里有：

- **每变体的 KPI 卡**：四项指标一个变体一行，扫一眼对比。
- **"这个数低了 — 去改哪" 建议表**：每个阈值都对应到具体的 `(文件, 动作)`。比如：准确率低了 → 提示去 `match_analysis.st` 加硬门槛自检；规则分严重偏低 → 去复查 `scoring.py` 的薪资/经验折算；别名漏判 → 补 `_SKILL_ALIASES`。
- **LLM 综合点评**（加了 `--llm-recommend` 才有）：让模型用一句话点评整体。
- **逐岗核对表**：每个岗位摆出「标准答案 vs 三种打法的判定」，判错的直接高亮。每行还能**展开看详情**：完整 JD、规则六维分与判定理由、AI 判定理由、标准答案依据、技能两侧。想手动抽查，就看这里。
- **简历概览卡**：报告顶部用你的简历列一版技能/经验/学历/期望薪资，方便对着岗位比对。

⚠️ 所有阈值都只是**提示线索，不是结论**——看到信号别直接改，先对着逐岗表确认。

## 文件布局

```
scripts/eval/matcher/
  __init__.py            # 子包命名空间注解
  evaluate_matcher.py    # 唯一入口：走「造 gold → 打分 → 算指标 → 出报告」整条流水线
  matcher_metrics.py     # 四项指标，纯算数、不联网
  matcher_gold.py        # gold 数据结构 + 三种来源加载 + 技能别名归一
  matcher_prompts.py     # prompt 模板加载（造岗 / 判官用）
  matcher_recommend.py   # 汇总 + 「数低改哪」的建议
  matcher_report_html.py # HTML + eval.json 渲染
  templates/{gen_gold, judge_gold, matcher_recommend}.st
```

## 测试

```bash
python -m pytest tests/test_matcher_metrics.py tests/test_eval_matcher.py \
               tests/test_gold_sources.py tests/test_matcher_recommend.py -q
```

全部离线、不联网：纯算数指标、8 条 fixture 端到端（规则应做到 accuracy=1.0）、manifest 来回读写、判官映射、阈值建议，连故意做错的样例 / 退位码 / 深度回填都覆盖到了。