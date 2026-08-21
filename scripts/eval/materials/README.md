# 材料环节评估（materials eval）

> `scripts/eval/materials/` —— 检验「AI 帮你改的简历 + 写的招呼语」到底靠不靠谱的一套工具。

投岗前，skill 会给每个岗位生成两样东西：**一句招呼语**（发给 HR 的第一条消息）和**一份按这个岗位改过的简历**。这两样都是模型写的，模型写东西就有个老毛病：**替你吹**。

它可能给你加一项你压根不会的技能，可能把「了解 Redis」改成「精通 Redis」，可能把你简历里的「教育背景」整章删掉，还可能替你答应「每周可到岗 4 天」——而你简历里根本没写过这句。这些东西发出去，轻的是面试被问穿，重的是骗人。

这套工具就是在**东西发出去之前**，把每份材料跟你的原简历逐字对一遍，把「哪句是你自己的、哪句是照着岗位要求靠上去的、哪句是它凭空编的」分清楚。

## 大白话：这套东西回答什么问题

```
你的原简历：会 Python、了解 Redis、3 年经验、有「教育背景」章节
岗位 JD：招 Python + Kubernetes

AI 改完的简历：
  会 Python              → 你原来就有            ✅ 保留
  熟悉 Kubernetes         → 岗位要求，往上靠了    ✅ 岗位驱动（合理）
  精通 PyTorch           → 你没有，JD 也没提      🟥 凭空编的（幻觉）
  精通 Redis             → 原文写的是「了解」      ⚠ 偷偷升级了
  （教育背景章节不见了）                          🟥 删了原文内容
```

**判定标准很简单，就一句话：一个词要么在你原简历/profile 里出现过，要么在这个岗位的 JD 里出现过；两边都没有，就是它自己编的。** 这叫「白名单式判定」，跟仓库里 `verify_no_fabrication` 用的是同一套口径，不搞两套标准。

关键点：**这套评估不需要标准答案。** 你的原简历就是基准——比对象是「改之前 vs 改之后」，所以它天生能离线跑、纯算数、不花钱。

## 和 `scripts/eval/matcher/` 的关系

`scripts/eval/` 底下管两类评估，别搞混：

| | 管什么 | 拿什么当基准 |
|---|---|---|
| `matcher/` | 「这个岗**该不该投**」判得对不对 | 独立的 gold（标准答案），得人工或模型先标 |
| `materials/`（本目录） | 「材料**写得好不好**」有没有乱改乱编 | 你的**原简历**，不需要外部标签 |

两边互不干扰：materials 写 `{run_dir}/eval/`，matcher 写 `{run_dir}/eval_matcher/`，同一个 run_dir 可以一起跑。

## 六个维度，用大白话逐条讲（`metrics.py`，纯算数、不联网）

### 1. 字符级 diff —— 删了多少、加了多少

拿原简历和改后简历逐字比（先去掉所有空白），算三个数：

- **删除率**：原文有多少字被删掉了（分母是原文长度）
- **新增率**：改后有多少字是新加的（分母是改后长度）
- **原文覆盖率** = `1 - 删除率`，就是「原文还剩多少 %」。**这个数低了就是删狠了**。

⚠ 删除率和新增率**分母不一样，不能相加**。

### 2. 术语级三分类 —— 每个技术词的来路查清

把改后简历里的每个技术术语挨个过筛，分三堆：

- **保留**：原简历/profile 里有过 → 你自己的，没问题
- **岗位驱动**：原文没有，但 JD 里有 → 往岗位上靠，这是**合理**的优化
- **无据（幻觉）**：两边都没有 → **凭空编的，这是硬伤**

主看的数是 `hallucination_pct = 无据 / 全部术语数`。报告里会把每个无据词连**上下文**一起列出来，你能直接看到它在哪句话里编的。

还有个次口径 `fabrication_within_new`：只在「新增的词」里算无据比例（分母去掉保留项），想看「它新加的东西有几成是瞎编的」就看这个。

**两种判词来源（默认走 LLM，`--offline` 才离线）**：

- **LLM 语义分类（默认）**：模型同时看「原简历 + 优化稿 + JD」逐词打 `retained / jd_driven / unfounded`，每个词带**理由**，还顺带输出一句「这一岗优化得健康吗」的人话总结。认得出 k8s⇄Kubernetes、能把「了解→精通」这种抬高当问题点出来（理由里单独提示）。**要联网配 key**；结果落盘到 `{run_dir}/eval/term_classify_cache.json` 缓存，跑过一次再跑（含 `--offline`）直接命中，不重复烧钱——从头到尾的默认都是这个（`--terms-llm` 只是显式重申，实际已是默认）。没配 key 或没缓存的岗自动回退规则，报告里那岗标「规则分类」照出不崩。
- **纯规则（`--offline` / 无缓存兜底）**：白名单式比对——优化稿的词在「原简历 ∪ JD」里出现过就放，没出现过就报。**离线、确定、不花钱**，但认不出语义等价（同义词、缩写、技能强弱），语言模型能一眼看穿的东西它照报。只在显式 `--offline` 或 LLM 不可用时出现。

不管哪路，报告里都能看到具体词，不只比例：
- **术语堆叠条**下方每 bucket 一行**具体术语标签**（🟩保留 / 🟦岗位驱动 / 🟥无据各一行，词直接列出来）。
- **「优化前 vs 优化后」折叠面板**：左原简历、右优化稿，**每个术语按来路着色**（规则、LLM 都着）；**悬停看理由**（LLM 是模型给的 reason，规则是那词所在的上下文）；LLM 在面板顶部多一句整岗点评，右上角标「LLM 语义分类」vs「规则分类」。

### 3. 新增块检查 —— 有没有凭空造数字

diff 出所有「新增的整段」，逐段查两件事：

- 段里有**无据术语**吗？
- 段里的**数字原文出现过吗**？没出现过 → 疑似凭空造量化成果（「性能提升 30%」这种）

这一项是**规则启发，不是结论**，报告里标「人工复核」。因为「3 年经验」改写成「三年」也可能被算成新数字。

### 4. 招呼语质量 —— 四道关

- **前 15 字有没有被客套话占掉**：HR 在消息列表里**只看得见前 15 个字**。开头写「您好，我是……」等于这条消息白发了。判定词表和实际发消息的 `auto_apply` 逐字一致。
- **有没有量化**：招呼语里有数字（年限、规模、指标）比空口说更容易被回。
- **有没有无据技术词**：口径同上，但招呼语这里只算**风险提示**——点名 JD 里的词是正常操作。
- **有没有编造到岗承诺**：⚠ **这一条是硬伤，不是提示。** 「可到岗」「每周 X 天」「可实习 N 个月」这类是 HR 会**据此安排面试和入职**的承诺，只能从你简历里照搬。如果你简历里根本没写这些（`format_availability` 为空），招呼语里却蹦出来了，就是替你瞎答应。这一层 `verify_no_fabrication` 抓不到，只有这里能抓。

### 5. 章节保真 —— 有没有整章丢掉

抓 Markdown 的二级标题（`## 开头`），归一化别名后（「技能」≡「专业技能」、「教育」≡「教育背景」）比对：**原简历有、改后没有的章节 = 被删了**。

生成侧本来就有自检重试（`_APPLY_RETRY`/`_PLAN_RETRY` 缺章会自动重试），所以这里还能报缺，说明自检漏了。

### 6. 客观性 / 夸大 —— 有没有偷偷抬级别（启发）

- **能力升级**：改后出现「精通/熟练掌握」，而原文对应位置写的是「了解/接触/入门」。
- **绝对化用语**：新增了「行业领先/顶尖/第一/最优」这类。

这一项只做**粗粒度计数**，是纯正则粗判，误报正常。要「结论级」的判断得靠 `--llm-recommend` 那次模型点评。不想看就 `--no-subjective` 关掉。

> 说白了：**1 看删没删狠，2 看词是哪来的，3 看数字有没有编，4 看招呼语能不能发，5 看章节丢没丢，6 看有没有替你吹。**

## 岗位从哪来（三选一）

评估要有岗位才能生成材料。三条路：

| 来源 | 命令 | 要联网吗？ | 说人话 |
|---|---|---|---|
| A. 复用既有 | `--jobs-existing`（默认） | ❌ | 直接读 run_dir 里跑过的 `state/qualified_jobs.json`，最省事。 |
| B. 本地 CSV | `--jobs-csv FILE` | ❌ | 读爬虫导出的中文列 CSV，自动筛掉已失效、按 link 去重（同 link 保 JD 更全那行）。 |
| C. AI 造岗 | `--jobs-ai N` | ✅ | 让模型编 N 个多样岗位，`--jobs-ai-spec` 可以补充多样性要求。**`--offline` 下会自动降级**成复用既有，不会偷偷联网。 |

岗位字段只留白名单那 8 个（`link/公司/职位/薪资/经验/学历/技能标签/岗位要求和职责`），多余字段全丢——不然会污染 `jd_keys` 的基线判断，让本该报的幻觉被放过。

## 两种跑法（关键区别）

### 跑法一：只评估已经生成好的材料（默认，不花钱）

run_dir 里已经跑过 `gen_materials`、`materials/` 目录有东西了，就直接评。**六维指标这五维（字符/新增块/招呼语/章节/客观性）永远离线本——只读文件 + 算数**。术语三分类**默认走 LLM（联网配 key，有磁盘缓存）**，`--llm-recommend`（整份点评）也是可选的联网项。只有显式 `--offline` 才会全程不触网：术语分类降级为「只读缓存，没缓存回退规则」、点评跳过；六维那五维无论哪种都离线。

### 跑法二：`--generate` 先生成再评估

现场生成材料再评。这里又分两种：

- **不带 `--offline`（真调，花钱）**：走 skill 原本那套逻辑（`gen_greeting`/`gen_resume`），真调模型。这是**真正在测生产链路的质量**。
- **带 `--offline`（stub，不花钱）**：生成换成 `stubs.py` 里的确定性规则产物，作为**干净对照组**：
  - 招呼语用保守模板，不含任何技术术语、不以客套话开头、简历没写到岗就绝不提 → 四道关应该全绿。
  - 简历只往「专业技能」加一行「JD 里有、原文没有」的驱动词，**绝不新增两边都没依据的词** → `hallucination_pct` 应该 ≈ 0。

  所以 stub 跑出来的数字是**参照系**：干净对照都不是 0，那是评估器本身有 bug；真跑比 stub 差得多，那才是生成质量的问题。同一份输入永远产出同一份结果（无随机），`--stub-registry` 能把产物落盘回放。

## 怎么跑（CLI）

```
python scripts/eval/materials/evaluate_materials.py <run_dir> \
    [--resume FILE] \
    [--jobs-existing | --jobs-csv F | --jobs-ai N [--jobs-ai-spec TEXT]] \
    [--generate][--offline][--stub-registry PATH] \
    [--llm-recommend][--force-llm-terms][--no-subjective] \
    [--out-dir D][--workers N]
```

**第一次上手，先跑这行**（加 `--offline` 才是全程零成本、几秒出报告；术语分类回退规则/缓存）：

```bash
python scripts/eval/materials/evaluate_materials.py <run_dir> --jobs-existing --offline
```

- `<run_dir>` 是你跑爬虫的工作目录。缺 `state/` 会自动搭，**不会覆盖既有数据**。
- `--resume FILE` 指定简历（md/txt），会写成 `state/resume_text.txt`；不给就复用既有的。**没有简历文本就没法评**（原简历是基准），会直接退 1。
- 缺省命令（不带 `--offline`）术语三分类走 **LLM**（联网配 key，结果缓存）——这是默认行为，不用加 flag。

### 参数速查表

| 参数 | 作用 | 触网？ | 说明 |
|---|---|---|---|
| `<run_dir>` | 工作目录 | ❌ | 爬虫产物所在目录；缺 `state/` 会自动搭，**不覆盖既有数据** |
| `--resume FILE` | 指定原简历 | ❌ | md/txt，写成 `state/resume_text.txt`；不给就复用既有的。**没简历文本就没法评**（原简历是基准），会退 1 |
| `--jobs-existing` | 复用既有岗位 | ❌ | 直接用 `state/qualified_jobs.json`，最省事 |
| `--jobs-csv FILE` | 岗位读本地 CSV | ❌ | 中文列岗位表，离线可用；自动筛已失效、按 link 去重 |
| `--jobs-ai N` | AI 造 N 个岗位 | ✅ | 让模型编 N 个多样岗位；`--offline` 下自动降级复用既有，不偷偷联网 |
| `--jobs-ai-spec TEXT` | 造岗多样性补充 | ✅ | 搭配 `--jobs-ai` 用 |
| `--generate` | 先生成材料再评估 | 视模式 | 先跑 `gen_greeting`/`gen_resume`；`--offline` 下换成确定性 stub（干净对照） |
| `--offline` | 全程不触网 | ❌ | 生成→stub；术语分类→只读缓存/规则兜底；`--llm-recommend` 会跳过并报冲突 |
| `--stub-registry PATH` | stub 产物落盘 | ❌ | 存成 registry 文件可回放/对拍 |
| `--llm-recommend` | 追加整份点评 | ✅ | 评估完再调模型总结一段话健康度；失败不致命，只少这一块 |
| `-w / --workers N` | 生成并发线程数 | — | 默认 4 |
| `--terms-llm` | 术语分类走 LLM | ✅ | **已默认**——只有 `--offline` 才强制离线；保留这个开关只是为了兼容旧命令 |
| `--force-llm-terms` | 忽略分类缓存强刷 | ✅ | 强制重新调 LLM 分类，不读缓存 |
| `--no-subjective` | 关「客观性/夸大」维 | ❌ | 不想看能力升级/绝对化提示就加 |
| `--out-dir D` | 报告输出目录 | — | 默认 `<run_dir>/eval/` |
| `--model / --base-url / --api-key` | 覆盖模型配置 | ✅ | 临时指定 LLM 配置，优先于 `.env` |

**几个易混点：**
- `--offline` = 把「钱的调用」全关掉：生成、造岗、术语分类、点评都不碰网。剩下六维那五维本来就纯算数。
- `--llm-recommend` 和 `--terms-llm` 是**两回事**：前者给一段整份点评，后者把术语三分类换成 AI 判词。`--offline --llm-recommend` 会**报冲突退 2**（因为点评必须调模型）。
- 术语分类默认走 LLM 是「开」的，`--offline` 才关；所以想省事不联网，就在命令里明确写 `--offline`。

**常见组合：**

```bash
# 1) 默认 —— 术语三分类走 LLM 语义判词（联网、带缓存），越跑越快
python scripts/eval/materials/evaluate_materials.py <run_dir> --jobs-existing

# 2) 全程离线 —— 零成本，术语分类读缓存/规则兜底；也可当干净对照组基准
python scripts/eval/materials/evaluate_materials.py <run_dir> --jobs-existing --offline

# 3) 干净对照组 —— stub 生成 + 评估，验证评估器本身没坏（幻觉应≈0）
python scripts/eval/materials/evaluate_materials.py <run_dir> \
    --resume <path>/简历.md --jobs-csv <path>/jobs.csv \
    --generate --offline --stub-registry /tmp/stub.json

# 4) 真调全量 —— AI 造 8 个岗 + 真生成 + LLM 综合点评（花钱，测生产链路）
python scripts/eval/materials/evaluate_materials.py <run_dir> \
    --resume <path>/简历.md --jobs-ai 8 --generate --llm-recommend -w 4
#    分类缓存重复跑直接命中不重调模型；--force-llm-terms 强刷缓存
```

**退出码扫一眼就知道咋回事：**

- `0` 全好
- `1` 致命（没简历文本 / 没岗位来源 / 报告写不出来）
- `2` 旗子打架（`--offline` 撞上 `--llm-recommend`——点评必须调模型）
- `3` 部分缺产物（个别岗位没生成出招呼语或简历，报告照常写，那几岗标 `missing`、六维不做判定、也不参与平均值）

## 报告里能看到啥

产物在 `{run_dir}/eval/`：`report.html` + `eval.json`。HTML 里有：

- **6 张 KPI 卡**：平均无据术语、岗位驱动新增、原文保留、章节缺失、前 15 字客套、编造到岗承诺。扫一眼定性。
- **术语堆叠条**：每个岗一行三色条（🟩保留 / 🟦岗位驱动 / 🟥无据）+ 三行**具体术语标签**，一眼看出哪个岗被编得最多、具体编了哪些词。
- **优化前 vs 优化后 · 逐词对照**：每岗一个折叠面板，左栏原简历、右栏优化稿，**每个术语按来路着色**（规则、LLM 都着）、**悬停看理由**（LLM 给模型 reason，规则给那词的上下文），顶部是这一岗的整句点评（仅 LLM），右上角标「LLM 语义分类」vs「规则分类」。
- **字符 diff 表**：逐岗的删除% / 新增% / 原文覆盖% / 字数变化。
- **无据术语表**：每个凭空词 + **它出现的上下文**。这是最该逐条看的一块。
- **新增块表**：疑似编造的新增片段（标了「人工复核」）。
- **招呼语卡片**：每条的前 15 字预览（带光标模拟 HR 视角）+ 徽章（前15字客套 / 编造到岗承诺 / 有量化 / 无据词 N / 干净）。
- **「数不好看 — 去改哪」建议表**（`recommend.py`）：每个阈值对应到具体的 `(文件, 动作)`。比如：幻觉高了 → 去 `prompts/resume_optimize_apply.st` 加白名单硬校验；保留率低了 → 去 `gen_materials.py` 把保留率做成计划①的校验项；前 15 字客套 → 去 `scripts/prompts/greeting.st` 或加宽 `_WASTED_OPENERS`。按 high/medium/low 排序。
- **LLM 综合点评**（加了 `--llm-recommend` 才有）：把六维聚合结果丢给模型，让它一段话点评。失败不致命，只少这一块。

⚠ 所有阈值都只是**提示线索，不是结论**——看到信号先去逐岗表和无据术语表确认，别直接改 prompt。

## 一个容易踩的坑

`optimization_suggestions` 字段**故意不查**。那个字段的本职工作就是「告诉你这份简历还缺哪些技能」，里面出现你不会的技能是**正常的**——去查它等于制造一堆误报。这跟 `verify_no_fabrication` 的口径一致：**只查会真正发出去的东西**（`optimized_resume` 和 `greeting`）。

## 文件布局

```
scripts/eval/materials/
  __init__.py             # 子包命名空间注解
  evaluate_materials.py   # 唯一入口：走「准备 state → 取岗位 → 生成材料 → 六维评估 → 报告」整条流水线
  metrics.py              # 六维指标。除术语分类外的五维纯算数、不联网、不 import llm
  terms_llm.py            # 术语三分类的 LLM 语义分类器（唯一 import llm 的落桶点）+ 磁盘缓存
  gen_test_jobs.py        # 三种岗位来源（AI 造 / CSV / 复用既有）+ 字段白名单归一
  stubs.py                # offline 确定性产物（干净对照）+ registry 回放 + 触网即炸的防御桩
  recommend.py            # 六维聚合 + 阈值→「改哪个文件」建议 + 可选 LLM 点评
  report_html.py          # HTML + eval.json 渲染 +「优化前 vs 优化后」对照面板
  prompts.py              # prompt 模板加载（造岗 / 评审 / 术语分类用）
  templates/{gen_test_jobs, llm_recommend, eval_terms}.st
```

⚠ **同源复制警告**：`metrics.py` 里的 `_extract_chapters`/`_norm_chapter`（抄自 `scripts/stages/gen_materials.py`）和 `preview`/`has_wasted_preview`（抄自 `scripts/resume_matcher/auto_apply.py`）是**逐字复制**的——因为那两个文件依赖 `llm`，不能在纯函数层 import。**原处一改，这里必须同步改**，不然评估口径和生成口径就打架了。

## 测试

```bash
python -m pytest tests/test_eval_metrics.py tests/test_eval_flow.py -q
```

全部离线、不联网：

- `test_eval_metrics.py` —— 六维纯函数单测，别名（`k8s`⇄`kubernetes`）这类容易误报的地方单独验；`terms_source='llm'` 直接采用分类器 terms dict、规则路径无 `mode/analysis`。
- `test_eval_flow.py` —— 端到端。关键断言：把模型调用 monkeypatch 成**必炸**，跑通即证明离线评估真的没触网；往优化稿里故意注入 `PyTorch`/`nginx`/`CrewAI` 的「臆造岗」幻觉率必须 > 0，stub 干净岗必须 == 0；stub registry 回放一致；monkeypatch `classify_all` 喂确定性三桶，断言 `report.html` 的「优化前 vs 优化后」对照面板 + 悬停理由 + 三类着色、无缓存岗回退规则。
