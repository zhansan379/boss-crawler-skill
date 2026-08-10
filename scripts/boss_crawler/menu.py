#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
交互式菜单：主菜单、步骤选择、筛选设置、确认页面、数据展示
"""

import math

from .config import (
    FILTER_LABELS,
    FILTER_PARAM_NAMES,
    PER_PAGE,
    PER_PAGE_TIME,
    PER_DETAIL_TIME,
    sleep_config,
)
from .data_loader import load_position_data, load_city_data
from .state import step_manager
from .utils import (
    format_filter_display,
    parse_multi_selection,
    resolve_filter_values,
)


# ==================== 显示辅助 ====================

def print_header(title):
    """打印标题头"""
    print("\n" + "=" * 50)
    print(f"  {title}")
    print("=" * 50)


def print_step_hint(step_num, total_steps=7):
    """打印步骤提示"""
    print(f"\n[步骤 {step_num}/{total_steps}]", end=" ")


# ==================== 数据展示命令 ====================

def list_all_positions():
    """打印所有可用岗位"""
    position_data = load_position_data()
    if not position_data:
        print("岗位数据加载失败")
        return

    print("\n可用岗位列表:")
    print("=" * 60)
    for cat_name, sub_categories in position_data.items():
        print(f"\n【{cat_name}】")
        for sub_cat in sub_categories:
            for pos in sub_cat['positions']:
                print(f"  {pos['name']}  (code: {pos['code']})")


def list_all_cities():
    """打印所有可用城市"""
    city_data = load_city_data()
    if not city_data['hot'] and not city_data['other']:
        print("城市数据加载失败")
        return

    all_cities = city_data['hot'] + city_data['other']
    print(f"\n可用城市列表（共 {len(all_cities)} 个）:")
    print("=" * 60)
    print("\n【热门城市】")
    for c in city_data['hot']:
        print(f"  {c['name']}  (code: {c['code']})")
    print(f"\n【其他城市】（共 {len(city_data['other'])} 个）")
    for c in city_data['other']:
        print(f"  {c['name']}  (code: {c['code']})")


# ==================== 主菜单 ====================

def show_main_menu():
    """显示主菜单"""
    print_header("BOSS直聘岗位爬取工具")

    print("\n请选择操作:")
    print("  1. 开始爬取岗位数据")
    print("  2. 更新岗位和城市数据")
    print("  3. 退出")

    while True:
        choice = input("\n> ").strip()
        if choice in ['1', '2', '3']:
            return int(choice)
        print("请输入有效选项 (1-3)")


# ==================== 步骤 1：岗位模式 ====================

def show_position_mode_menu():
    """
    选择岗位模式
    返回: 'list', 'custom' 或 'back'
    """
    while True:
        print_step_hint(1)
        print_header("岗位选择模式")
        step_manager.show_selections()

        print("请选择岗位输入方式:")
        print("  1. 从岗位列表选择")
        print("  2. 自定义输入岗位名称（搜索模式）")
        print("\n  b. 返回上一步")

        choice = input("\n> ").strip().lower()

        if choice == '1':
            step_manager.selections['mode'] = 'list'
            step_manager.next_step()
            return 'list'
        elif choice == '2':
            step_manager.selections['mode'] = 'custom'
            step_manager.next_step()
            return 'custom'
        elif choice in ['b', 'back']:
            return 'back'
        else:
            print("请输入有效选项 (1-2 或 b)")


# ==================== 步骤 2a：自定义关键词 ====================

def input_custom_position():
    """
    自定义输入岗位名称
    返回: [关键词列表] 或 'back'
    """
    while True:
        print_step_hint(2)
        print_header("自定义岗位关键词")
        step_manager.show_selections()

        print("请输入岗位名称关键词 (可多个，用逗号分隔):")
        print("示例: agent,数据分析,AI工程师")
        print("\n  b. 返回上一步")

        user_input = input("\n> ").strip()

        if user_input.lower() in ['b', 'back']:
            step_manager.go_back()
            return 'back'

        if user_input:
            keywords = [k.strip() for k in user_input.split(',') if k.strip()]
            if keywords:
                step_manager.selections['positions'] = [(None, None, k) for k in keywords]
                step_manager.next_step()
                return keywords

        print("请输入至少一个关键词")


# ==================== 步骤 2b：分层岗位选择 ====================

def show_position_menu(position_data):
    """
    分层菜单选择岗位
    返回: [(岗位列表)] 或 'back'
    """
    while True:
        # 第一层：选择大类
        print_step_hint(2)
        print_header("岗位选择 - 大类")
        step_manager.show_selections()

        categories = list(position_data.keys())
        for i, cat in enumerate(categories, 1):
            print(f"  {i}. {cat}")

        print(f"\n可多选，用逗号分隔，如 1,3,5 或 1-3 或 all")
        print("  b. 返回上一步")

        input_str = input("\n> ").strip().lower()

        if input_str in ['b', 'back']:
            step_manager.go_back()
            return 'back'

        selected_indices = parse_multi_selection(input_str, len(categories))
        if not selected_indices:
            print("请输入有效选项")
            continue

        selected_categories = [categories[i-1] for i in selected_indices]

        # 第二层：选择子类
        print_step_hint(2)
        print_header("岗位选择 - 子类")
        step_manager.show_selections()

        all_sub_categories = []
        sub_cat_map = {}

        for cat in selected_categories:
            for sub_cat in position_data[cat]:
                sub_cat_map[sub_cat['name']] = cat
                all_sub_categories.append(sub_cat)

        for i, sub_cat in enumerate(all_sub_categories, 1):
            print(f"  {i}. {sub_cat['name']} ({sub_cat_map[sub_cat['name']]})")

        print(f"\n可多选，用逗号分隔，如 1,3,5 或 1-3 或 all")
        print("  b. 返回上一步")

        input_str = input("\n> ").strip().lower()

        if input_str in ['b', 'back']:
            continue  # 返回大类选择

        selected_indices = parse_multi_selection(input_str, len(all_sub_categories))
        if not selected_indices:
            print("请输入有效选项")
            continue

        selected_sub_cats = [all_sub_categories[i-1] for i in selected_indices]

        # 第三层：选择具体岗位
        print_step_hint(2)
        print_header("岗位选择 - 具体岗位")
        step_manager.show_selections()

        all_positions = []
        for sub_cat in selected_sub_cats:
            for pos in sub_cat['positions']:
                all_positions.append({
                    'name': pos['name'],
                    'code': pos['code'],
                    'path': pos['path'],
                    'sub_cat': sub_cat['name']
                })

        for i, pos in enumerate(all_positions, 1):
            print(f"  {i}. {pos['name']} ({pos['sub_cat']})")

        print(f"\n可多选，用逗号分隔，如 1,3,5 或 1-3 或 all")
        print("  b. 返回上一步")

        input_str = input("\n> ").strip().lower()

        if input_str in ['b', 'back']:
            continue  # 返回子类选择

        selected_indices = parse_multi_selection(input_str, len(all_positions))
        if not selected_indices:
            print("请输入有效选项")
            continue

        selected_positions = [all_positions[i-1] for i in selected_indices]
        positions = [(p['path'], p['code'], p['name']) for p in selected_positions]

        step_manager.selections['positions'] = positions
        step_manager.next_step()
        return positions


# ==================== 步骤 3：城市选择 ====================

def show_city_menu(city_data):
    """
    选择城市
    返回: [(城市列表)] 或 'back'
    """
    while True:
        print_step_hint(3)
        print_header("城市选择")
        step_manager.show_selections()

        all_cities = city_data['hot'] + city_data['other']

        print("\n热门城市:")
        hot_count = len(city_data['hot'])
        for i, city in enumerate(city_data['hot'], 1):
            print(f"  {i}. {city['name']}", end="")
            if i % 4 == 0:
                print()
        if hot_count % 4 != 0:
            print()

        print("\n其他城市:")
        other_display = city_data['other'][:20]
        for i, city in enumerate(other_display, hot_count + 1):
            print(f"  {i}. {city['name']}", end="")
            if (i - hot_count) % 4 == 0:
                print()
        if len(other_display) % 4 != 0:
            print()

        if len(city_data['other']) > 20:
            print(f"  ... 还有 {len(city_data['other']) - 20} 个城市")

        print(f"\n可多选，用逗号分隔，如 1,3,5 或 1-3 或 all")
        print("  b. 返回上一步")

        input_str = input("\n> ").strip().lower()

        if input_str in ['b', 'back']:
            step_manager.go_back()
            return 'back'

        selected_indices = parse_multi_selection(input_str, len(all_cities))
        if not selected_indices:
            print("请输入有效选项")
            continue

        selected_cities = [all_cities[i-1] for i in selected_indices]
        cities = [(c['name'], c['code']) for c in selected_cities]

        step_manager.selections['cities'] = cities
        step_manager.next_step()
        return cities


# ==================== 步骤 4：爬取数量 ====================

def ask_crawl_count():
    """
    询问爬取数量
    返回: 数量限制 或 'back'
    """
    while True:
        print_step_hint(4)
        print_header("爬取数量")
        step_manager.show_selections()

        print("\n请选择爬取数量:")
        print("  1. 全部爬取")
        print("  2. 每个城市每个岗位爬取指定数量")
        print("\n  b. 返回上一步")

        choice = input("\n> ").strip().lower()

        if choice in ['b', 'back']:
            step_manager.go_back()
            return 'back'

        if choice == '1':
            step_manager.selections['count_limit'] = None
            step_manager.next_step()
            return None
        elif choice == '2':
            while True:
                print("\n请输入每个城市每个岗位的最大数量:")
                print("  b. 返回上一步")
                user_input = input("\n> ").strip().lower()

                if user_input in ['b', 'back']:
                    break

                try:
                    count = int(user_input)
                    if count > 0:
                        step_manager.selections['count_limit'] = count
                        step_manager.next_step()
                        return count
                    print("请输入大于0的数字")
                except ValueError:
                    print("请输入有效数字")
        else:
            print("请输入有效选项 (1-2 或 b)")


# ==================== 步骤 5：详情选项 ====================

def ask_detail_option():
    """
    询问是否爬取详情
    返回: True/False 或 'back'
    """
    while True:
        print_step_hint(5)
        print_header("爬取选项")
        step_manager.show_selections()

        print("\n是否同时爬取岗位详情？")
        print("  1. 是 - 爬取详情页（较慢，数据完整）")
        print("  2. 否 - 仅爬取列表页（较快）")
        print("\n  b. 返回上一步")

        choice = input("\n> ").strip().lower()

        if choice in ['b', 'back']:
            step_manager.go_back()
            return 'back'

        if choice == '1':
            step_manager.selections['with_detail'] = True
            return True
        elif choice == '2':
            step_manager.selections['with_detail'] = False
            return False
        else:
            print("请输入有效选项 (1-2 或 b)")


# ==================== 步骤 6：sleep 等待配置 ====================

def ask_sleep_option():
    """
    询问是否开启sleep等待
    返回: True/False 或 'back'
    """
    while True:
        print_step_hint(6)
        print_header("等待时间配置")
        step_manager.show_selections()

        print("\n是否开启请求等待时间？（防止被封IP）")
        print("  1. 开启 - 每次请求后等待，更安全（推荐）")
        print("  2. 关闭 - 无等待，速度更快（风险较高）")
        print("\n  b. 返回上一步")

        choice = input("\n> ").strip().lower()

        if choice in ['b', 'back']:
            step_manager.go_back()
            return 'back'

        if choice == '1':
            step_manager.selections['sleep_enabled'] = True
            sleep_config.set_enabled(True)
            return True
        elif choice == '2':
            step_manager.selections['sleep_enabled'] = False
            sleep_config.set_enabled(False)
            return False
        else:
            print("请输入有效选项 (1-2 或 b)")


# ==================== 步骤 7：筛选条件 ====================

def ask_filter_options():
    """
    高级筛选条件设置（可选步骤）
    返回: 'confirm' 或 'back'
    """
    filter_keys = ['jobType', 'salary', 'experience', 'degree', 'scale']

    while True:
        print_step_hint(7)
        print_header("筛选条件设置（可选）")
        step_manager.show_selections()

        current = step_manager.selections['filter_params']

        for i, key in enumerate(filter_keys, 1):
            values = current.get(key, [])
            if values:
                label_map = FILTER_LABELS.get(key, {})
                labels = [label_map.get(v, v) for v in values]
                display = f'{FILTER_PARAM_NAMES[key]}: {",".join(labels)}'
            else:
                display = f'{FILTER_PARAM_NAMES[key]}: 不限'
            print(f"  {i}. {display}")

        print("  y. 确认（跳过筛选）")
        print("  b. 返回上一步")

        choice = input("\n> ").strip().lower()

        if choice in ['b', 'back']:
            step_manager.go_back()
            return 'back'

        if choice == 'y':
            step_manager.next_step()
            return 'confirm'

        if choice in ['1', '2', '3', '4', '5']:
            key = filter_keys[int(choice) - 1]
            label_map = FILTER_LABELS.get(key, {})
            print(f"\n  {FILTER_PARAM_NAMES[key]} 可选值（逗号分隔多选，直接回车=不限）:")
            codes = []
            for code, label in label_map.items():
                codes.append((code, label))
                print(f"    {code} - {label}")
            print(f"  当前: {', '.join(label_map.get(v, v) for v in current.get(key, [])) or '不限'}")
            print("  b. 返回筛选菜单")

            sub_choice = input("\n> ").strip()
            if sub_choice.lower() in ['b', 'back']:
                continue

            if sub_choice == '':
                current[key] = []
            else:
                raw_values = [v.strip() for v in sub_choice.split(',') if v.strip()]
                current[key] = resolve_filter_values(raw_values, key)
        else:
            print("请输入有效选项 (1-5, y 或 b)")


# ==================== 摘要与确认 ====================

def estimate_time(positions, cities, count_limit, with_detail):
    """预估爬取时间"""
    total_positions = len(positions)
    total_cities = len(cities)

    if count_limit:
        total_items = count_limit * total_positions * total_cities
    else:
        total_items = 300 * total_positions * total_cities

    list_pages = math.ceil(total_items / PER_PAGE)
    list_time = list_pages * PER_PAGE_TIME

    if with_detail:
        detail_time = total_items * PER_DETAIL_TIME
    else:
        detail_time = 0

    total_time = (list_time + detail_time) / 60

    return total_items, total_time


def show_summary_and_confirm():
    """
    显示爬取摘要并确认
    返回: 'confirm', 'back', 'cancel'
    """
    selections = step_manager.selections
    positions = selections['positions']
    cities = selections['cities']
    count_limit = selections['count_limit']
    with_detail = selections['with_detail']
    sleep_enabled = selections['sleep_enabled']
    is_custom = selections['mode'] == 'custom'

    while True:
        print_header("预估爬取信息")

        total_items, total_time = estimate_time(positions, cities, count_limit, with_detail)

        list_time = math.ceil(total_items / PER_PAGE) * PER_PAGE_TIME / 60
        detail_time = total_items * PER_DETAIL_TIME / 60 if with_detail else 0

        print(f"\n  {'关键词' if is_custom else '岗位'}数量: {len(positions)} 个")
        print(f"  城市数量: {len(cities)} 个")
        if count_limit:
            print(f"  每城市每{'关键词' if is_custom else '岗位'}最大数量: {count_limit} 条")
        else:
            print(f"  爬取数量: 全部")
        print(f"  是否爬取详情: {'是' if with_detail else '否'}")
        print(f"  等待时间: {'开启' if sleep_enabled else '关闭'}")
        filter_display = format_filter_display(step_manager.selections.get('filter_params', {}))
        if filter_display != '不限':
            print(f"  筛选条件: {filter_display}")
        print(f"  预计总数据量: 约 {total_items} 条")
        print(f"  预计耗时: 约 {math.ceil(total_time)} 分钟")
        if with_detail:
            print(f"    （列表 {math.ceil(list_time)} 分钟 + 详情 {math.ceil(detail_time)} 分钟）")

        print("\n请选择:")
        print("  y. 确认开始爬取")
        print("  b. 返回上一步修改")
        print("  c. 取消爬取")

        choice = input("\n> ").strip().lower()

        if choice == 'y':
            return 'confirm'
        elif choice == 'b':
            step_manager.go_back()
            return 'back'
        elif choice == 'c':
            return 'cancel'
        else:
            print("请输入 y, b 或 c")
