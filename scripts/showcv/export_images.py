#!/usr/bin/env python3
"""通过 `/export` 直链把简历导出成图片，并确认文件真的落到磁盘上。

**为什么用直链而不是点导出按钮**：编辑器的导出按钮只能导当前那一份，而 `/export`
接受重复的 `id` 参数和 `all=1`，一次调用就能覆盖批量场景。页面在挂载时自行触发导出
（打包产物里的 `mO` 组件，`useRef` 守卫保证只跑一次），所以脚本要做的只有三件事：
把 URL 拼对、在导航**之前**把下载目录设好、然后等页面自己给出结果。

**为什么下载目录必须先设**：DrissionPage 靠 CDP 的 `Browser.setDownloadBehavior`
（`eventsEnabled=True`）接管下载，只有在这个回调装好之后开始的下载才会被登记成
mission。而这个页面是「一打开就导出」，先导航再设目录就是在和它赛跑 —— 输了的表现
是 `wait.downloads_done()` 立刻返回「没有任务」，脚本报成功而磁盘上什么都没有。

用法：

    # 导当前简历（和编辑器里点导出一致）
    python scripts/showcv/export_images.py --url http://127.0.0.1:3090

    # 按名字导两份，长图模式
    python scripts/showcv/export_images.py --url http://127.0.0.1:3090 \
        --name 贺秀莲 --name 张三-后端 --mode flat

    # 全导，3 倍图，落到指定目录
    python scripts/showcv/export_images.py --url http://127.0.0.1:3090 \
        --all --scale 3 --out D:/tmp/cv

前端行为（都抄自 app/ 的构建，改动前先核对）：

* 单张图直接下载 `<简历名>.png`；多张图（分页模式的多页、或多份简历）打成
  `showcv-images-<YYYYMMDD>.zip`。分页模式下多页文件名是 `<简历名>-01.png`。
* `scale` 只认 1/2/3，页面对非法值静默回落到 2 —— **本脚本反而会报错**：
  在命令行里 `--scale 4` 几乎总是笔误，静默回落只会让人以为拿到了 4 倍图。
* 找不到 id 时页面报「未找到对应的简历」，脚本会在导航前就先把这种情况拦下来。
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path
from urllib.parse import urlencode, urljoin

from _browser import SKILL_ROOT, connect, open_app, real_profile, use_utf8_output
from _resumes import format_list, read_resumes, resolve

# 计时埋点：stage_timer 在 scripts/ 下，而本脚本的 sys.path[0] 是 scripts/showcv/，
# 所以要往上补一层。导入失败一律退化成不计时，绝不影响导出。
try:
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
    import stage_timer
except ImportError:                                          # pragma: no cover
    stage_timer = None

# main() 解析出 --run-dir 后写进这里，供 __main__ 的计时读取。
# 之所以用模块级变量而不是把 main() 整体包进 with：main() 里那个大 try/finally
# 负责标签页清理，为了加计时给它做整体缩进改动，风险远大于收益。
_RUN_DIR = None

use_utf8_output()

# 落在 assets/ 下：导出的是个人简历，而 assets/* 已被 .gitignore 覆盖
DEFAULT_OUT = SKILL_ROOT / 'assets' / 'showcv_exports'

# 页面文案，用来判断状态机走到哪一步了（mO 组件的 running/done/error 三态）
RUNNING = '正在生成图片'
DONE_PREFIX = '已下载 '
# done 和 error 都会渲染这个按钮，所以它只用来区分「已结束」和「还在跑」
FINISHED_BUTTON = '重新生成'


def build_url(base: str, ids: list[str], all_: bool, mode: str, scale: int) -> str:
    """按前端解析器 qD 的规则拼直链。

    `all=1` 和重复的 `id=` 是互斥写法（前端 `all` 优先），这里只发其中一种，
    免得 URL 里出现自相矛盾的意图。
    """
    params: list[tuple[str, str]] = []
    if all_:
        params.append(('all', '1'))
    else:
        params.extend(('id', value) for value in ids)
    params.append(('mode', mode))
    params.append(('scale', str(scale)))
    # base 可能带路径，urljoin 才能正确处理 http://host:port 和 http://host:port/ 两种写法
    return urljoin(base if base.endswith('/') else base + '/', 'export') + '?' + urlencode(params)


def page_text(tab) -> str:
    try:
        return tab.run_js('return document.body.innerText') or ''
    except Exception:
        return ''


def wait_export(tab, timeout: float) -> tuple[str, str]:
    """等页面自己给出结论，返回 (状态, 文案)。

    状态是 'done' / 'error' / 'timeout'。**不看下载事件而先看页面**：图片是在页面里
    渲染的（等字体 ready、等分页稳定），耗时全在下载开始之前，页面文案是唯一能
    区分「还在渲染」和「渲染失败」的信号。
    """
    deadline = time.monotonic() + timeout
    last_progress = ''

    while time.monotonic() < deadline:
        text = page_text(tab)
        if DONE_PREFIX in text:
            after = text.split(DONE_PREFIX, 1)[1]
            return 'done', after.splitlines()[0].strip()
        if FINISHED_BUTTON in text:
            # 结束了却没有「已下载」，只剩 error 一种可能；首行就是 error 的消息
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            return 'error', lines[0] if lines else '（页面没给出错误消息）'
        if RUNNING in text:
            progress = next(
                (line.strip() for line in text.splitlines() if RUNNING in line), ''
            )
            if progress and progress != last_progress:
                print(f'  {progress}')
                last_progress = progress
        time.sleep(0.4)

    return 'timeout', last_progress


def snapshot(directory: Path) -> set[Path]:
    return set(directory.glob('*')) if directory.is_dir() else set()


def main() -> None:
    parser = argparse.ArgumentParser(description='用 /export 直链把 ShowCV 简历导成图片')
    # 刻意不给默认值，理由同 import_md.py：端口决定读的是哪个 origin
    parser.add_argument('--url', required=True, help='ShowCV 地址，从 SHOWCV_READY 那行读')
    parser.add_argument('--id', action='append', default=[], metavar='ID',
                        help='简历 id，可重复；也支持逗号分隔（和前端一致）')
    parser.add_argument('--name', action='append', default=[], metavar='NAME',
                        help='简历名，可重复；不拆逗号（简历名里可能就有逗号）')
    parser.add_argument('--all', action='store_true', help='导出该 origin 上的全部简历')
    parser.add_argument('--mode', choices=('paginated', 'flat'), default='paginated',
                        help='paginated=每页一张 A4（默认），flat=一张长图')
    parser.add_argument('--scale', type=int, choices=(1, 2, 3), default=2,
                        help='像素倍率，只能是 1/2/3，默认 2')
    parser.add_argument('--out', default=str(DEFAULT_OUT), help=f'下载目录，默认 {DEFAULT_OUT}')
    parser.add_argument('--dry-run', action='store_true', help='只解析目标并打印直链，不导出')
    parser.add_argument('--timeout', type=float, default=180, help='页面出图等待上限（秒），默认 180')
    parser.add_argument('--download-timeout', type=float, default=60,
                        help='下载落盘等待上限（秒），默认 60')
    parser.add_argument('--keep-page', action='store_true', help='导完保留标签页，便于手动重试')
    parser.add_argument('--browser', help='浏览器可执行文件，默认自动探测')
    parser.add_argument('--headless', action='store_true', help='无头模式')
    parser.add_argument('--run-dir', dest='run_dir', default=None,
                        help='运行目录（assets/<时间戳>/）。传了就把本次导出耗时写进该目录的 '
                             'run_timings.jsonl，供 stage_timer.py report 排行；不传则不计时')
    args = parser.parse_args()

    global _RUN_DIR
    _RUN_DIR = args.run_dir

    ids = [part.strip() for value in args.id for part in value.split(',') if part.strip()]
    names = [value for value in args.name if value.strip()]
    if args.all and (ids or names):
        raise SystemExit('--all 和 --id/--name 只能给一个：前端 all 优先，混着写等于有一半参数是废的')

    out_dir = Path(args.out).resolve()

    browser = connect(args.browser, args.headless)
    print(f'实际 profile：{real_profile(browser)}')

    # 读用临时标签页：导出是只读操作，不该打扰用户当前编辑的那一页
    tab = open_app(browser, args.url, new_tab=True)
    close_tab = not args.keep_page

    def fail(message: str) -> SystemExit:
        """导航之后的失败一律保留标签页：出图失败时页面上就有「重新生成」按钮，
        关掉它等于把唯一的现场证据和唯一的重试入口一起删了。"""
        nonlocal close_tab
        close_tab = False
        return SystemExit(message)

    try:
        resumes, current_id = read_resumes(tab)
        if not resumes:
            raise SystemExit(f'{args.url} 上没有任何简历，没东西可导')

        if args.all:
            targets = resumes
        elif ids or names:
            targets, missing = resolve(resumes, ids, names)
            if missing:
                # 前端对找不到的 id 是「静默忽略后照常导剩下的」，这里选择先报错：
                # 拼错一个名字却拿到一份不完整的导出，比直接失败更难发现
                raise SystemExit(
                    '以下目标在这个 origin 上不存在：' + '、'.join(missing) + '\n'
                    f'现有 {len(resumes)} 份：\n' + format_list(resumes, current_id)
                )
        else:
            # 和前端 pO 的兜底一致：没给 id 就导当前那份（导出猜错没代价）
            targets = [item for item in resumes if item['id'] == current_id] or resumes[:1]
            print('未指定简历，按前端规则导出「当前简历」')

        print(f'将导出 {len(targets)} 份（mode={args.mode} scale={args.scale}）：')
        print(format_list(targets, current_id))

        url = build_url(args.url, [item['id'] for item in targets], args.all, args.mode, args.scale)
        print(f'直链：{url}')

        if args.dry_run:
            print('--dry-run：到此为止，没有导出')
            return

        out_dir.mkdir(parents=True, exist_ok=True)
        before = snapshot(out_dir)

        # 浏览器级和标签页级都要设：Chrome 先把文件写到浏览器级目录（allowAndName 给的
        # 是 guid 临时名），DrissionPage 再按标签页级设置改名归位。两者一致就不会留下
        # 一半在 cwd、一半在目标目录的残局
        browser.set.download_path(str(out_dir))
        tab.set.download_path(str(out_dir))
        tab.set.when_download_file_exists('rename')

        tab.get(url)
        state, message = wait_export(tab, args.timeout)

        if state == 'error':
            raise fail(f'页面报错：{message}')
        if state == 'timeout':
            raise fail(
                f'等了 {args.timeout:.0f} 秒页面还没出图（最后进度：{message or "无"}）。\n'
                '简历多或 scale=3 时正常会更久，可加大 --timeout；标签页保留着可以自己看。'
            )

        print(f'页面报告已下载：{message}')

        if not tab.wait.downloads_done(timeout=args.download_timeout, cancel_if_timeout=False):
            raise fail(
                f'页面说下载了，但 {args.download_timeout:.0f} 秒内没落盘完。\n'
                f'去 {out_dir} 看看，或加大 --download-timeout。'
            )

        # 独立于页面自述做一次磁盘对账：页面只知道自己调了下载，落没落盘它并不知道
        created = sorted(snapshot(out_dir) - before, key=lambda item: item.name)
        if not created:
            raise fail(
                f'{out_dir} 里没有新文件 —— 下载被浏览器拦了，或落到了别的目录。\n'
                f'确认一下实际 profile 的下载设置（见上方 profile 行）。'
            )

        print(f'已落盘 {len(created)} 个文件，目录 {out_dir}：')
        for item in created:
            size = item.stat().st_size if item.is_file() else 0
            print(f'  {item.name}  {size / 1024:.1f}KB')
    finally:
        if close_tab:
            tab.close()


if __name__ == '__main__':
    _started = time.monotonic()
    _status = 'ok'
    try:
        main()
    except BaseException:
        # SystemExit 也算失败：这个脚本用 raise SystemExit(msg) 报所有导出错误，
        # 只捕 Exception 会把「没找到简历」「出图超时」这些真失败记成 ok。
        _status = 'error'
        raise
    finally:
        if _RUN_DIR and stage_timer is not None:
            stage_timer.span(_RUN_DIR, 'showcv_export',
                             time.monotonic() - _started, status=_status)
