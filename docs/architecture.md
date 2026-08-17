# 架构

> `README.md`「架构」的展开：两条运行路径共享同一套引擎，产物落到同一份目录结构。

## 数据流

```
两条路径，同一套脚本、同一个模型接口、同一份产物：

Skill 路径                              命令行路径
SKILL.md（逐阶段问你确认）               scripts/pipeline.py（参数一次给全）
    └───────────────┬───────────────────────┘
                    ▼
    scripts/llm/              统一模型客户端（anthropic + OpenAI 兼容，三层配置 + 重试 + 并发）
    scripts/prompts/*.st      5 个提示词模板（解析/深度匹配/招呼语/简历优化/参数推断）
    scripts/boss_crawler/     爬取   ──→  assets/post_data/**.csv
    scripts/resume_matcher/   评分   ──→  scored_jobs.json / matching_report.html
                    ▼
        assets/<ts>/state/qualified_jobs.json  ← 投递候选池（自动生成；收窄用 --only，别改文件）
                    ▼
    apply.py --yes ／ resume_matcher/auto_apply.py   浏览器自动投递

app/ 内置 ShowCV 编辑器 ──→ 简历长图渲染（deliver/render_images.py）／ assets/<ts>/intermediate/exports/
```

**两条路径共享同一套引擎**——只是「谁来问参数」不同：Skill 路径由 `SKILL.md` 逐阶段向你确认，命令行路径由 `pipeline.py` 把参数一次给全。投递是整条链上唯一不可撤销的一步，所以两条路径都不自动触发投递，必须显式 `apply.py --yes`。

## 目录结构

```
boss-crawler/                         # Skill 根目录 (~/.claude/skills/boss-crawler/)
├── SKILL.md                          # 🔑 Skill 定义（编排完整流程）
├── README.md                         # 使用文档（核心）
├── requirements.txt                  # 依赖（分「必需」和「按需」两段）
│
├── docs/                             # 📚 使用文档拆分（README 拆出）
│   ├── architecture.md               # 📖 本文件：架构与目录
│   ├── usage-skill.md                # 📖 方式一：Skill 图文走查
│   ├── usage-cli.md                  # 📖 方式二：命令行上手
│   └── outputs.md                    # 📖 运行数据与产物清单
│
├── assets/                           # 📦 运行输出（无需加载到上下文的最终产物）
│   ├── llm_config.json               # 🔑 你的模型配置（api_key 在这里，不进 Git）
│   ├── preferences.json              # 爬取参数预设（路径 C 重放用）
│   ├── post_data/                    # 爬取的岗位 CSV（跨运行累积）
│   ├── chrome_user_data/             # BOSS 直聘登录 cookie（含隐私）
│   ├── LATEST.txt                    # 最近运行指针
│   └── <timestamp>/                  # 每次运行隔离到时间戳子目录
│       ├── state/                    # 机器状态（续跑/回溯用，人不直接看）
│       │   ├── profile.json          # 简历结构化解析
│       │   ├── profile_validation.json # 简历交叉校验
│       │   ├── crawl_params.json     # 爬取参数（关键词/城市/筛选项/匹配模式）
│       │   ├── crawl_summary.json    # 爬取统计（也是「这轮真爬到了」的证据）
│       │   ├── scored_jobs.json      # 规则评分分档（tier1..tier4）
│       │   ├── qualified_jobs.json   # 投递候选池（自动生成，序号是下游对齐键）
│       │   ├── match_analysis.json   # 逐岗位深度匹配裁定
│       │   ├── apply_log.json        # 投递记录
│       │   └── resume_text.txt       # 简历纯文本
│       ├── materials/                # LLM 源（花钱的，再渲染靠它）
│       │   ├── greeting_#_*.txt      # 个性化招呼语
│       │   └── resume_#_*.json       # 定制简历 JSON（含优化后简历 + 建议）
│       ├── deliver/                  # 🎁 最终交付（人看的）
│       │   ├── matching_report.html  # HTML 可视化报告
│       │   └── #N-{公司}-{职位}/     # 每个岗位：简历图片 + 投递.md + 优化建议.md
│       └── intermediate/             # 跑完即无用：clean_run.py 可整体删
│           ├── deep_candidates.json  # 深度模式候选
│           ├── deep_results.json     # 深度分析结果
│           ├── run_timings.jsonl     # 各阶段耗时
│           ├── llm_usage.jsonl       # 每次模型调用的 token 与重试
│           ├── staging/              # 渲染长图时的中间稿
│           └── exports/              # ShowCV 编辑器导出（简历图片 zip）
│
├── app/                              # 🌐 内置 ShowCV 简历编辑器（静态站点 + 字体）
│
├── references/                       # 📚 CLI 完整参数参考
│   └── cli.md                        # 📖 命令行路径完整用法与排查
│
└── scripts/                          # 🐍 可执行脚本（按阶段/模块分目录）
    ├── pipeline.py                   # 🔑 一条命令串完 9 个阶段（不含投递）
    ├── stages/                       # 流水线各阶段入口（参数详见 references/cli.md）
    │   ├── parse_resume.py           # 简历 → profile.json
    │   ├── infer_params.py           # profile → crawl_params.json
    │   ├── boss_post_interactive.py  # 爬虫 CLI 入口
    │   ├── run_matcher.py            # 匹配系统 CLI 入口
    │   ├── deep_analyze.py           # 逐岗位调模型 → deep_results.json
    │   ├── gen_materials.py          # 招呼语 + 优化简历 → materials/
    │   └── validate_profile.py       # Profile 交叉校验
    ├── deliver/                      # 投递
    │   ├── write_application_md.py   # 组装 deliver/ 下的投递.md + 优化建议.md
    │   ├── render_images.py          # 简历 JSON → 简历长图（串行，共用一个浏览器）
    │   └── apply.py                  # 投递（必须显式 --yes，不加只演练）
    ├── verify/                       # 校验（图/内容质量门）
    │   ├── verify_no_fabrication.py  # 查材料里有没有简历原文没有的技术词（查到就拦住 render）
    │   └── verify_image.py           # 简历图体检（空白图发出去比不发更糟）
    ├── utils/                        # 工具
    │   ├── llm_check.py              # 体检模型配置（打印生效值 + 试发一次）
    │   ├── clean_run.py              # 清掉 intermediate/（无用桶），保留 state/materials/deliver
    │   ├── where_am_i.py             # 从产物反推当前阶段，提示下一步命令
    │   └── read_thin.py              # 瘦读数据文件（避免撑爆上下文）
    ├── check_artifacts.py            # 材料齐不齐（缺谁的哪一项）
    ├── match_index.py                # 匹配索引（材料/瘦读共用）
    ├── preferences.py                # 爬取参数预设
    ├── stage_timer.py                # 阶段计时埋点
    ├── boss_crawler/                 # 爬虫引擎包
    ├── llm/                          # 模型客户端（三层优先级 + 重试 + 并发）
    ├── resume_matcher/               # 匹配引擎包（评分/解析/报告/投递/深度分析）
    ├── showcv/                       # 简历长图渲染（ShowCV 编辑器）
    └── prompts/                      # LLM 提示词模板 (.st)，两条路径共用
        ├── resume_parse.st           #   简历 → 结构化字段
        ├── match_analysis.st         #   岗位深度匹配打分
        ├── greeting.st               #   个性化招呼语
        ├── resume_optimize.st        #   针对岗位优化简历
        └── crawl_params.st           #   简历 → 爬取参数推断
```

> **登录 cookie 与爬取数据都在 `assets/` 下**（`assets/chrome_user_data/`、`assets/post_data/`），由脚本运行时自动创建，整个 `assets/` 已在 `.gitignore` 里。