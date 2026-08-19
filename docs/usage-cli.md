# 方式二：命令行上手（SKILL.md 九阶段逐条命令）

> `README.md`「使用方式 → 方式二」的展开。命令行与 Skill 用**同一套脚本、同一个模型接口和数据产物**，区别只在谁来问你——Skill 在路径选择、参数确认、`gate:jobs`、`gate:send` 各有一次 `AskUserQuestion`，命令行把这些换成显式 flag。
>
> 本文按 **SKILL.md 的工作流逐阶段列出“用哪个命令、传哪些参数”**。阶段顺序：`parse → infer → crawl → match → deep → merge → materials → verify → render`（投递 `apply` 单独一步）。每个脚本都有 `--help`。
>
> **只驱动一个阶段。** `pipeline.py` 不带 `--to` 时只跑 `--from` 那一个阶段就停下并打印下一条命令。整轮跑到底才给 `--to render`——它停在「材料已生成」，投递是另一条命令。**别为了“先跑跑看”就 `--to render` 一口气跑完**：`deep` 逐岗位调一次模型、`materials` 逐岗位调两次，这道闸门正是为了不把 token 花在你还没看到的岗位列表上。

## 退出码（所有脚本共用，判定失败前先读）

| 码 | 含义 | 该怎么处理 |
|---|---|---|
| `0` | 成功 | 继续 |
| `1` | 前置条件未满足 / 输入缺失 / 全部失败——通常什么都没写 | 停下，排查 |
| `2` | 用法错误（argparse） | 修正命令 |
| `3` | **部分成功**——产物已落盘，部分条目缺失 | 只补缺失的，**不要**重跑整个阶段 |

一个例外：对 `verify` 来说，退出 `1` 表示核查**成功并发现了问题**（见 [verify](#6-verify--模型有没有凭空造技能)）。另一个：`llm_check.py` 里 `2` = 配置没问题但端点不可达。

---

## 在每个阶段之前/之间：定位进度

```bash
python scripts/utils/where_am_i.py                  # 这一轮跑到哪了、缺什么，并打印下一条命令
python scripts/utils/read_thin.py {run_dir} --kind ranked   # 在选岗位前必看：序号+公司+职位+分数+判定
python scripts/stage_timer.py report {run_dir}      # 各阶段耗时排序（自动埋点在 intermediate/run_timings.jsonl）
```

`read_thin.py --kind ranked` 接受**运行目录**（不是文件），`index` 就是 `--only` / `materials_*_N` / `apply --max` 用的同一个从 1 开始计数的编号。它是唯一能回答“我该挑几号岗位”的视图，因为分数和判定依模式分散在最多四个文件里，而 `qualified_jobs.json` 两者都不含。

---

## 0. 预检：LLM 配置

`pipeline.py` 在计划含任何 LLM 阶段时会自动跑 `llm_check.py --no-call`（紧跟在运行目录之后、`parse` 之前），不用单独敲。单独查配置 / 不想走流水线时：

```bash
python scripts/utils/llm_check.py --no-call    # 0=可用，1=配置缺失/非法（脚本会打印三种配置方式）
python scripts/utils/llm_check.py --no-call --stage deep   # 看单个阶段被解析成什么
```

- `--no-call`：不出网，不花任何钱。
- 去掉 `--no-call` 会发一个最小请求，此时 `2` = 配置完整但端点不可达（网络 / base-URL 问题，脚本不打印 key，只传路径和阶段名）。

---

## 1. parse —— 简历文件 → profile.json

```bash
python scripts/pipeline.py "D:\下载\简历.pdf"
```

- 唯一参数是简历路径（PDF / Word / md / markdown / txt），不带任何 stage flag（`--from` 默认 `parse`）。
- 产物：`resume_text.txt`、`profile.json`、`profile_validation.json` 写进**一个全新的带时间戳运行目录**，并把 `assets/LATEST.txt` 指向它。
- 退出 `1` = 简历里出现已知技术术语却没进 profile（字典查找 `KNOWN_TECH_TERMS` 触发，`hints` 下打印）。
- 单独重跑校验器（不重走 parse）：`python scripts/stages/validate_profile.py {run_dir}/resume_text.txt {run_dir}/profile.json`
- 需要确认单个字段用 `read_thin.py --kind profile`。**绝不手写 `profile.json`。**

---

## 2. infer —— profile.json → crawl_params.json（参数最密集的一步）

`crawl_params.json` 是**必须的**：`crawl` 用它的 argv 构建参数，`match` 从它读 `match_mode`/`top_n`。跳过 = 爬取无法启动、匹配静默回退 quick 模式。

```bash
python scripts/pipeline.py --from infer --city 太原 --keywords "AI应用开发,Python,后端开发,全栈" \
    --match-mode deep --top-n 10 --count 45 --degree 本科 \
    --job-type 全职 --salary 5-10K --min-count 10
```

**核心参数（`--city` + `--keywords`）给齐 → 整个阶段一次模型都不调。** `infer_params.py` 只对你「没用 flag 指定的字段」调模型；你指定过的字段一律以你的值为准。其余字段传了就用你的（`apply_overrides` 覆盖），没传才保留模型推断。

### 参数表

| 参数 | 可接受的值 | 说明 |
|---|---|---|
| `--city` | 城市名；**必须要有值** | 唯一必填。全国用 `--city 全国`（或 `不限`）；省略则退出 1。城市模型不能推断。 |
| `--keywords` | 逗号分隔的关键词 | 默认 3–4 个，默认给宽，让匹配阶段打分收窄。区分度高的概念优先。 |
| `--match-mode` | `quick` / `deep` | quick=纯规则 6 维打分（0-115 分，零 token）；deep=规则预筛 Top-N → 逐岗位调模型 → 融合 40% 规则 + 60% 模型 |
| `--top-n` | 数字 | deep 模式的候选数 |
| `--count` | 数字，默认 45，`0`=不限 | **每城市每关键词**上限，乘以关键词×城市 = 爬取时长最大杠杆 |
| `--min-count` | 数字，默认约 10，`0`=关闭 | 低于它判为“稀薄”并停下问用户 |
| `--degree` | `初中及以下` `中专/中技` `高中` `大专` `本科` `硕士` `博士` | |
| `--experience` | `在校生` `应届生` `经验不限` `1年以内` `1-3年` `3-5年` `5-10年` `10年以上` | ⚠️ 见下方“经验不限” |
| `--salary` | `3K以下` `3-5K` `5-10K` `10-20K` `20-50K` `50K以上` | |
| `--job-type` | `全职` `实习` `兼职` ——**没有 `校招`** | |
| `--scale` | `0-20人` `20-99人` `100-499人` `500-999人` `1000-9999人` `10000人以上` | 只给定，绝从不推断 |

无法识别的值会被**警告并跳过**（该筛选静默丢失，搜索范围悄悄变宽），所以拼写必须精确。

> **`经验不限` 是真实筛选值。** 必须原样传 `--experience 经验不限`；BOSS 里它（只返回发布时标「经验不限」的岗位）和「不筛选」（省略该 flag，所有经验档位全要）是**两个不同的查询**。只有当你真要“不筛选”时才省略该 flag。
>
> **`--city` 是唯一必须有的值**，全国也是一个城市值（`全国` / `不限`），所以要写出来。

### 存成预设（路径 C 重放）

```bash
python scripts/preferences.py show          # 打印预设；无预设退出 1
python scripts/preferences.py missing       # 打印预设缺哪些可补问字段
python scripts/preferences.py save --replace --city 太原 --keywords "AI应用开发,Python,后端开发,全栈" \
    --match-mode deep --top 10 --count 45 --degree 本科 \
    --job-type 全职 --salary 5-10K --min-count 10
python scripts/preferences.py crawl-args    # 打印完整爬取命令
python scripts/preferences.py clear         # 删除预设
```

`save` 默认**合并**（只写给出的字段）；`--replace` 整体重写。只有要清除某个字段才 `--replace` 重写或 `clear`。

---

## 3. crawl —— → assets/post_data/**.csv（路径 A、C）

先登录——天然交互式，跑在前台：

```bash
python scripts/stages/boss_post_interactive.py --ensure-login
```

`[LOGIN_OK]` → 浏览器关闭，继续。`[LOGIN_NEEDED]` → 浏览器保持打开，用户登录后重跑。登录态保存在 `assets/chrome_user_data/`。

再爬取——**几十分钟，后台跑**，argv 由 `crawl_params.json` 构建：

```bash
python scripts/pipeline.py --from crawl            # 自动定位 assets/LATEST.txt
python scripts/pipeline.py --from crawl --min-jobs 10   # 覆盖稀缺阈值（默认取 crawl_params 的 min_count）
```

- 下限检查自己跑：缺 `crawl_summary.json` = **什么都没爬到**（爬虫检测到登出会话时也会以 0 退出，光看退出码看不出）。下限触发时**停下问用户**——换关键词 / 放宽筛选 / 接受现状（`--min-jobs 0`），别拿着稀薄池子硬走。
- `--run-dir "assets/<时间戳>" --from crawl` 复用指定运行目录。

---

## 4. match → deep → merge（一条命令覆盖）

```bash
python scripts/pipeline.py --from match            # deep 模式：后台跑，逐岗位一次请求（自动取 assets/LATEST.txt）
python scripts/pipeline.py --run-dir "assets/<时间戳>" --from match --match-mode deep --top-n 10 --workers 4   # 常用
python scripts/stages/deep_analyze.py {run_dir} --resume   # 部分失败补齐：跳过 deep_results.json 里已有的 rank
```

`--from match` 是唯一例外，实际跑 **match → deep → merge** 三个阶段（单跑 match 产不出投递池）。quick 模式跳过 deep/merge 两步。

> **`--run-dir` 在 match 阶段可不给。** `--from match` 会自动取 `assets/LATEST.txt`（最近一次运行的目录）。只有目标目录**不是最新**那个、或想写清楚时，才需要显式给 `--run-dir`。

### match 阶段参数

`pipeline.py --from match` 只认下面三个额外参数（其余如 `--only`/`--name`/`--greeting-mode` 此阶段不读取；`--city`/`--keywords`/`--degree` 等是 infer 参数，对 match 无影响）：

| 参数 | 值 | 作用 |
|---|---|---|
| `--match-mode` | `quick` / `deep` | 覆盖 `crawl_params.json` 里存的模式。deep=逐岗位调模型；不给则读预设，预设没有时兜底 `quick` |
| `--top-n` | 数字，默认 **15** | deep 模式的候选数，传给 `run_matcher.py --top N` |
| `--workers`（`-w`） | 数字 | deep 的并发数——透传给 `deep_analyze.py --workers`，quick 无效 |

它们拼出的实际命令：quick → `run_matcher.py --mode quick`；deep → `run_matcher.py --mode deep --top N`，再 `deep_analyze.py [--workers N]`，最后 `run_matcher.py --mode deep --merge`。
- 产物：`matching_report.html` + `qualified_jobs.json` + `deep_results.json`。**绝不手写这些文件。**
- **部分失败是常态**：deep 退出 `3` = 结果已写但部分 rank 缺失 → 用 `--resume` 补齐，别重跑整个阶段。`deep_results.json` 靠 **`rank`** 映射回候选（那是唯一对齐键）。
- 投递池分三类（`application_category`，`read_thin` 打印原始枚举）：`qualified` 符合 / `need_optimization` 需优化 / `cannot_apply` 不可投递（打中硬闸门：学历低于要求、经验差 ≥3 年、或薪资差 >8K——总分盖不过它们）。
- 打开报告：`Invoke-Item {run_dir}\matching_report.html` / `start {run_dir}/matching_report.html`。

---

## 5. materials —— 招呼语 + 优化后简历

```bash
python scripts/pipeline.py --from materials --only 1,3,5        # 后台跑；--only 是 qualified_jobs.json 的 1-based 下标
```

- 每个岗位调两次模型（招呼语 + 简历改写），所以**只花在用户已批准的岗位列表上**。默认跑 AI 路径。
- 产物：`materials/greeting_{i}_{company}.txt`、`materials/resume_{i}_{company}.json`、`deliver/{company}-{position}/…`。已存在的非空产物会被跳过，绝不覆盖（`--force` 才覆盖）。
- **部分成功退出 3。** 检查并只补缺失的：

```bash
python scripts/check_artifacts.py {run_dir}       # 看缺什么
python scripts/stages/gen_materials.py {run_dir} --only 4      # 只补那个失败的
```

- 跑完自动落盘 `deliver/…/岗位信息+招呼语.md`、`优化建议.md` 和 `<姓名>-<岗位>.md`（只给 `--only` 选中的岗位建 deliver 文件夹）。

### 招呼语 / 简历图调整相关 flag（`gate:jobs` 的映射）

| 决定 | 如何传 |
|---|---|
| 招呼语 **AI生成**（默认） | `--greeting-mode ai` |
| 招呼语 **默认模板** | `--greeting-mode default`（规则模板，不调模型） |
| 招呼语 **自定义** | 把文字写进 `{run_dir}/materials/greeting_{i}_custom.txt`，再用 `--greeting-mode skip`（已有非空产物会被跳过） |
| 简历图 **AI调整**（默认） | materials 写简历 JSON → render 变长图 |
| 简历图 **自定义上传** | `--resume-mode skip`，发送时 `apply.py --image <path>` |
| 简历图 **不发送** | `--no-images`，发送时 `apply.py --no-image` |

> **招呼语前 15 个字是 HR 在消息列表唯一会看到的部分。** `materials` 守着 AI 路径：跑检查、花一次调用把糟糕的开头重新前置、并打印 `N 条招呼语的前 15 字被客套话占掉，已重写：…`。`default` 模板和用户手打的自定义文字**从不检查**——这两条你自己从包导入 `has_wasted_preview` 自查。

## 5.1 materials 参数表

`pipeline.py --from materials` 会把这些 flag 透传给 `gen_materials.py`。不传时取的是**下面列出的默认值**：

| 参数 | 取值 | 不传时的默认 | 含义 |
|---|---|---|---|
| `--greeting-mode` | `ai` / `default` / `skip` | **`ai`** | ai=调模型生成招呼语；default=套规则模板（不花 token）；skip=不生成 |
| `--resume-mode` | `ai` / `skip` | **`ai`** | ai=调模型优化简历；skip=不生成简历 JSON |
| `--only` | `1,3,5-7`（1-based） | **全部 qualified 岗位** | 只处理这些下标；不传处理整个投递池 |
| `--workers` / `-w` | 数字 | **取配置里的 concurrency** | 并发数 |
| `--force` | 无值 flag | **不传=跳过已有产物** | 覆盖已生成的材料（默认绝不动已存在的） |
| `--dry-run` | 无值 flag | 关 | 只打印计划，不请求、不写文件 |

`gen_materials.py` 独占、pipeline **不透传**的两个额外参数：

| 参数 | 默认 | 含义 |
|---|---|---|
| `--resume-text <路径>` | `<run_dir>/state/resume_text.txt` | 优化基底文件 |
| `--allow-generic-base` | 关（基底缺失会报错） | 基底缺失时允许用 profile 重述的通用稿作 AI 优化基底 |

你这条命令默认等效于 `gen_materials.py "<run_dir>" --greeting-mode ai --resume-mode ai`——两步都调模型、处理整个 qualified 池、按配置并发。

常用组合：

```bash
python scripts/pipeline.py --from materials --only 1,3,5 --workers 4        # gate:jobs 后：只给已批准的岗位，4 并发
python scripts/pipeline.py --from materials --greeting-mode default --resume-mode skip   # 省 token：模板招呼语、不出长图
python scripts/pipeline.py --from materials --only 2,4 --force              # 重写某几家（默认跳过已有产物）
```

---

## 6. verify —— 模型有没有凭空造技能？

**默认关闭**：整轮 `--to render` 不会自动查材料。要查就显式加 `--verify`，或把起点设成它（`--from verify` 本身就带查的意图）。把 `--verify` 加进 `--to render` 整轮：

```bash
python scripts/pipeline.py {简历.pdf} --to render --verify     # 整轮连材料核查一起跑
python scripts/pipeline.py --from verify                        # 只查这一环（自检，不拦下游）
```

它既做字符串比对，也调模型处理缩写 / 同义词 / 中英等价这些比对做不到的语义问题（所以按词烧模型，才默认关闭）。它收集真正会送出去的文本（`optimized_resume` + 招呼语）里的每个技术术语，报告在 `resume_text.txt` / `profile.json` 里没有依据的。

**退出 `1` 表示它发现了问题，不是它坏了。** 每个命中都带上下文打印。两条路之一：

```bash
python scripts/pipeline.py --run-dir {run_dir} --from verify --to render --allow PyTorch,nginx  # 站得住脚 → 加白名单并继续
python scripts/stages/gen_materials.py {run_dir} --only 1,3 --force   # 确系编造 → 重新生成那些岗位
```

- **绝不要自作主张传 `--allow`**——它以材料出门告终，得让用户决定。
- 不传 `--verify` 时 verify 不跑，但 `--to render` 会打一行「默认关闭」，提醒发送前自己逐份看材料。
- 退出码：`0` 干净 · `1` 有发现或没有基准/可检查材料 · `2` 坏的 `--only` · `3` 跑完但有些材料不可读（那些从没被检查过，自己读）。
- 抓不到：夸大其词（“了解”→“精通”）、中文短语（只评拉丁字母词）、`optimization_suggestions`（刻意排除）。

---

## 7. render —— 简历长图

```bash
python scripts/pipeline.py --from render --only 1,3,5
python scripts/pipeline.py --from render --only 1,3,5 --name "真实姓名"   # 覆盖 profile.json 的姓名
python scripts/pipeline.py --from materials --no-images                   # 整个跳过该阶段（选“不发送”时）
```

- **设计上串行，没有 `--workers`**——所有简历共用一个浏览器、一个 origin、一个 `localStorage`，并发会互相踩踏。
- 脚本在调试端口 9333 被非 `assets/showcv_profile` 的浏览器占住时**拒绝运行**（那个端口可能是持有用户真实简历的浏览器）。绝不要替用户传 `--adopt-browser`。
- **附件文件名是 `<姓名>-<应聘岗位>`，需要真名。** `profile.json` 的名字为空/占位符（`未提取`/`未知`/`无`/…）时脚本退出 1——不会渲染 `未提取-Python工程师.png`。
- 跑完自动跑 `verify_image.py "{run_dir}/deliver" --all`（十几行数字，不是图）。单独跑也行：

```bash
python scripts/verify/verify_image.py "{run_dir}/deliver" --all     # 检查图片的方式——别 Read 一张 PNG
```

---

## 8. apply —— 投递（唯一不可撤销的一步，必须显式 `--yes`）

```bash
python scripts/deliver/apply.py "{run_dir}"                # 演练：打印列表，不碰浏览器
python scripts/deliver/apply.py "{run_dir}" --yes --only 1,3,5     # 确认真投
python scripts/deliver/apply.py "{run_dir}" --yes --company 百度,棱镜数聚
python scripts/deliver/apply.py "{run_dir}" --yes --max 5
python scripts/deliver/apply.py "{run_dir}" --yes --image "自定义.png"   # 整批共用用户上传的图
python scripts/deliver/apply.py "{run_dir}" --yes --no-image
```

| 参数 | 说明 |
|---|---|
| `--yes` | 确认真投。**不给就只演练**（默认） |
| `--only` | 只投这些 1-based 序号，如 `"1,3,5-7"` |
| `--company` | 只投这些公司，逗号分隔；对池子做**子串匹配** |
| `--max` | 最多投几个 |
| `--name` | `<姓名>-<应聘岗位>` 里的姓名，覆盖 profile.json |
| `--image` | 整批共用这一张图（自定义上传） |
| `--no-image` | 不发简历附件 |

结果落在 `{run_dir}/apply_log.json`。三个检查**拒绝发送**而不是警告：缺招呼语、缺/空附件图片、运行目录不可读。

### 两个会咬人的参数

- **`--company` 是全有或全无。** 一个匹配不到任何东西的名字（拼写错 / 简称对全名）会退出 1 并发送给*零个人*，包括那些确实匹配上的公司。名字对池子做子串匹配；演练会打印一份你能传的确切菜单。
- **`--max N` 取 `qualified_jobs.json` 顺序的前 N 个**（符合在前、需优化在后，每组内部不按分数排序）。所以 `--max 5` 不是“最好的 5 个”——要最好的 N 个就从报告读显式 `--only` 下标。

---

## 附：ShowCV 简历编辑器（路径 D）

```bash
python scripts/showcv/serve.py                       # 第 1 步：启动静态服务器（后台）；第一行出 SHOWCV_READY http://127.0.0.1:3090
python scripts/showcv/launch.py http://127.0.0.1:3090   # 第 2 步：在隔离 Chromium 里打开；title 含 ShowCV 才算成功
```

三个刻意不接入任何路径的独立工具（都先跑阶段 0，从 `SHOWCV_READY` 行读 URL，`--url` 无默认值）：

```bash
python scripts/showcv/import_md.py --url http://127.0.0.1:3090 <文件或目录> [-r] [--dry-run]
python scripts/showcv/export_images.py --url http://127.0.0.1:3090 [--name N | --id I | --all] [--mode paginated|flat] [--scale 1|2|3] [--out DIR] [--dry-run]
python scripts/showcv/delete_resumes.py --url http://127.0.0.1:3090 --name NAME --dry-run   # 选 --yes 真删（localStorage 是唯一副本）
```

---

## 续跑 / 与其他方式混用

```bash
python scripts/pipeline.py --from crawl                 # 只重跑爬取（自动定位 assets/LATEST.txt）
python scripts/pipeline.py --from crawl --to render      # 从爬取起一路跑到底
python scripts/pipeline.py --run-dir "assets/<时间戳>" --from materials
python scripts/pipeline.py --from verify --to render --allow PyTorch,nginx   # 加白名单放行
```

所有脚本都有 `--help`。产物格式与 Skill 路径**完全一致，可以混着用**：Skill 跑到一半接着用命令行续跑同一个运行目录，反之亦然。区别只在谁来问——Skill 在投递范围和发送前各有一道确认闸门，命令行把这两道换成显式的 `--only` 和 `--yes`。