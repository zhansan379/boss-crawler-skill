#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
登录检测与页面状态：BOSS 直聘登录状态轮询、验证码检测、用户等待
"""

import threading
import time

from DrissionPage import WebPage

from .config import WAIT_TIMEOUT, IS_WINDOWS, co, sleep_config

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

def ensure_login():
    """
    阶段 1：检测登录状态（单次检测，不轮询）。

    打开浏览器，检测一次登录状态：
    - 已登录 → 返回 True，关闭浏览器
    - 未登录 → 保持浏览器打开，提示用户手动登录，返回 False

    用户手动登录后告诉 Claude "已登录"，Claude 再次调用此函数检测。
    登录成功后，Chrome 用户数据（含 cookie）持久化保存到 ./chrome_user_data，
    后续爬取命令可复用该登录状态。

    Returns:
        bool: True 登录成功，False 未登录（浏览器保持打开，等待用户操作）
    """
    print("\n" + "=" * 50)
    print("  登录检测")
    print("=" * 50)

    dp = WebPage(chromium_options=co)

    try:
        print("\n[检测] 正在打开 BOSS 直聘首页...")
        dp.get('https://www.zhipin.com/web/geek/jobs')
        time.sleep(0.5)

        if check_login_status(dp):
            print("\n[LOGIN_OK] 检测到已登录。")
            print("[LOGIN_OK] 登录状态已持久化保存（./chrome_user_data）")
            dp.quit()
            return True

        # 未登录 —— 自动跳转到登录页面，保持浏览器打开
        print("\n[LOGIN_NEEDED] 未检测到登录状态，正在跳转登录页...")
        dp.get('https://www.zhipin.com/web/user/')

        print("\n" + "=" * 50)
        print("  [LOGIN_NEEDED] 请在浏览器中完成登录")
        print("=" * 50)
        print()
        print("  已自动打开 BOSS 直聘登录页面。")
        print("  请扫码或使用账号密码登录。")
        print("  登录完成后告诉我：\"已登录\"，我会再次检测。")
        print()
        print("=" * 50)
        # 不关闭浏览器，让用户手动操作
        return False

    except Exception as e:
        print(f"\n[LOGIN_FAIL] 发生异常: {e}")
        try:
            dp.quit()
        except:
            pass
        return False
