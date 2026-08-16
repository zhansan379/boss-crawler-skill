#!/usr/bin/env python3
"""跨脚本共享的浏览器可执行文件定位。

没有哪种自动探测是百分百可靠的——便携版、绿化版、非默认目录装法都会落空。
所以这里按「可靠度从高到低」分层，命中即停；全部落空返回 None，由调用方决定
怎么提示（正确做法是让用户 --browser 或环境变量显式指定，那才是真正的保证）。

被 scripts/showcv/_browser.py 和 scripts/resume_matcher/auto_apply.py 共用，
避免两处各写一份还互相对不上。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

# 调用方可注入的「显式指定」环境变量，优先级仅低于 call 参数里的 explicit
ENV_VAR_NAMES = ('CHROME_PATH', 'BROWSER_PATH')

# shutil 探测/path 拼名用的可执行名
_PROCESS_NAMES = ('chrome', 'msedge', 'chromium')


def _is_exe(p: str | None) -> bool:
    return bool(p) and Path(p).is_file()


def _from_env() -> str | None:
    for name in ENV_VAR_NAMES:
        if _is_exe(os.environ.get(name)):
            return os.environ[name]
    return None


def _from_path() -> str | None:
    """查 PATH。通常浏览器不在 PATH 上，只能算运气，故放在 registry 前面碰一碰。"""
    for base in (*_PROCESS_NAMES, *(_ + '.exe' for _ in _PROCESS_NAMES)):
        p = shutil.which(base)
        if _is_exe(p):
            return p
    return None


def _from_registry() -> str | None:
    """读 Windows 注册表 App Paths，能反映非默认盘/自定义安装的真实位置。"""
    try:
        import winreg
    except ImportError:
        return None
    for app in ('chrome.exe', 'msedge.exe'):
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            key = rf'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{app}'
            try:
                with winreg.OpenKey(hive, key) as k:
                    val, _ = winreg.QueryValueEx(k, None)
            except OSError:
                continue
            if _is_exe(val):
                return val
    return None


def _from_composed() -> str | None:
    """用 %ProgramFiles% / %LOCALAPPDATA% 环境变量拼常见装法，不写死盘符/用户名。"""
    roots = (
        os.environ.get('ProgramFiles'),
        os.environ.get('ProgramFiles(x86)'),
        os.environ.get('LOCALAPPDATA'),
    )
    for root in roots:
        if not root:
            continue
        for app_dir in ('Google\\Chrome\\Application', 'Microsoft\\Edge\\Application'):
            exe = 'chrome.exe' if 'Chrome' in app_dir else 'msedge.exe'
            p = os.path.join(root, app_dir, exe)
            if _is_exe(p):
                return p
    return None


def find_chrome_browser(explicit: str | None = None) -> str | None:
    """分层定位浏览器可执行文件；命中即停，全落空返回 None。

    explicit 取自知名的 --browser 参数，优先级最高（用户的显式指定最可靠）。
    之后依次：环境变量 → PATH → 注册表 → 环境变量拼路径。
    """
    if _is_exe(explicit):
        return explicit
    for probe in (_from_env, _from_path, _from_registry, _from_composed):
        got = probe()
        if got:
            return got
    return None