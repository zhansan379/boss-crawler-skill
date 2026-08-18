# 方式二：命令行上手

> `README.md`「使用方式 → 方式二」的展开。命令行与 Skill 用**同一套脚本、同一个模型接口和数据产物**，只是参数一次给全、不再逐步问你，适合定时任务、批量重跑。
>
> 本文是「快速上手」；**每个 stage 的参数、退出码细则、排错**见 [references/cli.md](../references/cli.md)（完整命令行参考）。模型配置见 [README 安装第 3 步](../README.md)。

## 一次跑完

```bash
# 0. （可选）先看要执行什么，不动任何东西，也不建运行目录
python scripts/pipeline.py 简历.pdf --all --dry-run

# 1. 一条命令跑完：解析简历 → 推断参数 → 爬取 → 匹配 → 生成材料 → 渲染简历图
python scripts/pipeline.py 简历.pdf --all --city "杭州,上海" --keywords "Python,后端开发" --count 30
```

> ⚠️ 不给 `--all` 时 `pipeline.py` 默认只跑**一个**阶段就停下并打印下一步命令——`materials` 按岗位调两次模型、`deep` 按岗位调一次，这些钱不该在「先跑跑看」时花掉。

## 投递（单独一步，必须显式 `--yes`）

```bash
python scripts/deliver/apply.py "assets/<时间戳>"                            # 演练，顺便看有哪些公司可选
python scripts/deliver/apply.py "assets/<时间戳>" --yes --max 5
python scripts/deliver/apply.py "assets/<时间戳>" --yes --company 百度,棱镜数聚  # 只投指定公司
```

> ⚠️ 投递不在流水线上。`pipeline.py --all` 跑完停在「材料已生成」，只打印投递命令——投递是整条链上唯一不可撤销的一步（消息一发对方立刻收到），不该由「跑全流程」顺手完成。也有人问 `--max 5` 是不是「最好的 5 个」——不是，它是候选池顺序的前 5 个（符合在前、需优化在后），要最好的 N 个请从报告读显式下标。

## 续跑 / 定位进度 / 忠于产物

```bash
python scripts/pipeline.py --from crawl                 # 只重跑爬取（自动定位 assets/LATEST.txt）
python scripts/pipeline.py --from crawl --all           # 从爬取起一路跑到底
python scripts/pipeline.py --run-dir "assets/<时间戳>" --from materials
python scripts/utils/where_am_i.py                           # 这一轮跑到哪了、缺什么
```

**用文件和退出码判断一个阶段，别凭印象或通知。** 退出码：`0` 成功、`1` 前置条件不满足或整体失败、`2` 参数用错、`3` **部分成功**（产物写了但不齐，如 20 个岗位里有 2 个模型调用失败——补那 2 个，别重跑整个阶段）。

## 单独跑某一步

```bash
python scripts/pipeline.py "D:\Download\browserDownload\简历.md" --to parse                  # → profile.json
python scripts/stages/run_matcher.py --mode quick --profile assets/<ts>/profile.json -o assets/<ts>
python scripts/stages/deep_analyze.py assets/<ts> --limit 3     # 先拿 3 个试水，看质量和花费
python scripts/stages/gen_materials.py assets/<ts> --only 1,3   # 只补这几个（默认跳过已有产物）
```

所有脚本都有 `--help`。**产物格式与 Skill 路径完全一致，可以混着用**：Skill 跑到一半接着用命令行续跑同一个运行目录，反之亦然。区别只在谁来问你——Skill 在投递范围和发送前各有一道确认闸门，命令行把这两道换成显式的 `--only` 和 `--yes`。
