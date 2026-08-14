#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""校验 7d 各子 agent 的产物是否已落盘，用于 7d→7e 的 barrier。

为什么需要它：后台子 agent 的完成通知**不保证送达**。通知只是加速信号，
不是正确性依据——主 agent 只在收到事件时才获得一个回合，通知丢了就没有
任何东西唤醒它，于是明明所有 agent 都跑完了，整个流程还是静默挂死。
2026-08-13 的实测里 6 个 agent 全部完成，只有 3 条通知到达。

所以 barrier 由「文件在不在」判定，而不是由「通知来没来」判定。

**但「文件不在」不等于「agent 已死」。** 同一天的另一次实测：简历 agent
11:59:37 派出，12:00:24（仅 47 秒后）跑了一次本脚本，报「缺失 resume #1」，
于是重派了一个；而 12:03:11 的 task_status 显示原 agent 仍在 running。两个
agent 干了同一件事，白烧 4 分钟和一倍 token。简历优化实测要跑 241 秒，
派发后 47 秒去查，结果必然是空的。

所以默认 `--wait`：轮询到产物出现为止，只有超时才判缺失。一次性快照
（`--wait 0`）只适用于「已确认 agent 不在运行」之后的复核。

用法：
    python scripts/check_artifacts.py <run_dir>                 # 默认等 360s
    python scripts/check_artifacts.py <run_dir> --wait 600      # 岗位多时加长
    python scripts/check_artifacts.py <run_dir> --wait 0        # 一次性快照
    python scripts/check_artifacts.py <run_dir> [--greeting] [--resume]
    python scripts/check_artifacts.py <run_dir> --kinds deep_shards   # 阶段 2 分片

不带 --greeting/--resume 时两者都查。按 7bc 的 skip 规则，
`自定义上传` 不产简历、只有 `AI生成` 才产招呼语，此时用开关缩小范围。

`--kinds deep_shards` 是另一套：查深度分析并行分片（deep_shards/shard_NN.md
是否都有对应的 result_NN.json），额外多一道 JSON 解析校验。

注意 Bash 工具默认 120s 超时：用 --wait 360 时要把 timeout 提到 380000ms 以上。

退出码：0 = 齐全，1 = 有缺失（缺失项打印在 stdout）。
"""

import os
import sys
import json
import time
import argparse

POLL_SECONDS = 3
DEFAULT_WAIT = 360        # 简历优化 agent 实测 241s，留足余量


def _load_jobs(run_dir):
    path = os.path.join(run_dir, 'qualified_jobs.json')
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    # 容忍被包一层的情况
    if isinstance(data, dict):
        data = data.get('jobs') or data.get('data') or []
    return data


def check(run_dir, kinds, jobs=None):
    if jobs is None:
        jobs = _load_jobs(run_dir)
    gen = os.path.join(run_dir, 'generated')
    # 目录可能整个不存在——那就是全缺，而不是崩掉
    existing = os.listdir(gen) if os.path.isdir(gen) else []

    missing, found = [], []
    for i, job in enumerate(jobs, 1):
        company = job.get('公司') or job.get('company') or '?'
        for kind in kinds:
            prefix = '%s_%d_' % (kind, i)
            # 非空才算落盘：agent 可能刚 open() 出一个 0 字节的壳子，
            # 此时放行会把空简历送进 7e 渲染
            hits = []
            for name in existing:
                if not name.startswith(prefix):
                    continue
                try:
                    if os.path.getsize(os.path.join(gen, name)) > 0:
                        hits.append(name)
                except OSError:
                    pass          # 正在被写/刚被删，下一轮再看
            if hits:
                found.append(hits[0])
            else:
                missing.append('%s #%d (%s)' % (kind, i, company))
    return jobs, found, missing


def check_shards(run_dir, _kinds=None, jobs=None):
    """
    深度分析分片的产物校验：每个 shard_NN.md 都该有一个 result_NN.json。

    与 generated/ 那套的区别在于多一道 JSON 解析：分片结果要喂给
    deep_analysis.collect_shard_results，半截的 JSON 会让合并失败。解析不过就
    当作「还没写完」继续等——agent 正在写的文件本来就是残缺的。
    """
    shard_dir = os.path.join(run_dir, 'deep_shards')
    if not os.path.isdir(shard_dir):
        return [], [], ['deep_shards/ 目录不存在（先跑 shard_deep_candidates.py）']

    names = os.listdir(shard_dir)
    shards = sorted(n for n in names if n.startswith('shard_') and n.endswith('.md'))
    if not shards:
        return [], [], ['deep_shards/ 下没有 shard_*.md（先跑 shard_deep_candidates.py）']

    missing, found = [], []
    for shard_name in shards:
        index = shard_name[len('shard_'):-len('.md')]
        result_name = 'result_%s.json' % index
        result_path = os.path.join(shard_dir, result_name)
        try:
            if os.path.getsize(result_path) <= 0:
                raise OSError('empty')
            with open(result_path, encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, ValueError):
            missing.append('%s（对应 %s）' % (result_name, shard_name))
            continue

        results = data.get('results', []) if isinstance(data, dict) else data
        count = len(results) if isinstance(results, list) else 0
        if count == 0:
            missing.append('%s 的 results 为空' % result_name)
        else:
            found.append('%s（%d 条）' % (result_name, count))

    return shards, found, missing


def wait_for(run_dir, kinds, wait_seconds, checker=check):
    """轮询到产物齐全或超时。返回 (jobs, found, missing, elapsed)。"""
    jobs = _load_jobs(run_dir) if checker is check else None
    started = time.monotonic()
    deadline = started + wait_seconds
    reported = set()

    while True:
        jobs, found, missing = checker(run_dir, kinds, jobs)

        # 新落盘的产物即时播报，让等待过程可见而不是一片死寂
        for name in found:
            if name not in reported:
                reported.add(name)
                print('  ✅ %s  (+%ds)' % (name, int(time.monotonic() - started)))
                sys.stdout.flush()

        if not missing or time.monotonic() >= deadline:
            return jobs, found, missing, int(time.monotonic() - started)

        time.sleep(min(POLL_SECONDS, max(0.1, deadline - time.monotonic())))


def main():
    for _stream in (sys.stdout, sys.stderr):      # Windows 控制台是 GBK
        _stream.reconfigure(encoding='utf-8', errors='replace')

    ap = argparse.ArgumentParser()
    ap.add_argument('run_dir')
    ap.add_argument('--greeting', action='store_true', help='只查招呼语')
    ap.add_argument('--resume', action='store_true', help='只查简历')
    ap.add_argument('--kinds', nargs='+', metavar='KIND',
                    choices=['greeting', 'resume', 'deep_shards'],
                    help='要查的产物种类。deep_shards 查阶段 2 的并行分片结果，'
                         '不能和 greeting/resume 混用（它们在不同目录、不同命名）')
    ap.add_argument('--wait', type=int, default=DEFAULT_WAIT, metavar='SECONDS',
                    help='轮询等待产物落盘的秒数（默认 %d）。0 = 一次性快照，'
                         '仅在已确认 agent 不再运行时才该用' % DEFAULT_WAIT)
    args = ap.parse_args()

    kinds = list(args.kinds or [])
    for name, on in (('greeting', args.greeting), ('resume', args.resume)):
        if on and name not in kinds:
            kinds.append(name)

    if 'deep_shards' in kinds:
        if len(kinds) > 1:
            ap.error('deep_shards 不能与 greeting/resume 混用，请分两次运行')
        checker, label = check_shards, 'deep_shards'
    else:
        if not kinds:
            kinds = ['greeting', 'resume']
        checker, label = check, '/'.join(kinds)

    if args.wait > 0:
        units, found, missing, elapsed = wait_for(args.run_dir, kinds, args.wait, checker)
        print('%d 个待办 × %s → 齐全需 %d 个产物（等待 %ds）'
              % (len(units or []), label,
                 len(units or []) * (1 if checker is check_shards else len(kinds)), elapsed))
    else:
        units, found, missing = checker(args.run_dir, kinds)
        elapsed = 0
        print('%d 个待办 × %s → 齐全需 %d 个产物（一次性快照）'
              % (len(units or []), label,
                 len(units or []) * (1 if checker is check_shards else len(kinds))))
        for name in found:
            print('  ✅ %s' % name)

    for name in missing:
        print('  ❌ 缺失：%s' % name)

    if missing:
        if args.wait > 0:
            print('\n等了 %ds 仍缺 %d 个，可以认定这些 agent 不会再产出了。'
                  % (elapsed, len(missing)))
        else:
            print('\n缺 %d 个。这是一次性快照——产物不存在**不等于** agent 已死。'
                  '若刚派发不久，请改用 --wait，不要直接重派。' % len(missing))
        print('只重派缺失的这几个，不要重跑已有产物的 agent'
              '（浪费 token，且可能用更差的结果覆盖好的）。')
        return 1

    if checker is check_shards:
        print('\n分片结果齐全，可以合并：'
              'python scripts/run_matcher.py --mode deep --merge --output-dir %s'
              % args.run_dir)
    else:
        print('\n产物齐全，可以进入 7e。无需理会通知到没到。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
