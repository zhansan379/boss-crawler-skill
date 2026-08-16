#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把八个阶段串起来跑，**默认只跑一个阶段**；整轮要显式 `--all`，且停在「材料已生成」。

八个阶段，每个阶段就是调一次已有的入口脚本（业务逻辑一行都不在本文件里）：

    parse      parse_resume.py            简历文件 → profile.json
    infer      infer_params.py            profile.json → crawl_params.json
    crawl      boss_post_interactive.py   → assets/post_data/**.csv
    match      run_matcher.py             quick=直接评分 ／ deep=预筛出 deep_candidates.json
    deep       deep_analyze.py            逐个候选调模型（只有 deep 模式有这一步）
    merge      run_matcher.py --merge      → qualified_jobs.json（同上）
    materials  gen_materials.py           → generated/{greeting,resume}_{i}_*
    render     render_images.py           → applications/<公司>-<岗位>/<姓名>-<岗位>.png

**不给 `--to` 就只跑一个阶段。** 一次跑一步，每步跑完自己看一眼再决定要不要往下 ——
下游那几步不是免费的：materials 按岗位调两次模型（招呼语 + 简历优化），deep 按岗位调一次。
把整轮设成默认，等于让「我先跑跑看」顺手把钱花在一批你还没过目的岗位上。

**投递更不在这条流水线上**，`--all` 也不含它。跑完只把 apply.py 的命令打印出来，要投得
自己敲，而且必须带 --yes。投递是整条链上唯一不可撤销的一步（消息一发对方立刻收到，撤不
回来），不该由「我跑一下全流程」顺手完成。

用法：
    python scripts/pipeline.py 简历.pdf                     # 只解析简历，然后停
    python scripts/pipeline.py --from infer                 # 接着上一轮的下一步（自动定位 LATEST.txt）
    python scripts/pipeline.py --from match                 # match → deep → merge（见下）
    python scripts/pipeline.py --run-dir assets/2026-08-15_10-00-00 --from materials

    python scripts/pipeline.py 简历.pdf --all               # parse … render 一次跑完
    python scripts/pipeline.py --from crawl --all           # 从爬取起，一路跑到底
    python scripts/pipeline.py 简历.pdf --all --no-images   # 整轮但不渲染简历长图
    python scripts/pipeline.py 简历.pdf --to crawl          # 显式区间：爬完就停
    python scripts/pipeline.py 简历.pdf --all --dry-run     # 只打印每一步的命令，不执行

`--all` 就是 `--to render`（起点仍看 `--from`），两个只能给一个。`--from match` 是唯一
一个「一个阶段」不止一步的地方：深度模式下单跑 match 只产出 deep_candidates.json，没有
qualified_jobs.json，下游一步都走不了 —— 那是个半成品，不该是一条命令的终点。

失败就停，不跳过、不硬着头皮往下跑。屏幕上会给出接着跑的命令（`--from <失败的阶段>`），
因为最贵的两步 —— crawl 动辄几十分钟、deep 按岗位烧 token —— 不该因为下游一个小错重来。

退出码：0 = 跑到终点，1 = 某阶段失败或前置条件不满足，3 = 跑完了但有产物缺失（部分成功）。
"""

import os
import sys
import json
import time
import argparse
import subprocess

import stage_timer
import preferences
from llm import reconfigure_stdout
from resume_matcher.config import (
    CSV_FIELDS, OUTPUT_DIR, create_run_dir, get_latest_run_dir,
)

_HERE = os.path.dirname(os.path.abspath(__file__))

STAGES = ('parse', 'infer', 'crawl', 'match', 'deep', 'merge', 'materials', 'render')

# 只有深度模式跑的阶段。快速模式下它们不是「失败」而是「本来就没有」，
# 所以打一行说明就过，不影响退出码。
_DEEP_ONLY = ('deep', 'merge')

# 不给 --to 时，`--from X` 的隐含终点。查不到就是 X 自己（一个阶段就是一个阶段）。
#
# match/deep/merge 三步是一个整体：深度模式下单跑 match 只写出 deep_candidates.json，
# 没有 qualified_jobs.json —— materials / render / apply 全都只认后者，所以停在 match
# 是个谁都用不了的半成品状态，不该是一条命令的终点。
#
# 无条件展开到 merge 对快速模式也是安全的：build_cmd 在快速模式下对 deep/merge 返回
# None，主循环那句「○ 快速模式没有这一步，跳过」照旧接住，不影响退出码。
_GROUP_END = {'match': 'merge', 'deep': 'merge', 'merge': 'merge'}

VALID_MATCH_MODES = ('quick', 'deep')

# 阶段名 → 传给下游的 LLM 覆盖参数。写在一处，免得漏掉某个阶段导致
# 「--model 明明给了，某一步却还在用默认模型」。
_LLM_STAGES = ('parse', 'infer', 'deep', 'materials')


def show(cmd):
    """把 argv 拼成可以直接粘回终端的一行。"""
    return ' '.join(('"%s"' % a if (' ' in a or not a) else a) for a in cmd)


def script(name):
    return os.path.join(_HERE, name)


# ==================== 运行目录与参数 ====================

def resolve_run_dir(args, plan):
    """定位运行目录。返回 (run_dir, 错误信息)。"""
    if args.run_dir:
        run_dir = os.path.abspath(args.run_dir)
        if not args.dry_run:
            os.makedirs(run_dir, exist_ok=True)
        return run_dir, None

    if 'parse' in plan:
        if args.dry_run:
            # dry-run 只是给人看命令，不该留下痕迹。真跑一次会建目录并把 LATEST.txt
            # 指过去 —— 而 LATEST.txt 是 --from 续跑、run_matcher --merge、where_am_i
            # 共用的「当前运行」指针。让 --dry-run 把它指到一个空目录，等于把上一轮
            # 真实运行的续跑入口悄悄换掉，而用户以为自己什么都没动。
            return os.path.join(OUTPUT_DIR,
                                time.strftime('%Y-%m-%d_%H-%M-%S')), None
        # create_run_dir 顺手写 LATEST.txt —— 后面 --from 续跑、run_matcher --merge
        # 自动定位、where_am_i 都靠这个指针，所以目录必须由它来建，不能自己拼时间戳。
        return create_run_dir(), None

    latest = get_latest_run_dir()
    if not latest:
        return None, ('从 %s 开始跑需要一个已有的运行目录，但 %s 里没有 LATEST.txt。\n'
                      '  显式指定：--run-dir assets/<时间戳>'
                      % (plan[0], OUTPUT_DIR))
    return latest, None


def load_params(run_dir):
    """读回 crawl_params.json。没有就返回空 dict（由调用方决定这是否致命）。"""
    path = os.path.join(run_dir, 'crawl_params.json')
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        print('⚠ crawl_params.json 读不动（%s），当作没有' % exc)
        return {}
    return data if isinstance(data, dict) else {}


def match_mode_of(params, override):
    """确定匹配模式，并说明是哪来的 —— 这一项决定后面烧不烧钱，不能悄悄定。"""
    if override:
        return override, '命令行 --match-mode'
    mode = params.get('match_mode')
    if mode in VALID_MATCH_MODES:
        return mode, 'crawl_params.json'
    # 兜底选 quick 而不是 deep：quick 是纯规则、不花钱。没人明确要求过的时候，
    # 默认干一件按岗位烧 token 的事，比默认干一件免费的事错得更贵。
    return 'quick', '兜底默认（crawl_params.json 里没有 match_mode）'


# ==================== 快速模式补写投递池 ====================

def qualified_from_scored(run_dir):
    """快速模式补写 qualified_jobs.json。返回 (状态, 说明)。

    run_quick_mode 只写 scored_jobs.json；qualified_jobs.json 是**深度模式的 merge**
    才写的。而下游（gen_materials / write_application_md / render_images / apply）
    一律只认 qualified_jobs.json —— 快速模式跑完直接进 materials 会撞上「文件不存在」。

    这里把 tier1 + tier2 的**原始爬取字段**摘出来落盘，与 merge 写的那份同形：
    同样是「符合 + 需优化」两类，同样只留 CSV 字段（评分字段不带进去，
    下游按 link 回查 CSV 拿全量信息）。

    已存在就不动：用户很可能已经手工收窄过投递池（删掉不想投的），
    覆盖回全量等于把他的选择丢了，而且他多半不会立刻发现。
    """
    target = os.path.join(run_dir, 'qualified_jobs.json')
    if os.path.exists(target):
        return 'kept', '已存在 qualified_jobs.json，不覆盖（手工收窄过的池子不能被冲掉）'

    scored_path = os.path.join(run_dir, 'scored_jobs.json')
    if not os.path.exists(scored_path):
        return 'error', '快速模式跑完了却没有 scored_jobs.json，无法生成投递池'

    try:
        with open(scored_path, encoding='utf-8') as f:
            scored = json.load(f)
    except (OSError, ValueError) as exc:
        return 'error', 'scored_jobs.json 解析失败: %s' % exc

    pool = []
    for tier in ('tier1', 'tier2'):
        for job in scored.get(tier) or []:
            if not isinstance(job, dict):
                continue
            pool.append({k: job[k] for k in CSV_FIELDS if k in job})

    if not pool:
        return 'empty', ('tier1 + tier2 都是空的 —— 这批岗位没有一个够格投递。\n'
                         '  换个方向：放宽关键词/城市重爬，或改用 --match-mode deep 让模型再看一遍')

    with open(target, 'w', encoding='utf-8') as f:
        json.dump(pool, f, ensure_ascii=False, indent=2)
    return 'written', '已写出 qualified_jobs.json（%d 个岗位，含符合 + 需优化）' % len(pool)


# ==================== 爬取结果核查 ====================

def job_pool_rows():
    """assets/post_data 下所有 CSV 的行数之和 —— 也就是 match 会读到的量。"""
    from resume_matcher.data_loader import list_available_job_files
    return sum(f.get('count') or 0 for f in list_available_job_files())


def check_crawl(run_dir, min_jobs):
    """爬完之后的两道核查。返回 (ok, [(级别, 文案), ...])。

    级别分开是因为前两条是**事实**（新增多少条、池子多大），不是失败项 ——
    整批判负时把它们也标成 ❌，读的人会去找「40 条 CSV」哪里错了。
    """
    msgs = []
    summary_path = os.path.join(run_dir, 'crawl_summary.json')
    if not os.path.exists(summary_path):
        # 爬虫检测到未登录时会打印「登录完成后再次运行」然后**正常退出**，
        # 退出码 0。只看退出码会把「一条都没爬」当成成功，接着拿旧 CSV 去匹配。
        # crawl_summary.json 只在真的爬完之后才写，所以用它判定。
        msgs.append(('bad', '没有 crawl_summary.json —— 这一轮实际上没爬到东西。\n'
                            '  最常见的原因是没登录：\n'
                            '    python scripts/boss_post_interactive.py --ensure-login\n'
                            '  登录后接着跑：--from crawl'))
        return False, msgs

    try:
        with open(summary_path, encoding='utf-8') as f:
            summary = json.load(f)
        msgs.append(('info', '本轮新增 %s 条（重复跳过 %s 条）'
                             % (summary.get('written', '?'), summary.get('skipped', '?'))))
    except (OSError, ValueError):
        pass

    rows = job_pool_rows()
    # 行数是**上限**而不是唯一岗位数：同一个岗位会同时命中多个关键词，各写一份。
    # 实测太原 5 个关键词爬出 117 行，去重后只有 53 个岗位。所以这里如实说明。
    msgs.append(('info', '岗位池共 %d 行 CSV（跨关键词会重复，去重后的唯一岗位更少）' % rows))
    if min_jobs and rows < min_jobs:
        msgs.append(('bad', '低于最低岗位数 %d。深度分析按岗位烧 token，池子太小不值得往下跑：\n'
                            '  放宽条件重爬（加城市/换关键词），或确认无碍后 '
                            '--from match --min-jobs 0' % min_jobs))
        return False, msgs
    return True, msgs


# ==================== 各阶段的命令 ====================

def llm_flags(args):
    out = []
    for flag, value in (('--model', args.model), ('--base-url', args.base_url),
                        ('--api-key', args.api_key)):
        if value:
            out += [flag, value]
    return out


def infer_flags(args):
    """pipeline 的推断相关参数 → infer_params.py 的参数。"""
    out = []
    for flag, value in (('--city', args.city), ('--keywords', args.keywords),
                        ('--mode', args.crawl_mode), ('--match-mode', args.match_mode),
                        ('--degree', args.degree), ('--experience', args.experience),
                        ('--salary', args.salary), ('--job-type', args.job_type),
                        ('--scale', args.scale)):
        if value is not None:
            out += [flag, value]
    for flag, value in (('--count', args.count), ('--top-n', args.top_n),
                        ('--min-count', args.min_count)):
        if value is not None:
            out += [flag, str(value)]
    return out


def build_cmd(name, args, ctx):
    """返回该阶段的 argv；返回 None 表示这个阶段在当前模式下不跑。"""
    run_dir = ctx['run_dir']
    profile = os.path.join(run_dir, 'profile.json')
    py = sys.executable
    extra = llm_flags(args) if name in _LLM_STAGES else []

    if name == 'parse':
        cmd = [py, script('parse_resume.py'), args.resume_file, '--output-dir', run_dir]
        if args.force:
            cmd.append('--force')
        return cmd + extra

    if name == 'infer':
        return [py, script('infer_params.py'), run_dir] + infer_flags(args) + extra

    if name == 'crawl':
        argv = preferences.crawl_argv(ctx['params'])
        if not argv:
            return None
        return [py, script('boss_post_interactive.py')] + argv + ['--run-dir', run_dir]

    if name == 'match':
        if ctx['match_mode'] == 'quick':
            return [py, script('run_matcher.py'), '--mode', 'quick',
                    '--profile', profile, '--output-dir', run_dir]
        return [py, script('run_matcher.py'), '--mode', 'deep',
                '--profile', profile, '--output-dir', run_dir,
                '--top', str(ctx['top_n'])]

    if name == 'deep':
        if ctx['match_mode'] != 'deep':
            return None
        cmd = [py, script('deep_analyze.py'), run_dir]
        if args.workers:
            cmd += ['--workers', str(args.workers)]
        return cmd + extra

    if name == 'merge':
        if ctx['match_mode'] != 'deep':
            return None
        return [py, script('run_matcher.py'), '--mode', 'deep', '--merge',
                '--output-dir', run_dir]

    if name == 'materials':
        cmd = [py, script('gen_materials.py'), run_dir,
               '--greeting-mode', args.greeting_mode,
               '--resume-mode', args.resume_mode]
        if args.scene:
            cmd += ['--scene', args.scene]
        if args.only:
            cmd += ['--only', args.only]
        if args.workers:
            cmd += ['--workers', str(args.workers)]
        if args.force:
            cmd.append('--force')
        return cmd + extra

    if name == 'render':
        if args.resume_mode == 'skip':
            return None
        cmd = [py, script('render_images.py'), run_dir]
        if args.only:
            cmd += ['--only', args.only]
        if args.name:
            cmd += ['--name', args.name]
        if args.headless:
            cmd.append('--headless')
        if args.adopt_browser:
            cmd.append('--adopt-browser')
        return cmd

    raise AssertionError('未知阶段: %s' % name)


def run_stage(name, cmd, run_dir, timed=False):
    """跑一个阶段。子进程的输出直接透传（不捕获），返回退出码。"""
    print('\n%s' % ('─' * 60))
    print('▶ %s' % name)
    print('  %s' % show(cmd))
    print('%s' % ('─' * 60))
    sys.stdout.flush()

    started = time.monotonic()
    # 子进程各自都会把 stdout 改成 UTF-8，这里再兜一层：漏改的那个脚本一旦
    # print 中文就会在 GBK 控制台上抛 UnicodeEncodeError，把整轮带崩。
    #
    # BOSS_PIPELINE_STAGE 让子进程知道自己是被流水线启动的，从而印出
    # `pipeline.py --from <阶段>` 形式的修复命令 —— 单独跑的形式补完参数只会重跑
    # 这一步，后面的阶段还得自己一个个敲。子进程没有别的办法知道调用方是谁。
    env = dict(os.environ, PYTHONIOENCODING='utf-8', BOSS_PIPELINE_STAGE=name)
    code = subprocess.call(cmd, cwd=_HERE, env=env)
    elapsed = time.monotonic() - started

    if timed:
        # crawl 是唯一不给自己埋点的阶段（其余脚本内部都用了 stage_timer）。
        # 子进程非零退出**不是异常**，所以 status 得自己判，不能靠 with stage()。
        stage_timer.span(run_dir, name, elapsed,
                         status='ok' if code == 0 else 'error')

    print('  ← %s 结束（退出码 %d，%.1fs）' % (name, code, elapsed))
    return code


# ==================== 产物核查 ====================

def check_materials(run_dir, greeting_mode, resume_mode):
    """materials 之后的一次性快照核查。返回 (ok, 缺失列表)。

    gen_materials.py 是同步跑完的（不像 7d 那批后台 agent），所以这里不必 --wait ——
    它返回了就代表不会再有新产物落盘。
    """
    import check_artifacts

    kinds = []
    if greeting_mode != 'skip':
        kinds.append('greeting')
    if resume_mode != 'skip':
        kinds.append('resume')
    if not kinds:
        return True, []

    try:
        _, _, missing = check_artifacts.check(run_dir, kinds)
    except (OSError, ValueError) as exc:
        return False, ['产物核查失败: %s' % exc]
    return not missing, missing


# ==================== 主流程 ====================

def resume_cmd(run_dir, stage, end):
    """从 stage 接着跑到 end 的命令。

    带上范围是必须的：只印 `--from stage` 在新语义下只跑一个阶段，「修完接着跑」
    会跑完一步又停 —— 而人以为自己已经把整轮续上了。
    end 与 `--from stage` 的隐含终点一致时就不啰嗦了。
    """
    if end == _GROUP_END.get(stage, stage):
        tail = ''
    elif end == 'render':
        tail = ' --all'
    else:
        tail = ' --to %s' % end
    return ('python scripts/pipeline.py --run-dir "%s" --from %s%s'
            % (run_dir, stage, tail))


def next_stage(last, match_mode):
    """last 之后下一个**真会跑**的阶段；到头了返回 None。

    快速模式下 deep / merge 根本不存在，指着它们说「下一步」等于把人送去一条
    只会印「跳过」的命令。
    """
    for name in STAGES[STAGES.index(last) + 1:]:
        if name in _DEEP_ONLY and match_mode != 'deep':
            continue
        return name
    return None


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        prog='pipeline.py',
        description='把整轮流程串起来跑。默认只跑一个阶段；整轮要显式 --all，'
                    '且停在「材料已生成」—— 投递是另一条命令。',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='阶段顺序：%s\n'
               '不给 --to 就只跑 --from 那一个阶段；--all 等于 --to render。\n'
               '例外：--from match 会跑 match → deep → merge（单跑 match 产不出投递池）。\n'
               '深度模式才有 deep / merge 两步；快速模式会自动补写 qualified_jobs.json。\n'
               % ' → '.join(STAGES))

    ap.add_argument('resume_file', nargs='?', help='简历文件（PDF / Word / md / txt）')
    ap.add_argument('--run-dir', help='复用已有运行目录；不给则新建（或 --from 时取 LATEST.txt）')
    ap.add_argument('--from', dest='from_stage', choices=STAGES, default='parse',
                    help='从哪个阶段开始（默认 parse）')
    # --to 与 --all 互斥：两个都是「终点」，同时给必然有一个被无声忽略。
    # default=None 是为了区分「没给 --to」（隐含单阶段）和「显式 --to render」。
    endpoint = ap.add_mutually_exclusive_group()
    endpoint.add_argument('--to', dest='to_stage', choices=STAGES, default=None,
                          help='跑到哪个阶段为止（不给则只跑 --from 那一个阶段）')
    endpoint.add_argument('--all', dest='run_all', action='store_true',
                          help='一路跑到 render（等于 --to render；仍然不含投递）')
    ap.add_argument('--no-images', action='store_true',
                    help='不渲染简历长图（终点是 render 时下压到 materials）')
    ap.add_argument('--dry-run', action='store_true', help='只打印每一步的命令，不执行')

    g = ap.add_argument_group('爬取与匹配（透传给 infer_params.py）')
    g.add_argument('--city', help='城市，如 "杭州,上海"')
    g.add_argument('--keywords', help='搜索关键词')
    g.add_argument('--crawl-mode', dest='crawl_mode', choices=('list', 'custom'),
                   help='爬取模式（infer_params 的 --mode）')
    g.add_argument('--match-mode', dest='match_mode', choices=VALID_MATCH_MODES,
                   help='quick=纯规则评分，deep=逐岗位调模型')
    g.add_argument('--count', type=int, help='每关键词每城市条数，0=不限')
    g.add_argument('--top-n', dest='top_n', type=int, help='深度模式的候选数')
    g.add_argument('--min-count', dest='min_count', type=int, help='最低岗位数（写进预设）')
    g.add_argument('--degree')
    g.add_argument('--experience')
    g.add_argument('--salary')
    g.add_argument('--job-type', dest='job_type')
    g.add_argument('--scale', help='公司规模（只能手动给，不推断）')
    g.add_argument('--min-jobs', dest='min_jobs', type=int,
                   help='爬完的岗位池低于这个数就停（默认取 crawl_params 的最低岗位数，0=不拦）')

    g = ap.add_argument_group('材料与渲染')
    g.add_argument('--greeting-mode', dest='greeting_mode',
                   choices=('ai', 'default', 'skip'), default='ai')
    g.add_argument('--resume-mode', dest='resume_mode', choices=('ai', 'skip'), default='ai')
    g.add_argument('--scene', help='投递场景（社招/校招/实习），默认按简历推断')
    g.add_argument('--only', help='只处理这些 1-based 序号，如 1,3,5-7（materials 与 render 共用）')
    g.add_argument('--name', help='简历图文件名里的姓名，覆盖 profile.json')
    g.add_argument('--force', action='store_true', help='覆盖已有的 profile.json 与材料产物')
    g.add_argument('--workers', '-w', type=int, help='deep / materials 的并发数')
    g.add_argument('--headless', action='store_true', help='渲染时不显示浏览器窗口')
    g.add_argument('--adopt-browser', dest='adopt_browser', action='store_true',
                   help='接管端口 9333 上已有的浏览器（清楚风险再用）')

    g = ap.add_argument_group('模型（覆盖 assets/llm_config.json 与环境变量）')
    g.add_argument('--model')
    g.add_argument('--base-url')
    g.add_argument('--api-key')

    return ap.parse_args(argv)


def resolve_end(args):
    """算出终点阶段，并说明是哪来的（终点决定花多少钱，不能悄悄定）。"""
    if args.run_all:
        return 'render', '--all'
    if args.to_stage:
        return args.to_stage, '--to'
    return _GROUP_END.get(args.from_stage, args.from_stage), '默认只跑这一个阶段'


def make_plan(args):
    """算出要跑的阶段序列。返回 (plan, 错误信息)。"""
    end_stage, _ = resolve_end(args)
    start, end = STAGES.index(args.from_stage), STAGES.index(end_stage)
    if start > end:
        return None, ('--from %s 在 --to %s 之后，没有阶段可跑'
                      % (args.from_stage, end_stage))
    if args.no_images and end == STAGES.index('render'):
        end = STAGES.index('materials')
    plan = list(STAGES[start:end + 1])
    if not plan:
        # 只有 `--from render --no-images` 会走到这里：终点被下压到 materials，
        # 反而跑到了起点前面。说清楚是哪两个参数打架，别丢一个空计划下去。
        return None, ('--from render 配 --no-images 没有阶段可跑'
                      '（--no-images 把终点压到 materials，在 render 之前）')
    return plan, None


def main(argv=None):
    reconfigure_stdout()        # Windows 控制台是 GBK；重定向成 StringIO 时静默跳过

    args = parse_args(argv)

    plan, err = make_plan(args)
    if err:
        print('❌ %s' % err)
        return 1
    if 'parse' in plan and not args.resume_file:
        print('❌ parse 阶段需要简历文件：python scripts/pipeline.py 简历.pdf')
        print('  想跳过解析就指定起点，如 --from crawl（会用运行目录里已有的 profile.json）')
        return 1
    if 'parse' in plan and not os.path.exists(args.resume_file):
        print('❌ 找不到简历文件: %s' % args.resume_file)
        return 1

    run_dir, err = resolve_run_dir(args, plan)
    if err:
        print('❌ %s' % err)
        return 1

    params = load_params(run_dir)
    match_mode, mode_src = match_mode_of(params, args.match_mode)
    ctx = {
        'run_dir': run_dir,
        'params': params,
        'match_mode': match_mode,
        'top_n': args.top_n or params.get('top_n') or 15,
    }

    print('=' * 60)
    print('运行目录: %s' % run_dir)
    print('阶段    : %s（%s）' % (' → '.join(plan), resolve_end(args)[1]))
    print('匹配模式: %s（来自%s）' % (match_mode, mode_src))
    if args.no_images:
        print('简历长图: 关闭（--no-images）')
    elif args.resume_mode == 'skip':
        print('简历长图: 关闭（--resume-mode skip 不产简历 JSON，无从渲染）')
    print('=' * 60)

    if 'infer' not in plan and 'crawl' in plan and not params:
        print('❌ 要跑 crawl 却没有 crawl_params.json（%s）' % run_dir)
        print('  先跑推断：python scripts/infer_params.py "%s"' % run_dir)
        print('  或把起点挪到 infer：--from infer')
        return 1

    min_jobs = args.min_jobs if args.min_jobs is not None else (params.get('min_count') or 0)
    partial = []        # 记下退出码 3 的阶段：跑完了但有缺口
    started = time.monotonic()

    for name in plan:
        cmd = build_cmd(name, args, ctx)

        if cmd is None:
            if name in _DEEP_ONLY:
                print('\n○ %s：快速模式没有这一步，跳过' % name)
            elif name == 'render':
                print('\n○ render：--resume-mode skip 没产出简历 JSON，跳过')
            elif name == 'crawl' and args.dry_run:
                # infer 还没跑，crawl_params.json 自然不在。dry-run 只是给人看命令，
                # 不该因为「未来才会存在的文件现在不存在」就报失败。
                print('\n▶ crawl\n  %s <infer 跑完后按 crawl_params.json 拼装> '
                      '--run-dir "%s"'
                      % (show([sys.executable, script('boss_post_interactive.py')]), run_dir))
            else:
                print('\n❌ %s 无法拼出命令（crawl_params.json 里缺关键词或城市）' % name)
                return 1
            continue

        if args.dry_run:
            print('\n▶ %s\n  %s' % (name, show(cmd)))
            continue

        code = run_stage(name, cmd, run_dir, timed=(name == 'crawl'))
        if code == 3:
            partial.append(name)
        elif code != 0:
            print('\n❌ %s 失败（退出码 %d），流水线停在这里。' % (name, code))
            print('  修完接着跑：%s' % resume_cmd(run_dir, name, plan[-1]))
            return 1

        # ── 阶段后的核查 ──
        if name == 'infer':
            params = load_params(run_dir)
            if not params:
                print('❌ infer 跑完了却没有 crawl_params.json，无法继续')
                return 1
            ctx['params'] = params
            new_mode, new_src = match_mode_of(params, args.match_mode)
            if new_mode != ctx['match_mode']:
                print('  匹配模式改为 %s（来自%s）' % (new_mode, new_src))
                ctx['match_mode'] = new_mode
            ctx['top_n'] = args.top_n or params.get('top_n') or 15
            if args.min_jobs is None:
                min_jobs = params.get('min_count') or 0

        elif name == 'crawl':
            ok, msgs = check_crawl(run_dir, min_jobs)
            for level, msg in msgs:
                print('  %s %s' % ('·' if level == 'info' else '❌', msg))
            if not ok:
                return 1

        elif name == 'match' and ctx['match_mode'] == 'quick':
            state, note = qualified_from_scored(run_dir)
            print('  %s %s' % ('❌' if state in ('error', 'empty') else '✅', note))
            if state in ('error', 'empty'):
                return 1

        elif name == 'materials':
            ok, missing = check_materials(run_dir, args.greeting_mode, args.resume_mode)
            if ok:
                print('  ✅ 材料齐全')
            else:
                for item in missing:
                    print('  ❌ 缺失：%s' % item)
                print('  只补缺的这几个：python scripts/gen_materials.py "%s" --only <序号>'
                      % run_dir)
                if 'materials' not in partial:
                    partial.append('materials')

    if args.dry_run:
        print('\n--dry-run：以上命令均未执行。')
        return 0

    # ── 收尾 ──
    print('\n%s' % ('=' * 60))
    print('流水线结束（%.1f 分钟）' % ((time.monotonic() - started) / 60))
    if partial:
        print('⚠ 这些阶段部分成功：%s —— 下面的岗位数可能少于预期' % '、'.join(partial))
    print('运行目录: %s' % run_dir)
    print('耗时排行: python scripts/stage_timer.py report "%s"' % run_dir)

    nxt = next_stage(plan[-1], ctx['match_mode'])
    if nxt == 'render' and (args.no_images or args.resume_mode == 'skip'):
        nxt = None          # 人明说了不要长图，别再劝他跑 render

    if nxt:
        # 默认只跑一个阶段，所以「停下来」是正常结束而不是中断 —— 但对习惯了
        # 一条命令跑完整轮的人来说，这里看着像半路断了。把接着跑的命令摆出来。
        print('\n跑完 %s 就停了（%s）。接着跑：'
              % (' → '.join(plan), resolve_end(args)[1]))
        one = resume_cmd(run_dir, nxt, _GROUP_END.get(nxt, nxt))
        whole = resume_cmd(run_dir, nxt, 'render')
        print('  下一个阶段：%s' % one)
        if whole != one:
            print('  余下全部  ：%s' % whole)
        if nxt == 'materials':
            print('  materials 会给每个岗位各调两次模型（招呼语 + 简历优化），'
                  '先看一眼投递池再跑：')
            print('    %s' % os.path.join(run_dir, 'qualified_jobs.json'))
    elif STAGES.index(plan[-1]) >= STAGES.index('materials'):
        print('\n材料已生成。**投递是单独一条命令**，本流水线不会自己投：')
        print('  python scripts/apply.py "%s" --yes%s'
              % (run_dir, ' --no-image' if (args.no_images or args.resume_mode == 'skip')
                 else ''))
        print('  不带 --yes 是空跑：只打印要投的岗位与材料，不碰浏览器。')
        print('  建议先空跑一次看清名单：')
        print('    python scripts/apply.py "%s"' % run_dir)

    return 3 if partial else 0


if __name__ == '__main__':
    sys.exit(main())
