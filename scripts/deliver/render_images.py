#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把 materials/resume_*.json 渲染成简历长图（流水线的 render 阶段）。

这一步曾经是五条手工命令：起服务 → 暂存 md → 批量导入 → 批量导出 → 分发改名 →
删临时简历。每一步的失败模式都不一样，而且**必须串行** —— 全部简历共用
一个浏览器、一个 origin、一份 localStorage，并发只会互相踩。所以这里不给 --workers。

三个不能省的保护：

1. **端口 9333 归属检查。** DrissionPage 发现 9333 上已有实例时会直接接管它，
   `set_user_data_path` 被静默丢弃 —— 于是导入和删除都作用在**别人的** profile 上。
   本脚本会先确认那个浏览器的 --user-data-dir 是不是 assets/showcv_profile，
   不是就拒绝跑（要接管得显式给 --adopt-browser）。
2. **暂存名必须唯一。** import_md.py 按「文件名去扩展名」取简历名，重名会变成
   `名字 (2)`，而第 3、4 步全靠「名字 → id」定位 —— 一重名就静默错位。
   所以暂存文件名带 run 时间戳。
3. **姓名不猜。** `<姓名>-<应聘岗位>.png` 是 HR 看到的附件名。profile 里没有姓名时
   直接报错要 --name，绝不用占位符。

用法：
    python scripts/deliver/render_images.py <run_dir>
    python scripts/deliver/render_images.py <run_dir> --only 1,3,5-7
    python scripts/deliver/render_images.py <run_dir> --dry-run          # 只暂存 md 并打印命令
    python scripts/deliver/render_images.py <run_dir> --url http://127.0.0.1:3090
    python scripts/deliver/render_images.py <run_dir> --keep-temp        # 不删 ShowCV 里的临时简历

退出码：0 = 全部岗位拿到图，1 = 前置条件不满足（没简历产物/没姓名/端口归属不对），
3 = 跑完了但有岗位缺图。
"""

import os
import re
import sys
import json
import glob
import time
import shutil
import socket
import zipfile
import argparse
import subprocess

_SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _SCRIPTS)

import stage_timer
from write_application_md import (sanitize, load_jobs, resolve_csv_row, merge,
                                  job_dir_stem, find_duplicate_links)
from resume_matcher import (materials_dir, resume_pattern, deliver_dir,
                            profile_path as _profile_path,
                            showcv_staging_dir, showcv_exports_dir)

_SKILL_ROOT = os.path.dirname(_SCRIPTS)
_SHOWCV_DIR = os.path.join(_SKILL_ROOT, 'scripts', 'showcv')

SHOWCV_DEBUG_PORT = 9333          # 与 showcv/_browser.py 的 DEBUG_PORT 一致
EXPECTED_PROFILE = os.path.join(_SKILL_ROOT, 'assets', 'showcv_profile')

# profile.json 里表示「没提取到」的几种写法，都不能当姓名用
_NO_NAME = ('', '未提取', '未知', '未提供', 'null', 'None', '无')

MIN_RESUME_CHARS = 50             # 与 gen_materials 的门槛一致：更短的不可能是一份简历


class _StepFailed(Exception):
    """子步骤失败。用异常而不是 return，好让 stage_timer 记成 error —— 见 main() 里的说明。"""

    def __init__(self, step):
        super().__init__(step)
        self.step = step


def parse_only(spec, total):
    """"1,3,5-7" → [1,3,5,6,7]。越界的静默丢掉（岗位数会随 --limit 变）。"""
    if not spec:
        return list(range(1, total + 1))
    picked = []
    for chunk in str(spec).split(','):
        chunk = chunk.strip()
        if not chunk:
            continue
        if '-' in chunk:
            lo, _, hi = chunk.partition('-')
            try:
                lo, hi = int(lo), int(hi)
            except ValueError:
                raise ValueError('--only 里的 %r 不是范围' % chunk)
            picked.extend(range(lo, hi + 1))
        else:
            try:
                picked.append(int(chunk))
            except ValueError:
                raise ValueError('--only 里的 %r 不是数字' % chunk)
    seen, out = set(), []
    for i in picked:
        if 1 <= i <= total and i not in seen:
            seen.add(i)
            out.append(i)
    return out


def resolve_name(profile, override):
    """`<姓名>-<应聘岗位>` 里的姓名。取不到就报错，不给占位符。"""
    if override and override.strip():
        return override.strip()
    # profile.json 会写显式 null，get(k, default) 的默认值永远不触发
    name = str(((profile or {}).get('basic_info') or {}).get('name') or '').strip()
    if name in _NO_NAME:
        raise ValueError(
            'profile.json 的 basic_info.name 是空的（当前值：%r）。\n'
            '  这个字符串会成为 HR 看到的附件名，不能用占位符。请加 --name "你的名字"。'
            % name)
    return name


def find_resume_artifact(run_dir, index):
    """materials/resume_{i}_*.json 的路径。找不到返回 None。"""
    pattern = resume_pattern(run_dir, index)
    hits = [p for p in sorted(glob.glob(pattern)) if os.path.getsize(p) > 0]
    return hits[0] if hits else None


def _load_resume_data(path):
    """读 resume_{i}_*.json 并校验是个对象。返回整个 dict。"""
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError('%s 不是 JSON 对象' % os.path.basename(path))
    return data


def read_markdown(path):
    """从 resume_{i}_*.json 取 optimized_resume。"""
    data = _load_resume_data(path)
    text = str(data.get('optimized_resume') or '').strip()
    if len(text) < MIN_RESUME_CHARS:
        raise ValueError('optimized_resume 只有 %d 字（<%d），渲染出来会是一张空白图'
                         % (len(text), MIN_RESUME_CHARS))
    return text


def job_dir_name(job, index):
    """`deliver/#N-<公司>-<岗位>` 目录名 + 岗位展示名。

    刻意走 write_application_md 的 resolve_csv_row + merge + job_dir_stem：那个脚本
    写 `投递.md` 时就是这么定目录的，自己另算一套的下场是 md 和 png 落进两个
    只差几个字的目录里，而且要等用户翻文件夹才发现。序号前缀（见 job_dir_stem）
    保证同一批里「公司+岗位」撞名也不互相覆盖。
    """
    csv_row, _ = resolve_csv_row(job)
    row = merge(job, csv_row)
    company = row.get('公司') or row.get('company') or '未知公司'
    position = sanitize(row.get('职位') or row.get('position') or '未知岗位')
    return job_dir_stem(index, company, position), position


def run_stamp(run_dir, override):
    """暂存名的唯一性来源。优先用运行目录名（同一轮多次渲染复用同一批简历名）。"""
    if override:
        return sanitize(override)
    base = os.path.basename(os.path.normpath(run_dir))
    if re.match(r'^\d{4}-\d{2}-\d{2}[_-]\d{2}', base):
        return re.sub(r'[^0-9]', '', base)[:14] or time.strftime('%Y%m%d-%H%M%S')
    return time.strftime('%Y%m%d-%H%M%S')


# ==================== 暂存 ====================

def stage_all(run_dir, jobs, indexes, person, stamp):
    """把 materials/resume_*.json 渲染成 deliver/#N-<公司>-<岗位>/<姓名>-<岗位>.png。

    只向岗位目录写最终要交的 PNG（和投递.md，那是 write_application_md 的事）。
    优化后的简历正文不进岗位目录 —— 想看或改就用 read_thin.py 读 materials/resume_#.json，
    避免同一份内容在多个地方派生、漂移（见 SKILL.md 的去冗余说明）。

    返回 (items, failures)。item 是 dict：index / job_dir / staged_path /
    staged_name / png_path。
    """
    staging_dir = showcv_staging_dir(run_dir)
    os.makedirs(staging_dir, exist_ok=True)

    items, failures = [], []
    for index in indexes:
        job = jobs[index - 1]
        artifact = find_resume_artifact(run_dir, index)
        if not artifact:
            failures.append((index, '没有 materials/resume_%d_*.json（先跑 gen_materials.py）' % index))
            continue
        try:
            data = _load_resume_data(artifact)
            markdown = str(data.get('optimized_resume') or '').strip()
            if len(markdown) < MIN_RESUME_CHARS:
                raise ValueError('optimized_resume 只有 %d 字（<%d），渲染出来会是一张空白图'
                                 % (len(markdown), MIN_RESUME_CHARS))
        except (ValueError, OSError) as exc:
            failures.append((index, str(exc)))
            continue

        dir_name, position = job_dir_name(job, index)
        job_dir = os.path.join(deliver_dir(run_dir), dir_name)
        os.makedirs(job_dir, exist_ok=True)

        base = '%s-%s' % (sanitize(person), sanitize(position))

        # 暂存名带时间戳，见模块 docstring 第 2 条
        staged_name = '%s__%s' % (dir_name, stamp)
        staged_path = os.path.join(staging_dir, staged_name + '.md')
        with open(staged_path, 'w', encoding='utf-8') as f:
            f.write(markdown if markdown.endswith('\n') else markdown + '\n')

        items.append({
            'index': index,
            'job_dir': job_dir,
            'staged_path': staged_path,
            'staged_name': staged_name,
            'png_path': os.path.join(job_dir, base + '.png'),
        })
    return items, failures


# ==================== 浏览器 / 服务 ====================

def port_busy(port, host='127.0.0.1', timeout=0.35):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def check_browser_owner(adopt, browser_path=None, headless=False):
    """确认 9333 上的浏览器用的是 assets/showcv_profile。

    返回 (ok, 说明)。9333 空闲时直接放行 —— 后面 connect 会用正确的 profile 起一个新的。
    """
    if not port_busy(SHOWCV_DEBUG_PORT):
        return True, '端口 %d 空闲，将用 assets/showcv_profile 新起浏览器' % SHOWCV_DEBUG_PORT

    sys.path.insert(0, _SHOWCV_DIR)
    try:
        from _browser import connect, real_profile
    except ImportError as exc:
        return False, ('端口 %d 上已有浏览器，但导入 showcv/_browser 失败（%s），'
                       '无法确认它的 profile 归属。' % (SHOWCV_DEBUG_PORT, exc))

    try:
        browser = connect(browser_path, headless)
        actual = real_profile(browser)
    except BaseException as exc:            # connect 用 SystemExit 报错
        return False, '连不上端口 %d 上的浏览器：%s' % (SHOWCV_DEBUG_PORT, exc)

    def norm(path):
        return os.path.normcase(os.path.normpath(str(path).strip().strip('"')))

    if norm(actual) == norm(EXPECTED_PROFILE):
        return True, '接管的是本技能自己的浏览器（%s）' % actual
    if adopt:
        return True, '⚠ --adopt-browser：明知 profile 是 %s 仍继续' % actual
    return False, (
        '端口 %d 上跑着**别人的**浏览器。\n'
        '  实际 profile: %s\n'
        '  期望 profile: %s\n'
        '  DrissionPage 会直接接管这个实例并丢弃 set_user_data_path —— 接着跑，'
        '导入和删除就作用在那份 profile 的简历上（delete_resumes 会真删）。\n'
        '  处理办法：关掉那个浏览器窗口后重跑，或确认无碍后加 --adopt-browser。'
        % (SHOWCV_DEBUG_PORT, actual, EXPECTED_PROFILE))


def ensure_server(url_override):
    """拿到 ShowCV 地址。返回 (url, popen 或 None)。

    不复制 serve.py 的复用判断：直接跑它，它自己会探测「已在跑就复用同端口并退出」。
    端口决定 origin，origin 决定看得见哪些简历 —— 换端口等于简历消失。
    """
    if url_override:
        return url_override.rstrip('/'), None

    script = os.path.join(_SHOWCV_DIR, 'serve.py')
    print('  启动/复用 ShowCV 静态服务 …')
    proc = subprocess.Popen(
        [sys.executable, script],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding='utf-8', errors='replace',
    )
    # 首行固定是 SHOWCV_READY <url>（serve.py 保证），复用场景下打印完就退出
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if not line:
            if proc.poll() is not None:
                break
            continue
        line = line.strip()
        if line.startswith('SHOWCV_READY'):
            url = line.split(None, 1)[1].strip().rstrip('/')
            print('  %s' % line)
            return url, (proc if proc.poll() is None else None)

    err = (proc.stderr.read() or '').strip() if proc.stderr else ''
    if proc.poll() is None:
        proc.terminate()
    raise RuntimeError('serve.py 没有在 30 秒内打印 SHOWCV_READY。%s'
                       % ('\n  stderr: %s' % err if err else ''))


def run_step(label, cmd, timeout):
    """跑一个 showcv/ 子命令。返回 (ok, 合并输出)。"""
    print('\n  $ %s' % ' '.join(('"%s"' % a if ' ' in a else a) for a in cmd))
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding='utf-8', errors='replace', timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, '%s 超过 %.0f 秒没结束' % (label, timeout)
    output = ((proc.stdout or '') + (proc.stderr or '')).strip()
    for line in output.splitlines():
        print('    %s' % line)
    return proc.returncode == 0, output


# ==================== 分发 ====================

def snapshot(directory):
    return set(os.listdir(directory)) if os.path.isdir(directory) else set()


def unique_export_dir(base, stamp):
    """给本次导出一个**空**目录：intermediate/exports/<stamp>[-n]。

    不复用同一个目录，因为 export_images.py 设了 when_download_file_exists('rename')：
    目录里已有同名 png 时，Chrome 会把新文件存成 `<名字> (1).png`，而分发是按
    「文件名去扩展名 == 暂存名」匹配的 —— 于是图明明下载成功，却被报成缺图。
    空目录让这种改名不可能发生。
    """
    for attempt in range(1, 100):
        candidate = os.path.join(base, stamp if attempt == 1 else '%s-%d' % (stamp, attempt))
        if not os.path.isdir(candidate) or not os.listdir(candidate):
            os.makedirs(candidate, exist_ok=True)
            return candidate
    raise RuntimeError('%s 下已有 99 个导出目录，清理一下再跑' % base)


# Chrome 的重名后缀：`名字 (1).png`。目录是新建的空目录，正常不会出现，
# 但用户用 --url 指了自己的目录时兜一下 —— 匹配不上就等于报「图丢了」。
_DUP_SUFFIX = re.compile(r' \(\d+\)$')


def collect_pngs(export_dir, before):
    """导出目录里新出现的 PNG，{文件名去扩展名: 路径}。zip 会先解开。"""
    created = sorted(snapshot(export_dir) - before)
    pngs = {}

    def record(stem, path):
        pngs[stem] = path
        stripped = _DUP_SUFFIX.sub('', stem)
        if stripped != stem:
            pngs.setdefault(stripped, path)

    for name in created:
        path = os.path.join(export_dir, name)
        if name.lower().endswith('.zip'):
            # 多份简历时前端打成 showcv-images-<日期>.zip，里面每份一个 <暂存名>.png
            try:
                with zipfile.ZipFile(path) as zf:
                    for member in zf.namelist():
                        if not member.lower().endswith('.png'):
                            continue
                        stem = os.path.splitext(os.path.basename(member))[0]
                        target = os.path.join(export_dir, os.path.basename(member))
                        with zf.open(member) as src, open(target, 'wb') as dst:
                            shutil.copyfileobj(src, dst)
                        record(stem, target)
            except (zipfile.BadZipFile, OSError) as exc:
                print('    ⚠ 解压 %s 失败：%s' % (name, exc))
        elif name.lower().endswith('.png'):
            record(os.path.splitext(name)[0], path)
    return pngs


def distribute(items, pngs):
    """把 PNG 按暂存名搬进各岗位目录并改成 <姓名>-<应聘岗位>.png。返回缺图的 index。"""
    missing = []
    for item in items:
        source = pngs.get(item['staged_name'])
        if not source or not os.path.exists(source):
            missing.append(item['index'])
            continue
        shutil.copyfile(source, item['png_path'])
        size = os.path.getsize(item['png_path']) / 1024.0
        print('    #%d → %s  %.1fKB' % (item['index'], item['png_path'], size))
    return missing


# ==================== 主流程 ====================

def main():
    for stream in (sys.stdout, sys.stderr):        # Windows 控制台是 GBK
        stream.reconfigure(encoding='utf-8', errors='replace')

    ap = argparse.ArgumentParser(
        description='把 materials/resume_*.json 渲染成 <姓名>-<应聘岗位>.png（串行）',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='全部简历共用一个浏览器和一份 localStorage，所以本步骤必须串行，没有 --workers。\n')
    ap.add_argument('run_dir')
    ap.add_argument('--only', help='只渲染这些岗位，如 "1,3,5-7"')
    ap.add_argument('--name', help='<姓名>-<应聘岗位> 里的姓名，覆盖 profile.json')
    ap.add_argument('--url', help='ShowCV 地址；不给就自动起/复用 serve.py')
    ap.add_argument('--mode', choices=('flat', 'paginated'), default='flat',
                    help='flat=一张长图（默认，投递用），paginated=每页一张 A4')
    ap.add_argument('--scale', type=int, choices=(1, 2, 3), default=2)
    ap.add_argument('--stamp', help='暂存名后缀，默认取运行目录的时间戳')
    ap.add_argument('--keep-temp', action='store_true',
                    help='渲染完不删 ShowCV 里的临时简历（便于手动微调后重导）')
    ap.add_argument('--adopt-browser', action='store_true',
                    help='端口 %d 上是别的 profile 时仍继续（危险，见 --help 说明）'
                         % SHOWCV_DEBUG_PORT)
    ap.add_argument('--headless', action='store_true')
    ap.add_argument('--browser', help='浏览器可执行文件，默认自动探测')
    ap.add_argument('--timeout', type=float, default=300, help='单个子步骤上限（秒），默认 300')
    ap.add_argument('--dry-run', action='store_true',
                    help='只暂存 md 并打印将要执行的命令，不碰浏览器')
    args = ap.parse_args()

    run_dir = os.path.abspath(args.run_dir)
    if not os.path.isdir(run_dir):
        print('❌ 运行目录不存在: %s' % run_dir)
        return 1

    # ── 前置：姓名与岗位 ──
    profile = {}
    profile_path = _profile_path(run_dir)
    if os.path.exists(profile_path):
        try:
            with open(profile_path, 'r', encoding='utf-8') as f:
                profile = json.load(f)
        except ValueError as exc:
            print('❌ profile.json 解析失败: %s' % exc)
            return 1
    try:
        person = resolve_name(profile, args.name)
    except ValueError as exc:
        print('❌ %s' % exc)
        return 1

    try:
        jobs = load_jobs(run_dir)
    except (OSError, ValueError) as exc:
        print('❌ 读不到 qualified_jobs.json: %s' % exc)
        print('  它由 run_matcher.py 自动生成（quick 或 deep --merge 之后）')
        return 1
    if not jobs:
        print('❌ qualified_jobs.json 是空的，没有岗位需要渲染')
        return 1

    try:
        indexes = parse_only(args.only, len(jobs))
    except ValueError as exc:
        print('❌ %s' % exc)
        return 1
    if not indexes:
        print('❌ --only=%s 没有落在 1..%d 范围内的岗位' % (args.only, len(jobs)))
        return 1

    dup = find_duplicate_links(run_dir, jobs, indexes)
    if dup:
        for link, ixs in dup.items():
            print('❌ 同一岗位出现多次（link=%s）：%s'
                  % (link, '、'.join('#%d' % i for i in ixs)))
        print('  同一 link = 同一 HR，重复渲染只会产出两套几乎一样的简历。')
        print('  请先在上游去重，或用 --only 只渲染其中一个。')
        return 1

    stamp = run_stamp(run_dir, args.stamp)
    print('渲染 %d/%d 个岗位｜姓名「%s」｜暂存戳 %s｜mode=%s scale=%d'
          % (len(indexes), len(jobs), person, stamp, args.mode, args.scale))

    # ── 1. 暂存 ──
    items, failures = stage_all(run_dir, jobs, indexes, person, stamp)
    for index, reason in failures:
        print('  ⚠ #%d 跳过：%s' % (index, reason))
    if not items:
        print('\n❌ 没有一个岗位有可用的简历产物，无事可做。')
        print('  先跑：python scripts/stages/gen_materials.py "%s"' % run_dir)
        return 1
    print('\n已暂存 %d 份 markdown → %s'
          % (len(items), showcv_staging_dir(run_dir)))

    export_base = showcv_exports_dir(run_dir)
    names = []
    for item in items:
        names.extend(['--name', item['staged_name']])

    import_cmd = [sys.executable, os.path.join(_SHOWCV_DIR, 'import_md.py'),
                  '--url', '<url>'] + [i['staged_path'] for i in items]
    # <out> 在确认要真跑之后才换成实际目录 —— dry-run 不该建目录
    export_cmd = [sys.executable, os.path.join(_SHOWCV_DIR, 'export_images.py'),
                  '--url', '<url>', '--mode', args.mode, '--scale', str(args.scale),
                  '--out', '<out>', '--run-dir', run_dir] + names

    if args.dry_run:
        print('\n--dry-run：markdown 已落盘，以下命令未执行')
        for cmd in (import_cmd, export_cmd):
            shown = [export_base + os.sep + stamp if a == '<out>' else a for a in cmd]
            print('  %s' % ' '.join(('"%s"' % a if ' ' in a else a) for a in shown))
        if not args.keep_temp:
            print('  %s' % ' '.join([sys.executable,
                                     os.path.join(_SHOWCV_DIR, 'delete_resumes.py'),
                                     '--url', '<url>', '--yes'] + names))
        for item in items:
            print('    #%d %s → %s' % (item['index'], item['staged_name'], item['png_path']))
        return 0

    # ── 2. 端口归属（写操作之前） ──
    ok, note = check_browser_owner(args.adopt_browser, args.browser, args.headless)
    print('\n%s %s' % ('✅' if ok else '❌', note))
    if not ok:
        return 1

    # ── 3. 服务 ──
    server = None
    try:
        try:
            url, server = ensure_server(args.url)
        except RuntimeError as exc:
            print('❌ %s' % exc)
            return 1

        extra = (['--headless'] if args.headless else []) + \
                (['--browser', args.browser] if args.browser else [])
        try:
            export_dir = unique_export_dir(export_base, stamp)
        except RuntimeError as exc:
            print('❌ %s' % exc)
            return 1
        for cmd in (import_cmd, export_cmd):
            cmd[cmd.index('<url>')] = url
            if '<out>' in cmd:
                cmd[cmd.index('<out>')] = export_dir
            cmd.extend(extra)

        before = snapshot(export_dir)

        # stage_timer.stage 只在**异常**穿过时记 status='error'；从 with 里 return 会被
        # 记成 ok。导入/导出失败是本步骤最常见的失败，用异常抛出去才不会把它记成成功。
        try:
            with stage_timer.stage(run_dir, 'render_images', note='%d 份' % len(items)):
                # ── 4. 导入 ──
                ok, _ = run_step('import_md', import_cmd, args.timeout)
                if not ok:
                    raise _StepFailed('import')

                # ── 5. 导出 ──
                ok, _ = run_step('export_images', export_cmd, args.timeout)
                if not ok:
                    raise _StepFailed('export')

                # ── 6. 分发 ──
                print('\n  分发 PNG：')
                pngs = collect_pngs(export_dir, before)
                missing = distribute(items, pngs)
        except _StepFailed as exc:
            if exc.step == 'import':
                print('\n❌ 导入失败，未继续导出。暂存文件留在 intermediate/staging/ 可手动重试。')
            else:
                print('\n❌ 导出失败。ShowCV 里的临时简历**保留**着，'
                      '可以打开编辑器看看是哪份出的问题。')
                print('  清理：python scripts/showcv/delete_resumes.py --url %s --yes %s'
                      % (url, ' '.join('--name "%s"' % i['staged_name'] for i in items)))
            return 1
    finally:
        if server is not None:
            server.terminate()

    # ── 7. 清理临时简历 ──
    if args.keep_temp:
        print('\n  --keep-temp：ShowCV 里保留了 %d 份临时简历' % len(items))
    else:
        delete_cmd = [sys.executable, os.path.join(_SHOWCV_DIR, 'delete_resumes.py'),
                      '--url', url, '--yes'] + names + extra
        ok, _ = run_step('delete_resumes', delete_cmd, args.timeout)
        if not ok:
            # 临时简历没删掉不影响已经拿到的图，所以只告警不改退出码
            print('    ⚠ 临时简历没删干净，下次导入可能撞名。可手动重跑上面这条命令。')

    # ── 汇总 ──
    print('\n%s' % ('=' * 60))
    done = len(items) - len(missing)
    print('  拿到图 %d 份，缺图 %d 份，跳过 %d 个岗位' % (done, len(missing), len(failures)))
    if missing:
        print('  缺图的岗位：%s' % '、'.join('#%d' % i for i in missing))
        print('  导出目录里的实际文件名和暂存名对不上时会这样 —— 去 %s 看一眼' % export_dir)
    if failures:
        print('  跳过的岗位：%s（缺简历产物）' % '、'.join('#%d' % i for i, _ in failures))
    return 3 if (missing or failures) else 0


if __name__ == '__main__':
    sys.exit(main())
