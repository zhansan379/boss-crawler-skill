#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
爬取引擎：列表翻页、详情采集、流程编排
"""

import csv
import math
import os
import time
import urllib.parse

from DrissionPage import WebPage

from .auth import check_login_elements, check_login_status, check_page_status, wait_for_user_action
from .config import CSV_FIELDS, ENCODING, ASSETS_DIR, co, sleep_config
from .data_loader import load_existing_links, init_csv_file
from .menu import print_header, estimate_time
from .state import time_stats


# ==================== 列表数据处理 ====================

def process_job_list(job_list, file_path, existing_links, csv_writer):
    """处理岗位列表数据（去重写入 CSV）"""
    written = 0
    skipped = 0

    for job in job_list:
        job_link = f"https://www.zhipin.com/job_detail/{job['encryptJobId']}.html"

        if job_link in existing_links:
            skipped += 1
            continue

        dit = {
            'link': job_link,
            '职位': job.get('jobName', ''),
            '城市': job.get('cityName', ''),
            '区域': job.get('areaDistrict', ''),
            '商圈': job.get('businessDistrict', ''),
            '公司': job.get('brandName', ''),
            '薪资': job.get('salaryDesc', ''),
            '经验': job.get('jobExperience', ''),
            '学历': job.get('jobDegree', ''),
            '领域': job.get('brandIndustry', ''),
            '性质': job.get('brandStageName', ''),
            '规模': job.get('brandScaleName', ''),
            '技能标签': ' '.join(job.get('skills', [])),
            '福利标签': ' '.join(job.get('welfareList', [])),
            '位置': str(job.get('gps', '')),
            '岗位要求和职责': '',
            '公司信息': '',
            # HR 活跃度只存在于详情 API，列表阶段一律留空，由 crawl_job_details 回填
            'HR活跃度': '',
            'HR在线': '',
            'HR职位': ''
        }

        csv_writer.writerow(dit)
        existing_links.add(job_link)
        written += 1

    return written, skipped


# ==================== 单页爬取循环（共用） ====================

def _crawl_paginated(dp, url, file_path, count_limit, existing_links):
    """
    通用的翻页爬取循环。
    两个爬取函数（by_position / by_query）共用此翻页逻辑。

    Returns:
        (total_processed, total_written, total_skipped)
    """
    dp.get(url)
    dp.listen.start('zpgeek/search/joblist.json')
    time_stats.end_request(True, url)

    total_written = 0
    total_skipped = 0
    page_num = 1
    consecutive_no_data = 0

    with open(file_path, 'a', encoding=ENCODING, newline='') as f:
        csv_writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)

        while True:
            if count_limit and total_written >= count_limit:
                print(f"\n已达到数量限制 {count_limit} 条")
                break

            print(f"\n[第{page_num}页] 爬取中...")

            try:
                time_stats.start_request('scroll')
                dp.scroll.to_bottom()
                time_stats.end_request(True)
            except Exception as e:
                time_stats.end_request(False, str(e))
                print(f"滚动失败: {e}")
                continue

            time_stats.start_request('api', 'zpgeek/search/joblist.json')
            r = dp.listen.wait(timeout=5)
            status = check_page_status(dp, r)

            if status == 'need_login':
                time_stats.end_request(False, '需要登录')
                print(f"[第{page_num}页] [!] 检测到需要登录，请手动登录...")
                if not wait_for_user_action(dp):
                    print("等待超时，停止爬取")
                    break
                continue

            if status == 'no_data':
                time_stats.end_request(False, '无数据')
                consecutive_no_data += 1
                if consecutive_no_data >= 3:
                    print(f"\n连续 {consecutive_no_data} 页无数据，爬取结束")
                    break
                print(f"[第{page_num}页] 无数据，等待重试...")
                sleep_config.sleep('retry')
                continue

            consecutive_no_data = 0

            try:
                job_list = r.response.body.get('zpData', {}).get('jobList', [])
                written, skipped = process_job_list(job_list, file_path, existing_links, csv_writer)
                total_written += written
                total_skipped += skipped

                time_stats.end_request(True, f"获取{len(job_list)}条,写入{written}条")
                print(f"[第{page_num}页] 获取到 {len(job_list)} 条数据，已写入 {written} 条（跳过 {skipped} 条重复）")
            except Exception as e:
                time_stats.end_request(False, str(e))
                print(f"[第{page_num}页] 处理数据失败: {e}")

            page_num += 1
            sleep_config.sleep('page')

    dp.listen.stop()
    return total_written + total_skipped, total_written, total_skipped


# ==================== 按岗位 code 爬取 ====================

def crawl_jobs_by_position(dp, position_code, city_code, file_path, count_limit, existing_links, filter_query=''):
    """按岗位code爬取"""
    init_csv_file(file_path)

    url = f'https://www.zhipin.com/web/geek/job?position={position_code}&city={city_code}{filter_query}'
    print(f"\n访问: {url}")
    time_stats.start_request('page', url)

    return _crawl_paginated(dp, url, file_path, count_limit, existing_links)


# ==================== 按关键词爬取 ====================

def crawl_jobs_by_query(dp, query, city_code, file_path, count_limit, existing_links, filter_query=''):
    """按关键词爬取"""
    init_csv_file(file_path)

    encoded_query = urllib.parse.quote(query)
    url = f'https://www.zhipin.com/web/geek/jobs?city={city_code}&query={encoded_query}{filter_query}'
    print(f"\n访问: {url}")
    time_stats.start_request('page', url)

    return _crawl_paginated(dp, url, file_path, count_limit, existing_links)


# ==================== 详情爬取 ====================

def get_single_job_detail(dp, url):
    """
    获取单个岗位详情（优先API直调，兜底DOM解析）

    Returns:
        dict: {'jd', 'company_info', 'hr_active', 'hr_online', 'hr_title'}
              HR 三项仅 API 直调分支可得，DOM 兜底时为空串。
        None: 获取失败或需要登录
    """
    import re
    try:
        time_stats.start_request('detail', url)

        # Step 1: 快速加载详情页 HTML（不等 JS 渲染，~0.8s）
        dp.get(url)
        html = dp.html

        # Step 2: 从 HTML 中提取 securityId 和 jobId
        m_sid = re.search(r"securityId:\s*'([^']+)'", html)
        m_jid = re.search(r"job_id:\s*'([^']+)'", html)

        if not m_sid or not m_jid:
            # securityId 提取失败，回退到 DOM 解析
            if check_login_elements(dp):
                time_stats.end_request(False, '需要登录')
                return None
            return _extract_detail_from_dom(dp)

        security_id = m_sid.group(1)
        job_id = m_jid.group(1)

        # Step 3: 直接调用详情 API（~0.1s）
        api_url = (
            f'https://www.zhipin.com/wapi/zpgeek/job/detail.json'
            f'?securityId={security_id}&jobId={job_id}'
        )
        dp.listen.start('job/detail.json')
        dp.get(api_url)
        r = dp.listen.wait(timeout=8)
        dp.listen.stop()

        if not r:
            # API 超时，回退 DOM
            dp.get(url)
            return _extract_detail_from_dom(dp)

        body = r.response.body
        zp = body.get('zpData', {})

        # 提取岗位描述
        job_info = zp.get('jobInfo', {})
        post_desc = job_info.get('postDescription', '')
        if not post_desc:
            # 备用：某些岗位可能用不同字段名
            post_desc = job_info.get('postDesc', '')

        # 拼装完整描述：岗位职责 + 任职要求等
        for key in ('job_description', 'job_require', 'jobRequire', 'jobDescription'):
            val = job_info.get(key, '')
            if val:
                post_desc += '\n' + val

        # 提取公司信息
        brand_info = zp.get('brandComInfo', {})
        comp_info = ''
        if isinstance(brand_info, dict):
            comp_info = brand_info.get('content', '') or brand_info.get('companyInfo', '')
            if not comp_info:
                comp_info = brand_info.get('introduction', '')
        elif isinstance(brand_info, str):
            comp_info = brand_info

        if post_desc:
            time_stats.end_request(True, 'API直调')
            return {
                'jd': post_desc,
                'company_info': comp_info if comp_info else '',
                **_extract_hr_info(zp),
            }

        # API 没有描述内容，回退 DOM
        dp.get(url)
        return _extract_detail_from_dom(dp)

    except Exception as e:
        time_stats.end_request(False, str(e))
        print(f"获取详情失败: {e}")
        return None


def _extract_hr_info(zp):
    """
    从详情 API 的 zpData.bossInfo 提取招聘者活跃度。

    快照语义：这三项是爬取瞬间的状态。bossOnline 波动极快，
    投递时基本已过期；activeTimeDesc 粒度粗但相对稳定，是排序的主要依据。

    hr_online 取不到时留空串而非 '否' —— 「未采集」不能被读成「不在线」。
    """
    boss = zp.get('bossInfo') or {}
    if not isinstance(boss, dict):
        return {'hr_active': '', 'hr_online': '', 'hr_title': ''}

    online = boss.get('bossOnline')
    return {
        'hr_active': boss.get('activeTimeDesc') or '',
        'hr_online': '' if online is None else ('是' if online else '否'),
        'hr_title': boss.get('title') or '',
    }


def _extract_detail_from_dom(dp):
    """兜底方案：从 DOM 中提取详情（需要等 JS 渲染完成）"""
    empty_hr = {'hr_active': '', 'hr_online': '', 'hr_title': ''}

    if dp.wait.ele_displayed('css:.job-sec-text', timeout=15):
        pass  # 元素已出现

    if check_login_elements(dp):
        time_stats.end_request(False, '需要登录')
        return None

    res = dp.eles('css:.job-sec-text')

    if len(res) == 1:
        time_stats.end_request(True, 'DOM解析')
        return {'jd': res[0].text, 'company_info': '', **empty_hr}
    elif len(res) == 2:
        time_stats.end_request(True, 'DOM解析')
        return {'jd': res[0].text, 'company_info': res[1].text, **empty_hr}
    else:
        time_stats.end_request(False, '未找到详情元素')
        return None


def crawl_job_details(dp, file_path, existing_links):
    """
    爬取岗位详情
    使用传入的dp实例，不创建新浏览器
    """
    if not os.path.exists(file_path):
        print(f"文件不存在: {file_path}")
        return 0

    rows = []
    links_to_update = []

    with open(file_path, 'r', encoding=ENCODING) as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
            if not row.get('岗位要求和职责') and row['link'] not in existing_links:
                links_to_update.append(row['link'])

    if not links_to_update:
        print("所有岗位详情已存在，无需爬取")
        return 0

    print(f"\n正在爬取 {len(links_to_update)} 个岗位详情...")

    success_count = 0

    for i, link in enumerate(links_to_update, 1):
        print(f"\n[{i}/{len(links_to_update)}] {link}")

        detail = get_single_job_detail(dp, link)

        if detail is None:
            print(f"  [!] 可能需要登录或遇到验证码，自动等待检测...")
            retry_start = time.time()
            retry_timeout = 120
            retry_success = False

            while time.time() - retry_start < retry_timeout:
                elapsed = int(time.time() - retry_start)
                try:
                    dp.get('https://www.zhipin.com/web/geek/jobs')
                    if check_login_status(dp):
                        print(f"  [恢复] 登录状态恢复，重试获取详情...")
                        detail = get_single_job_detail(dp, link)
                        retry_success = True
                        break
                except:
                    pass
                if elapsed % 15 < 3:
                    print(f"  [等待恢复] {elapsed}s / {retry_timeout}s ...")

            if not retry_success:
                print(f"  [跳过] 等待超时，跳过此岗位详情")
                continue

        if detail:
            for row in rows:
                if row['link'] == link:
                    row['岗位要求和职责'] = detail['jd']
                    row['公司信息'] = detail['company_info']
                    row['HR活跃度'] = detail['hr_active']
                    row['HR在线'] = detail['hr_online']
                    row['HR职位'] = detail['hr_title']
                    break
            success_count += 1
            existing_links.add(link)
        else:
            print(f"  [FAIL] 获取失败")

        # 增量保存：每获取 5 个详情或最后一条时写入文件
        if success_count % 5 == 0 or i == len(links_to_update):
            try:
                with open(file_path, 'w', encoding=ENCODING, newline='') as f:
                    writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction='ignore')
                    writer.writeheader()
                    writer.writerows(rows)
            except Exception as save_err:
                print(f"  [警告] 增量保存失败: {save_err}")

        sleep_config.sleep('detail')  # 请求间隔（API直调模式下减至0.5s）

    # 最终保存
    try:
        with open(file_path, 'w', encoding=ENCODING, newline='') as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(rows)
    except Exception as final_save_err:
        print(f"  [警告] 最终保存失败: {final_save_err}")

    print(f"\n详情爬取完成！成功 {success_count} 条")
    return success_count


# ==================== 共享编排函数（消除重复） ====================

def execute_crawl_iteration(dp, positions, cities, is_custom, count_limit,
                            with_detail, filter_query=''):
    """
    执行爬取迭代（被 run_crawl_process 和 run_crawl_cli 共用）。

    Args:
        dp: WebPage 实例
        positions: [(pos_path, pos_code, pos_name), ...]
        cities: [(city_name, city_code), ...]
        is_custom: True=关键词搜索, False=列表模式
        count_limit: int or None
        with_detail: bool
        filter_query: URL 筛选参数字符串

    Returns:
        {'total': int, 'written': int, 'skipped': int}
    """
    total_stats = {'total': 0, 'written': 0, 'skipped': 0}
    detail_existing_links = set()

    for pos_path, pos_code, pos_name in positions:
        for city_name, city_code in cities:
            print(f"\n{'='*50}")
            print(f"正在爬取: {pos_name} - {city_name}")
            print(f"{'='*50}")

            if is_custom:
                file_path = os.path.join(ASSETS_DIR, 'post_data', 'custom', f"{pos_name}_{city_name}.csv")
            else:
                file_path = f"{pos_path}_{city_name}.csv"

            existing_links = load_existing_links(file_path)

            if is_custom:
                stats = crawl_jobs_by_query(dp, pos_name, city_code, file_path,
                                            count_limit, existing_links, filter_query)
            else:
                stats = crawl_jobs_by_position(dp, pos_code, city_code, file_path,
                                               count_limit, existing_links, filter_query)

            total_stats['total'] += stats[0]
            total_stats['written'] += stats[1]
            total_stats['skipped'] += stats[2]

            if with_detail and stats[1] > 0:
                detail_success = crawl_job_details(dp, file_path, detail_existing_links)
                print(f"\n详情爬取完成: 成功 {detail_success} 条")

    return total_stats


def print_crawl_summary(total_stats):
    """打印爬取完成统计（被 run_crawl_process 和 run_crawl_cli 共用）"""
    print_header("爬取完成")
    print(f"\n数据统计:")
    print(f"  获取数据: {total_stats['total']} 条")
    print(f"  写入数据: {total_stats['written']} 条")
    print(f"  跳过重复: {total_stats['skipped']} 条")
    time_stats.print_summary()


# ==================== 交互式爬取流程 ====================


def run_crawl_process():
    """执行完整的交互式爬取流程（支持返回上一步）"""
    from .config import sleep_config as sc
    from .data_loader import load_position_data, load_city_data
    from .menu import (
        show_position_mode_menu, input_custom_position, show_position_menu,
        show_city_menu, ask_crawl_count, ask_detail_option, ask_sleep_option,
        ask_filter_options, show_summary_and_confirm,
    )
    from .state import step_manager
    from .utils import build_filter_query_string

    # 重置状态
    step_manager.reset()
    time_stats.reset()
    step_manager.next_step()  # 进入步骤1

    # 1. 加载数据
    position_data = load_position_data()
    city_data = load_city_data()

    if not position_data:
        print("岗位数据加载失败，请先更新数据")
        return

    if not city_data['hot'] and not city_data['other']:
        print("城市数据加载失败，请先更新数据")
        return

    # 2-7. 交互式选择流程
    while True:
        mode = show_position_mode_menu()
        if mode == 'back':
            return

        if mode == 'custom':
            keywords = input_custom_position()
            if keywords == 'back':
                step_manager.go_back()
                continue
        else:
            positions = show_position_menu(position_data)
            if positions == 'back':
                step_manager.go_back()
                continue

        cities = show_city_menu(city_data)
        if cities == 'back':
            step_manager.go_back()
            continue

        count_limit = ask_crawl_count()
        if count_limit == 'back':
            step_manager.go_back()
            continue

        with_detail = ask_detail_option()
        if with_detail == 'back':
            step_manager.go_back()
            continue

        sleep_enabled = ask_sleep_option()
        if sleep_enabled == 'back':
            step_manager.go_back()
            continue

        filter_result = ask_filter_options()
        if filter_result == 'back':
            step_manager.go_back()
            continue

        result = show_summary_and_confirm()
        if result == 'back':
            step_manager.go_back()
            continue
        elif result == 'cancel':
            print("已取消爬取")
            return
        elif result == 'confirm':
            break

    # 获取最终选择
    selections = step_manager.selections
    positions = selections['positions']
    cities = selections['cities']
    count_limit = selections['count_limit']
    with_detail = selections['with_detail']
    sleep_enabled = selections['sleep_enabled']
    is_custom = selections['mode'] == 'custom'
    filter_params = selections.get('filter_params', {})
    filter_query = build_filter_query_string(filter_params)

    sc.set_enabled(sleep_enabled)

    # 8. 开始爬取
    print_header("开始爬取")
    time_stats.start()

    dp = WebPage(chromium_options=co)

    total_stats = execute_crawl_iteration(
        dp, positions, cities, is_custom, count_limit, with_detail, filter_query
    )

    dp.quit()
    time_stats.stop()
    print_crawl_summary(total_stats)


# ==================== CLI 模式爬取 ====================

def run_crawl_cli(args):
    """CLI模式：使用命令行参数直接爬取，无需交互"""
    from .config import sleep_config as sc
    from .data_loader import load_position_data, load_city_data, find_positions_by_name, find_cities_by_name
    from .utils import _expand_arg, build_filter_query_string, format_filter_display, resolve_filter_values

    time_stats.reset()

    # 1. 加载数据
    position_data = load_position_data()
    city_data = load_city_data()

    if not position_data:
        print("岗位数据加载失败，请先运行 --update-data 更新数据")
        return

    if not city_data['hot'] and not city_data['other']:
        print("城市数据加载失败，请先运行 --update-data 更新数据")
        return

    # 2. 展开参数
    pos_names = _expand_arg(args.positions)
    city_names = _expand_arg(args.cities)

    if not pos_names:
        print("错误: 请通过 --position/-p 指定至少一个岗位名称或搜索关键词")
        return

    if not city_names:
        print("错误: 请通过 --city/-c 指定至少一个城市")
        return

    # 3. 解析位置
    is_custom = args.mode == 'custom'

    if is_custom:
        positions = [(None, None, name) for name in pos_names]
    else:
        positions = find_positions_by_name(position_data, pos_names)

    if not positions:
        print("错误: 未匹配到任何岗位，请检查名称")
        return

    # 4. 解析城市
    cities = find_cities_by_name(city_data, city_names)
    if not cities:
        print("错误: 未匹配到任何城市，请检查名称")
        return

    # 5. 解析其他设置
    count_limit = args.count if args.count > 0 else None
    with_detail = args.detail
    sleep_enabled = not args.no_sleep

    sc.set_enabled(sleep_enabled)

    # 5.5. 解析筛选条件
    filter_params = {
        'jobType': resolve_filter_values(_expand_arg(args.job_types), 'jobType'),
        'salary': resolve_filter_values(_expand_arg(args.salaries), 'salary'),
        'experience': resolve_filter_values(_expand_arg(args.experiences), 'experience'),
        'degree': resolve_filter_values(_expand_arg(args.degrees), 'degree'),
        'scale': resolve_filter_values(_expand_arg(args.scales), 'scale'),
    }
    filter_query = build_filter_query_string(filter_params)

    # 6. 显示摘要
    total_items, total_time = estimate_time(positions, cities, count_limit, with_detail)

    print_header("爬取信息摘要")
    mode_text = "关键词搜索" if is_custom else "列表选择"
    print(f"\n  模式: {mode_text}")
    label = '关键词' if is_custom else '岗位'
    print(f"  {label}: {', '.join(p[2] for p in positions)}")
    print(f"  城市: {', '.join(c[0] for c in cities)}")
    print(f"  数量限制: {count_limit if count_limit else '全部'}")
    print(f"  爬取详情: {'是' if with_detail else '否'}")
    print(f"  请求等待: {'开启' if sleep_enabled else '关闭'}")
    filter_display = format_filter_display(filter_params)
    if filter_display != '不限':
        print(f"  筛选条件: {filter_display}")
    print(f"  预计数据量: 约 {total_items} 条")
    print(f"  预计耗时: 约 {max(1, math.ceil(total_time))} 分钟")

    if not args.yes:
        try:
            confirm = input("\n确认开始爬取? (y/n): ").strip().lower()
        except EOFError:
            print("\n无法读取输入，使用 -y 跳过确认")
            return
        if confirm != 'y':
            print("已取消爬取")
            return

    # 7. 打开浏览器并检测登录（单次检测，不轮询）
    print_header("打开浏览器")

    dp = WebPage(chromium_options=co)

    print("\n[登录检测] 正在打开 BOSS 直聘首页...")
    dp.get('https://www.zhipin.com/web/geek/jobs')

    if not check_login_status(dp):
        print("\n" + "=" * 50)
        print("  [LOGIN_NEEDED] 未检测到登录状态")
        print("=" * 50)
        print()
        print("  请先运行 python boss_post_interactive.py --ensure-login 完成登录。")
        print("  登录完成后，再次运行爬取命令即可。")
        print()
        print("=" * 50)
        dp.quit()
        return

    print("\n[OK] 登录状态已确认，开始爬取...")

    # 8. 开始爬取
    print_header("开始爬取")
    time_stats.start()

    total_stats = execute_crawl_iteration(
        dp, positions, cities, is_custom, count_limit, with_detail, filter_query
    )

    dp.quit()
    time_stats.stop()
    print_crawl_summary(total_stats)
