#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
登录检测与页面状态：BOSS 直聘登录状态轮询、验证码检测、用户等待
"""

import os
import sys
import threading
import time

from DrissionPage import WebPage

from .config import WAIT_TIMEOUT, IS_WINDOWS, ASSETS_DIR, co, sleep_config
from .utils import entry_cmd, script_cmd
from resume_matcher.paths import crawl_summary_path, crawl_params_path

# 条件导入 Windows 键盘检测
if IS_WINDOWS:
    import msvcrt


# ==================== 页面状态检测 ====================

def check_login_elements(page):
    """检测登录相关元素（旧版回退逻辑）"""
    login_elements = page.eles('css:.login-btn, css:.scan-login')
    if login_elements:
        return True

    verify_elements = page.eles('css:geetest_radar_tip, css:.geetest_panel')
    if verify_elements:
        return True

    current_url = page.url
    if 'login' in current_url.lower() or 'security-check' in current_url.lower():
        return True

    return False


def check_login_status(page):
    """
    检测 BOSS 直聘页面的登录状态

    通过 XPath 定位特定元素来判断当前是否已登录:
    - 已登录: 页面存在 //a[@href='https://www.zhipin.com/web/geek/recommend']//img
              （头像/用户入口图标，登录后才会显示）
    - 未登录: 页面存在 //a[@class='btn btn-outline header-login-btn']
              （首页右上角的「登录/注册」按钮）

    Returns:
        bool: True 表示已登录，False 表示未登录
    """
    # 先检查是否已登录 —— 查找用户头像/推荐入口元素
    try:
        logged_in_elements = page.eles('xpath://a[@href="https://www.zhipin.com/web/geek/recommend"]//img')
        if logged_in_elements:
            return True
    except Exception:
        pass

    # 再检查是否未登录 —— 查找首页登录按钮
    try:
        not_logged_in_elements = page.eles('xpath://a[@class="btn btn-outline header-login-btn"]')
        if not_logged_in_elements:
            return False
    except Exception:
        pass

    # 如果都未找到，回退到旧版检测逻辑
    if check_login_elements(page):
        return False  # 旧版检测认为需要登录

    # 默认认为已登录（页面正常加载且没有登录提示）
    return True


def check_page_status(page, response):
    """检测页面状态"""
    if check_login_elements(page):
        return 'need_login'

    if response is None:
        return 'no_data'

    try:
        body = response.response.body
        if body is None or body == "":
            return 'no_data'

        job_list = body.get('zpData', {}).get('jobList', [])
        if not job_list:
            return 'no_data'

        return 'normal'
    except Exception:
        return 'no_data'


def wait_for_user_action(page, timeout=WAIT_TIMEOUT):
    """等待用户操作完成"""
    print(f"\n[等待中] 操作完成后按回车继续，或等待自动检测（最长 {timeout} 秒）...")

    start_time = time.time()

    def input_thread():
        if IS_WINDOWS:
            while time.time() - start_time < timeout:
                if msvcrt.kbhit():
                    key = msvcrt.getch()
                    if key == b'\r':
                        return True
                time.sleep(0.1)
        else:
            try:
                input()
                return True
            except:
                return False
        return False

    input_result = {'done': False}

    def run_input():
        input_result['done'] = input_thread()

    input_thread_obj = threading.Thread(target=run_input, daemon=True)
    input_thread_obj.start()

    while time.time() - start_time < timeout:
        if input_result['done']:
            print("用户确认继续")
            return True

        elapsed = int(time.time() - start_time)
        if elapsed % 5 == 0 and elapsed > 0:
            remaining = timeout - elapsed
            print(f"[检测中] 剩余 {remaining} 秒...", end="\r")

            if not check_login_elements(page):
                try:
                    r = page.listen.wait(timeout=1)
                    if r and not isinstance(r, bool):
                        body = r.response.body
                        if body and body.get('zpData', {}).get('jobList'):
                            print("\n检测到登录成功，继续爬取")
                            return True
                except:
                    pass

        time.sleep(0.5)

    print("\n等待超时")
    return False


# ==================== 独立登录命令 ====================

JOBS_URL = 'https://www.zhipin.com/web/geek/jobs'
LOGIN_URL = 'https://www.zhipin.com/web/user/'

# 复检次数上限。这不是轮询 —— 每一次复检都由人按回车触发，脚本自己不会重试。
# 给上限只是防着终端把回车喂成死循环（比如 stdin 被重定向到一个不断吐空行的东西）。
_MAX_RECHECKS = 5


def _is_interactive():
    """人是不是正坐在这个终端前面。

    两条路径跑的是同一条命令（`--ensure-login`），但需要的收尾完全相反：
      · 命令行路径：人在键盘前，应该等他扫完码按回车，然后复检、告诉他下一步；
      · skill 路径：Claude Code 用管道跑这条命令，stdin 不是终端。这时 input() 会
        立刻读到 EOF（更糟的情况是把整个会话挂住），所以必须保持原来的行为 ——
        印出 [LOGIN_NEEDED] 标记就退出，由 Claude 去问用户、再重跑一次本命令。
    isatty() 正是区分这两者的标准办法，也免得再加一个只有一条路径用的开关。
    """
    try:
        return bool(sys.stdin) and sys.stdin.isatty()
    except (AttributeError, ValueError):    # stdin 被关掉时 isatty() 抛 ValueError
        return False


def _latest_run_dir():
    """assets/LATEST.txt 指向的运行目录（读不到就返回 None）。

    这里不去 import resume_matcher.config.get_latest_run_dir：那是另一个包，为了读一行
    文本把它整包拉进爬虫的依赖里不值当。读法必须跟它一致 —— 文件内容就是目录名。
    """
    try:
        with open(os.path.join(ASSETS_DIR, 'LATEST.txt'), 'r', encoding='utf-8') as f:
            name = f.read().strip()
        run_dir = os.path.join(ASSETS_DIR, name)
        return run_dir if name and os.path.isdir(run_dir) else None
    except OSError:
        return None


def _print_next_steps():
    """登录成功后告诉人接下来跑什么。

    这一段是这次改动的重点：原来登录成功只印一句「已持久化保存」就退出了，人得自己回去
    翻文档找下一条命令。而卡在登录这一步的人，多半正是被流水线中断赶过来的。
    """
    print('\n下一步：')
    latest = _latest_run_dir()
    crawled = latest and os.path.exists(crawl_summary_path(latest))
    ready = latest and os.path.exists(crawl_params_path(latest))
    if ready and not crawled:
        # 正是被爬取那一步打断的那一轮：参数都在，接着跑不会重复调模型。
        # --all 不能省：pipeline 不给终点只跑一个阶段，这里的意图是「把后面跑完」。
        print('  接着上一轮跑（简历解析和参数推断的结果都还在，不会再花模型钱）：')
        print('    %s' % script_cmd('pipeline.py', '--run-dir', latest,
                                    '--from', 'crawl', '--all'))
    else:
        print('  从简历开始整轮跑：')
        print('    %s' % script_cmd('pipeline.py', '你的简历.pdf', '--all'))
        print('  只想爬一批岗位看看：')
        print('    %s' % entry_cmd('-m', 'custom', '-p', 'Python', '-c', '西安',
                                   '-n', '20', '-d', '-y'))


def _wait_for_manual_login(dp):
    """等人扫码，回车复检一次。不轮询、不定时探测。

    Returns:
        bool: True 复检通过，False 用户放弃或复检没过
    """
    for attempt in range(1, _MAX_RECHECKS + 1):
        try:
            answer = input('  扫码登录完成后回到这个窗口按回车复检'
                           '（输入 q 放弃）：').strip().lower()
        except (EOFError, KeyboardInterrupt):
            print('\n  已放弃。浏览器留着，下次重跑本命令继续。')
            return False
        if answer in ('q', 'quit', 'exit', 'n'):
            print('  已放弃。浏览器留着，下次重跑本命令继续。')
            return False

        # 复检前先回到爬虫真正会用的那个页面：人扫完码可能停在任意一个页面上，
        # 而 check_login_status 看的是当前页面的头像元素。在这里确认，等于确认
        # 爬虫下一步看到的就是登录态。
        print('  正在复检…')
        try:
            dp.get(JOBS_URL)
            time.sleep(0.5)
        except Exception as exc:                       # noqa: BLE001
            print('  页面打不开（%s），再试一次。' % exc)
            continue
        if check_login_status(dp):
            return True
        print('  还是没检测到登录（第 %d/%d 次）。确认右上角已经出现你的头像，'
              '再按回车。' % (attempt, _MAX_RECHECKS))

    print('  复检 %d 次都没过，先停下。浏览器留着，登录后重跑本命令即可。'
          % _MAX_RECHECKS)
    return False


def ensure_login():
    """独立登录命令：确保 BOSS 直聘处于登录态，并把会话持久化下来。

    单次检测，不轮询。命令行路径下未登录时会停下来等人按回车（回车 → 复检一次），
    skill 路径（stdin 不是终端）保持原行为：印 [LOGIN_NEEDED] 就返回，由 Claude 去问
    用户、再重跑本命令。判据见 _is_interactive()。

    登录状态存在 assets/chrome_user_data（Chrome 用户数据目录）里，是**浏览器正常关闭
    时写盘**的 —— 所以这里确认成功后要 dp.quit()。直接叉掉终端或杀进程有可能让这一次
    的 cookie 没落盘，下次爬取又得重新扫码。

    Returns:
        bool: True 已登录（会话已保存），False 未登录
    """
    print("\n" + "=" * 50)
    print("  登录检测")
    print("=" * 50)

    dp = WebPage(chromium_options=co)

    try:
        print("\n[检测] 正在打开 BOSS 直聘首页...")
        dp.get(JOBS_URL)
        time.sleep(0.5)

        if check_login_status(dp):
            print("\n[LOGIN_OK] 检测到已登录。")
            print("[LOGIN_OK] 登录状态已持久化保存（assets/chrome_user_data）")
            dp.quit()
            _print_next_steps()
            return True

        # 未登录 —— 自动跳转到登录页面
        print("\n[LOGIN_NEEDED] 未检测到登录状态，正在跳转登录页...")
        dp.get(LOGIN_URL)

        print("\n" + "=" * 50)
        print("  [LOGIN_NEEDED] 请在浏览器中完成登录")
        print("=" * 50)
        print()
        print("  已自动打开 BOSS 直聘登录页面。")
        print("  请扫码或使用账号密码登录。")

        if not _is_interactive():
            # skill 路径：没有「我」可以按回车，这句是给 Claude 读的。
            print("  登录完成后告诉我：\"已登录\"，我会再次检测。")
            print()
            print("=" * 50)
            # 不关闭浏览器，让用户手动操作
            return False

        print()
        if not _wait_for_manual_login(dp):
            print("=" * 50)
            return False

        print("\n[LOGIN_OK] 复检通过，已登录。")
        # 正常关闭浏览器把 cookie 落盘到 assets/chrome_user_data，后续爬取直接复用
        dp.quit()
        print("[LOGIN_OK] 登录状态已写入 assets/chrome_user_data，"
              "以后爬取不用再扫码（除非 BOSS 那边把会话踢掉）。")
        _print_next_steps()
        return True

    except Exception as e:
        print(f"\n[LOGIN_FAIL] 发生异常: {e}")
        try:
            dp.quit()
        except:
            pass
        return False
