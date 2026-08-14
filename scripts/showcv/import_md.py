#!/usr/bin/env python3
"""把磁盘上的 .md 批量灌进 ShowCV 编辑器的简历列表。

**为什么不点「导入 md」按钮**：那个按钮的 onClick 只是 `m.current?.click()`，
会弹出系统文件对话框 —— 原生窗口不在 CDP 的可控范围内。真正的入口是它背后那个
`<input type="file" accept=".md,.markdown" multiple class="hidden">`：
DrissionPage 对 file input 走 `DOM.setFileInputFiles`（见
`_elements/chromium_element.py:678`），按 backendNodeId 定位，**不做可见性检查**，
所以 `hidden` 的 input 可以直接喂路径，而且 CDP 这条命令会触发 change 事件，
React 的 onChange 因此照常执行。

前端的硬约束（读自 app/assets/index-*.js，都落在下面的常量里）：单批最多 50 个，
超了整批拒绝；5MB 是 localStorage **总配额**而不是单文件上限；同名简历会被自动
改成 `名字 (2)`，导入是纯追加，不覆盖既有简历。

用法：

    # 导入一个目录里的所有 .md
    python scripts/showcv/import_md.py --url http://127.0.0.1:3090 resumes/

    # 先看看会导入什么，不碰浏览器
    python scripts/showcv/import_md.py --url http://127.0.0.1:3090 a.md b.md --dry-run
"""

from __future__ import annotations

import argparse
import re
import time
from collections import Counter
from pathlib import Path

from _browser import connect, open_app, real_profile, use_utf8_output

# 「读 localStorage 里的简历清单」三个脚本共用，见 _resumes.py
from _resumes import read_resumes

use_utf8_output()

# 以下都抄自前端，改动前先确认 app/ 的构建有没有变
EXTENSIONS = ('.md', '.markdown')
# importFiles 里 `if (i.length > 50)` 是**整批拒绝**而不是截断，所以必须自己分批
BATCH_LIMIT = 50
# 前端拿这个值同时当单文件上限和 localStorage 总配额，超了会弹「本地存储空间不足」
MAX_BYTES = 5 * 1024 * 1024
# 撞名时前端改成 `${名字} (${n})`，n 从 2 起（打包产物里的 tO 函数）
SUFFIXED = re.compile(r'^(?P<stem>.+) \((?P<n>\d+)\)$')

# 全站有两个 file input，另一个是 accept="image/*" 的头像上传，必须按 accept 区分
FILE_INPUT = 'x://input[@type="file" and contains(@accept, ".md")]'


def collect(paths: list[str], recursive: bool) -> tuple[list[Path], list[tuple[Path, str]]]:
    """把位置参数展开成 .md 文件列表，同时收集被排除的项和原因。

    目录里的非 .md 不算「跳过」——用户没点名要它们；但显式写在命令行上的非 .md
    会明确报出来，因为前端只会静默忽略，不说话最容易让人以为全导进去了。
    """
    found: list[Path] = []
    skipped: list[tuple[Path, str]] = []

    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            for child in sorted(path.glob('**/*' if recursive else '*')):
                if child.is_file() and child.suffix.lower() in EXTENSIONS:
                    found.append(child)
        elif path.is_file():
            if path.suffix.lower() in EXTENSIONS:
                found.append(path)
            else:
                skipped.append((path, '不是 .md/.markdown，前端会静默忽略'))
        else:
            skipped.append((path, '路径不存在'))

    # 同一个文件可能既被目录扫到、又被显式点名，去重后才好对账
    unique: list[Path] = []
    seen = set()
    for item in found:
        resolved = item.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(item)

    kept: list[Path] = []
    for item in unique:
        size = item.stat().st_size
        if size > MAX_BYTES:
            skipped.append((item, f'{size / 1024 / 1024:.1f}MB，超过 5MB'))
        else:
            kept.append(item)

    return kept, skipped


def resume_names(tab) -> list[str]:
    """读当前 origin 已落盘的简历名。

    空 origin 返回空列表；内容存在却解析不了时会报错（见 _resumes.read_state）——
    那种情况下把它当成 0 份，后面所有对账都是错的。
    """
    resumes, _ = read_resumes(tab)
    return [item['name'] for item in resumes]


def match_files(added: list[str], stems: list[str]) -> tuple[list[tuple[str, str]], list[str]]:
    """把新增的简历名对应回源文件（前端用「文件名去扩展名」当简历名）。

    返回 `([(stem, 实际落地名), ...], 对应不上的名字)`。

    **为什么要按名字对账而不是数点数**：origin 全新时页面自带一份默认简历，它在
    首次挂载时还没落盘，基线因此读成 0，导入触发 persist 后才一起出现。只比总数的话，
    这份默认简历会替一个失败的文件把数字凑齐，让部分失败伪装成全部成功。

    先吃精确同名再吃带后缀的，否则文件名本身带 ' (2)' 时会归错组。
    """
    pending = Counter(stems)
    pairs: list[tuple[str, str]] = []
    leftover: list[str] = []

    for name in added:
        if pending[name] > 0:
            pending[name] -= 1
            pairs.append((name, name))
        else:
            leftover.append(name)

    unmatched: list[str] = []
    for name in leftover:
        matched = SUFFIXED.match(name)
        stem = matched.group('stem') if matched else name
        if stem != name and pending[stem] > 0:
            pending[stem] -= 1
            pairs.append((stem, name))
        else:
            unmatched.append(name)

    return pairs, unmatched


def wait_batch(tab, before: list[str], stems: list[str], timeout: float):
    """等这一批文件真的落盘，返回 (对应关系, 对应不上的名字)。

    前端用 FileReader 异步读文件，喂完 input 立刻返回**不代表**数据落盘，
    所以只认 localStorage 里的实际内容，不认「命令已发出」。
    """
    deadline = time.monotonic() + timeout
    while True:
        added = list((Counter(resume_names(tab)) - Counter(before)).elements())
        pairs, unmatched = match_files(added, stems)
        if len(pairs) >= len(stems) or time.monotonic() >= deadline:
            return pairs, unmatched
        time.sleep(0.3)


def read_toasts(tab) -> list[str]:
    """尽力抓 sonner 的 toast 文本，用来解释「为什么没导进去」。

    toast 几秒后自动消失，抓不到是常态 —— 所以任何异常都咽掉：诊断信息缺失
    不该让导入本身失败。
    """
    try:
        return [t.text.strip() for t in tab.eles('css:[data-sonner-toast]') if t.text.strip()]
    except Exception:
        return []


def main() -> None:
    parser = argparse.ArgumentParser(description='批量把 .md 导入 ShowCV 简历编辑器')
    parser.add_argument('paths', nargs='+', help='.md 文件，或包含 .md 的目录')
    # 刻意不给默认值：SKILL.md 要求从 serve.py 的 SHOWCV_READY 读实际地址，
    # 设默认值等于鼓励假设 3090，而端口决定了写进哪个 origin
    parser.add_argument('--url', required=True, help='ShowCV 地址，从 SHOWCV_READY 那行读')
    parser.add_argument('-r', '--recursive', action='store_true', help='目录递归扫子目录')
    parser.add_argument('--dry-run', action='store_true', help='只列出将要导入的文件，不连浏览器')
    parser.add_argument('--timeout', type=float, default=30, help='单批落盘等待上限（秒），默认 30')
    parser.add_argument('--browser', help='浏览器可执行文件，默认自动探测')
    parser.add_argument('--headless', action='store_true', help='无头模式')
    args = parser.parse_args()

    files, skipped = collect(args.paths, args.recursive)
    for path, reason in skipped:
        print(f'跳过 {path}：{reason}')
    if not files:
        raise SystemExit('没有可导入的 .md 文件')

    batches = (len(files) + BATCH_LIMIT - 1) // BATCH_LIMIT
    print(f'待导入 {len(files)} 个文件' + (f'，分 {batches} 批（前端单批上限 50）' if batches > 1 else ''))

    if args.dry_run:
        for item in files:
            print(f'  {item}')
        print('--dry-run，未连接浏览器')
        return

    browser = connect(args.browser, args.headless)
    # 写操作走主标签页而非临时标签页：若另有标签页持有旧 zustand state，
    # 它下一次 mutation 会把整个 state 写回 localStorage，覆盖掉刚导入的数据。
    # 同理见 storage.py 的 write_origin。
    tab = open_app(browser, args.url)
    print(f'目标 origin：{tab.url}')
    print(f'实际 profile：{real_profile(browser)}')
    print(f'导入前：{len(resume_names(tab))} 份简历（已落盘的）')

    file_input = tab.ele(FILE_INPUT, timeout=10)
    if not file_input:
        raise SystemExit(
            '页面里找不到 .md 文件输入框 —— app/ 的构建可能不含导入功能。\n'
            '（侧栏收起不影响：它靠 width:0 隐藏，子树始终挂载）'
        )

    done: list[tuple[str, str]] = []
    extras: list[str] = []
    for index in range(0, len(files), BATCH_LIMIT):
        batch = files[index:index + BATCH_LIMIT]
        stems = [item.stem.strip() for item in batch]
        before = resume_names(tab)

        # input() 内部会转绝对路径，这里显式转一次，报错时日志更好读
        file_input.input([str(item.resolve()) for item in batch])

        pairs, unmatched = wait_batch(tab, before, stems, args.timeout)
        if len(pairs) < len(batch):
            landed = {name for _, name in pairs}
            missing = [s for s in stems if s not in landed and f'{s} (' not in ''.join(landed)]
            toasts = read_toasts(tab)
            raise SystemExit(
                f'第 {index // BATCH_LIMIT + 1} 批只落盘 {len(pairs)}/{len(batch)} 份，已中止。\n'
                f'没落盘的：{", ".join(missing) or "无法判定"}'
                + (f'\n页面提示：{" | ".join(toasts)}' if toasts else '')
                + (f'\n此前已导入的 {len(done)} 份仍在页面里，不会回滚。' if done else '')
            )

        done.extend(pairs)
        extras.extend(unmatched)
        if batches > 1:
            print(f'  第 {index // BATCH_LIMIT + 1}/{batches} 批完成，累计 {len(done)}/{len(files)}')

    print(f'已导入 {len(done)} 份，当前 origin 共 {len(resume_names(tab))} 份')
    renamed = [(stem, name) for stem, name in done if stem != name]
    for stem, name in done:
        print(f'  {name}' if stem == name else f'  {name}（源文件名 {stem}）')

    if renamed:
        print('注意：上面带括号后缀的是前端改的名，说明撞了同名 —— 原有那份没被覆盖，现在是两份。')
    if extras:
        # 最常见的就是页面自带的默认简历：它在首次 persist 时才写进 localStorage
        print(f'另外出现了 {len(extras)} 份不是这次导入的简历：{", ".join(extras)}')
        print('（大概率是页面自带、这次才随 persist 一起落盘的，不是本次导入的文件）')


if __name__ == '__main__':
    main()
