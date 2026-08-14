#!/usr/bin/env python3
"""通过 `/delete` 直链删简历，删之前先备份、再让页面把要删的名单摆出来核对。

这个脚本的每一处设计都来自同一句话：**导出猜错没代价，删除有**。前端自己也是这么
设计的 —— `/export` 在没给 id 时会回落到当前简历，`/delete` 则宁可报错也不猜
（打包产物里的 `hO`：`all ? resumes : ids.length===0 ? [] : filter`）。脚本在这之上
再加三层，因为它比人手点更容易一次删一堆：

1. **删之前自动备份**：页面上的「撤销」只在内存里（`deleteResumes` 返回的快照存在
   一个 useRef 里），刷新即失效；而 localStorage 是这些简历的唯一副本，删错了没有
   第二个地方能捞。备份是一次 storage.py 格式的 dump，可以直接 `storage.py load` 灌回去。
2. **走确认页而不是 `confirm=1`**：直链带 `confirm=1` 时页面一挂载就删，谁也没机会
   看一眼名单。不带这个参数时页面会先列出**它自己认为**要删的那几份，脚本把这份
   名单和本地解析结果逐个比对，不一致就直接不点按钮 —— 一份都不会删。
3. **必须显式 `--yes`**：只给 `--dry-run` 或什么都不给，都只打印计划。

用法：

    # 先看要删什么（不连浏览器改任何东西）
    python scripts/showcv/delete_resumes.py --url http://127.0.0.1:3090 --name 张三-后端 --dry-run

    # 真删
    python scripts/showcv/delete_resumes.py --url http://127.0.0.1:3090 --name 张三-后端 --yes

    # 清空这个 origin（前端会自动补一份空白简历）
    python scripts/showcv/delete_resumes.py --url http://127.0.0.1:3090 --all --yes
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from urllib.parse import urlencode, urljoin

from _browser import SKILL_ROOT, connect, open_app, real_profile, use_utf8_output
from _resumes import format_list, read_resumes, resolve
from storage import KEY

use_utf8_output()

BACKUP_DIR = SKILL_ROOT / 'assets' / 'showcv_backups'

# 页面文案，对应 gO 组件的 confirm/deleting/done/restored/error 五态
CONFIRM_MARK = '即将删除'
DONE_MARK = '已删除'
EMPTIED_MARK = '简历已被清空，已自动新建一份空白简历'
CONFIRM_BUTTON = '确认删除'
# 「一份都没命中」时页面给的两种消息
ERROR_MARKS = ('直链里没有指定', '未找到对应的简历')


def build_url(base: str, ids: list[str], all_: bool) -> str:
    """按前端解析器 sO 的规则拼直链；刻意**不带** confirm，理由见模块开头第 2 条。"""
    params: list[tuple[str, str]] = []
    if all_:
        params.append(('all', '1'))
    else:
        params.extend(('id', value) for value in ids)
    return urljoin(base if base.endswith('/') else base + '/', 'delete') + '?' + urlencode(params)


def backup(tab, url: str) -> Path:
    """把整个 origin 的简历原样存一份，格式和 storage.py dump 一致（可直接 load 回去）。

    存全量而不是只存要删的那几份：恢复路径因此只有一条现成命令，不需要在事故现场
    先手写一个合并脚本。简历是纯文本，全量也就几十 KB。
    """
    raw = tab.local_storage(KEY)
    if not raw:
        raise SystemExit(f'{url} 上没有 {KEY} —— 这个 origin 是空的，没东西可删')

    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        value = raw  # 存不下来也别丢，原样保留

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    path = BACKUP_DIR / f'showcv-resume-{time.strftime("%Y%m%d-%H%M%S")}.json'
    path.write_text(
        json.dumps({'key': KEY, 'origin': url, 'value': value}, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    return path


def page_text(tab) -> str:
    try:
        return tab.run_js('return document.body.innerText') or ''
    except Exception:
        return ''


def wait_text(tab, marks: tuple[str, ...], timeout: float) -> tuple[str | None, str]:
    """等页面文案里出现 marks 之一，返回 (命中的 mark, 完整文案)。"""
    deadline = time.monotonic() + timeout
    text = ''
    while time.monotonic() < deadline:
        text = page_text(tab)
        for mark in marks:
            if mark in text:
                return mark, text
        time.sleep(0.3)
    return None, text


def page_names(tab) -> list[str]:
    """读确认页列出的简历名（gO 用 <ul><li> 渲染 t.map，一条一份）。"""
    try:
        return [item.text.strip() for item in tab.eles('css:ul li') if item.text.strip()]
    except Exception:
        return []


def main() -> None:
    parser = argparse.ArgumentParser(description='用 /delete 直链删除 ShowCV 简历')
    parser.add_argument('--url', required=True, help='ShowCV 地址，从 SHOWCV_READY 那行读')
    parser.add_argument('--id', action='append', default=[], metavar='ID',
                        help='简历 id，可重复；也支持逗号分隔（和前端一致）')
    parser.add_argument('--name', action='append', default=[], metavar='NAME',
                        help='简历名，可重复；不拆逗号（简历名里可能就有逗号）')
    parser.add_argument('--all', action='store_true', help='删除该 origin 上的全部简历')
    parser.add_argument('--yes', action='store_true', help='确认真删；不给就只打印计划')
    parser.add_argument('--dry-run', action='store_true', help='只打印计划（等价于不给 --yes）')
    parser.add_argument('--no-backup', action='store_true', help='跳过自动备份（不建议）')
    parser.add_argument('--return-to-editor', action='store_true',
                        help='删完把标签页导航回编辑器（会让页面上的「撤销」失效）')
    parser.add_argument('--timeout', type=float, default=30, help='等页面响应的上限（秒），默认 30')
    parser.add_argument('--browser', help='浏览器可执行文件，默认自动探测')
    parser.add_argument('--headless', action='store_true', help='无头模式')
    args = parser.parse_args()

    ids = [part.strip() for value in args.id for part in value.split(',') if part.strip()]
    names = [value for value in args.name if value.strip()]
    if args.all and (ids or names):
        raise SystemExit('--all 和 --id/--name 只能给一个，混着写等于有一半参数是废的')
    if not args.all and not ids and not names:
        # 和前端一致：不给目标就报错，绝不回落到「当前简历」
        raise SystemExit('没指定要删哪些简历。给 --id/--name，或者用 --all 清空整个 origin')

    browser = connect(args.browser, args.headless)
    print(f'实际 profile：{real_profile(browser)}')

    # 删除是写操作，必须用**主标签页**：如果这个 origin 上另开着一个持有旧状态的标签页，
    # zustand persist 会在它下一次 mutation 时把整个 state 写回 localStorage，
    # 把刚删掉的简历原地复活。理由同 storage.py 的 write_origin。
    tab = open_app(browser, args.url)

    resumes, current_id = read_resumes(tab)
    if not resumes:
        raise SystemExit(f'{args.url} 上没有任何简历，没东西可删')

    if args.all:
        targets, missing = list(resumes), []
    else:
        targets, missing = resolve(resumes, ids, names)
    if missing:
        # 前端对找不到的 id 只在角落写一行「已忽略」然后照删其余的。这里选择整体中止：
        # 名字打错一个就少删一份，而「以为删了其实没删」要到很久以后才会被发现
        raise SystemExit(
            '以下目标在这个 origin 上不存在：' + '、'.join(missing) + '\n'
            f'一份都没删。现有 {len(resumes)} 份：\n' + format_list(resumes, current_id)
        )

    print(f'{args.url} 上共 {len(resumes)} 份，计划删除 {len(targets)} 份：')
    print(format_list(targets, current_id))
    if len(targets) == len(resumes):
        print('这会删光所有简历，前端随后会自动补一份空白简历')

    if args.dry_run or not args.yes:
        print('未执行删除。确认无误后加 --yes 再跑一次。')
        return

    if args.no_backup:
        print('已跳过备份（--no-backup）：删掉的内容将无法从磁盘恢复')
    else:
        path = backup(tab, args.url)
        print(f'已备份：{path}')
        # --force 是 storage.py 的顶层参数，argparse 要求它出现在子命令**之前**
        print(f'  恢复：python scripts/showcv/storage.py --force load '
              f'--url {args.url} --file "{path}"')

    url = build_url(args.url, [item['id'] for item in targets], args.all)
    print(f'直链：{url}')
    tab.get(url)

    mark, text = wait_text(tab, (CONFIRM_MARK,) + ERROR_MARKS, args.timeout)
    if mark is None:
        raise SystemExit(f'{args.timeout:.0f} 秒内确认页没出现，一份都没删。页面现在是：\n{text[:300]}')
    if mark in ERROR_MARKS:
        raise SystemExit(f'页面说它一份都没匹配上，未执行删除：{mark}…')

    # 拿页面自己列出的名单和本地解析结果对账。两边都从同一份 localStorage 来，
    # 对不上就说明中途有别的东西改了数据（另一个标签页、另一个脚本），
    # 这种情况下继续点「确认删除」删掉的可能不是我们刚给用户看的那几份
    listed = page_names(tab)
    expected = [item['name'] for item in targets]
    if sorted(listed) != sorted(expected):
        raise SystemExit(
            '页面列出的名单和本地解析不一致，已中止（没点确认，一份都没删）：\n'
            f'  页面：{listed}\n  本地：{expected}\n'
            '大概是期间有别的标签页或脚本改了这个 origin 的数据，重跑一次即可。'
        )

    # 用 //button 而不是 'text:确认删除'：按钮里还有个图标 <svg>，而页面上另一个
    # 「返回编辑器」渲染成 <a>，限定 button 就只剩确认按钮一个可能
    button = tab.ele(f'x://button[contains(., "{CONFIRM_BUTTON}")]', timeout=5)
    if not button:
        raise SystemExit('确认页上找不到「确认删除」按钮，已中止（一份都没删）')
    button.click()

    mark, text = wait_text(tab, (DONE_MARK,), args.timeout)
    if mark is None:
        raise SystemExit(
            f'点了确认但 {args.timeout:.0f} 秒内页面没显示结果。**不要重跑**，'
            f'先看页面和 localStorage 现在的状态。页面现在是：\n{text[:300]}'
        )

    # 独立于页面自述做一次对账：页面说的是它的内存状态，我们要的是落盘结果
    remaining, current_id = read_resumes(tab)
    remaining_ids = {item['id'] for item in remaining}
    survived = [item['name'] for item in targets if item['id'] in remaining_ids]
    if survived:
        raise SystemExit(f'页面说删完了，但这些还在 localStorage 里：{survived}')

    print(f'已删除 {len(targets)} 份，剩余 {len(remaining)} 份：')
    print(format_list(remaining, current_id))
    if EMPTIED_MARK in text:
        print(EMPTIED_MARK)

    if args.return_to_editor:
        tab.get(args.url)
        print('已回到编辑器（页面上的「撤销」随之失效，需要恢复请用上面的备份）')
    else:
        print('标签页停在结果页：刷新前还能点页面上的「撤销」当场恢复')


if __name__ == '__main__':
    main()
