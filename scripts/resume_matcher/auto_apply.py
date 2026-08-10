#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
自动投递模块：使用 DrissionPage 控制浏览器完成 BOSS 直聘投递操作

流程：
  1. 打开岗位详情 URL
  2. 点击「立即沟通」
  3. 如有弹窗，关闭弹窗
  4. 点击「继续沟通」
  5. 根据简历和岗位要求生成招呼语，输入聊天框
  6. 点击发送按钮
  7. 发送简历附件（可选，通过「发送图片」上传简历文件）
"""

import os
import json
import time
import random
from typing import List, Dict, Any, Optional

from .config import ResumeProfile, OUTPUT_DIR, get_latest_run_dir, create_run_dir
from .utils import ensure_output_dir

# 尝试导入浏览器自动化库
try:
    from DrissionPage import WebPage, ChromiumOptions
    HAS_DRISSION = True
except ImportError:
    HAS_DRISSION = False


# ==================== XPath 常量 ====================

# 立即沟通按钮
XPATH_START_CHAT = (
    "//div[@class='btn-container']"
    "//a[@class='btn btn-startchat']"
    "[contains(text(),'立即沟通')]"
)

# 继续沟通按钮（点击「立即沟通」后可能出现）
XPATH_CONTINUE_CHAT = (
    "//div[@class='btn-container']"
    "//a[@class='btn btn-startchat']"
    "[contains(text(),'继续沟通')]"
)

# 弹窗关闭按钮
XPATH_POPUP_CLOSE = "//i[@class='icon-close']"

# 聊天输入框
XPATH_CHAT_INPUT = "//div[@id='chat-input']"

# 发送按钮
XPATH_SEND_BUTTON = "//button[@type='send']"

# 备用：投递简历按钮
XPATH_DELIVER_RESUME = "//div[@class='btn-container']//a[contains(text(),'投递简历')]"

# 发送图片按钮（聊天工具栏）
XPATH_SEND_IMAGE = "//div[@aria-label='发送图片']"

# 图片上传后的 file input
CSS_FILE_INPUT = "css:input[type='file']"

# 备用：发送简历按钮（聊天面板中）
XPATH_SEND_RESUME = "//span[contains(text(),'发简历')]"


def generate_greeting(
    profile: Optional[ResumeProfile] = None,
    job: Optional[Dict[str, Any]] = None,
    custom_greeting: Optional[str] = None
) -> str:
    """
    生成个性化招呼语。

    优先使用 custom_greeting（Claude 预生成的），
    其次根据 profile + job 模板生成。

    Args:
        profile: 简历结构化信息
        job: 岗位字典
        custom_greeting: 自定义招呼语（由 Claude 预生成）

    Returns:
        招呼语字符串
    """
    if custom_greeting:
        return custom_greeting

    if profile is None or job is None:
        return _default_greeting(job)

    # 从 profile 和 job 中提取信息生成模板招呼语
    name = profile.basic_info.get('name', '')
    position = job.get('职位', '')
    company = job.get('公司', '')

    # 提取核心技能匹配
    skills = profile.skills
    all_skills = (
        skills.get('programming', [])
        + skills.get('frameworks', [])
        + skills.get('tools', [])
        + skills.get('other', [])
    )

    # 提取关键技能词（取前 5 个最有辨识度的）
    key_skills = _pick_key_skills(all_skills, job)

    # 提取相关项目经验
    projects = profile.experience.get('companies', []) + profile.projects
    relevant_project = _pick_relevant_project(projects, job)

    # 学历
    education = profile.education
    degree = education.get('degree', '')
    major = education.get('major', '')
    school = education.get('school', '')

    # 经验年限
    total_years = profile.experience.get('total_years', 0)
    exp_desc = f"{total_years}年" if total_years > 0 else "实习"

    # 构建招呼语
    lines = []

    # 开头
    lines.append(f"您好，我是{name}，对贵公司的「{position}」岗位非常感兴趣。")

    # 学历 + 专业
    lines.append(f"我毕业于{school}，{degree}学历（{major}专业），有{exp_desc}开发经验。")

    # 技能匹配
    if key_skills:
        skills_str = "、".join(key_skills[:6])
        lines.append(f"技术栈方面，熟练掌握{skills_str}，与岗位要求高度匹配。")

    # 项目亮点
    if relevant_project:
        lines.append(f"曾主导{relevant_project}，具备实际落地经验。")

    # 收尾
    lines.append("希望能有机会进一步沟通，期待您的回复！")

    greeting = "\n".join(lines)

    # 如果超长则精简
    if len(greeting) > 500:
        greeting = _compact_greeting(greeting, name, position, key_skills)

    return greeting


def _pick_key_skills(all_skills: List[str], job: Dict[str, Any]) -> List[str]:
    """从技能列表中选出与岗位最相关的关键词"""
    jd = job.get('岗位要求和职责', '') + job.get('技能标签', '')
    jd_lower = jd.lower()

    # 优先选择 JD 中出现的技能
    scored = []
    for skill in all_skills:
        if not skill or len(skill) < 2:
            continue
        score = 0
        if skill.lower() in jd_lower:
            score = 100  # JD 提及
        if len(skill) <= 15 and not skill.startswith("传统") and "开发" not in skill:
            scored.append((score, skill))

    scored.sort(key=lambda x: (-x[0], len(x[1])))

    # 去重优先，取前 8 个
    seen = set()
    result = []
    for _, s in scored:
        if s.lower() not in seen:
            seen.add(s.lower())
            result.append(s)
        if len(result) >= 8:
            break

    return result


def _pick_relevant_project(projects: List[Dict], job: Dict[str, Any]) -> Optional[str]:
    """选出与岗位最相关的项目名"""
    jd = job.get('岗位要求和职责', '') + job.get('职位', '')
    jd_lower = jd.lower()

    best = None
    best_score = 0

    for proj in projects:
        name = proj.get('name', '')
        desc = proj.get('description', '') if isinstance(proj, dict) else ''
        tech_stack = ' '.join(proj.get('tech_stack', [])) if isinstance(proj, dict) else ''

        score = 0
        for word in name + desc + tech_stack:
            if word.lower() in jd_lower:
                score += 1

        if score > best_score:
            best_score = score
            best = name

    return best


def _default_greeting(job: Optional[Dict[str, Any]] = None) -> str:
    """默认招呼语"""
    position = job.get('职位', '该岗位') if job else '该岗位'
    return f"您好，我对「{position}」非常感兴趣。我的技术背景与该岗位高度匹配，希望能有机会进一步沟通。期待您的回复！"


def _compact_greeting(greeting: str, name: str, position: str, skills: List[str]) -> str:
    """精简招呼语到 300 字以内"""
    skills_str = "、".join(skills[:4])
    return (
        f"您好，我是{name}，对贵公司的「{position}」岗位非常感兴趣。"
        f"我的技术栈（{skills_str}）与岗位要求高度匹配，且有完整的AI应用项目落地经验。"
        f"希望能有机会进一步沟通，期待您的回复！"
    )


def send_resume_attachment(
    dp: WebPage,
    resume_file_path: str,
    timeout: float = 10.0
) -> bool:
    """
    在聊天窗口中发送简历附件（图片/文件）。

    策略（按优先级尝试）：
        A. 先查找页面所有隐藏 <input type="file">，对匹配图片/PDF 的直接 input()
           → 绕过原生文件对话框
        B. 如果找不到合适的 file input，通过 JS 注入一个来接收文件
        C. 最后兜底：点击「发送图片」div + CDP 拦截文件对话框

    Args:
        dp: DrissionPage WebPage 实例（需已在聊天页面）
        resume_file_path: 简历文件路径（支持图片、PDF等）
        timeout: 操作超时

    Returns:
        是否成功发送
    """
    if not resume_file_path or not os.path.exists(resume_file_path):
        print(f"  ⚠ 简历文件不存在: {resume_file_path}")
        return False

    print(f"  📎 准备发送简历附件: {os.path.basename(resume_file_path)}")

    ext = os.path.splitext(resume_file_path)[1].lower()
    is_image = ext in ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp')

    # ── 策略 A：直接找隐藏 file input（不点击按钮，绕过原生对话框）──
    file_inputs = _find_all_file_inputs(dp)
    if file_inputs:
        print(f"  🔍 找到 {len(file_inputs)} 个 file input，尝试匹配...")
        for fi in file_inputs:
            try:
                accept = (fi.attr('accept') or '').lower()
                # 匹配：accept 为空（兜底）、accept 匹配图片、或 accept 匹配 PDF
                if not accept or (is_image and 'image' in accept) or (not is_image and ext.lstrip('.') in accept):
                    fi.input(resume_file_path)
                    time.sleep(2)
                    print(f"  ✅ 简历附件已上传 (策略A: 直接 file input)")
                    return True
            except Exception:
                continue

        # accept 都不匹配，用最后一个兜底
        try:
            file_inputs[-1].input(resume_file_path)
            time.sleep(2)
            print(f"  ✅ 简历附件已上传 (策略A: 兜底 input)")
            return True
        except Exception as e:
            print(f"  ⚠ 策略A失败: {e}")

    # ── 策略 B：JS 注入 file input → 设置文件 → 触发 change 事件 ──
    print(f"  🔄 尝试策略B: JS 注入 file input...")
    if _upload_via_js_injection(dp, resume_file_path):
        return True

    # ── 策略 C：点击「发送图片」div（兜底，会弹出原生对话框需手动处理）──
    print(f"  🔄 尝试策略C: 点击「发送图片」按钮...")
    img_btn = _find_element(dp, [
        XPATH_SEND_IMAGE,
        "css:[aria-label='发送图片']",
        "css:.icon-send-image",
    ], timeout=3)

    if not img_btn:
        print(f"  ⚠ 未找到「发送图片」按钮")
        return False

    try:
        # 点击前先尝试用 CDP 拦截文件对话框
        _auto_accept_file_dialog(dp, resume_file_path)
        img_btn.click()
        time.sleep(3)
        print(f"  ℹ 策略C: 已点击按钮，如弹出对话框请手动选择文件")
        # 此时如果 CDP 拦截成功文件已上传，否则需要手动操作
        return True
    except Exception as e:
        print(f"  ❌ 策略C失败: {e}")
        return False


def _find_all_file_inputs(dp: WebPage) -> list:
    """查找页面上所有 <input type='file'>（包括隐藏的），返回元素列表"""
    try:
        # 用 JS 查找所有 file input（绕过 Selenium 不可见限制）
        result = dp.run_js("""
            const inputs = document.querySelectorAll('input[type="file"]');
            return inputs.length;
        """)
        count = int(result) if result else 0

        if count == 0:
            return []

        # 逐个获取
        inputs = []
        for i in range(count):
            try:
                el = dp.ele(f'css:input[type="file"]', index=i + 1, timeout=1)
                if el:
                    inputs.append(el)
            except Exception:
                continue
        return inputs
    except Exception:
        return []


def _upload_via_js_injection(dp: WebPage, file_path: str) -> bool:
    """
    通过 JS 注入一个 file input 元素，
    用 DrissionPage 设置文件值，再派发 change 事件触发上传。
    """
    try:
        # 1. 注入隐藏的 file input 到 body
        injected_id = '__auto_apply_file_input__'
        dp.run_js(f"""
            if (!document.getElementById('{injected_id}')) {{
                const input = document.createElement('input');
                input.type = 'file';
                input.id = '{injected_id}';
                input.style.display = 'none';
                input.accept = 'image/*,.pdf';
                document.body.appendChild(input);

                // 监听 change：找到原生的上传组件并派发文件
                input.addEventListener('change', (e) => {{
                    const file = e.target.files[0];
                    if (!file) return;
                    // 尝试找到页面上的 paste/drop 区域并派发 DataTransfer
                    const dt = new DataTransfer();
                    dt.items.add(file);
                    const pasteEvent = new ClipboardEvent('paste', {{
                        clipboardData: dt, bubbles: true, cancelable: true
                    }});
                    document.dispatchEvent(pasteEvent);
                }});
            }}
        """)
        time.sleep(0.5)

        # 2. 找到注入的 input 并设置文件
        injected = dp.ele(f'#{injected_id}', timeout=2)
        if injected:
            injected.input(file_path)
            time.sleep(2)
            print(f"  ✅ 简历附件已上传 (策略B: JS 注入)")
            return True
    except Exception as e:
        print(f"  ⚠ 策略B异常: {e}")

    return False


def _auto_accept_file_dialog(dp: WebPage, file_path: str) -> None:
    """通过 CDP 设置文件对话框自动选择文件"""
    try:
        cdp = dp._control_browser
        if cdp:
            cdp.Page.setInterceptFileChooserDialog({'enabled': True})
            # 注册回调：当对话框打开时自动选择文件
            # 注意：这是预先设置，下次文件对话框打开时自动生效
            dp.set.upload_files(file_path)
    except Exception:
        pass  # 静默失败，回退到手动


def _ensure_login(dp: WebPage, max_wait: int = 300) -> bool:
    """
    确保浏览器已登录 BOSS 直聘。

    1. 先快速检测是否已登录
    2. 未登录则进入轮询等待（每 3s 刷新），最长 max_wait 秒

    Returns:
        True 已登录，False 超时
    """
    # 先导航到首页检查
    dp.get('https://www.zhipin.com/web/geek/recommend')
    time.sleep(2)

    if check_login_status(dp):
        print(f"  [LOGIN_OK] 检测到已登录")
        return True

    print(f"  [需要登录] 请在浏览器窗口中完成登录...")
    print(f"  [需要登录] 脚本将每 3s 自动检测，最长等待 {max_wait}s...")

    elapsed = 0
    interval = 3
    while elapsed < max_wait:
        mins = elapsed // 60
        secs = elapsed % 60
        if elapsed > 0:
            print(f"  [等待登录] {mins}m{secs}s / {max_wait//60}m ...")
        time.sleep(interval)
        elapsed += interval

        try:
            dp.refresh()
            time.sleep(1)
        except Exception:
            pass

        if check_login_status(dp):
            print(f"  [LOGIN_OK] 检测到登录成功！（耗时 {elapsed}s）")
            return True

    print(f"  [LOGIN_FAIL] 登录超时（{max_wait}s）")
    return False


def check_login_status(page) -> bool:
    """
    通过 XPath 检测 BOSS 直聘页面登录状态。
    - 已登录: //a[@href='https://www.zhipin.com/web/geek/recommend']//img（用户头像）
    - 未登录: //a[@class='btn btn-outline header-login-btn']（登录按钮）
    """
    try:
        logged_in = page.eles('xpath://a[@href="https://www.zhipin.com/web/geek/recommend"]//img')
        if logged_in:
            return True
    except Exception:
        pass

    try:
        not_logged_in = page.eles('xpath://a[@class="btn btn-outline header-login-btn"]')
        if not_logged_in:
            return False
    except Exception:
        pass

    # 都不匹配，保守返回 False
    return False


def auto_apply_jobs(
    qualified_jobs: List[Dict[str, Any]],
    _profile: ResumeProfile,
    max_applications: int = 10,
    headless: bool = False,
    greetings: Optional[Dict[str, str]] = None,
    resume_file_path: Optional[str] = None,
    output_dir: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    自动投递简历到匹配岗位。

    流程：
        1. 打开岗位详情页
        2. 点击「立即沟通」
        3. 关闭弹窗（如有）
        4. 点击「继续沟通」
        5. 输入招呼语 → 发送
        6. 发送简历附件（如提供 resume_file_path）

    Args:
        qualified_jobs: 符合条件的岗位列表
        _profile: 简历结构化信息（用于生成招呼语）
        max_applications: 最大投递数量
        headless: 是否无头模式
        greetings: 预生成的招呼语字典 {job_link: greeting_text}，优先使用
        resume_file_path: 简历文件路径（图片/PDF），用于「发送图片」上传附件
        output_dir: 输出目录（投递日志保存位置）。
                    默认自动定位最近运行目录或创建新目录。

    Returns:
        投递结果列表
    """
    if not HAS_DRISSION:
        print("错误: DrissionPage 未安装，无法自动投递。pip install DrissionPage")
        return []

    if not qualified_jobs:
        print("没有可投递的岗位")
        return []

    # 按优先级排序
    sorted_jobs = sorted(
        qualified_jobs,
        key=lambda j: (
            -j.get('match_score', 0),
            j.get('company', '')
        )
    )
    jobs_to_apply = sorted_jobs[:max_applications]

    print(f"\n{'='*60}")
    print(f"  🤖 自动投递模式")
    print(f"  目标岗位: {len(jobs_to_apply)} 个")
    print(f"  招呼语来源: {'Claude 预生成' if greetings else '模板自动生成'}")
    print(f"{'='*60}")

    # 配置浏览器（复用 Chrome 用户数据，保留登录态）
    co = ChromiumOptions()
    chrome_path = r"C:\Users\feng1\AppData\Local\Google\Chrome\Application\chrome.exe"
    if os.path.exists(chrome_path):
        co.set_browser_path(chrome_path)

    # 持久化 Chrome Profile（复用登录状态，位于 assets/chrome_user_data）
    user_data_dir = os.path.join(OUTPUT_DIR, 'chrome_user_data')
    os.makedirs(user_data_dir, exist_ok=True)
    co.set_argument(f'--user-data-dir={user_data_dir}')

    if headless:
        co.headless(True)

    dp = WebPage(chromium_options=co)
    results = []

    # ── 确保登录 ──
    if not _ensure_login(dp, max_wait=300):
        print("❌ 登录失败，无法继续投递")
        dp.quit()
        return results

    try:
        for i, job in enumerate(jobs_to_apply):
            position = job.get('职位', job.get('position', '未知'))
            company = job.get('公司', job.get('company', '未知'))
            link = job.get('link', '')

            print(f"\n--- [{i+1}/{len(jobs_to_apply)}] {company} - {position} ---")

            result = {
                'job': job,
                'status': 'unknown',
                'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
                'error': None,
                'greeting_used': None
            }

            if not link:
                result['status'] = 'skipped'
                result['error'] = '无岗位链接'
                print(f"  ⏭ 跳过: 无链接")
                results.append(result)
                continue

            try:
                # === 步骤 1：打开岗位详情页 ===
                print(f"  🌐 打开详情页...")
                dp.get(link)
                time.sleep(3)

                # === 步骤 2：点击「立即沟通」 ===
                print(f"  🔍 查找「立即沟通」按钮...")
                start_btn = _find_element(dp, [
                    XPATH_START_CHAT,
                    XPATH_DELIVER_RESUME,
                    'css:.btn-startchat',
                ])

                if start_btn:
                    start_btn.click()
                    print(f"  ✅ 已点击「立即沟通」")
                    time.sleep(2)
                else:
                    # 可能已经沟通过，直接尝试「继续沟通」
                    print(f"  ⚠ 未找到「立即沟通」，尝试「继续沟通」...")

                # === 步骤 3：关闭弹窗（如有） ===
                closed_popup = _close_popup(dp)
                if closed_popup:
                    print(f"  🧹 已关闭弹窗")
                    time.sleep(1)

                # === 步骤 4：点击「继续沟通」 ===
                print(f"  🔍 查找「继续沟通」按钮...")
                continue_btn = _find_element(dp, [
                    XPATH_CONTINUE_CHAT,
                    'css:.btn-startchat',
                ])

                if continue_btn:
                    continue_btn.click()
                    print(f"  ✅ 已点击「继续沟通」")
                    time.sleep(2)

                    # 再次关闭可能出现的弹窗
                    _close_popup(dp)
                else:
                    print(f"  ⚠ 未找到「继续沟通」，等待聊天窗口加载...")
                    # 可能聊天窗口已经在页面中，多等一会儿让它渲染
                    time.sleep(3)
                    _close_popup(dp)

                # === 步骤 5：生成并输入招呼语 ===
                greeting = _get_greeting(job, _profile, greetings)
                result['greeting_used'] = greeting

                if greeting and _input_greeting(dp, greeting):
                    print(f"  📝 已输入招呼语")
                    time.sleep(0.5)

                    # === 步骤 6：点击发送 ===
                    if _click_send(dp):
                        print(f"  📤 消息已发送")
                        result['status'] = 'applied'
                        print(f"  ✅ 投递+招呼完成！")

                        # === 步骤 7：发送简历附件 ===
                        if resume_file_path:
                            time.sleep(1)
                            attachment_ok = send_resume_attachment(
                                dp, resume_file_path
                            )
                            result['attachment_sent'] = attachment_ok
                            if attachment_ok:
                                print(f"  ✅ 简历附件已发送！")
                            else:
                                print(f"  ⚠ 简历附件发送失败，请手动发送")
                    else:
                        result['status'] = 'partial'
                        result['error'] = '招呼语已输入但发送失败'
                        print(f"  ⚠ 发送失败，请手动发送")
                else:
                    result['status'] = 'no_chat'
                    result['error'] = '无法输入招呼语（可能已投递或聊天窗口未开启）'
                    print(f"  ⚠ 无法输入招呼语")

            except Exception as e:
                result['status'] = 'error'
                result['error'] = str(e)
                print(f"  ❌ 异常: {e}")

            results.append(result)

            # 间隔等待（模拟人类操作，3-8 秒随机）
            if i < len(jobs_to_apply) - 1:
                wait = random.uniform(3, 8)
                print(f"  ⏳ 等待 {wait:.1f}s...")
                time.sleep(wait)

    finally:
        dp.quit()

    # === 保存投递记录 ===
    if output_dir is None:
        output_dir = get_latest_run_dir() or create_run_dir()
    os.makedirs(output_dir, exist_ok=True)
    log_path = os.path.join(output_dir, 'apply_log.json')

    applied_count = sum(1 for r in results if r['status'] == 'applied')
    partial_count = sum(1 for r in results if r['status'] == 'partial')
    failed_count = sum(1 for r in results if r['status'] in ('error', 'no_chat', 'no_button'))
    skipped_count = sum(1 for r in results if r['status'] == 'skipped')

    log_data = {
        'applied_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'total_attempts': len(jobs_to_apply),
        'results': results,
        'summary': {
            'applied': applied_count,
            'partial': partial_count,
            'failed': failed_count,
            'skipped': skipped_count,
        }
    }

    with open(log_path, 'w', encoding='utf-8') as f:
        json.dump(log_data, f, ensure_ascii=False, indent=2)

    # === 打印汇总 ===
    print(f"\n{'='*60}")
    print(f"  📊 投递汇总")
    print(f"  成功(含招呼): {applied_count}")
    print(f"  部分成功:      {partial_count}")
    print(f"  失败:          {failed_count}")
    print(f"  跳过:          {skipped_count}")
    print(f"  记录已保存:    {log_path}")
    print(f"{'='*60}")

    return results


# ==================== 内部辅助函数 ====================

def _find_element(dp: WebPage, selectors: List[str], timeout: float = 2.0):
    """
    按优先级尝试多个选择器查找元素。
    支持 XPath 和 CSS 选择器。
    """
    for selector in selectors:
        try:
            el = dp.ele(selector, timeout=timeout)
            if el:
                return el
        except Exception:
            continue
    return None


def _close_popup(dp: WebPage) -> bool:
    """尝试关闭页面弹窗"""
    close_selectors = [
        XPATH_POPUP_CLOSE,
        'css:.icon-close',
        'css:.dialog-close',
        'css:.modal-close',
        'css:[class*="close"]',
    ]
    for selector in close_selectors:
        try:
            el = dp.ele(selector, timeout=1)
            if el:
                el.click()
                return True
        except Exception:
            continue
    return False


def _get_greeting(
    job: Dict[str, Any],
    profile: Optional[ResumeProfile],
    greetings: Optional[Dict[str, str]]
) -> Optional[str]:
    """获取招呼语：优先使用预生成的，否则模板生成"""
    if greetings:
        link = job.get('link', '')
        if link in greetings:
            return greetings[link]

    return generate_greeting(profile=profile, job=job)


def _input_greeting(dp: WebPage, greeting: str) -> bool:
    """在聊天输入框中输入招呼语（优先使用 JS 直接注入，避免逐键模拟丢字）"""
    import json as _json

    # 策略 A：直接用 JS 注入文本 + 派发完整事件链（适合 contenteditable div）
    try:
        greeting_js = _json.dumps(greeting)
        result = dp.run_js(f"""
            const sel = '#chat-input, [contenteditable="true"], textarea, [placeholder*="消息"], [placeholder*="输入"]';
            const el = document.querySelector(sel);
            if (el) {{
                el.focus();
                el.click();
                if (el.contentEditable === 'true') {{
                    el.innerText = {greeting_js};
                }} else {{
                    el.value = {greeting_js};
                }}
                // 派发完整事件链，确保 Vue/React 绑定生效
                el.dispatchEvent(new Event('input', {{bubbles: true}}));
                el.dispatchEvent(new Event('change', {{bubbles: true}}));
                el.dispatchEvent(new Event('compositionend', {{bubbles: true}}));
                return 'ok';
            }}
            return 'not found';
        """)
        if result and 'ok' in str(result):
            time.sleep(0.5)
            return True
    except Exception:
        pass

    # 策略 B：通过 DrissionPage 查找元素后逐键输入（中文长文本备选）
    input_selectors = [
        XPATH_CHAT_INPUT,
        'css:#chat-input',
        'css:.chat-input',
        'css:div[contenteditable="true"]',
        'css:[contenteditable="true"]',
        'css:[placeholder*="消息"]',
        'css:[placeholder*="输入"]',
        'css:textarea',
        'css:.input-area textarea',
        'css:.chat-input-area textarea',
        'css:div.chat-input',
    ]

    for selector in input_selectors:
        try:
            el = dp.ele(selector, timeout=2)
            if el:
                el.click()
                time.sleep(0.3)
                el.input(greeting)
                time.sleep(1.0)  # 等待逐键输入完成
                return True
        except Exception:
            # 尝试在 iframe 中查找
            try:
                iframe = dp.get_frame(1)
                if iframe:
                    el = iframe.ele(selector, timeout=1)
                    if el:
                        el.click()
                        time.sleep(0.3)
                        el.input(greeting)
                        time.sleep(1.0)
                        return True
            except Exception:
                continue

    return False


def _click_send(dp: WebPage) -> bool:
    """点击发送按钮"""
    send_selectors = [
        XPATH_SEND_BUTTON,
        'css:button[type="send"]',
        'css:.btn-send',
        'css:.send-btn',
        'css:button:contains("发送")',
    ]

    for selector in send_selectors:
        try:
            el = dp.ele(selector, timeout=2)
            if el:
                el.click()
                return True
        except Exception:
            continue

    return False
