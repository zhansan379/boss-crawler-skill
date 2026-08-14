#!/usr/bin/env python3
"""用 DrissionPage 打开一个隔离的 Chromium 指向本地 ShowCV 简历编辑器。

浏览器用独立的 user-data-dir（assets/showcv_profile/），绝不碰用户日常浏览器的
profile，也不碰 boss_crawler 的 assets/chrome_user_data/ —— 否则 DrissionPage
会接管、甚至重启用户正在用的窗口。连接细节见 _browser.py。
"""

from __future__ import annotations

import argparse

from _browser import PROFILE_DIR, connect, open_app, use_utf8_output

use_utf8_output()


def main() -> None:
    parser = argparse.ArgumentParser(description='打开本地 ShowCV 简历编辑器')
    parser.add_argument('url', help='ShowCV 地址，如 http://127.0.0.1:3090')
    parser.add_argument('--browser', help='浏览器可执行文件，默认自动探测 Chrome/Edge')
    parser.add_argument('--headless', action='store_true', help='无头模式')
    parser.add_argument(
        '--close',
        action='store_true',
        help='打开并校验后立刻关掉浏览器（用于冒烟测试，默认保持打开）',
    )
    args = parser.parse_args()

    browser = connect(args.browser, args.headless)
    tab = open_app(browser, args.url)

    print(f'url={tab.url}')
    print(f'title={tab.title}')
    print(f'profile={PROFILE_DIR}')

    if args.close:
        browser.quit()
    else:
        # 不 quit，让浏览器留给用户接着用；DrissionPage 的连接随进程退出自然断开
        print('浏览器已打开，本脚本退出后窗口保留')


if __name__ == '__main__':
    main()


# 无人值守自动化的接口：
# 注入 localStorage 后打开 /export?id=all&mode=paginated&scale=2 即可拿到批量导出的 PNG，
# 该直链在前端挂载前就会自动触发导出。注入/导出数据用 scripts/showcv/storage.py。
# 导出这一半已经实现成 scripts/showcv/export_images.py（连下载落盘的对账一起做了）。
