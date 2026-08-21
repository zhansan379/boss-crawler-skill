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

from llm.client import reconfigure_stdout
from resume_matcher.requirements import perform_extraction


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

    return perform_extraction(workers=args.workers, run_dir=args.run_dir,
                              all_=args.all, dry_run=args.dry_run)


if __name__ == '__main__':
    sys.exit(main())