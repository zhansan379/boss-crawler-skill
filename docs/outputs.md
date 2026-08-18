# 运行数据在哪里

> `README.md`「运行数据在哪里」的展开。Skill 目录一般在 `C:\Users\用户名\.claude\skills` 或当前项目的 `.claude\skills`。所有运行产物都落在 Skill 目录下，**每次运行独立隔离在一个时间戳子目录** `assets/<timestamp>/`（例如 `assets/2026-08-14_16-05-21/`）。

## 数据存放位置

| 位置                  | 内容                                                       |
| --------------------- | ---------------------------------------------------------- |
| `assets/post_data/`   | 爬取的岗位 CSV（按关键词/城市分文件，**跨运行累积**）      |
| `assets/chrome_user_data/` | 你的 BOSS 直聘登录 cookie（**含隐私，勿提交 Git / 分享**） |
| `assets/<timestamp>/` | 本次运行的全部产物（简历、匹配、投递、报告）               |

## 时间戳目录下你会得到的数据（按用途）

| 数据                  | 路径（均在 `assets/<timestamp>/` 下）                        | 用途                                                     |
| --------------------- | ------------------------------------------------------------ | -------------------------------------------------------- |
| **简历图片**          | `deliver/#N-{公司}-{职位}/{姓名}-{职位}.png`                | 不含个人信息的定制简历图片，可直接上传/发送给 HR         |
| **岗位信息 + 招呼语** | `deliver/#N-{公司}-{职位}/岗位信息+招呼语.md`                          | 该岗位采集信息 + 个性化招呼语 + 匹配分析，方便核对/手动发送 |
| **简历优化建议**      | `deliver/#N-{公司}-{职位}/优化建议.md`                      | 只含优化建议，给「改简历」用，跟岗位信息+招呼语.md 一并生成         |
| **可视化匹配报告**    | `deliver/matching_report.html`                               | 全部岗位的匹配度卡片、分类统计、投递状态，浏览器打开即看 |
| **岗位评分明细**      | `state/qualified_jobs.json`                                  | 投递候选池：每个岗位的评分、匹配理由、缺失技能、风险提示（自动生成，别手工改——序号是下游对齐键） |
| **逐岗匹配裁定**      | `state/match_analysis.json`                                  | merge 时按 link 落盘的逐岗位深度分析裁定，供岗位信息+招呼语.md 回填 |
| **投递记录**          | `state/apply_log.json`                                       | 每次投递的时间、岗位、结果，方便复盘                     |
| **爬取统计**          | `state/crawl_summary.json`                                   | 本次爬取写入/跳过/去重数量                               |
| **简历解析结果**      | `state/profile.json` / `state/profile_validation.json`       | 结构化简历字段 + 交叉校验结果                            |
| **LLM 源**            | `materials/greeting_#_*.txt` / `materials/resume_#_*.json`   | 招呼语与优化后简历（再渲染与核对靠它，花钱产的）         |
| **深度分析明细**      | `intermediate/deep_candidates.json` / `intermediate/deep_results.json` | LLM 深度匹配的候选与理由                                 |
| **编辑器导出**        | `intermediate/exports/` / `intermediate/staging/`            | ShowCV 编辑器导出的简历图片与中间稿                      |
| **阶段耗时**          | `intermediate/run_timings.jsonl`                             | 各阶段耗时埋点，便于优化                                 |
| **模型花费**          | `intermediate/llm_usage.jsonl`                               | 每次模型调用的 token 与重试次数                          |

> **对你最有用的**是 `deliver/#N-{公司}-{职位}/` 下的三件套：定制简历图片 + 岗位信息+招呼语.md + 优化建议.md——它们就是投递时真正发出去的东西，随时可手动复用或再次投递。`intermediate/` 里的深度分析、日志、ShowCV 暂存都是跑完即无用的，用 `python scripts/utils/clean_run.py <run_dir>` 整体清掉。