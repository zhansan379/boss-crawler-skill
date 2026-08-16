---
name: boss-crawler
description: 用 DrissionPage 爬取 BOSS 直聘职位，解析简历，用规则打分 + LLM 语义分析做岗位匹配，生成 HTML 可视化报告，自动投递匹配岗位，并启动内嵌的 Markdown 简历编辑器（ShowCV）。当用户想搜索 BOSS 直聘职位、上传简历做岗位匹配、生成岗位匹配报告、自动投递匹配岗位、针对特定 JD 优化简历，或打开简历编辑器写/改简历（"打开简历编辑器"、"启动 ShowCV"、"写一份简历"、"预览简历"）时使用。
---

# BOSS 直聘 爬取与匹配

> **⚠️ 语言强制要求（最高优先级）**：本 skill 面向中文用户。所有对用户的输出——包括提问（AskUserQuestion 的 question/header/option 文案）、说明、进度、报告、确认、错误提示——**一律使用简体中文**。术语、命令、脚本名、文件路径、代码片段可保留英文；除此之外的用户可见文本必须为中文。与用户的任何对话都不允许用英文正文。

一条流水线、九个阶段、一个驱动程序。每个阶段都是一条命令——你运行它，读最后几行，自己决定是否继续。**所有模型推断都发生在这些命令内部**，对着用户自己的 OpenAI 兼容端点调用；你从不自己填提示词模板，也不派子代理去做推断。

```
parse → infer → crawl → match → deep → merge → materials → verify → render        [ apply ]
```

`scripts/pipeline.py` 是驱动程序。所有脚本都位于相对本 skill 目录的 `scripts/` 下。**[references/cli.md](references/cli.md)** 保存完整的参数表和每个阶段的故障排查——当一条命令以本文件无法解释的方式失败时打开它，而不是当作例行阅读。

**所有脚本共用同一套四个退出码。在判定某个阶段失败之前先读它们：**

| 码 | 含义 | 该怎么处理 |
|---|---|---|
| `0` | 成功 | 继续 |
| `1` | 前置条件未满足 / 输入缺失 / 全部失败——通常什么都没写 | 停下，排查，告诉用户 |
| `2` | 用法错误（argparse）。*唯一的例外是 `llm_check.py`，其中 `2` = 配置没问题但端点不可达* | 修正命令 |
| `3` | **部分成功——产物已在磁盘上，部分条目缺失** | 检查缺了什么，只补齐那部分；**不要**重跑整个阶段 |

`3` 是最容易被误读成失败的那个。`deep`、`materials` 和 `render` 都会常规性地走到它，因为它们按岗位逐个处理。

有一个阶段弯曲了 `1`：对 `verify` 来说，退出 `1` 表示检查**成功并发现了问题**。没有坏任何东西，重跑也不会改变什么——动手之前先读 [verify](#verify--模型有没有凭空造技能) 一节。

**一次只驱动一个阶段。** `pipeline.py --from <stage>` 只运行那一个阶段（`match` 还会连带 `deep`+`merge`，因为停在 `match` 会留下一个下游谁也读不了的半成品），然后停下并打印下一条命令。**绝不用 `--all`**：下面的几道闸门处在阶段之间，而 `--all` 会直接冲过去，把用户的 token 花在一份他们还没看到的岗位列表上。

## 前置条件：LLM 配置

每个推断阶段都需要 API key。在别的任何事之前先检查一次，切莫晚于第一次 `parse`：

```bash
python scripts/llm_check.py --no-call        # 退出 0 = 可用，1 = 配置缺失/非法
```

`--no-call` 不花任何钱。退出 1 → 把三种配置方式展示给用户（脚本会打印它们）然后停下；不要启动一场会在 `match` 处死掉的爬取。加 `--stage deep` 看单个阶段被解析成什么，或去掉 `--no-call` 顺便发一个最小请求——**那一条路径会多出第三个码，`2` = 配置完整但端点不可达**（这个脚本是仓库范围内 `2 = 用法错误` 的唯一例外；是网络或 base-URL 的问题，不是配置问题）。**绝不打印、记录或回显 `api_key`**——脚本会把它打码，所以只传路径和阶段名，别传 key。

## 路径选择

**每次调用开始都必须让用户选一条路径**——一次 `AskUserQuestion`，**恰好 4 个选项**。不要从保存过的预设或磁盘上恰好存在的东西自动选；预设是在路径选好*之后*才提供参数。

| 选项 | 何时用 | 流程 |
|------|------|------|
| **A: 简历驱动** ✨ | 有简历，想要精确 | parse → infer → crawl → match… → apply |
| **B: 已有岗位数据** | 有简历，且 `assets/post_data/` 里已有 CSV | parse → infer → *(跳过 crawl)* → match… → apply |
| **C: 预设重放** | 用保存的预设重跑，不用重新声明 | preset → parse → infer *(预设值)* → crawl → match… |
| **D: 仅编辑简历** | 还没有简历文件，想写或改一份 | 启动简历编辑器 → **到此为止** |

**每条路径都从 `parse` 开始。** `infer` 读 `profile.json`，`crawl` 读 `infer` 写出的 `crawl_params.json`——所以"先爬后解析"不是受支持的顺序。路径 B 与 A 只差一件事：跳过 `crawl` 阶段（`infer` 之后直接 `--from match`）。

**AskUserQuestion 选项上限（本 skill 的每个提问都一样）：** 每个问题最多 4 个选项。当某个选择有更多候选——薪资档、关键词选择——取最相关的 4 个，把推荐默认值放第一个，让自动加的「其他」兜住其余。绝不发出第 5 个选项；工具会拒绝该调用。与其让调用失败，不如把一个过长的提问拆成后续问题。

**每个阶段一轮 `AskUserQuestion`，而不是每个字段一轮。** 该工具接受一个问题列表；把一个阶段需要的所有决定都打包进那一次调用（最多 4 个问题）。本 skill 恰好有四个交互停点——路径选择、`infer` 确认、`gate:jobs`、`gate:send`——每个都**恰好一次**调用。绝不要单独问一个字段（`--salary?` 然后 `--degree?` 然后 `--count?`）：那正是 9 轮爬取参数会话的由来，而每一轮大约耗掉用户 1.5 分钟的注意力。如果某个阶段确实需要两批，就在下面的清单里点名写明，免得它悄悄变成四批。

**推荐路径 A**：简历会告诉你搜什么——技能、城市、薪资区间——所以爬到的岗位跟候选人的背景对齐，而不是跟瞎猜的关键词对齐。

**路径 D 以启动告终。** 它打开编辑器、报告 URL，仅此而已。它通常作为前奏：用户写一份简历，然后从 A/B/C 重新进入。编辑器里存的 Markdown 可以直接喂给 `parse`——但只在用户要求时。

**路径 C 是预设路径。** `preferences.py show` 打印已存的参数，`preferences.py missing` 点名预设缺了哪些可问字段（薪资/规模/最低岗位数 以及同类）。只问**恰好**那些，用 `preferences.py save` 合并回去，再把整组作为 flag 传给 `infer`。如果 `show` 退出 1，说明没有预设——退回路径 A 的全新确认，而不是报错。

`save` 是**合并**——只传你刚问过的字段；其余都保留。只有在要整体重写预设时才传 `--replace`（这正是 `infer --save` 在打印完整组待确认之后做的事）。合并模式下，你无法通过省略某个字段来清除它：用 `--replace` 重写，或用 `clear` 全部清空。

> **两道闸门，都必须过。** `gate:jobs`（投哪些岗位，以及材料怎么做）和 `gate:send`（批准真正落到磁盘的东西）。缺了任何一道都不投递。
>
> **预设永远到不了这两道闸门。** `assets/preferences.json` 只覆盖爬取和匹配参数——它没有"投哪些岗位、用什么招呼语、是否发送"这些字段，`load()` 会丢弃白名单之外的任何键。

## 运行目录

`parse` 在 `assets/` 下创建带时间戳的运行目录（例如 `assets/2026-08-16_14-30-00/`）并把 `assets/LATEST.txt` 指向它。之后每个阶段都会自动找到它；要显式指定就传 `--run-dir`。路径 D 不产生运行产物，也不需要运行目录。

---

## 工作流

复制这份清单，完成一项就在前头打勾：

```
进度：
- [ ] llm_check.py --no-call        (前置条件——退出 1 会停掉整次运行)
- [ ] 路径选择：一次 AskUserQuestion，4 个选项 (A / B / C / D)
- [ ] 阶段 0：启动简历编辑器（路径 D——终止步骤）
- [ ] parse:     简历文件 → profile.json
- [ ] infer:     确认参数（2 次打包提问 + min_count）→ crawl_params.json
- [ ] crawl:     后台运行，然后检查下限（路径 A、C）
- [ ] match:     → deep → merge → matching_report.html + qualified_jobs.json
- [ ] gate:jobs  一次 AskUserQuestion：投哪些 + 招呼语方式 + 图片方式
- [ ] materials: 招呼语 + 优化后简历
- [ ] verify:    没有凭空造技能（退出 1 = 发现了——停下给用户看）
- [ ] render:    简历长图（用 --no-images 跳过）
- [ ] materials 后自动落盘 岗位信息+招呼语.md（无需手动 — 见下）
- [ ] verify_image.py  看一眼长图（返回数字，不加载图片）
- [ ] gate:send  一次 AskUserQuestion → apply.py --yes
- [ ] verify_image.py  看一眼长图（返回数字，不加载图片）
- [ ] gate:send  一次 AskUserQuestion → apply.py --yes
```

**四个停点。** 路径选择、`infer` 确认（两次打包的 `AskUserQuestion` 调用加一次小小的 `min_count` 后续——当路径 C 复用完整预设时跳过）、`gate:jobs`、`gate:send`。外加一个条件停点：爬取下限，只在池子来得太稀薄时。

**迷失了位置（例如在上下文压缩之后）？不要重读文档来重建状态。** 问文件系统：

```bash
python scripts/where_am_i.py           # 或传一个显式的 <run_dir>
```

它会根据磁盘上的产物推断出当前阶段，并用约 1k 字符打印下一条命令。只在它指向的那*一个*章节去查参考资料。

**让一次运行保持廉价的三个习惯。**

1. **绝不 `Read` 一张渲染好的简历图片。** 用 `scripts/verify_image.py`。一张 0.5 MB 的 PNG 花掉 638,960 个输入 token——单次工具调用就占了那个会话 79% 的新鲜输入。
2. **绝不 `Read` 完整数据文件——用 `read_thin.py`。** `qualified_jobs.json` 带着完整的 JD 和公司描述；你只需要 link/公司/职位/分数/判定：

   ```bash
   python scripts/read_thin.py {run_dir}/qualified_jobs.json --kind jobs     # → 表格字段
   python scripts/read_thin.py {run_dir}/profile.json --kind profile         # → 汇总统计
   python scripts/read_thin.py {run_dir}/deep_results.json --kind deep       # → 只看判定
   python scripts/read_thin.py {run_dir} --kind ranked                       # → 序号+公司+职位+分数+判定
   ```

   `ranked` 接受的是**运行目录**，不是文件——分数和判定依模式分散在最多四个不同的文件里（`scored_jobs` → `job_classification` → `deep_results`，通过 `deep_candidates` 按 `rank` 连接），而 `qualified_jobs.json` 两者都没有。它是唯一能回答"我该挑几号岗位"的视图，因为它的 `index` 就是 `--only`、`materials_*_N` 和 `apply --max` 用的同一个从 1 开始计数的编号。**优先用它，而不要手工去连文件。** 它会同时报告 `matched` 和 `total`：`matched < total` 说明有些岗位从未出现在任何匹配产物里（通常是只爬不匹配的运行），而不是分数真地是空的。

3. **在后台跑长阶段并用 grep 过滤输出。** `crawl` 要几十分钟，`deep`/`materials` 每个岗位打印一行——把全部输出灌进你的上下文正是触发压缩的原因。用 `run_in_background` 启动它们，然后只读关键部分：

   ```bash
   grep -E "✅|❌|⚠|阶段|失败|写入" <后台任务输出文件> | tail -20
   ```

耗时自动落在 `{run_dir}/run_timings.jsonl`——每个阶段都会自我埋点，所以不用手工标记。`python scripts/stage_timer.py report <run_dir>` 给它们排序。

### 阶段 0：启动简历编辑器（路径 D）

在本地提供内嵌的 ShowCV 构建（`app/`）并在一个隔离的 Chromium 里打开它。不需要 `pnpm install` 或 node。

**第 1 步——启动静态服务器**（后台任务）：

```bash
python scripts/showcv/serve.py
```

等待就绪信号并从它读出实际地址：

```bash
until grep -q "SHOWCV_READY" "<后台任务输出文件>"; do sleep 0.3; done
grep "SHOWCV_READY" "<后台任务输出文件>"
```

第一行永远是 `SHOWCV_READY http://127.0.0.1:<port>`，默认 3090。**如果那个后台进程立刻退出却仍打印了 `SHOWCV_READY`**：说明服务已经在运行，本次复用了它。用那个地址继续——**不要**重启它或另选端口；端口正是用户保存的简历的作用域。

**第 2 步——打开浏览器**（用第 1 步的地址，别假设是 3090）：

```bash
python scripts/showcv/launch.py http://127.0.0.1:3090
```

成功时打印 `url=` / `title=` / `profile=`，title 里有 `ShowCV`。**如果 title 里没有 `ShowCV` 脚本就退出 1**——构建不完整或服务器没起来。不要报告成功。可选参数：`--headless`、`--close`、`--browser <exe>`。

**第 3 步——向用户报告**:URL、浏览器已打开，以及**怎么停下它**——对第 1 步的后台任务执行 `TaskStop`；浏览器窗口由用户自己关。

然后停下。路径 D 到此结束。

### 阶段 0.5 / 0.6 / 0.7：ShowCV 独立工具（仅在要求时）

**刻意不接入任何路径，也不在进度清单里。** 三个工具都假设阶段 0 已经跑过（服务器起来、浏览器打开），失败也不自己启动。从阶段 0 的 `SHOWCV_READY` 行读 URL——`--url` 故意没有默认值。

```bash
# 0.5 批量把 Markdown 导入编辑器的简历列表
python scripts/showcv/import_md.py --url http://127.0.0.1:3090 <文件或目录> [-r] [--dry-run]

# 0.6 把简历导出为图片（可重复的 --id，或 --all；一次调用覆盖一个批次）
python scripts/showcv/export_images.py --url http://127.0.0.1:3090 [--name N | --id I | --all] \
    [--mode paginated|flat] [--scale 1|2|3] [--out DIR] [--dry-run]

# 0.7 删除简历——破坏性操作，localStorage 是唯一副本
python scripts/showcv/delete_resumes.py --url http://127.0.0.1:3090 --name NAME --dry-run
python scripts/showcv/delete_resumes.py --url http://127.0.0.1:3090 --name NAME --yes
```

`export_images.py` 在本地把名字解析成 id，所以拼写错误会在导出任何东西之前就失败，而且它会确认文件真地落盘，而不是相信页面的"已下载"文字。`delete_resumes.py` 不带 `--yes` 只打印计划；带 `--yes` 先做备份（打印恢复命令），走站点自己的确认页，如果那里的名字与它解析出来的不一致就中止。与 `/export` 不同，缺失的 `id` 绝不会被当作"当前这份简历"。

### parse —— 简历文件 → profile.json

先问简历文件路径，然后一条命令：

```bash
python scripts/pipeline.py "简历.pdf"
```

PDF / Word / md / markdown / txt。它把 `resume_text.txt`、`profile.json` 和 `profile_validation.json` 写进一个全新的运行目录。**不要为了"检查"一次干净的 parse 而去读简历**——校验器已经做过了，而这段文本只会为毫无新信息地花掉上下文。只有当校验器退出 1、某条 hint 看起来像真实的遗漏、或用户要求彻底检查时，才去读 `resume_text.txt`。

`profile_validation.json` 就是那个校验器的输出。parse 阶段退出码 1 表示简历里出现了一个已知的技术术语却没进 profile——那是一次字典查找（`KNOWN_TECH_TERMS`），是这里唯一不依赖产生 JSON 的那个模型的信号。它在 `hints` 下打印的任何东西（未匹配的项目/公司名、稀薄的技能类别）都来自宽松的正则：读 hints，别照做，也别让某一条变成闸门。单独重跑它：

```bash
python scripts/validate_profile.py {run_dir}/resume_text.txt {run_dir}/profile.json
```

需要确认某个具体字段时用 `read_thin.py --kind profile`。**你绝不手写 `profile.json`。**

### infer —— profile.json → crawl_params.json

`crawl_params.json` 是**必须的**，不是优化：`crawl` 用它的 argv 构建参数，`match` 从它读 `match_mode`/`top_n`。跳过这个阶段意味着爬取无法启动，匹配会静默回退到 quick 模式。

**默认给 3–4 个关键词，别更少。** 爬取时间与关键词个数成线性，但稀薄的池子比长得多的难关更糟——1 个关键词 + 应届生筛选 曾经只爬出 19 行，浪费了整整一次运行；4 个关键词 + 经验不限拿到了 197。所以默认给宽，把关键词花在区分度高的概念上（`AI应用开发` 和 `大模型应用开发` 是同一个搜索），让匹配阶段的打分来做收窄。小市场是唯一要削减（2–3）的理由，即便如此也要封顶在城市预算之内（`infer_params.py` 里的 `keyword_budget`：3 个 小城市 / 5 个 一线）。

用**两次** `AskUserQuestion` 调用加一次小小的后续来确认（每次最多 4 个问题）：

1. **爬取与匹配核心** —— 城市、关键词（多选，默认 3-4 个）、匹配模式（quick/deep）、deep 的 Top-N。
2. **列表筛选** —— 经验、职位类型、薪资下限、公司规模。**经验默认是 `经验不限`**——不要按简历里的年数去收窄它；匹配阶段会做。其余筛选的第一个选项就是适合候选人的默认，所以接受就是一次点击；留空一个就跳过该筛选。
3. **最低岗位数量（`min_count`）** —— 低于它的爬取就算稀薄。默认约 10，0 关闭检查。单独拎出来是因为它是充足性阈值，不是列表筛选。

然后把每个确认过的值作为 flag 传进去。参数完全指定意味着该阶段**根本不调模型**——它只为那些你留空的字段调模型：

```bash
python scripts/pipeline.py --from infer --city 太原 --keywords "AI应用开发,Python,后端开发,全栈" \
    --match-mode deep --top-n 10 --count 20 --degree 本科 \
    --job-type 全职 --salary 5-10K --min-count 10
```

可接受的筛选值——下面这些中文标签就是全集（`boss_crawler/config.py:42-89`）。无法识别的值会被**警告并跳过**，这会让该筛选静默丢失而不是失败，所以这里的拼写错误会悄悄把搜索范围放宽：

| flag | 可接受的值 |
|---|---|
| `--job-type` | `全职` `实习` `兼职` ——**没有 `校招`** |
| `--salary` | `3K以下` `3-5K` `5-10K` `10-20K` `20-50K` `50K以上` |
| `--experience` | `在校生` `应届生` `经验不限` `1年以内` `1-3年` `3-5年` `5-10年` `10年以上` |
| `--degree` | `初中及以下` `中专/中技` `高中` `大专` `本科` `硕士` `博士` |
| `--scale` | `0-20人` `20-99人` `100-499人` `500-999人` `1000-9999人` `10000人以上` —— 从不推断，只给定 |

这五种里 `不限` 会被跳过——优先省略该 flag。**`--city` 是例外，而且它也是唯一必须要有值的字段**：全国是一个*城市值*，所以要写出来（`--city 全国`，或 `不限`）。省略 `--city` 会退出 1，而不是悄悄全国搜索。关键词模型通常能推断；城市它不能。

`--count` 是**每个城市每个关键词**的上限（默认 0 = 不限制），所以它要乘以关键词 × 城市，是爬取时长的最大杠杆——`--count 20` 配 3 个关键词 2 个城市，最多 120 个岗位，不是 20。（如果你直接调用爬虫，它的 `-c all` 表示*374 个城市一个一个爬*——几乎从不是任何人想要的。）

然后把答案保存下来，这样下次运行可以重放（路径 C）。这会写入整组，所以这里用 `--replace` 是对的；之后单字段微调就去掉这个 flag 走合并：

```bash
python scripts/preferences.py save --replace --city 太原 --keywords "AI应用开发,Python,后端开发,全栈" \
    --match-mode deep --top 10 --count 20 --degree 本科 \
    --job-type 全职 --salary 5-10K --min-count 10
```

**即使推断看起来毫无歧义，也要保留这道闸门。** 爬取是透过用户自己登录的浏览器驱动的对外动作：参数错了，代价是一次漫长的爬取加一批没用的数据，而且没有任何可撤销的东西。这里被压缩掉的是多轮输入，不是确认本身。

### crawl —— → assets/post_data/**.csv（路径 A、C）

先登录。这一步天然是交互式的，跑在前台：

```bash
python scripts/boss_post_interactive.py --ensure-login
```

`[LOGIN_OK]` → 浏览器关闭，继续。`[LOGIN_NEEDED]` → 浏览器保持打开，用户登录后告诉你 已登录，然后重跑。登录状态保存在 `assets/chrome_user_data/`。

然后爬取——**后台任务，几十分钟**，argv 由 `crawl_params.json` 构建：

```bash
python scripts/pipeline.py --from crawl
```

之后下限检查自己跑：阈值来自 `crawl_params.json` 里的 `min_count`，`--min-jobs N` 只覆盖它。缺失 `crawl_summary.json` 意味着**什么都没爬到**——爬虫在检测到已登出会话时会以 0 退出，所以光靠退出码看不出来。当下限触发时，**停下问用户**——换关键词 / 放宽筛选 / 接受现状（继续，`--min-jobs 0`）——而不是拿着稀薄的池子硬往下走。小城市爬取可以合理地提前结束；这正是这个检查要暴露的情况，而不是要覆盖掉。

行数只是岗位数的上界，而且它数的是**整个 `assets/post_data/` 池子**，不只是本次运行：一个命中三个关键词的岗位会被写三次，加载时去重。你需要的每个筛选值都在上面的表里。

### match → deep → merge —— 打分与报告

一条命令覆盖全部三个；quick 模式下 `deep`/`merge` 是空操作：

```bash
python scripts/pipeline.py --from match          # deep 模式：后台任务，每个岗位一次请求
```

- **quick** —— 基于规则的 6 维打分（0-115 分），数秒，零 token 成本。
- **deep** —— 规则预筛到 Top-N，然后每个候选一次模型请求，再做一次合并，把规则分（40%）与模型分（60%）融合、重新分类并重新生成报告。

两者都写 `matching_report.html` 和 `qualified_jobs.json`（投递池 = 符合 + 需优化，用原始爬取字段）。**绝不手写 `qualified_jobs.json`。** 也别自己调 `generate_html_report()`——脚本已经调过了，而且 CLI 运行之后你手里也没有它需要的那个对象。

为用户打开报告：`Invoke-Item {run_dir}\matching_report.html`（PowerShell）或 `start {run_dir}/matching_report.html`（Bash）。**你消费 `application_category` 和 `match_score`，你绝不去重算它们。**

每个岗位带三个 `application_category` 值之一——枚举是英文，报告是中文，而 `read_thin.py --kind jobs` 和 `--kind ranked` 都打印原始枚举，所以跟用户说话时你自己翻译：

| 枚举 | 中文 | 含义 |
|---|---|---|
| `qualified` | 符合 | 没碰到硬闸门，技能和经验已在 |
| `need_optimization` | 需优化 | 没碰到硬闸门，差距可弥合——包括 经验差 1–3 年 / 薪资差 3–8K |
| `cannot_apply` | 不可投递 | **打中了硬闸门** |

只有三件事会落到 `cannot_apply`，而且总分永远盖不过它们（`resume_matcher/scoring.py:320-354`）：学历低于 JD 的要求、经验差距 ≥ 3 年、或薪资差距 > 8K。绝不要把中档岗位说成 不可投递。

**deep 模式每个岗位发一次请求，所以部分失败是常态。** `deep` 退出 **3** 表示结果文件已写但部分 rank 缺失——用 `--resume` 补齐，而不是重跑整个阶段：

```bash
python scripts/deep_analyze.py <run_dir> --resume    # 跳过 deep_results.json 里已有的 rank
```

`deep_results.json` 靠 **`rank`** 映射回候选，而不是靠 `job_id` 或 link——那是唯一的对齐键，一旦 rank 错位，就会把某岗位的分析安到另一个岗位上而没有任何地方报错。`read_thin.py --kind ranked` 已经做这个连接了；用它而不是自己重建。

### gate:jobs —— 一个问题，三个轴

先展示表格（`read_thin.py --kind ranked` 把分数 + 判定 + 公司 + 职位放一张表，或 `--kind jobs` 拿爬取列——绝不 `Read` 文件），然后**一次** `AskUserQuestion` 覆盖三个互相独立的抉择：

`--kind jobs` 打印的三列是决策信号，不是细节——把它们放进表格里，而不是让用户在死岗位上瞎选：

- `已失效=是` —— BOSS 返回了 `invalidStatus=true`；投了也白投，把它从范围里拿掉
- `代招=是`，或 `HR公司` ≠ `公司` —— 联系人是猎头/外包，不是雇主自己的 HR
- 三者都是**三态**：空值表示 未采集（爬取是没带 `-d` 跑的），绝不是 否

| 轴 | 选项 |
|---|---|
| 投递范围 | 投哪些岗位（岗位选择不影响另外两个） |
| 招呼语生成方式 | 自定义 / 默认模板 / AI生成 |
| 是否发送图片 | 自定义上传 / AI调整（渲染长图） / 不发送 |

把答案映射成 flag，而不是事后编辑文件：

| 答案 | 如何执行 |
|---|---|
| 投递范围 = 子集 | 在 `materials` **和** `render` 上用 `--only 1,3,5-7`（对 `qualified_jobs.json` 从 1 开始计数的下标）——别去改文件 |
| 招呼语 **自定义** | 把文字写进每个选中的 `i` 的 `{run_dir}/generated/greeting_{i}_custom.txt`，然后用 `--greeting-mode skip` 跑 materials…或把模式留在 `ai`：已存在的非空产物会被跳过，绝不覆盖 |
| 招呼语 **默认模板** | `--greeting-mode default`（规则模板，不调模型） |
| 招呼语 **AI生成** | `--greeting-mode ai`（默认） |
| 图片 **自定义上传** | 校验路径，`--resume-mode skip`，发送时 `apply.py --image <path>`。`skip` 也会抑制 `render`——生成的 PNG 反正不会被发送 |
| 图片 **AI调整** | 默认：`materials` 写简历 JSON，`render` 把它变成长图 |
| 图片 **不发送** | `pipeline.py --no-images`，发送时 `apply.py --no-image` |

**招呼语的前 15 个字是大多数 HR 唯一会看到的部分。** BOSS 的消息列表预览在那里截断，所以 `您好，我是…` 把整个窗口都浪费在废话上了。规则和各场景公式在 `scripts/prompts/greeting.st`——唯一来源，别转述。

`materials` 已经守住了 **AI** 路径：它跑检查，花一次额外调用把糟糕的开头重新前置，并打印 `N 条招呼语的前 15 字被客套话占掉，已重写：…`。别再去复查那些。有三种情况留着没守住——你自己查，从包里导入（不是裸 `auto_apply`）：

```bash
python -c "from resume_matcher.auto_apply import has_wasted_preview; print(has_wasted_preview(open('X.txt',encoding='utf-8').read()))"
```

- `--greeting-mode default` —— 离线模板路径从不检查
- 用户手动打的一段自定义文字 —— 从不检查
- 一段重试也失败了的 AI 招呼语 —— 原样保留，且**不会**出现在那行打印里

如果检查失败，说出来并提供重新前置，但**绝不静默改写用户提供的招呼语。**

### materials —— 招呼语 + 优化后简历

```bash
python scripts/pipeline.py --from materials --only 1,3,5     # 后台任务
```

每个岗位两次模型请求（招呼语 + 简历改写），所以这是对用户刚批准的岗位列表花钱的阶段——这正是 `gate:jobs` 先行的原因。产物是 `generated/greeting_{i}_{company}.txt` 和 `generated/resume_{i}_{company}.json`，其中 `{i}` 是 `qualified_jobs.json` 里从 1 开始计数的下标。**这个下标是下游一切的对齐键**，所以一个失败的岗位会留下空隙，而不是把后面的都错位。

部分成功退出 3，不是 1。检查并只补齐缺失的——已存在的非空产物会被跳过，绝不覆盖（`--force` 才覆盖）：

```bash
python scripts/check_artifacts.py {run_dir}
python scripts/gen_materials.py {run_dir} --only 4        # 只补那个失败的
```

**材料失败的岗位会从批次里剔除，而不是无限重试。** 把它从 `render` 和投递列表里排除，并在 `gate:send` 告诉用户。

### verify —— 模型有没有凭空造技能？

```bash
python scripts/pipeline.py --from verify
```

只用标准库，不调模型，几秒跑完。它收集真正会被送出去的文本里的每个技术术语——`optimized_resume` 和招呼语——并报告那些在 `resume_text.txt` / `profile.json` 里没有依据的。一次悄悄加进 PyTorch 或 Kubernetes 的简历改写是本 skill 最糟糕的失败模式：用户带着它去面试，却答不上来。

**退出 1 表示它发现了问题，不是它坏了。** 流水线故意在 `render` 之前停下——长图一旦存在，材料读起来就是定稿。每个命中都带着周围上下文打印出来，好让人判断；然后选两条路之一：

```bash
# 确系编造 → 重新生成那些岗位
python scripts/gen_materials.py {run_dir} --only 1,3 --force
# 站得住脚（它*确实*在简历里，只是措辞不同）→ 加白名单并继续
python scripts/pipeline.py --run-dir {run_dir} --from verify --all --allow PyTorch,nginx
```

**绝不要自作主张传 `--allow` 或 `--skip-verify`**——两者都是用户的决定，因为两者都以材料出门告终。把列表展示出来并询问。（`apply.py` 有自己的、不相关的 `--skip-verify`，用于*图片*健康检查；别把关于一个的决定搬到另一个身上。）

它抓不到的：夸大其词。「了解」被改写为「精通」、三个月实习被拉长到一年、一种不像用户的语气——任何术语匹配都发现不了这些，所以 `gate:send` 仍然意味着要读材料。还有两个值得知道的局限：它只评估拉丁字母词（像 多智能体面试系统 这样的中文短语永远不登记），而且 `optimization_suggestions` 刻意排除在外，因为给一份简历提出它缺的技能正是那个字段的全部工作。

退出码：`0` 干净 · `1` 有发现，或没有基准 / 没有可检查的材料（没检查不等于干净）· `2` 坏的 `--only` · `3` 跑完了但有些材料不可读——那些从没被检查过，所以你自己读。

它写 `verify_report.json`，记录它检查了哪些文件以及什么 mtime，这正是 `where_am_i.py` 知道该阶段跑没跑过的方式——也在一份材料被重新生成时知道某个 `✅` 已经过期。一次 `--only` 运行不写报告（子集结果会把没检查过的文件标记成已检查）。

### render —— 简历长图

```bash
python scripts/pipeline.py --from render --only 1,3,5
```

**设计上就是串行——没有 `--workers`。** 所有简历共用一个浏览器、一个 origin 和一个 `localStorage`；并发只会让它们互相踩踏。脚本在调试端口 9333 被一个 `--user-data-dir` 不是 `assets/showcv_profile` 的浏览器占住时拒绝运行——那个端口可能是持有用户真实简历的*被接管的*浏览器。绝不要替用户传 `--adopt-browser`。

当用户选了 不发送 时，用 `--no-images` 整个跳过该阶段。

**附件文件名是 `<姓名>-<应聘岗位>`，所以这个阶段需要一个真名。** 当 `profile.json` 的 `basic_info.name` 为空或占位符（`未提取` / `未知` / `无` / …）时，脚本退出 1，而不是渲染一张 `未提取-Python工程师.png`——HR 会看到那个字符串。传 `--name "真实姓名"`（`apply.py` 上有同一个 flag）。这里的退出码：`0` 每张图都渲染了，`1` 前置条件失败且什么都没跑，`3` 阶段跑完但部分岗位没有图——在 `gate:send` 之前检查是哪些。

### 岗位信息+招呼语.md 自动落盘，然后看一眼

`岗位信息+招呼语.md` **不需要你动手** —— `materials` 阶段跑完会自动跑 `write_application_md.py --all`，为每个岗位写 `applications/{company}-{position}/岗位信息+招呼语.md`（所有爬取字段加招呼语；绝不手写）。它挂在 materials 而非 render 上，所以 `--resume-mode skip` / `--no-images` 跳过渲染时也照写。

你只需检查长图：

```bash
python scripts/verify_image.py "{run_dir}/applications" --all
```

`verify_image.py` 是你检查图片的方式：它返回十几行数字，而不是一张 639k token 的截图。如果用户想看某一张，把路径给他们，让他们自己打开。

### gate:send —— 批准，然后投递

一次 `AskUserQuestion`：全部投递 / 返回修改 / 取消投递。然后，也只有在这之后：

```bash
python scripts/apply.py "{run_dir}"                # 干跑：打印列表，不碰浏览器
python scripts/apply.py "{run_dir}" --yes          # 发送
```

**`--yes` 是本 skill 里唯一不可撤销的一步**——一条已发送的消息瞬间到达、无法撤回。永远先干跑并把那份列表展示出来。`gate:send` **不可合并、不可预设**：它必须看到实际落到磁盘的材料，所以不能提前，任何保存的偏好都不能替代它。`pipeline.py` 从不运行 `apply.py`，即使带 `--all` 也不。

有用的参数：`--only 1,3,5`、`--company 百度,棱镜数聚`、`--max N`、`--image <path>`、`--no-image`、`--name 张三`。结果落在 `{run_dir}/apply_log.json`。三个检查拒绝发送而不是警告——缺招呼语、缺/空附件图片、运行目录不可读。

其中两个参数会咬人：

- **`--company` 是全有或全无。** 一个匹配不到任何东西的名字（拼写错误、池子里存的是简称却给了全名）会退出 1 并发送给*零个人*，包括那些确实匹配上的公司。名字对池子做子串匹配；干跑会打印一份你能传的确切菜单。
- **`--max N` 取 `qualified_jobs.json` 顺序的前 N 个，而那个顺序是 符合 在前、需优化 在后，每组内部不按分数排序**（`deep_analysis.py:377` 写的是原始列表；只有报告里的副本会被排序）。所以 `--max 5` 不是"最好的 5 个"。如果用户要最好的 N 个，传从报告读出来的显式 `--only` 下标。HR 活跃优先的排序属于库的入口点 `auto_apply.apply_to_jobs(max_applications=…)`，**不属于**这个 CLI。

## references/ —— 留下的那一份文档

**一次运行需要的一切都在本文件里。** `references/` 下唯一的文档是 `cli.md`，命令行路径参考。如果你发现自己正要用 shell 命令去探一个*常量*——一个合法值、一个键名、一个退出码——它就在本文件，上面。

| 文件 | 读者 / 触发时机 |
|---|---|
| [cli.md](references/cli.md) | **故障排查。** 一条命令以本文件无法解释的方式失败，或你需要一个上面没列出的 flag |

## 关键原则

1. **规则优先，然后才是 LLM**：Python 规则打分预筛；模型只对顶部候选做深度分析
2. **绝不编造**：简历优化不得发明经历或技能。三个 `basic_info.availability` 字段——到岗日期 / 可实习时长 / 每周出勤——更严格：它们是 HR 会据以行动的排期*承诺*，所以只能从简历复制。如果它们是 `null`，招呼语就什么也不说时间安排，并去问用户；绝不要从入学或毕业年份推导日期（`prompts/resume_parse.st:92`、`prompts/greeting.st:46`）
3. **安全投递**：每次投递间隔 3-5 秒，每次会话最多 10-20 个，验证码时暂停
4. **始终可视化**：每次运行都生成并打开 HTML 报告
5. **磁盘上的产物才是真相**：用文件和退出码判断一个阶段，而不是凭通知