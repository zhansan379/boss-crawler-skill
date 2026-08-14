#!/usr/bin/env python3
"""在 origin 之间搬 ShowCV 的简历数据，以及和磁盘文件互导。

**为什么需要这个**：localStorage 按 origin（scheme://host:port）分区，这是浏览器
存储模型的硬设计，没有任何启动参数能让两个 origin 共享同一份数据
（`--disable-web-security` 只影响跨源请求，不动 storage 分区）。
所以「不同端口复用同一份简历」只能靠显式搬运。

导出的文件里 `value.state.resumes[i].content` 是 **Markdown 原文**，可直接当
resume_text 喂给 Stage 3 解析，不需要 PDF/Word 解析 —— 见
references/resume-editor.md。

用法：

    # 导出 3090 的简历到磁盘
    python scripts/showcv/storage.py dump --url http://127.0.0.1:3090

    # 把磁盘上的简历灌进 3091
    python scripts/showcv/storage.py load --url http://127.0.0.1:3091

    # 一步从 3090 搬到 3091
    python scripts/showcv/storage.py move --from http://127.0.0.1:3090 --to http://127.0.0.1:3091
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from _browser import SKILL_ROOT, connect, open_app, use_utf8_output

use_utf8_output()

# zustand persist 的键名，见 ShowCV 仓库 src/store/resumeStore.ts
KEY = 'showcv-resume'
# 落在 assets/ 下：简历是个人数据，而 assets/* 已被 .gitignore 覆盖
DEFAULT_FILE = SKILL_ROOT / 'assets' / 'showcv-resume.json'


def describe(value) -> str:
    """从 zustand persist 的结构里数出简历份数，用于让用户确认搬的是对的东西。"""
    try:
        resumes = value['state']['resumes']
        names = [item.get('name', '?') for item in resumes]
        return f'{len(resumes)} 份：{", ".join(names[:5])}' + (' …' if len(names) > 5 else '')
    except (TypeError, KeyError, AttributeError):
        return '结构无法识别'


def read_origin(browser, url: str) -> str | None:
    """读某个 origin 的原始字符串，没有数据时返回 None。

    读用临时标签页，读完就关，不打扰用户当前窗口。
    """
    tab = open_app(browser, url, new_tab=True)
    try:
        return tab.local_storage(KEY)
    finally:
        tab.close()


def write_origin(browser, url: str, raw: str, force: bool) -> None:
    """把数据写进某个 origin。

    刻意用**主标签页**而不是临时标签页：如果目标 origin 上还开着一个持有旧状态的
    标签页，zustand persist 会在下一次 mutation 时把整个 state 写回 localStorage，
    直接覆盖掉我们刚注入的数据。把主标签页导航到目标并 refresh，可以保证
    页面内存里的状态就是刚注入的那份，不存在会反向清掉它的旧标签页。
    """
    tab = open_app(browser, url)

    existing = tab.local_storage(KEY)
    if existing and not force:
        raise SystemExit(
            f'{url} 上已有简历（{describe(json.loads(existing))}），拒绝覆盖。\n'
            '确认要覆盖请加 --force；想保险就先对它跑一次 dump。'
        )

    tab.set.local_storage(KEY, raw)
    # zustand persist 只在挂载时读一次 localStorage，不刷新页面看不到新数据
    tab.refresh()


def load_file(path: Path) -> str:
    if not path.is_file():
        raise SystemExit(f'{path} 不存在，先跑一次 dump')
    payload = json.loads(path.read_text(encoding='utf-8'))
    value = payload.get('value')
    if value is None:
        raise SystemExit(f'{path} 里没有 value 字段，文件可能损坏')
    # 存的是解析后的对象（便于人读和 diff），灌回去时要还原成 zustand 期望的字符串
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def cmd_dump(args, browser) -> None:
    raw = read_origin(browser, args.url)
    if not raw:
        raise SystemExit(f'{args.url} 上没有 {KEY} —— 这个 origin 确实是空的')

    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        value = raw  # 存不下来也别丢，原样保留

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {'key': KEY, 'origin': args.url, 'value': value},
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )
    print(f'已导出 {describe(value)}')
    print(f'  {args.url} → {out}')


def cmd_load(args, browser) -> None:
    raw = load_file(Path(args.file))
    write_origin(browser, args.url, raw, args.force)
    print(f'已灌入 {describe(json.loads(raw))}')
    print(f'  {args.file} → {args.url}')


def cmd_move(args, browser) -> None:
    raw = read_origin(browser, args.src)
    if not raw:
        raise SystemExit(f'{args.src} 上没有 {KEY} —— 源 origin 是空的，没东西可搬')

    write_origin(browser, args.to, raw, args.force)
    print(f'已搬运 {describe(json.loads(raw))}')
    print(f'  {args.src} → {args.to}')
    print('源 origin 的数据仍在原处（本操作是复制，不是剪切）')


def main() -> None:
    parser = argparse.ArgumentParser(description='ShowCV 简历数据搬运')
    parser.add_argument('--browser', help='浏览器可执行文件，默认自动探测')
    parser.add_argument('--headless', action='store_true', help='无头模式')
    parser.add_argument('--force', action='store_true', help='目标已有数据时也覆盖')
    subparsers = parser.add_subparsers(dest='command', required=True)

    dump = subparsers.add_parser('dump', help='origin → 磁盘文件')
    dump.add_argument('--url', required=True)
    dump.add_argument('--out', default=str(DEFAULT_FILE))

    load = subparsers.add_parser('load', help='磁盘文件 → origin')
    load.add_argument('--url', required=True)
    load.add_argument('--file', default=str(DEFAULT_FILE))

    move = subparsers.add_parser('move', help='origin → origin')
    # --from 是 Python 关键字，argparse 的属性名必须换成 src
    move.add_argument('--from', dest='src', required=True)
    move.add_argument('--to', required=True)

    args = parser.parse_args()
    handlers = {'dump': cmd_dump, 'load': cmd_load, 'move': cmd_move}
    browser = connect(args.browser, args.headless)
    handlers[args.command](args, browser)


if __name__ == '__main__':
    main()
