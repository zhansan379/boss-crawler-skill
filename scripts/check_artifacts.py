#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""校验 7d 各子 agent 的产物是否已落盘，用于 7d→7e 的 barrier。

为什么需要它：后台子 agent 的完成通知**不保证送达**。通知只是加速信号，
不是正确性依据——主 agent 只在收到事件时才获得一个回合，通知丢了就没有
任何东西唤醒它，于是明明所有 agent 都跑完了，整个流程还是静默挂死。
2026-08-13 的实测里 6 个 agent 全部完成，只有 3 条通知到达。

所以 barrier 由「文件在不在」判定，而不是由「通知来没来」判定。

用法：
    python scripts/check_artifacts.py <run_dir> [--greeting] [--resume]

不带 --greeting/--resume 时两者都查。按 7c 的 skip 规则，
`自定义上传` 不产简历、只有 `AI生成` 才产招呼语，此时用开关缩小范围。

退出码：0 = 齐全，1 = 有缺失（缺失项打印在 stdout）。
"""

import os
import sys
import json
import argparse


def _load_jobs(run_dir):
    path = os.path.join(run_dir, 'qualified_jobs.json')
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    # 容忍被包一层的情况
    if isinstance(data, dict):
        data = data.get('jobs') or data.get('data') or []
    return data


def check(run_dir, kinds):
    jobs = _load_jobs(run_dir)
    gen = os.path.join(run_dir, 'generated')
    # 目录可能整个不存在——那就是全缺，而不是崩掉
    existing = os.listdir(gen) if os.path.isdir(gen) else []

    missing, found = [], []
    for i, job in enumerate(jobs, 1):
        company = job.get('公司') or job.get('company') or '?'
        for kind in kinds:
            prefix = '%s_%d_' % (kind, i)
            hits = [f for f in existing if f.startswith(prefix)]
            if hits:
                found.append(hits[0])
            else:
                missing.append('%s #%d (%s)' % (kind, i, company))
    return jobs, found, missing


def main():
    sys.stdout.reconfigure(encoding='utf-8')      # Windows 控制台是 GBK

    ap = argparse.ArgumentParser()
    ap.add_argument('run_dir')
    ap.add_argument('--greeting', action='store_true', help='只查招呼语')
    ap.add_argument('--resume', action='store_true', help='只查简历')
    args = ap.parse_args()

    kinds = [k for k, on in (('greeting', args.greeting), ('resume', args.resume)) if on]
    if not kinds:
        kinds = ['greeting', 'resume']

    jobs, found, missing = check(args.run_dir, kinds)
    print('%d 个岗位 × %s → 期望 %d 个产物' % (len(jobs), '/'.join(kinds), len(jobs) * len(kinds)))
    for name in found:
        print('  ✅ %s' % name)
    for name in missing:
        print('  ❌ 缺失：%s' % name)

    if missing:
        print('\n缺 %d 个。只重派缺失的这几个，不要重跑已有产物的 agent'
              '（浪费 token，且可能用更差的结果覆盖好的）。' % len(missing))
        return 1

    print('\n产物齐全，可以进入 7e。无需理会通知到没到。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
