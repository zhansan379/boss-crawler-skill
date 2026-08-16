# 命令行用法

这套脚本有两条并行的使用路径，共用同一批文件、同一个模型接口与同一个产物目录：

| | Skill 路径 | **命令行路径（本文）** |
|---|---|---|
| 入口 | Claude Code 里 `/boss-crawler` | `python scripts/pipeline.py 简历.pdf --all` |
| LLM | 你自己配的 OpenAI 兼容接口 | 同一个（配置方式见第 2 节） |
| 交互 | 逐阶段跑，投递范围与发送前各有一道确认 | 参数一次给全，或用 `--dry-run` 先看 |
| 收窄投递范围 | 问你，然后翻译成 `--only` | 你自己写 `--only` / `--company` |
| 适合 | 想让 Claude 帮你判断和决策 | 定时任务、批量重跑、不想开 Claude Code |

两条路径**产物格式完全一致**，可以混着用：Skill 跑到一半，接着用命令行续跑同一个运行目录，反过来也行。区别只在谁来做决定 —— Skill 路径把每个阶段的参数问你确认，命令行路径要求你一次写全。

> 📍 **如果你是 Skill（Claude Code），本文是参考资料，不是操作指南。** 本文按「一次给全参数、`--all` 一把跑完」来写，那是给人和定时任务的用法；Skill 路径必须一个阶段一条命令、**禁用 `--all`**，因为收窄投递范围和发送前的两道确认就卡在阶段之间。冲突时以 `SKILL.md` 为准。本文真正只此一份的内容是：第 2 节的模型配置排查、第 3 节的 `--from/--to` 区间表、第 7 节的退出码约定、第 8 节的故障排查。

> ⚠️ **投递不在流水线上。** `pipeline.py --all` 跑完停在「材料已生成」，只把投递命令打印出来。投递要单独执行 `apply.py` 并显式带 `--yes` —— 那是整条链上唯一不可撤销的一步（消息一发对方立刻收到）。

---

## 1. 安装

```bash
git clone https://github.com/zhansan379/boss-crawler-skill.git
cd boss-crawler-skill
pip install -r requirements.txt
```

Python 3.8+。爬取与投递需要本机装有 Chrome。

## 2. 配置模型

三层配置，**命令行 > 环境变量 > 配置文件 > 内置默认**。

**配置文件**（推荐，一次配好）：

```bash
cp assets/llm_config.example.json assets/llm_config.json
# 编辑它，填 base_url / api_key / model
```

（Windows PowerShell：`Copy-Item assets/llm_config.example.json assets/llm_config.json`）

`assets/` 已在 `.gitignore` 里，api_key 不会进仓库。示例文件里列了 DeepSeek、通义、月之暗面、智谱、火山方舟、本地 ollama / vLLM 的 `base_url` 写法。

**环境变量**（临时切换、CI 用）：

```bash
export LLM_BASE_URL=https://api.deepseek.com/v1
export LLM_API_KEY=sk-xxx
export LLM_MODEL=deepseek-chat
# 也认 OPENAI_BASE_URL / OPENAI_API_KEY / OPENAI_MODEL（LLM_* 优先）
```

**按阶段用不同模型**。配置文件里的 `stages` 段可以覆盖任意字段 —— 便宜模型跑解析，强模型跑深度匹配：

```json
{
  "model": "deepseek-chat",
  "stages": {
    "deep":     { "model": "deepseek-reasoner", "concurrency": 6 },
    "greeting": { "temperature": 0.75 }
  }
}
```

阶段名只有五个，**必须逐字写对**：`parse`（简历解析）、`infer`（爬取参数推断）、`deep`（岗位深度分析）、`greeting`（招呼语）、`resume`（简历优化）。写错不会报错，只是那段覆盖静默不生效。

**先验一下再跑**（`pipeline.py` 会在任何 LLM 阶段前自动跑 `--no-call` 预检，这里单独跑是给「只想查配置、不整轮跑」的场景）：

```bash
python scripts/llm_check.py               # 打印生效配置（key 打码）并真发一次请求
python scripts/llm_check.py --no-call     # 只看配置，不花钱
python scripts/llm_check.py --stage deep  # 看某个阶段最终生效的是什么
python scripts/llm_check.py --json        # 额外验 JSON 模式能不能拿到合法 JSON
```

输出里每一项后面都标了它从哪一层来（`← file:stages.deep`、`← env:LLM_MODEL`、`← cli`），「我明明配了却没生效」看这一栏就知道被谁盖了。

> `json_mode` 在部分兼容端点上不被支持，报错就在配置里设 `"json_mode": false` —— 关掉后仍会靠输出解析兜住。

## 3. 跑起来

`pipeline.py` **默认只跑一个阶段**。一次跑一步、每步跑完自己看一眼再决定要不要往下 —— 下游那几步不是免费的：`materials` 按岗位调两次模型（招呼语 + 简历优化），`deep` 按岗位调一次。想一次跑完整轮，显式给 `--all`。

```bash
# 一个阶段一个阶段来（每步跑完都会印出下一步的命令）
python scripts/pipeline.py 简历.pdf              # 只解析简历
python scripts/pipeline.py --from infer          # 只推断爬取参数
python scripts/pipeline.py --from crawl          # 只爬
python scripts/pipeline.py --from match          # 匹配（深度模式下含 deep + merge）
python scripts/pipeline.py --from materials      # 只生成材料
python scripts/pipeline.py --from render         # 只渲染简历长图

# 一次跑完：解析简历 → 推断参数 → 爬取 → 匹配 → 生成材料 → 渲染简历图
python scripts/pipeline.py 简历.pdf --all

# 先看要执行什么，不动任何东西（不会新建运行目录，也不改 LATEST.txt）
python scripts/pipeline.py 简历.pdf --all --dry-run

# 常用组合
python scripts/pipeline.py 简历.pdf --all --city "杭州,上海" --keywords "Python,后端开发" --count 30
python scripts/pipeline.py 简历.pdf --all --match-mode deep --top-n 15   # 逐岗位调模型
python scripts/pipeline.py 简历.pdf --all --match-mode quick             # 纯规则，零 token
python scripts/pipeline.py 简历.pdf --all --no-images                    # 不渲染简历长图
python scripts/pipeline.py 简历.pdf --all --greeting-mode default        # 招呼语套模板，不调模型
```

**首次运行需要登录**。爬虫检测到未登录时会正常退出（不是报错），流水线会停在 crawl 这一步。先单独把登录做掉：

```bash
python scripts/boss_post_interactive.py --ensure-login
```

它会打开浏览器让你扫码，然后**停下来等你按回车**（不轮询、不定时探测）。按回车后复检一次：过了就正常关闭浏览器把会话写进 `assets/chrome_user_data`，并直接印出续跑命令；没过就告诉你还差什么，再按回车重试（最多 5 次，输入 `q` 放弃）。

登录状态是**浏览器正常关闭时**写盘的 —— 所以别在扫完码之后直接叉掉终端或杀进程，那一次的 cookie 可能没落盘，下次爬取还得重扫。让它自己走完复检就行。会话有效期由 BOSS 那边定，被踢掉了重跑这条命令即可。

**阶段区间**。任何一步失败，流水线会打印从这一步接着跑的命令（带上范围，粘回去就是接着跑）：

```bash
python scripts/pipeline.py --run-dir "assets/2026-08-15_10-00-00" --from crawl        # 只重跑爬取
python scripts/pipeline.py --from materials                # 不给 --run-dir 时取 assets/LATEST.txt
python scripts/pipeline.py --from crawl --all              # 从爬取起，一路跑到底
python scripts/pipeline.py --from match --to merge          # 显式区间
```

阶段名：`parse → infer → crawl → match → deep → merge → materials → verify → render`。`deep` / `merge` 只在深度模式下存在，快速模式会自动跳过并补写 `qualified_jobs.json`。

终点是这么定的：

| 给的参数 | 实际跑的阶段 |
|---|---|
| `--from X`（不给终点） | 只跑 `X` |
| `--from match` | `match → deep → merge` |
| `--from deep` | `deep → merge` |
| `--all` | `--from` 起，一路到 `render` |
| `--to Y` | `--from` 起到 `Y` 为止 |

`--all` 就是 `--to render`，两个只能给一个（同时给按用法错误退出，码 2）。

`--from match` 是唯一一个「一个阶段」不止一步的地方：深度模式下单跑 `match` 只产出 `deep_candidates.json`，没有 `qualified_jobs.json` —— 而下游（`materials` / `render` / `apply`）全都只认后者，停在那里是个谁都用不了的半成品。

`--no-images` 把终点从 `render` 下压到 `verify`，**不是** `materials`：不要长图不代表不用核查，招呼语照样会发出去。所以 `--from render --no-images` 没有阶段可跑，会直接报错而不是空跑一轮。

### verify 阶段

`materials` 之后、`render` 之前插了一步 `verify_no_fabrication.py`：把要发出去的文本（`optimized_resume` + 招呼语）里的技术词，跟 `resume_text.txt` / `profile.json` 对一遍，报出没有出处的那些。纯标准库，不调模型，几秒钟。

| 参数 | 作用 |
|---|---|
| `--allow 词表` | 放行这些技术词，逗号分隔，可重复给。透传给 `verify_no_fabrication.py` |
| `--skip-verify` | 整步跳过（跳过就得自己逐份看材料） |
| `--only` | 与 `materials` / `render` 共用同一份序号 |

> `--skip-verify` 在两个脚本里是**两件不同的事**：`pipeline.py --skip-verify` 跳过的是这里的技术词核查；`apply.py --skip-verify` 跳过的是附件图的 `verify_image.py` 体检。两条命令从不同时出现，但别把结论互相搬。

**退出码 1 在这一步的含义不一样：它是「查出来了」，不是「坏了」。** 流水线会停在 `render` 之前 —— 长图一渲出来，材料在人眼里就定稿了。逐条看完上下文后二选一：确属编造就 `gen_materials.py --only <序号> --force` 重生成；有据可依就加 `--allow` 续跑。

查不出的东西也要知道：夸大（「了解」写成「精通」）、实习时长注水、语气不像本人，一个都查不出。另外只认拉丁字母开头的词（中文短语不参与），`optimization_suggestions` 故意不查 —— 那一栏本职工作就是提简历里还没有的技术。

## 4. 投递（单独一条命令）

```bash
python scripts/apply.py "assets/2026-08-15_10-00-00"              # 演练：列出可选公司 + 打印计划
python scripts/apply.py "assets/2026-08-15_10-00-00" --yes        # 真投
python scripts/apply.py "assets/…" --yes --company 百度,棱镜数聚   # 只投这两家
python scripts/apply.py "assets/…" --yes --only 1,3,5 --max 3     # 按序号只投这几个、最多 3 个
python scripts/apply.py "assets/…" --yes --no-image               # 不发简历图
```

不加 `--yes` 时**不做任何浏览器操作**，只把「有哪些公司可选」「要投谁、用哪条招呼语、发哪张图」列出来。建议先演练一次，确认名单和招呼语，再加 `--yes`。

挑公司投有两种写法，可以叠加使用（取交集）：

- `--company 百度,棱镜数聚` —— 按公司名，逗号分隔。支持简称（子串匹配，`铁牛` 命中 `铁牛科技`）；同一家公司挂了多个岗位时会**一起**选中，命中明细会打印出来。
- `--only 1,3` / `--only 9-11` —— 按序号，逗号分隔单个、短横线表示连续区间。序号就是演练清单里 `#N` 的那个 N。

不确定填什么就先跑一次演练：它开头会列出本轮所有公司、每家对应的序号，以及**按本轮真实数据拼出来的**可填示例，复制即可用。

`--company` 里任何一个名字没匹配上（打错字、这一轮没爬到这家），整条命令直接失败，**已命中的公司也不会投** —— 消息发出去撤不回，宁可让用户重敲一次。可选值就是演练开头那张公司清单。

投递前会自动跑 `verify_image.py` 体检每张简历图（尺寸、内容占比、是否整张空白）—— 空白图发出去比不发更糟。想收窄投递池就用 `--only` / `--company`，**别去编辑 `qualified_jobs.json`**：文件里的 1-based 序号是招呼语、简历、简历图三者的对齐键，删一行就整片错位（见第 6 节）。

## 5. 单独跑某一步

每个阶段都是可以独立执行的脚本。所有脚本都有 `--help`。

| 阶段 | 命令 | 读 | 写 |
|---|---|---|---|
| 解析简历 | `parse_resume.py 简历.pdf` | PDF/Word/md/txt | `profile.json` |
| 推断参数 | `infer_params.py <run_dir>` | `profile.json` | `crawl_params.json` |
| 爬取岗位 | `boss_post_interactive.py -m custom -p "Python" -c "杭州" -n 20 -d -y --run-dir <run_dir>` | BOSS 直聘 | `assets/post_data/*.csv`、`crawl_summary.json` |
| 规则匹配 | `run_matcher.py --mode quick --profile <run_dir>/profile.json -o <run_dir>` | CSV + profile | `scored_jobs.json`、HTML 报告 |
| 深度预筛 | `run_matcher.py --mode deep --profile … --top 15 -o <run_dir>` | CSV + profile | `deep_candidates.json` |
| 深度分析 | `deep_analyze.py <run_dir>` | `deep_candidates.json` | `deep_results.json` |
| 合并出报告 | `run_matcher.py --mode deep --merge -o <run_dir>` | 上面两个 | `qualified_jobs.json`、HTML 报告 |
| 生成材料 | `gen_materials.py <run_dir>` | `qualified_jobs.json` + profile | `generated/greeting_*.txt`、`generated/resume_*.json` |
| 渲染简历图 | `render_images.py <run_dir>` | `generated/resume_*.json` | `applications/<公司>-<岗位>/<姓名>-<岗位>.png` |
| 可读投递材料 | `write_application_md.py <run_dir> --all` | `qualified_jobs.json` + 原始 CSV + 招呼语 | `applications/<公司>-<岗位>/岗位信息+招呼语.md` |
| 投递 | `apply.py <run_dir> --yes` | 以上全部 | `apply_log.json` |

爬取那一行的 `--run-dir` 别省：`crawl_summary.json` 只在传了它的时候才写，而流水线正是靠这个文件判定「这一轮到底爬没爬」（爬虫检测到未登录时是**正常退出**的，光看退出码分不出来）。

「可读投递材料」那一行现在**挂在 `materials` 阶段之后**：`pipeline.py --from materials`（或整轮 `--all`）会在材料生成后自动跑 `write_application_md.py --all`，不用单独跑它。它不依赖长图渲染，所以 `--resume-mode skip` / `--no-images` 时也照写。

同理还有两道自动子步骤：**`render` 之后自动跑 `verify_image.py <run_dir>/applications --all`** 图检（图刚渲出来就把几十行数字打给模型看，替代 Read 一张 640k token 的 PNG）；**计划里含任何 LLM 阶段时，`parse` 之前自动跑 `llm_check.py --no-call` 预检配置**（缺了早停，不把最贵的爬取/匹配跑死在配置上）。

### parse_resume.py — 简历 → profile.json

```bash
python scripts/parse_resume.py 简历.pdf                      # 新建时间戳运行目录
python scripts/parse_resume.py 简历.pdf -o "assets/2026-08-15_10-00-00"
python scripts/parse_resume.py 简历.pdf --cross-check        # 校验发现缺口时再让模型复核一遍
python scripts/parse_resume.py 简历.pdf --force              # 覆盖已有 profile.json
```

解析完会跑一遍字段校验并把缺口打印出来（缺联系方式、没有项目经历之类）。`--cross-check` 会带着缺口清单再做一次全文推理，多花一次调用换准确度。

### infer_params.py — profile → 爬取参数

```bash
python scripts/infer_params.py "assets/2026-08-15_10-00-00"
python scripts/infer_params.py <run_dir> --city "杭州,上海" --keywords "Python,Go"
python scripts/infer_params.py <run_dir> --match-mode deep --count 30 --min-count 40
python scripts/infer_params.py <run_dir> --save            # 同时写 assets/preferences.json
python scripts/infer_params.py <run_dir> --dry-run         # 只打印提示词
```

任何字段都能用命令行覆盖推断结果；传空串表示不筛该项（如 `--salary ""`）。**参数全部由命令行给出时不会调用模型。** 公司规模（`--scale`）不做推断 —— 简历里没有能推出它的信息。

**城市是唯一必须有值的字段**（关键词模型基本都能推出来，城市推不出来就只能停）。全国搜是一个**城市值**，不是「省略即全国」：

```bash
python scripts/infer_params.py <run_dir> --city 全国            # BOSS 的全国代码，一次搜完（也可写 不限）
python scripts/infer_params.py <run_dir> --city "西安,北京,杭州"  # 多个城市，逐个爬
python scripts/boss_post_interactive.py --list-cities           # 全部城市名
```

省略 `--city` 时脚本会直接停下（退出码 1）并把上面这些印出来 —— 不会悄悄按全国去爬。

**experience 默认 `["经验不限"]`，把收窄留给匹配打分 —— 别在爬取阶段就把池子筛空**（1 个关键词 + 应届生筛选曾只爬到 19 条，白爬一场）。只有简历明确是应届（毕业年份就是今年且已过 7 月）或在校找实习（毕业年份 > 今年）才给 `["应届生"]` / `["在校生"]`。**job_type 另说，它是拿今天的日期跟毕业年份比出来的**：提示词在 `scripts/prompts/crawl_params.st`（约束 6），脚本默认把系统日期传进去：毕业年份晚于今年 → 在校 → `["实习"]`；毕业年份就是今年则按 6 月毕业季再比月份；已毕业 → `["全职"]`。简历里明确在找实习（期望职位带「实习」、给了可实习时长）时按实习算，不看日期。

```bash
python scripts/infer_params.py <run_dir> --today 2027-03-01   # 换个「今天」再推一次
python scripts/infer_params.py <run_dir> --dry-run            # 看装配好的提示词，含日期那行
```

`--today` 只影响这条判断，用来预演「明年三月我该投实习还是全职」，或者机器时钟不对时兜一手。不传就取系统日期。改判定口径直接改 `.st` 文件，不用碰 Python —— 但那四行枚举（`{salary}` / `{experience}` / `{degree}` / `{job_type}`）是从爬虫的 `FILTER_LABELS` 注进去的，别在模板里抄一份写死。

### deep_analyze.py — 逐岗位调模型

```bash
python scripts/deep_analyze.py <run_dir>
python scripts/deep_analyze.py <run_dir> --workers 6 --model deepseek-reasoner
python scripts/deep_analyze.py <run_dir> --limit 3        # 先拿 3 个试水，看看质量和花费
python scripts/deep_analyze.py <run_dir> --resume         # 续跑，跳过已有结果的 rank
python scripts/deep_analyze.py <run_dir> --dry-run        # 打印将发什么、发几次，不花钱
```

按岗位并发。单个岗位失败不影响其余（退出码 3），失败的用 `--resume` 补。合并时未分析成功的岗位会沿用规则侧分类，**不会丢岗位**。

### gen_materials.py — 招呼语 + 优化简历

```bash
python scripts/gen_materials.py <run_dir>
python scripts/gen_materials.py <run_dir> --greeting-mode default   # 套规则模板，不花钱
python scripts/gen_materials.py <run_dir> --resume-mode skip        # 只出招呼语
python scripts/gen_materials.py <run_dir> --only 1,3,5-7            # 只补这几个
python scripts/gen_materials.py <run_dir> --force                   # 覆盖已有产物
python scripts/gen_materials.py <run_dir> --scene 实习
```

默认**跳过已有产物**，所以重跑一次就是「补齐缺的」，手工改过的招呼语不会被冲掉（要覆盖得显式 `--force`）。

招呼语生成时会检查前 15 个字 —— HR 在消息列表里只看得到这么多，被「您好，我对贵公司…」占掉等于白发。检测到就自动重写一次。

### render_images.py — 简历 JSON → PNG

```bash
python scripts/render_images.py <run_dir>
python scripts/render_images.py <run_dir> --mode paginated --scale 2
python scripts/render_images.py <run_dir> --only 1,3
python scripts/render_images.py <run_dir> --headless
```

通过内置的 ShowCV 编辑器渲染，所有简历共用一个浏览器和一份 localStorage，**因此必须串行，没有 `--workers`**。

> ⚠️ 端口 9333 上如果已经有别的 Chrome（比如 Skill 路径开的那个），本步骤会拒绝继续 —— DrissionPage 会静默接管已有浏览器并忽略 `--user-data-path`，那样清理临时简历时删的是别人 ShowCV 里的真实简历。确认无害再用 `--adopt-browser`。

## 6. 运行目录里有什么

所有产物落在 `assets/<时间戳>/`，每轮独立隔离。`assets/LATEST.txt` 指向最近一轮。

```
assets/2026-08-15_10-00-00/
├── profile.json            简历结构化结果
├── crawl_params.json       爬取参数（关键词/城市/筛选项/匹配模式）
├── crawl_summary.json      这一轮真的爬到了东西的证据
├── scored_jobs.json        规则评分分档（tier1..tier4）
├── deep_candidates.json    深度模式：预筛选出的候选 + profile
├── deep_results.json       深度模式：逐岗位的模型分析
├── qualified_jobs.json     ★ 投递候选池（自动生成，**别手工编辑** —— 序号是下游的对齐键）
├── matching_report.html    可视化报告
├── generated/
│   ├── greeting_1_甲公司.txt      招呼语（序号 = 在 qualified_jobs.json 里的位置）
│   └── resume_1_甲公司.json       优化后的简历
├── applications/
│   └── 甲公司-AI应用开发工程师/
│       ├── 张三-AI应用开发工程师.md    优化后的简历（Markdown，render_images.py 顺手写的）
│       ├── 张三-AI应用开发工程师.png   投递用简历长图（HR 看到的就是这个文件名）
│       ├── 岗位信息+招呼语.md          岗位全字段 + 招呼语（materials 后自动写）
│       └── 优化建议.md                 简历优化建议（render_images.py 顺手写的）
├── apply_log.json          投递记录
├── run_timings.jsonl       各阶段耗时
└── llm_usage.jsonl         每次模型调用的 token 与重试
```

`generated/` 里文件名的 `{序号}` 是岗位在 `qualified_jobs.json` 里的 1-based 位置，下游全靠它把招呼语、简历、岗位三者对上 —— **别手工重命名，也别删改 `qualified_jobs.json` 的行或顺序**。要少投几个用 `--only`，那是为此存在的参数。

查看状态与花费：

```bash
python scripts/where_am_i.py                       # 这一轮跑到哪了、缺什么
python scripts/stage_timer.py report <run_dir>     # 各阶段耗时
python scripts/check_artifacts.py <run_dir>        # 材料齐不齐（缺谁的哪一项）
```

## 7. 退出码

脚本之间的约定，方便串到 shell 或 CI 里：

| 码 | 含义 |
|---|---|
| `0` | 成功 |
| `1` | 前置条件不满足 / 输入缺失 / 全部失败（通常什么都没产出） |
| `2` | 用法错误（argparse） |
| `3` | **部分成功** —— 产物已写出，但有条目失败或缺失 |

`3` 是最需要注意的那个：它意味着「能用，但不全」。多数情况下重跑同一条命令就会只补缺的部分。

**唯一的例外是 `llm_check.py`：它的 `2` 表示「配置齐全但端点连不上」**（`llm_check.py:90,103`，
真实语义见 [scripts.md](scripts.md) 的 `0 可用 / 1 缺失或非法 / 2 已配置但不可达`）。
判断 LLM 配置时按这套读，别套上面那张表。

```bash
python scripts/pipeline.py 简历.pdf --all; code=$?
if [ $code -eq 3 ]; then echo "部分产物缺失，检查后重跑"; fi
```

## 8. 排错

| 现象 | 原因与处理 |
|---|---|
| `❌ 没有配置 api_key` | 见第 2 节；`llm_check.py --no-call` 看当前生效的是哪一层 |
| 提示要扫码登录，之后什么都没爬到 | 爬虫未登录时会正常退出（退出码 0），流水线靠 `crawl_summary.json` 判定这一轮到底爬没爬。先跑 `--ensure-login`（见第 4 节），它会等你扫完码复检并印出续跑命令 |
| `can't open file 'boss_post_interactive.py'` | 脚本在 `scripts/` 下，从仓库根目录要写 `python scripts/boss_post_interactive.py …`。提示里的路径现在按当前目录算，照着粘就行 |
| 扫完码了，下次爬取还是说未登录 | 会话是浏览器**正常关闭**时写盘的。让 `--ensure-login` 自己走完复检，别中途叉掉终端或杀进程 |
| `❌ 没有推断出城市，也没有 --city` | 简历里没写期望城市，模型不会替你编 —— **不给城市不等于全国搜**，全国得显式写 `--city 全国`（或 `不限`）。报错时会把完整命令和常见参数都印出来。注意只补 `--city` 会再调一次模型，想完全跳过推断得连 `--keywords` 一起给，但那样学历/经验/工作类型也要自己填 |
| `❌ 岗位池只有 N 行，低于最低岗位数` | 池子太小不值得往下烧 token。放宽筛选重爬，或 `--from match --min-jobs 0` 强行继续 |
| 配置了 `stages.xxx` 但没生效 | 阶段名只有 `parse/infer/deep/greeting/resume` 五个，拼错会静默忽略。用 `llm_check.py --stage deep` 确认 |
| `json_mode` 报 400 | 端点不支持 `response_format`，配置里设 `"json_mode": false` |
| 429 / 超时 | 已内置指数退避重试；持续 429 就降 `concurrency`。401/404/400 不重试（重试三遍错的 key 只是多等三轮） |
| 渲染报「端口 9333 上是别的浏览器」 | 关掉那个 Chrome，或确认无害后加 `--adopt-browser`（会动它的 localStorage） |
| 投递材料里招呼语一栏是空的 | 材料文件名的序号与 `qualified_jobs.json` 的顺序对不上了。`gen_materials.py <run_dir> --force` 重生成 |
| Windows 终端中文乱码 | 每个入口脚本都会把 stdout 切到 UTF-8；如果是自己包了一层调用，记得设 `PYTHONIOENCODING=utf-8` |

## 9. 测试

不发任何真实请求、不碰浏览器，可离线跑：

```bash
for f in scripts/test_*.py; do python "$f" || echo "FAIL $f"; done
```

其中几个是接缝测试，值得知道它们在盯什么：

- `test_llm_client.py` — JSON 提取三形态、重试边界（429 重试 / 401 不重试）、并发隔离与顺序、配置优先级、api_key 不出现在任何报错或日志里
- `test_deep_analyze.py` — 产出真的能被**真实的** `merge_deep_results` 吃下，rank 不错位
- `test_gen_materials.py` — 文件名真的能被**真实的** `check_artifacts` 和 `write_application_md` 的 glob 认出（公司名带 `[` 时尤其）
- `test_pipeline.py` — 阶段区间（不给 `--to` 只跑一个阶段）、闸门，以及「全程没有任何一条命令指向 `apply.py`」
- `test_infer_hint.py` — 缺城市/关键词时印出的修复命令真的能直接粘（跨进程契约：`pipeline` 塞环境变量、`infer_params` 读它决定印哪种形态）
- `test_infer_prompt.py` — `crawl_params.st` 的占位符和 Python 传的关键字一一对上（对不上时 `str.format` 抛 KeyError，而这要跑到 infer 阶段才炸），今天的日期确实进了提示词，枚举确实来自 `FILTER_LABELS`
- `test_ensure_login.py` — 同一条 `--ensure-login` 要服务两种调用者：命令行下必须等回车复检，skill 路径（stdin 是管道）下**一次都不许调 `input()`**，否则会话会挂死等一个永不到来的回车。判据是 `isatty()`
- `test_where_am_i.py` — 从产物反推阶段的判据（文件存在**且非空**）、印出的续跑命令真的能粘，以及输出长度不会撑爆上下文
- `test_apply_gate.py` — 不带 `--yes` 时投递函数零调用
