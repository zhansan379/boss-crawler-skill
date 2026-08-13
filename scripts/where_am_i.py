#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""从 run_dir 的磁盘产物反推「现在跑到第几阶段、下一步该干什么」。

为什么需要它：上下文被压缩后，流程状态是最先丢的东西之一，而重建它的默认做法
是重读 references/auto-apply.md——那是 25k 字符。2026-08-13 的实测里这份文档
被读了 5 遍（3 次 Read + 2 次 sed），累计约 46k 字符，其中两次纯粹是因为压缩把
前面读到的内容丢掉了。

本脚本用约 1k 字符回答同一个问题。它**不依赖任何状态文件**，纯粹从产物反推，
所以不会因为忘记更新状态而说谎，也不需要流程去维护它。

压缩后的标准动作：
    python scripts/where_am_i.py <run_dir>
只有当它指向的那一步需要细节时，才去翻 auto-apply.md 的对应小节。

用法：
    python scripts/where_am_i.py <run_dir>
    python scripts/where_am_i.py               # 自动取 assets/ 下最新的运行目录

退出码恒为 0——这是查询工具，不是 gate。
"""

import os
import sys
import json
import glob
import argparse

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _latest_run():
    runs = [p for p in glob.glob(os.path.join(SKILL_ROOT, 'assets', '*'))
            if os.path.isdir(p) and os.path.basename(p)[:2].isdigit()]
    return max(runs, key=os.path.getmtime) if runs else None


def _count(path, pattern):
    return len(glob.glob(os.path.join(path, pattern))) if os.path.isdir(path) else 0


def _load(path):
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if isinstance(data, dict):
        data = data.get('jobs') or data.get('data') or data
    return data


def survey(run_dir):
    """返回 (已完成阶段列表, 下一步 (标题, 命令列表), 备注列表)。"""
    has = lambda *parts: os.path.exists(os.path.join(run_dir, *parts))
    done, notes = [], []

    # ── Stage 1: 采集 ──
    csvs = glob.glob(os.path.join(SKILL_ROOT, 'assets', 'post_data', '**', '*.csv'),
                     recursive=True)
    if csvs:
        done.append('Stage 1 采集：%d 个 CSV' % len(csvs))
    else:
        return done, ('Stage 1 采集岗位', [
            'python scripts/boss_post_interactive.py --ensure-login',
            'python scripts/boss_post_interactive.py',
        ]), ['assets/post_data/ 下没有 CSV']

    # ── Stage 2-3: 解析简历 ──
    if has('profile.json') and has('resume_text.txt'):
        done.append('Stage 2-3 解析：profile.json')
    else:
        return done, ('Stage 2-3 读简历并解析出 profile.json', [
            '按 references/resume-parsing.md 写 resume_text.txt + profile.json',
        ]), ['缺 profile.json 或 resume_text.txt']

    # ── Stage 3.5b: 交叉校验（强制 gate）──
    if has('profile_validation.json'):
        done.append('Stage 3.5b 校验：profile_validation.json')
    else:
        return done, ('Stage 3.5b 交叉校验 profile（强制 gate）', [
            'python scripts/validate_profile.py "%s/resume_text.txt" "%s/profile.json"'
            % (run_dir, run_dir),
        ]), ['缺 profile_validation.json——这一步不能跳']

    # ── Stage 4-6: 匹配与报告 ──
    if has('matching_report.html'):
        done.append('Stage 4-6 匹配：matching_report.html')
    elif has('deep_candidates.json') and not has('deep_results.json'):
        # 深度模式阶段 2 拆成了「切片 → 并行 agent → 收拢」，按分片状态细分下一步
        shard_dir = os.path.join(run_dir, 'deep_shards')
        shards, results = [], []
        if os.path.isdir(shard_dir):
            names = os.listdir(shard_dir)
            shards = [n for n in names if n.startswith('shard_') and n.endswith('.md')]
            results = [n for n in names if n.startswith('result_') and n.endswith('.json')]

        if not shards:
            return done, ('Stage 4-6 深度模式阶段 2：先切片，再并行派发 subagent', [
                'python scripts/shard_deep_candidates.py %s' % run_dir,
            ]), ['deep_candidates.json 在，但还没切片（deep_shards/ 没有 shard_*.md）',
                 '不要在主上下文里逐个分析——JD 全文会把上下文顶到触发压缩']

        if len(results) < len(shards):
            return done, ('Stage 4-6 深度模式阶段 2：派发缺失的分片并等产物齐全', [
                'python scripts/check_artifacts.py %s --kinds deep_shards --wait 360'
                % run_dir,
                '（缺哪片就只派哪片：Read deep_shards/shard_NN.md 并按其中要求执行）',
            ]), ['%d 片里已回收 %d 份结果' % (len(shards), len(results)),
                 'Bash 默认 120s 超时，--wait 360 要把 timeout 提到 380000ms']

        return done, ('Stage 4-6 深度模式阶段 3：合并分片结果并出报告', [
            'python scripts/run_matcher.py --mode deep --merge --run-id %s'
            % os.path.basename(run_dir),
        ]), ['%d 片结果齐全，merge 会自动收拢成 deep_results.json' % len(shards)]
    else:
        return done, ('Stage 4-6 跑匹配', [
            'python scripts/run_matcher.py --mode deep --profile "%s/profile.json" --top 15'
            % run_dir,
        ]), ['缺 matching_report.html']

    # ── Stage 7b/7c: 用户确认岗位 ──
    jobs = _load(os.path.join(run_dir, 'qualified_jobs.json'))
    if not jobs:
        return done, ('Stage 7a-7c 给用户看榜单、确认岗位，然后写 qualified_jobs.json', [
            '打开 %s/matching_report.html' % run_dir,
            '7b 确认岗位 + 7c 一次问齐招呼语/简历两个问题（auto-apply.md 的 7c）',
        ]), ['缺 qualified_jobs.json（这个由主 agent 写，没有脚本）']
    n = len(jobs)
    done.append('Stage 7b-7c 确认：%d 个岗位' % n)

    # ── Stage 7d: 逐岗位生成素材 ──
    gen = os.path.join(run_dir, 'generated')
    greets, resumes = _count(gen, 'greeting_*'), _count(gen, 'resume_*')
    if greets < n or resumes < n:
        return done, ('Stage 7d 生成素材 + 按产物判定 barrier', [
            '每个岗位派 greeting/resume agent，产物写 %s/generated/' % run_dir,
            'python scripts/check_artifacts.py "%s" --wait 360' % run_dir,
        ]), ['generated/ 有 %d 个招呼语、%d 份简历，期望各 %d 个' % (greets, resumes, n),
             '产物不齐**不等于** agent 已死——先 --wait 等够再决定重派']
    done.append('Stage 7d 素材：%d 招呼语 + %d 简历' % (greets, resumes))

    # ── Stage 7e: 渲染图片 ──
    pngs = _count(os.path.join(run_dir, 'showcv_exports'), '*')
    apps = os.path.join(run_dir, 'applications')
    job_dirs = [d for d in glob.glob(os.path.join(apps, '*')) if os.path.isdir(d)]
    if not pngs and not job_dirs:
        return done, ('Stage 7e 批量渲染简历图（串行，别并发）', [
            'python scripts/showcv/serve.py   # 后台，读 SHOWCV_READY',
            'python scripts/showcv/launch.py <url>   # 核对打印出来的 profile',
            'python scripts/showcv/import_md.py --url <url> "%s/showcv_staging"/*.md' % run_dir,
            'python scripts/showcv/export_images.py --url <url> --mode flat --out "%s/showcv_exports" ...'
            % run_dir,
        ]), ['showcv_exports/ 和 applications/ 都是空的']
    if pngs:
        done.append('Stage 7e 渲染：showcv_exports/ 有 %d 项' % pngs)

    # ── Stage 7f: 投递材料落到各岗位目录 ──
    ready = [d for d in job_dirs if os.path.exists(os.path.join(d, '岗位信息+招呼语.md'))]
    if len(ready) < n:
        return done, ('Stage 7f 写各岗位的 岗位信息+招呼语.md', [
            'python scripts/write_application_md.py "%s" --all' % run_dir,
            'python scripts/verify_image.py "%s" --all   # 别用 Read 看图' % apps,
        ]), ['%d/%d 个岗位目录有 岗位信息+招呼语.md' % (len(ready), n)]
    done.append('Stage 7f 材料：%d/%d 个岗位目录齐全' % (len(ready), n))

    # ── Stage 7g/7h: 二次确认与投递 ──
    log = _load(os.path.join(run_dir, 'apply_log.json'))
    if not log:
        return done, ('Stage 7g 让用户确认，然后 7h 投递', [
            'python scripts/verify_image.py "%s" --all   # 投出去之前先查图' % apps,
            '7g 一次 AskUserQuestion 覆盖全部岗位',
            '7h 投递；发送后必须回读校验，别信点击成功',
        ]), ['缺 apply_log.json']
    done.append('Stage 7h 投递：apply_log.json 有 %d 条'
                % (len(log) if isinstance(log, list) else 1))
    return done, ('全流程已走完', ['核对 apply_log.json，向用户汇报结果']), []


def main():
    for _stream in (sys.stdout, sys.stderr):      # Windows 控制台是 GBK
        _stream.reconfigure(encoding='utf-8', errors='replace')

    ap = argparse.ArgumentParser()
    ap.add_argument('run_dir', nargs='?', help='运行目录；省略则取 assets/ 下最新的')
    args = ap.parse_args()

    run_dir = args.run_dir or _latest_run()
    if not run_dir or not os.path.isdir(run_dir):
        print('找不到运行目录。显式传一个：python scripts/where_am_i.py <run_dir>')
        return 0

    done, (title, commands), notes = survey(run_dir)

    print('run_dir: %s' % run_dir)
    print('\n已完成：')
    for item in done:
        print('  ✅ %s' % item)
    if not done:
        print('  （什么都还没有）')

    print('\n下一步 → %s' % title)
    for note in notes:
        print('  · %s' % note)
    for cmd in commands:
        print('  $ %s' % cmd)

    print('\n细节看 references/auto-apply.md 的对应小节——只读那一节，别整篇读。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
