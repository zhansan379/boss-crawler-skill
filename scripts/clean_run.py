#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""清理一轮运行里的「无用桶」intermediate/，保留 state/ materials/ deliver/。

一轮跑完、投递结束后，大部分机器产物就没用了：deep_candidates/deep_results、
llm 用量、埋点日志、showcv 的暂存与导出 —— 它们只对「这轮」有用，留着既碍事又占盘。
本脚本只删 intermediate/，state/（续跑/回溯）、materials/（LLM 源，花钱的）、
deliver/（最终交付）一律不碰。

安全设计：
- 默认只打印将删/将留的清单，加 --yes 才真删。
- 只对 `intermediate/` 下手，绝不碰 run_dir 下任何其他东西 —— 就算哪天有人
  把目录摆错了，也删不到交付物。
- 删完打印保留的三个桶，方便对照确认。

用法：
    python scripts/clean_run.py <run_dir>            # dry-run：打印清单
    python scripts/clean_run.py <run_dir> --yes      # 真删 intermediate/
    python scripts/clean_run.py <run_dir> --keep run_timings.jsonl   # 保留个别文件

退出码：0 = 完成（无论删没删），1 = run_dir 不存在。
"""

import os
import sys
import argparse

from resume_matcher import intermediate_dir


def list_tree(root):
    """root 下所有条目（相对路径），目录带尾斜杠，便于人类核对。"""
    out = []
    if not os.path.isdir(root):
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            rel = os.path.relpath(os.path.join(dirpath, name), root)
            out.append(rel)
        for name in dirnames:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root)
            out.append(rel + os.sep)
    return out


def main():
    for _stream in (sys.stdout, sys.stderr):      # Windows 控制台是 GBK
        _stream.reconfigure(encoding='utf-8', errors='replace')

    ap = argparse.ArgumentParser(
        description='删除一轮运行的 intermediate/（无用桶），保留 state/ materials/ deliver/')
    ap.add_argument('run_dir')
    ap.add_argument('--yes', action='store_true',
                    help='真删；不加只打印清单（dry-run）')
    ap.add_argument('--keep', metavar='NAME', action='append', default=[],
                    help='intermediate/ 下要保留的相对路径（可多次），如 run_timings.jsonl')
    args = ap.parse_args()

    run_dir = os.path.abspath(args.run_dir)
    if not os.path.isdir(run_dir):
        print('❌ 运行目录不存在: %s' % run_dir)
        return 1

    inter = intermediate_dir(run_dir)
    keep = {os.path.normpath(k) for k in args.keep if k}

    entries = list_tree(inter)
    if not entries:
        print('intermediate/ 不存在或已是空的，无需清理。')
    else:
        print('将清理 intermediate/（%d 个条目）：' % len(entries))
        keep_shown = False
        for rel in entries:
            if rel.rstrip(os.sep) in keep:
                if not keep_shown:
                    print('  （以下保留）')
                    keep_shown = True
                print('  保留  %s' % rel)
            else:
                print('  删除  %s' % rel)

    # ── 保留的三个桶 ──
    for bucket, label in (('state', '机器状态（续跑/回溯）'),
                          ('materials', 'LLM 源，花钱的，再渲染靠它'),
                          ('deliver', '最终交付')):
        path = os.path.join(run_dir, bucket)
        n = len(os.listdir(path)) if os.path.isdir(path) else 0
        print('  · %s/ %s：保留（%d 个条目）' % (bucket, label, n))

    if not args.yes:
        print('\n（dry-run，未删除。确认后加 --yes 真删。）')
        # 同结构中保留的个别文件也要能完整展示才不误导
        return 0

    # ── 真删 ──
    if not os.path.isdir(inter):
        print('\nintermediate/ 不存在，跳过删除。')
        return 0

    gone = 0
    # 先删文件，再删目录：目录必须等里面全空了才能 rmdir
    dirs = []
    for rel in entries:
        if rel.rstrip(os.sep) in keep:
            continue
        full = os.path.join(inter, rel)
        if rel.endswith(os.sep):
            dirs.append(rel)
        else:
            try:
                os.unlink(full)
                gone += 1
            except OSError as exc:
                print('  ⚠ %s 未删（%s）' % (rel, exc))

    # 目录按深度从深到浅删，删不掉的（非空）自动跳过 —— 文件已先删，通常都空
    for rel in sorted(dirs, key=lambda r: r.count(os.sep), reverse=True):
        full = os.path.join(inter, rel)
        try:
            os.rmdir(full)
        except OSError:
            pass

    # 清理后把空的中间目录也移走，让 run_dir 恢复「只有三个桶 + 个别文件」的样子
    try:
        os.rmdir(inter)
    except OSError:
        pass
    print('\n已删除 %d 个文件，intermediate/ 已清空。' % gone)
    print('run_dir 现在只剩：state/ materials/ deliver/（+ 你显式 --keep 的）。')
    return 0


if __name__ == '__main__':
    sys.exit(main())