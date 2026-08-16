#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""从 run_dir 的磁盘产物反推「现在跑到第几阶段、下一条命令是什么」。

为什么需要它：上下文被压缩后，流程状态是最先丢的东西之一，而重建它的默认做法
是重读 SKILL.md / references/cli.md ——那是几万字符。本脚本用约 1k 字符回答同一个问题。
它**只看产物、不看进度记录**，所以不会因为忘记更新状态而说谎。

只有一个阶段例外：`verify` 不产出材料，它的产物就是 `verify_report.json` 本身。
所以那一步不能光看文件在不在 —— 报告里记了被查文件的 mtime，材料重新生成过就算
过期，得回去重查。否则改完简历还留着上一版的 ✅，正好是最该拦住的情况。

阶段名与 `pipeline.py` 的九阶段完全一致（parse / infer / crawl / match / deep /
merge / materials / verify / render），所以它给出的下一步永远能直接粘去执行。两个例外是
流水线之外的收尾步骤：`write_application_md.py` 和 `apply.py` —— 后者是唯一不可
撤销的一步，本脚本只会把命令摆出来，绝不建议加 `--yes`。

用法：
    python scripts/where_am_i.py <run_dir>
    python scripts/where_am_i.py               # 自动取 LATEST.txt 指的运行目录

退出码恒为 0——这是查询工具，不是 gate。
"""

import os
import sys
import json
import glob
import argparse

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

SKILL_ROOT = os.path.dirname(_HERE)

# 需要 api_key 的阶段。下一步落在这些阶段上时，提醒先跑一次 llm_check。
_LLM_STAGES = ('parse', 'infer', 'match', 'deep', 'materials')


def _latest_run():
    """LATEST.txt 指的目录；读不到就退回 assets/ 下最新改动的时间戳目录。"""
    try:
        from resume_matcher.config import get_latest_run_dir
        latest = get_latest_run_dir()
        if latest and os.path.isdir(latest):
            return latest
    except Exception:                              # noqa: BLE001
        pass
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


def _match_mode(run_dir):
    """这一轮是 quick 还是 deep —— 决定 deep/merge 两步算不算「缺」。

    只信 crawl_params.json 里 infer 写下的那个值。读不出来时看产物：有
    deep_candidates.json 就是 deep，否则按 pipeline 的默认当 quick。
    """
    params = _load(os.path.join(run_dir, 'crawl_params.json'))
    if isinstance(params, dict):
        mode = params.get('match_mode')
        if mode in ('quick', 'deep'):
            return mode
    if os.path.exists(os.path.join(run_dir, 'deep_candidates.json')):
        return 'deep'
    return 'quick'


def _llm_ready():
    """api_key 配没配好。返回 (ok, 说明)；查询工具不该因为配置问题崩掉。"""
    try:
        from llm import resolve
        cfg = resolve()
        if not cfg.api_key:
            return False, '还没配 api_key（assets/llm_config.json 或 LLM_API_KEY）'
        return True, ''
    except Exception as exc:                       # noqa: BLE001
        return False, 'LLM 配置读不出来：%s' % exc


def _pipe(run_dir, stage):
    return 'python scripts/pipeline.py --run-dir "%s" --from %s' % (run_dir, stage)


def survey(run_dir):
    """返回 (已完成阶段列表, 下一步 (标题, 命令列表), 备注列表)。"""
    has = lambda *parts: os.path.exists(os.path.join(run_dir, *parts))
    done, notes = [], []
    mode = _match_mode(run_dir)

    def nxt(stage, title, extra_cmds=(), extra_notes=()):
        cmds = [_pipe(run_dir, stage)] + list(extra_cmds)
        ns = list(extra_notes)
        if stage in _LLM_STAGES:
            ok, why = _llm_ready()
            if not ok:
                ns.append('%s —— 先跑 python scripts/llm_check.py --no-call' % why)
        return done, ('%s（pipeline 阶段 %s）' % (title, stage), cmds), ns

    # ── parse: 简历 → profile.json ──
    if has('profile.json') and has('resume_text.txt'):
        done.append('parse：profile.json + resume_text.txt')
        if not has('profile_validation.json'):
            # parse_resume.py 自己会写这个文件，缺了说明那一步是旧版/半途中断的产物
            notes.append('没有 profile_validation.json（正常由 parse_resume.py 一起写）：'
                         'python scripts/validate_profile.py "%s/resume_text.txt" '
                         '"%s/profile.json"' % (run_dir, run_dir))
    else:
        return nxt('parse', '解析简历',
                   extra_notes=['缺 profile.json 或 resume_text.txt',
                                'parse 阶段要给简历文件：pipeline.py 简历.pdf'])

    # ── infer: profile.json → crawl_params.json ──
    if has('crawl_params.json'):
        done.append('infer：crawl_params.json（match_mode=%s）' % mode)
    else:
        return nxt('infer', '推断爬取参数',
                   extra_notes=['缺 crawl_params.json —— crawl 和 match 都要读它',
                                '用户已确认的条件用 --city/--salary/--match-mode 等直接传进去'])

    # ── crawl: → assets/post_data/**.csv ──
    if has('crawl_summary.json'):
        summary = _load(os.path.join(run_dir, 'crawl_summary.json'))
        written = summary.get('written', '?') if isinstance(summary, dict) else '?'
        done.append('crawl：crawl_summary.json（本轮新增 %s 条）' % written)
    else:
        csvs = glob.glob(os.path.join(SKILL_ROOT, 'assets', 'post_data', '**', '*.csv'),
                         recursive=True)
        return nxt('crawl', '采集岗位',
                   extra_notes=[
                       '缺 crawl_summary.json —— 这一轮没爬到东西（岗位池现有 %d 个 CSV）'
                       % len(csvs),
                       '最常见的原因是没登录：'
                       'python scripts/boss_post_interactive.py --ensure-login',
                       '爬取动辄几十分钟：放后台跑，别在前台等'])

    # ── match / deep / merge: 三步一组 ──
    if has('qualified_jobs.json') and has('matching_report.html'):
        done.append('match%s：matching_report.html + qualified_jobs.json'
                    % (' → deep → merge' if mode == 'deep' else ''))
    elif mode == 'deep' and has('deep_candidates.json') and not has('deep_results.json'):
        cands = _load(os.path.join(run_dir, 'deep_candidates.json'))
        n = len(cands) if isinstance(cands, list) else '?'
        return nxt('deep', '逐个候选调模型做深度分析',
                   extra_notes=['deep_candidates.json 在（%s 个候选），缺 deep_results.json' % n,
                                '按岗位各一次请求，会花钱；--from deep 会连着把 merge 跑完'])
    elif mode == 'deep' and has('deep_results.json'):
        return nxt('merge', '把深度结果与规则评分合并、出报告',
                   extra_notes=['deep_results.json 在，缺 matching_report.html '
                                '或 qualified_jobs.json'])
    else:
        return nxt('match', '匹配评分',
                   extra_notes=['缺 matching_report.html / qualified_jobs.json',
                                'match_mode=%s（deep 会连着跑 deep → merge）' % mode])

    # ── gate:jobs — 投递池要人过目 ──
    jobs = _load(os.path.join(run_dir, 'qualified_jobs.json'))
    if not jobs:
        return done, ('qualified_jobs.json 是空的 —— 没有岗位进投递池', [
            '打开 %s   # 看评分明细' % os.path.join(run_dir, 'matching_report.html'),
        ]), ['放宽条件重跑 match，或确认这一轮确实没有合适岗位']
    n = len(jobs)
    done.append('投递池：%d 个岗位' % n)

    # ── materials: 招呼语 + 优化简历 ──
    gen = os.path.join(run_dir, 'generated')
    greets, resumes = _count(gen, 'greeting_*'), _count(gen, 'resume_*')
    if greets < n or resumes < n:
        return nxt('materials', '生成招呼语 + 优化简历',
                   extra_cmds=['python scripts/gen_materials.py "%s" --only <序号>   '
                               '# 只补缺的那几个' % run_dir],
                   extra_notes=['generated/ 有 %d 个招呼语、%d 份简历，期望各 %d 个'
                                % (greets, resumes, n),
                                '每个岗位两次请求（招呼语 + 简历改写）—— 先确认投递池再跑'])
    done.append('materials：%d 招呼语 + %d 简历' % (greets, resumes))

    # ── verify: 有没有简历原文之外的技术词 ──
    # 这一步不产材料，只留 verify_report.json。判「过期」而不只判「有没有」：
    # 材料重生成过之后，旧报告的通过结论对新材料不成立。
    try:
        import verify_no_fabrication
        why, report = verify_no_fabrication.report_is_stale(run_dir)
    except Exception as exc:                           # noqa: BLE001
        why, report = None, None
        notes.append('verify 状态读不出来（%s）—— 手动跑一次 pipeline --from verify' % exc)
    if why:
        extra = []
        if report is not None and not report.get('clean'):
            bad = report.get('findings') or []
            extra.append('上次查出 %d 份材料有问题：%s'
                         % (len(bad),
                            '、'.join(str(f.get('index')) for f in bad) or '?'))
            extra.append('确属编造就 gen_materials.py --only <序号> --force 重生成；'
                         '有据可依就加 --allow —— 两者都要先问用户')
        return nxt('verify', '核查材料里有没有简历原文之外的技术词',
                   extra_notes=[why] + extra)
    if report is not None:
        done.append('verify：%d 份材料查过，没有简历原文之外的技术词'
                    % len(report.get('checked') or {}))

    # ── render: 简历长图 ──
    apps = os.path.join(run_dir, 'applications')
    job_dirs = [d for d in glob.glob(os.path.join(apps, '*')) if os.path.isdir(d)]
    pngs = len(glob.glob(os.path.join(apps, '*', '*.png')))
    if pngs < n:
        return nxt('render', '渲染简历长图',
                   extra_notes=['applications/ 下有 %d 张 PNG，期望 %d 张' % (pngs, n),
                                '必须串行（共用一个浏览器 + 一份 localStorage），'
                                '所以没有 --workers',
                                '不需要长图就跳过这一步：pipeline 加 --no-images'])
    done.append('render：%d 张简历长图' % pngs)

    # ── 收尾一：各岗位的 岗位信息+招呼语.md（不在流水线里）──
    ready = [d for d in job_dirs
             if os.path.exists(os.path.join(d, '岗位信息+招呼语.md'))]
    if len(ready) < n:
        return done, ('写各岗位的 岗位信息+招呼语.md（流水线之外）', [
            'python scripts/write_application_md.py "%s" --all' % run_dir,
        ]), ['%d/%d 个岗位目录有 岗位信息+招呼语.md' % (len(ready), n)]
    done.append('材料：%d/%d 个岗位目录齐全' % (len(ready), n))

    # ── 收尾二：gate:send → 投递（唯一不可撤销的一步）──
    log = _load(os.path.join(run_dir, 'apply_log.json'))
    if not log:
        return done, ('确认后投递（唯一不可撤销的一步）', [
            'python scripts/verify_image.py "%s" --all   # 投出去之前先查图，别用 Read 看' % apps,
            'python scripts/apply.py "%s"                # 空跑：只打印名单，不碰浏览器' % run_dir,
        ]), ['缺 apply_log.json',
             '真投递要用户明确同意后才加 --yes —— 消息一发对方立刻收到，撤不回来']
    done.append('投递：apply_log.json 有 %d 条'
                % (len(log) if isinstance(log, list) else 1))
    return done, ('全流程已走完', [
        'python scripts/stage_timer.py report "%s"   # 各阶段耗时' % run_dir,
    ]), ['核对 apply_log.json，向用户汇报结果']


def main():
    for _stream in (sys.stdout, sys.stderr):      # Windows 控制台是 GBK
        _stream.reconfigure(encoding='utf-8', errors='replace')

    ap = argparse.ArgumentParser(
        description='从产物反推流水线跑到哪一步了，并打印下一条命令')
    ap.add_argument('run_dir', nargs='?',
                    help='运行目录；省略则取 LATEST.txt 指的那个')
    args = ap.parse_args()

    run_dir = args.run_dir or _latest_run()
    if not run_dir or not os.path.isdir(run_dir):
        print('找不到运行目录。显式传一个：python scripts/where_am_i.py <run_dir>')
        print('全新一轮从解析简历开始：python scripts/pipeline.py 简历.pdf')
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

    print('\n阶段细节看 references/cli.md 的对应小节——只读那一节，别整篇读。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
