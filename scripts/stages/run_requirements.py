#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""批量抽取岗位权威要求 → assets/job_requirements.json。

以 `岗位要求和职责`(detail.jd 文本) 为唯一事实来源，用 LLM 抽出 学历/经验/薪资/技能
权威要求，供评分/投递判定使用。结果按 link 缓存、幂等：已缓存的链接默认跳过，
--all 才会无条件重抽。配合 run_matcher 使用（run_matcher 自动读这份缓存）。

用法:
  python scripts/stages/run_requirements.py                 # 增量：只抽没缓存的
  python scripts/stages/run_requirements.py --all           # 无条件重抽全部含 JD 的岗位
  python scripts/stages/run_requirements.py --workers 6     # 并发数（默认取配置）
  python scripts/stages/run_requirements.py --dry-run       # 只统计将抽的链接，不发请求

退出码: 0=全部成功 / 3=部分失败（成功的已落缓存，失败的评分回退卡片）
"""

import argparse
import os
import sys

_SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _SCRIPTS)

from llm.client import map_concurrent, reconfigure_stdout
from llm.config import ConfigError, resolve
from resume_matcher import list_available_job_files, load_job_data
from resume_matcher.requirements import (
    STAGE, extract_job_requirements, load_all, save_all,
)


def main():
    reconfigure_stdout()

    ap = argparse.ArgumentParser(
        description='批量抽取岗位权威要求（学历/经验/薪资/技能）→ assets/job_requirements.json',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument('--all', action='store_true', help='无条件重抽全部含 JD 的岗位')
    ap.add_argument('--workers', '-w', type=int, help='并发数，默认取配置 concurrency')
    ap.add_argument('--dry-run', action='store_true', help='只统计将抽的链接，不发请求')
    ap.add_argument('--run-dir', help='记账 run_dir（写 llm_usage.jsonl，可选）')
    args = ap.parse_args()

    try:
        cfg = resolve(stage=STAGE)
    except ConfigError as exc:
        print('❌ 配置错误：\n%s' % exc)
        return 1
    workers = args.workers or cfg.concurrency

    # ── 加载岗位池 ──
    job_files = list_available_job_files()
    if not job_files:
        print('❌ 未找到岗位数据文件（assets/post_data/ 下无 CSV）')
        return 1
    jobs = load_job_data([jf['path'] for jf in job_files])

    cached = load_all()
    todo = []
    for job in jobs:
        if not (job.get('岗位要求和职责') or '').strip():
            continue                            # 无 JD 则无从抽，评分回退卡片
        link = (job.get('link') or '').strip()
        if not link:
            continue
        if args.all or not cached.get(link):
            todo.append(job)

    with_jd = sum(1 for j in jobs if (j.get('岗位要求和职责') or '').strip())
    suffix = '（--all 无条件）' if args.all else '（增量：跳过已缓存）'
    print(f'岗位 {len(jobs)} 个；含 JD 可抽 {with_jd} 个；本次将抽 {len(todo)} 个 {suffix}')

    if args.dry_run:
        print('--dry-run：不发起请求，结束')
        return 0
    if not todo:
        print('✅ 无需抽取（全部已缓存或无 JD），评分将直接用岗位要求')
        return 0

    def _run(candidate):
        return extract_job_requirements(candidate.get('岗位要求和职责', ''),
                                        run_dir=args.run_dir, cfg=cfg)

    outcomes = map_concurrent(
        todo, _run, workers=workers,
        label=lambda job, _i: (job.get('职位') or '') + ' · ' + (job.get('公司') or ''))

    mapping = dict(cached)
    failures = 0
    for outcome in outcomes:
        link = (outcome.item.get('link') or '').strip()
        if outcome.ok:
            mapping[link] = outcome.value
        else:
            failures += 1

    save_all(mapping)
    new = len(mapping) - len(cached)
    print(f'写入缓存 {len(mapping)} 条（新增/更新 {new} 条）')
    if failures:
        print(f'⚠️  {failures}/{len(todo)} 个岗位抽取失败，已用旧缓存兜底，评分回退卡片')
        return 3
    print('✅ 权威要求抽取完成，评分将按岗位要求判定')
    return 0


if __name__ == '__main__':
    sys.exit(main())