# -*- coding: utf-8 -*-
"""--ensure-login 的收尾行为测试（命令行路径 vs skill 路径）。

同一条命令要服务两种调用者，而它们需要的收尾正好相反：

  · 命令行：人在键盘前。扫完码必须有人告诉他「按回车我来复检」，复检过了要说清
    会话存哪了、下一条命令是什么。原来这里印的是「登录完成后告诉我：已登录」——
    那是给 skill 路径的 Claude 读的，命令行下没有「我」，人对着一个已经退出的进程
    不知道该干什么。
  · skill：Claude Code 用管道跑这条命令，stdin 不是终端。这时**绝对不能**调 input()
    —— 轻则立刻 EOF，重则把整个会话挂死等一个永不到来的回车。

判据是 isatty()。这个测试把两边都钉住：交互式必须等回车，非交互式必须一次都不问。
外加一条：复检只由回车触发，脚本自己不许轮询重试。

跑法：python tests/test_ensure_login.py
"""

import io
import os
import sys
import json
import shutil
import tempfile
import contextlib

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

for _stream in (sys.stdout, sys.stderr):
    _stream.reconfigure(encoding='utf-8', errors='replace')

from boss_crawler import auth


class FakePage(object):
    """够用的 WebPage 替身：记下访问过的 URL 和有没有正常关闭。"""

    def __init__(self):
        self.urls = []
        self.quit_calls = 0

    def get(self, url):
        self.urls.append(url)

    def quit(self):
        self.quit_calls += 1


def run_ensure_login(logged_in, interactive, answers=(), assets_dir=None):
    """跑一遍 ensure_login()，不碰真浏览器。

    logged_in: 布尔或布尔序列 —— 序列时每次 check_login_status 依次取一个，
               用来表达「第一次没登上、按完回车才登上」。
    返回 (返回值, 打印全文, FakePage, 问了几次 input)
    """
    page = FakePage()
    states = list(logged_in) if isinstance(logged_in, (list, tuple)) else [logged_in]
    asked = []

    def fake_input(prompt=''):
        asked.append(prompt)
        if len(asked) > len(answers):
            raise EOFError
        return answers[len(asked) - 1]

    def fake_check(_page):
        return states.pop(0) if len(states) > 1 else states[0]

    saved = (auth.WebPage, auth.check_login_status, auth._is_interactive,
             auth.time.sleep, auth.ASSETS_DIR, getattr(auth, 'input', None))
    auth.WebPage = lambda chromium_options=None: page
    auth.check_login_status = fake_check
    auth._is_interactive = lambda: interactive
    auth.time.sleep = lambda _s: None
    auth.input = fake_input
    if assets_dir:
        auth.ASSETS_DIR = assets_dir

    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            result = auth.ensure_login()
    finally:
        (auth.WebPage, auth.check_login_status, auth._is_interactive,
         auth.time.sleep, auth.ASSETS_DIR, old_input) = saved
        if old_input is None:
            auth.__dict__.pop('input', None)
        else:
            auth.input = old_input
    return result, buf.getvalue(), page, len(asked)


def build_assets(with_params=False, with_summary=False, space=False):
    """造一个 assets/ 目录：LATEST.txt + 一个运行目录。

    space=True 时目录名里带空格 —— Windows 上（「我的文档」之类）很常见，而没加引号的
    路径会被 shell 拆成两个参数，粘过去就是「找不到运行目录」。
    """
    assets = tempfile.mkdtemp(prefix='ensure login ' if space else 'ensure_login_')
    run_dir = os.path.join(assets, '2026-08-16_09-09-45')
    os.makedirs(run_dir)
    with open(os.path.join(assets, 'LATEST.txt'), 'w', encoding='utf-8') as f:
        f.write('2026-08-16_09-09-45')
    if with_params:
        json.dump({'keywords': ['Python'], 'cities': ['西安']},
                  open(os.path.join(run_dir, 'crawl_params.json'), 'w', encoding='utf-8'))
    if with_summary:
        json.dump({'total': 20},
                  open(os.path.join(run_dir, 'crawl_summary.json'), 'w', encoding='utf-8'))
    return assets, run_dir


def main():
    failures = []

    def check(label, cond, detail=''):
        print('%s %s%s' % ('  ✅' if cond else '  ❌', label,
                           '' if cond else '  ← ' + detail))
        if not cond:
            failures.append(label)

    print('=== 1. skill 路径（stdin 不是终端）：一次都不许问 ===')
    ok, out, page, asked = run_ensure_login(logged_in=False, interactive=False)
    check('返回 False', ok is False)
    check('**一次都没调 input()**（管道里问回车会把会话挂死）', asked == 0,
          '问了 %d 次' % asked)
    check('保留 [LOGIN_NEEDED] 标记（Claude 靠它决定下一步）',
          '[LOGIN_NEEDED]' in out, out)
    check('保留「告诉我：已登录」那句', '告诉我' in out, out)
    check('浏览器不关（用户要在里面手动登录）', page.quit_calls == 0)
    check('没印命令行专属的复检提示', '按回车' not in out, out)

    print('\n=== 2. 交互式 · 本来就已登录 ===')
    assets, run_dir = build_assets()
    try:
        ok, out, page, asked = run_ensure_login(True, True, assets_dir=assets)
        check('返回 True', ok is True)
        check('没问回车（已经登上了，问什么）', asked == 0, '问了 %d 次' % asked)
        check('正常关闭浏览器（cookie 靠这一步落盘）', page.quit_calls == 1,
              'quit 调了 %d 次' % page.quit_calls)
        check('印了下一步', '下一步' in out, out)
    finally:
        shutil.rmtree(assets, ignore_errors=True)

    print('\n=== 3. 交互式 · 扫码后按回车 → 复检通过 ===')
    assets, run_dir = build_assets(with_params=True)
    try:
        ok, out, page, asked = run_ensure_login(
            logged_in=[False, True], interactive=True, answers=[''], assets_dir=assets)
        check('返回 True', ok is True, out)
        check('等了人按回车', asked == 1, '问了 %d 次' % asked)
        check('复检前回到爬虫真正用的那个页面（人可能停在任意页面上）',
              page.urls.count(auth.JOBS_URL) >= 2, str(page.urls))
        check('去过登录页', auth.LOGIN_URL in page.urls, str(page.urls))
        check('复检通过后关掉浏览器落盘', page.quit_calls == 1,
              'quit 调了 %d 次' % page.quit_calls)
        check('说清了会话存在哪', 'chrome_user_data' in out, out)
        check('说了以后不用再扫码', '不用再扫码' in out, out)
    finally:
        shutil.rmtree(assets, ignore_errors=True)

    print('\n=== 4. 交互式 · 下一步命令要指到被打断的那一轮 ===')
    assets, run_dir = build_assets(with_params=True, space=True)
    try:
        ok, out, page, asked = run_ensure_login([False, True], True, [''], assets)
        check('印的是 --from crawl 续跑', '--from crawl' in out, out)
        check('带上了那个运行目录', run_dir in out, out)
        check('路径带空格时加了引号（不加会被 shell 拆成两个参数）',
              '"%s"' % run_dir in out, out)
        check('没让人从头再跑一遍简历（那要重花模型钱）',
              '你的简历.pdf' not in out, out)
    finally:
        shutil.rmtree(assets, ignore_errors=True)

    print('\n=== 4b. 那一轮已经爬完了 → 不该再劝人续跑 ===')
    assets, run_dir = build_assets(with_params=True, with_summary=True)
    try:
        ok, out, page, asked = run_ensure_login([False, True], True, [''], assets)
        check('不印 --from crawl', '--from crawl' not in out, out)
        check('给的是整轮跑和单独爬取', '你的简历.pdf' in out, out)
    finally:
        shutil.rmtree(assets, ignore_errors=True)

    print('\n=== 4c. 没有 LATEST.txt（第一次用）→ 仍要给得出下一步 ===')
    empty = tempfile.mkdtemp(prefix='ensure_login_empty_')
    try:
        ok, out, page, asked = run_ensure_login([False, True], True, [''], empty)
        check('没崩', ok is True, out)
        check('给了整轮跑的命令', 'pipeline.py' in out, out)
        check('路径里带 scripts（从仓库根目录能直接粘）', 'scripts' in out, out)
    finally:
        shutil.rmtree(empty, ignore_errors=True)

    print('\n=== 5. 交互式 · 输入 q 放弃 ===')
    ok, out, page, asked = run_ensure_login(False, True, answers=['q'])
    check('返回 False', ok is False)
    check('只问了一次就停', asked == 1, '问了 %d 次' % asked)
    check('说了浏览器留着', '浏览器留着' in out, out)
    check('放弃时不关浏览器（人可能还想接着登）', page.quit_calls == 0)

    print('\n=== 6. 交互式 · 复检不过：有上限，且每次都由回车触发（不是轮询） ===')
    ok, out, page, asked = run_ensure_login(
        False, True, answers=[''] * (auth._MAX_RECHECKS + 3))
    check('返回 False', ok is False)
    check('问的次数正好是上限 %d' % auth._MAX_RECHECKS,
          asked == auth._MAX_RECHECKS, '问了 %d 次' % asked)
    check('复检次数 == 回车次数（脚本自己没偷偷多探测）',
          page.urls.count(auth.JOBS_URL) == auth._MAX_RECHECKS + 1,
          '访问 jobs 页 %d 次' % page.urls.count(auth.JOBS_URL))
    check('印了放弃提示', '重跑本命令' in out, out)

    print('\n=== 7. 交互式 · 直接 Ctrl-C / EOF ===')
    ok, out, page, asked = run_ensure_login(False, True, answers=[])
    check('返回 False 而不是抛异常', ok is False)
    check('提示了下次怎么继续', '重跑本命令' in out, out)

    print('\n=== 8. _is_interactive 的判据就是 isatty ===')
    saved_stdin = sys.stdin
    try:
        for label, stub, expect in (
                ('真终端', type('S', (), {'isatty': lambda self: True})(), True),
                ('管道', type('S', (), {'isatty': lambda self: False})(), False),
                ('stdin 是 None', None, False),
                ('stdin 已关闭', type('S', (), {
                    'isatty': lambda self: (_ for _ in ()).throw(ValueError)})(), False)):
            sys.stdin = stub
            check('%s → %s' % (label, expect), auth._is_interactive() is expect)
    finally:
        sys.stdin = saved_stdin

    print('\n=== 9. 退出码：只有命令行路径把「没登上」变成 1 ===')
    import boss_crawler
    saved = (boss_crawler.ensure_login, boss_crawler._login_is_interactive, sys.argv)
    try:
        for label, ok_ret, interactive, expect in (
                ('命令行 · 没登上 → 1', False, True, 1),
                ('命令行 · 登上了 → 0', True, True, 0),
                ('skill · 没登上 → 0（靠标记而不是退出码）', False, False, 0),
                ('skill · 登上了 → 0', True, False, 0)):
            boss_crawler.ensure_login = lambda: ok_ret
            boss_crawler._login_is_interactive = lambda: interactive
            sys.argv = ['boss_post_interactive.py', '--ensure-login']
            code = 0
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    boss_crawler.main()
            except SystemExit as exc:
                code = exc.code or 0
            check(label, code == expect, '实际 %s' % code)
    finally:
        boss_crawler.ensure_login, boss_crawler._login_is_interactive, sys.argv = saved

    print('\n' + '=' * 60)
    if failures:
        print('❌ %d 项未通过：' % len(failures))
        for label in failures:
            print('   - %s' % label)
        return 1
    print('✅ 全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
