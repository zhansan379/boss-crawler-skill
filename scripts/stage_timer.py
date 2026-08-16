#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
阶段计时埋点 —— 往 {run_dir}/run_timings.jsonl 追加耗时记录。

为什么要有这个文件：
2026-08-13 复盘一次实跑（46 分钟只投出 1 个岗位）时，唯一的数据来源是
~/.claude/projects/.../<session>.jsonl —— 一个几十 MB 的对话流水，得写脚本
逐条解析 turn_duration / compactMetadata 才能反推出「推理 30min、工具 11.4min、
压缩 4.7min」。代价高、只能事后做、而且换一次运行就得重做一遍。

有了本模块，每个阶段自己报时，下次优化直接 `--report` 看排行，不必再考古。

两种记录：
  span —— 有明确起止的代码块，用 `with stage(run_dir, '名字'):` 包住，
           异常也会落盘（status=error），所以崩掉的阶段同样看得见耗时。
  mark —— 只打一个时间点。给「由 Claude 驱动、没有单一脚本包裹」的阶段用
           （深度分析、简历优化这些）：在阶段边界各敲一次，report 用相邻
           mark 的时间差还原耗时。

并发安全：以 'a' 模式打开，一次 write() 写完整一行再关闭。行短（<1KB），
POSIX 和 Windows 上都不会撕裂，所以多个并行 agent 可以同时往里写。

用法（CLI）：
  python scripts/stage_timer.py mark   <run_dir> <阶段名>   # 打一个边界点
  python scripts/stage_timer.py span   <run_dir> <阶段名> <秒数>
  python scripts/stage_timer.py report <run_dir>            # 看耗时排行

用法（模块）：
  from stage_timer import stage
  with stage(run_dir, 'deep_prepare'):
      ...
"""

import json
import os
import sys
import time
from contextlib import contextmanager

from resume_matcher import timings_path

# report 里高亮的阈值：单阶段超过这个秒数就标记出来，提示值得优化
SLOW_SECONDS = 60


def _path(run_dir):
    return timings_path(run_dir)


def _append(run_dir, record):
    """追加一行。埋点绝不能因为自己失败而带崩业务流程，所以整体兜住异常。"""
    try:
        os.makedirs(os.path.dirname(_path(run_dir)), exist_ok=True)
        line = json.dumps(record, ensure_ascii=False) + '\n'
        with open(_path(run_dir), 'a', encoding='utf-8') as f:
            f.write(line)
    except Exception as exc:                      # noqa: BLE001
        print('[stage_timer] 埋点写入失败（已忽略）: %s' % exc, file=sys.stderr)


def mark(run_dir, name, note=''):
    """记录一个时间点边界。"""
    now = time.time()
    _append(run_dir, {
        'kind': 'mark',
        'stage': name,
        'at': now,
        'at_str': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now)),
        'note': note,
        'pid': os.getpid(),
    })


def span(run_dir, name, duration_s, status='ok', note=''):
    """记录一段已知耗时。"""
    now = time.time()
    _append(run_dir, {
        'kind': 'span',
        'stage': name,
        'duration_s': round(float(duration_s), 2),
        'status': status,
        'end': now,
        'end_str': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now)),
        'note': note,
        'pid': os.getpid(),
    })


@contextmanager
def stage(run_dir, name, note=''):
    """
    包住一段代码并记录耗时。

    异常照常向上抛（不吞），但落盘时 status=error —— 「哪个阶段崩了、崩之前
    跑了多久」是复盘时最想知道的两件事。
    """
    started = time.monotonic()
    status = 'ok'
    try:
        yield
    except BaseException:
        status = 'error'
        raise
    finally:
        span(run_dir, name, time.monotonic() - started, status=status, note=note)


# ==================== 报告 ====================

def load(run_dir):
    """读回全部记录。坏行跳过——半行 JSON 不该让 report 挂掉。"""
    path = _path(run_dir)
    if not os.path.exists(path):
        return []
    records = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except ValueError:
                continue
    return records


def report(run_dir):
    """打印耗时排行 + mark 之间的间隔。返回 exit code。"""
    records = load(run_dir)
    if not records:
        print('没有计时记录: %s' % _path(run_dir))
        print('（阶段脚本还没跑过，或者跑的是没埋点的旧版本）')
        return 0

    spans = [r for r in records if r.get('kind') == 'span']
    marks = [r for r in records if r.get('kind') == 'mark']
    marks.sort(key=lambda r: r.get('at', 0))

    print('=' * 60)
    print('  阶段耗时报告  %s' % run_dir)
    print('=' * 60)

    if spans:
        # 同名阶段可能跑多次（重试、分片），合并统计
        agg = {}
        for r in spans:
            name = r.get('stage', '?')
            slot = agg.setdefault(name, {'total': 0.0, 'count': 0, 'errors': 0})
            slot['total'] += r.get('duration_s') or 0
            slot['count'] += 1
            if r.get('status') == 'error':
                slot['errors'] += 1

        total = sum(s['total'] for s in agg.values())
        print('\n【脚本阶段】按耗时降序   合计 %s' % _fmt(total))
        for name, s in sorted(agg.items(), key=lambda kv: -kv[1]['total']):
            share = (s['total'] / total * 100) if total else 0
            flags = []
            if s['count'] > 1:
                flags.append('×%d' % s['count'])
            if s['errors']:
                flags.append('❌%d' % s['errors'])
            if s['total'] >= SLOW_SECONDS:
                flags.append('⚠ 值得优化')
            suffix = ('   ' + ' '.join(flags)) if flags else ''
            print('  %-28s %8s  %5.1f%%%s' % (name, _fmt(s['total']), share, suffix))

    if len(marks) >= 2:
        print('\n【边界间隔】相邻 mark 的时间差（含 Claude 推理时间）')
        for prev, cur in zip(marks, marks[1:]):
            gap = (cur.get('at') or 0) - (prev.get('at') or 0)
            flag = '   ⚠ 值得优化' if gap >= SLOW_SECONDS else ''
            print('  %-28s → %-24s %8s%s' % (
                prev.get('stage', '?'), cur.get('stage', '?'), _fmt(gap), flag))
        wall = (marks[-1].get('at') or 0) - (marks[0].get('at') or 0)
        print('  %-28s   %-24s %8s' % ('（首尾墙钟）', '', _fmt(wall)))
    elif len(marks) == 1:
        print('\n【边界间隔】只有 1 个 mark（%s），至少要 2 个才能算间隔'
              % marks[0].get('stage', '?'))

    print()
    return 0


def _fmt(seconds):
    seconds = max(0.0, float(seconds or 0))
    if seconds < 60:
        return '%.1fs' % seconds
    return '%dm%02ds' % (int(seconds) // 60, int(seconds) % 60)


# ==================== CLI ====================

USAGE = """用法:
  python scripts/stage_timer.py mark   <run_dir> <阶段名> [备注]
  python scripts/stage_timer.py span   <run_dir> <阶段名> <秒数> [备注]
  python scripts/stage_timer.py report <run_dir>
"""


def main(argv):
    if len(argv) < 2:
        print(USAGE)
        return 2

    cmd = argv[0]
    run_dir = argv[1]

    if cmd == 'mark':
        if len(argv) < 3:
            print(USAGE)
            return 2
        mark(run_dir, argv[2], note=argv[3] if len(argv) > 3 else '')
        print('✅ mark %s' % argv[2])
        return 0

    if cmd == 'span':
        if len(argv) < 4:
            print(USAGE)
            return 2
        try:
            duration = float(argv[3])
        except ValueError:
            print('秒数必须是数字: %s' % argv[3])
            return 2
        span(run_dir, argv[2], duration, note=argv[4] if len(argv) > 4 else '')
        print('✅ span %s %.1fs' % (argv[2], duration))
        return 0

    if cmd == 'report':
        return report(run_dir)

    print('未知命令: %s' % cmd)
    print(USAGE)
    return 2


if __name__ == '__main__':
    for _stream in (sys.stdout, sys.stderr):      # Windows 控制台是 GBK
        _stream.reconfigure(encoding='utf-8', errors='replace')
    sys.exit(main(sys.argv[1:]))
