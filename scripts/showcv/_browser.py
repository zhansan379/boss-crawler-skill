#!/usr/bin/env python3
"""scripts/showcv/ 下各脚本共用的浏览器连接逻辑。

刻意不用 DrissionPage 的 `auto_port()`：它每次都配临时 profile 并在退出时删掉，
而简历全在 localStorage 里，那样每次打开都是空的。固定端口 + 固定 profile
才能让数据留存，顺带带来幂等（端口上已有实例时直接接管，不重复开窗）。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from DrissionPage import Chromium, ChromiumOptions

# scripts/showcv/_browser.py → 上溯三级到 skill 根
SKILL_ROOT = Path(__file__).resolve().parents[2]

# 本脚本以 `python scripts/showcv/xxx.py` 运行，sys.path[0] 是自己所在目录；
# browser_finder 在 scripts/ 根，需手动把上一级加进来（和 stage_timer 同款做法）。
_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from browser_finder import find_chrome_browser

# 放在 assets/ 下有两个原因：首启后约 28MB（Chrome 自己的缓存），
# 而 assets/* 已被 .gitignore 覆盖；且和 assets/chrome_user_data/（BOSS 登录态）
# 刻意分开 —— 简历编辑器不该跑在登录了 zhipin.com 的那个浏览器里。
PROFILE_DIR = SKILL_ROOT / 'assets' / 'showcv_profile'

# 9222 是通用默认值，也是 boss_crawler 在用的，撞上会互相接管窗口
DEBUG_PORT = 9333


def use_utf8_output() -> None:
    """Windows 上 Python 默认按控制台代码页（cp936）写 stdout，中文提示会变乱码。"""
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding='utf-8')


def find_browser() -> str:
    """定位浏览器可执行文件；自动探测全落空时给出明确的指定入口。"""
    path = find_chrome_browser()
    if path:
        return path
    raise SystemExit(
        '找不到 Chrome 或 Edge。用 --browser <exe> 显式指定，'
        '或设环境变量 CHROME_PATH / BROWSER_PATH。'
    )


def connect(browser: str | None = None, headless: bool = False) -> Chromium:
    options = ChromiumOptions()
    options.set_browser_path(browser or find_browser())
    options.set_user_data_path(str(PROFILE_DIR))
    options.set_local_port(DEBUG_PORT)
    if headless:
        options.headless(True)
    try:
        return Chromium(options)
    except Exception as error:
        raise SystemExit(f'浏览器启动失败：{error}') from error


def open_app(browser: Chromium, url: str, new_tab: bool = False):
    """打开 url 并确认 ShowCV 真的加载了，然后返回该标签页。

    这个校验不能省：服务没起来时 Chrome 会停在错误页，而错误页的 origin
    不是目标 origin —— 此时读 localStorage 拿到的是空的，会让人误以为数据丢了；
    写 localStorage 则会落到错误的 storageKey 上。

    new_tab=True 时新开一个标签页（读操作用，不打扰用户当前窗口）；
    写操作应当用主标签页，理由见 storage.py 里的说明。
    """
    tab = browser.new_tab() if new_tab else browser.latest_tab
    try:
        tab.get(url)
        title = tab.title or ''
        if 'ShowCV' not in title:
            raise SystemExit(
                f'{url} 上没能加载 ShowCV（title={title!r}）——服务可能没起来。\n'
                '继续操作会读写到错误的 origin，已中止。'
            )
    except BaseException:
        if new_tab:
            tab.close()  # 别留下一个停在错误页的孤儿标签
        raise
    return tab


def real_profile(browser: Chromium) -> str:
    """报告**实际**在驱动的浏览器 profile，而不是我们配置的那个。

    这一步不能省：debug 端口上已有实例时 DrissionPage 会直接接管它，
    `set_user_data_path` 被静默丢弃，于是 launch.py 打印的 profile 是配置值而非
    实际值。导入和删除都是写操作，
    profile 错了就是在动别人的抽屉。

    走「pid → 命令行」而不是 CDP 的 `Browser.getBrowserCommandLine`：后者要求浏览器
    带 `--enable-automation` 启动，而那个 flag 只有我们自己启动时加得上 —— 恰好在
    「被接管」这个真正危险的场景里失效。pid 来自 CDP SystemInfo.getProcessInfo，
    不管浏览器是谁启动的都是真的。
    """
    pid = browser.process_id
    if not pid:
        return '未能确认（拿不到浏览器进程 pid）'

    # Windows 专用；这个技能的浏览器探测路径本来就是 Windows 的（见 browser_finder）
    try:
        line = subprocess.run(
            ['powershell', '-NoProfile', '-Command',
             f'(Get-CimInstance Win32_Process -Filter "ProcessId = {pid}").CommandLine'],
            capture_output=True, text=True, timeout=15,
        ).stdout
        for arg in line.split('--'):
            if arg.startswith('user-data-dir='):
                return arg[len('user-data-dir='):].strip().strip('"')
    except Exception:
        pass
    return f'未能确认（pid={pid}，可用它反查 --user-data-dir）'
