#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
BOSS直聘岗位爬虫包

提供交互式和命令行两种爬取模式，支持按岗位/关键词搜索、
城市筛选、高级筛选条件、详情爬取等功能。
"""

from contextlib import contextmanager

# ── 配置与常量
from .config import (
    PER_PAGE,
    PER_PAGE_TIME,
    PER_DETAIL_TIME,
    WAIT_TIMEOUT,
    ENCODING,
    DUP_PAGE_RATIO,
    DUP_PAGE_MIN_LINKS,
    IS_WINDOWS,
    JOB_TYPE_MAP,
    SALARY_MAP,
    EXPERIENCE_MAP,
    DEGREE_MAP,
    SCALE_MAP,
    FILTER_MAPS,
    FILTER_PARAM_NAMES,
    FILTER_LABELS,
    CSV_FIELDS,
    co,
    USER_DATA_DIR,
    ASSETS_DIR,
    SleepConfig,
    sleep_config,
)

# ── 工具函数
from .utils import (
    _expand_arg,
    resolve_filter_values,
    build_filter_query_string,
    format_filter_display,
    detect_encoding,
    ensure_output_dir,
    parse_multi_selection,
    estimate_time,
)

# ── 数据加载
from .data_loader import (
    load_position_data,
    load_city_data,
    update_json_data,
    find_positions_by_name,
    find_cities_by_name,
    load_existing_links,
    init_csv_file,
)

# ── 登录与页面检测
from .auth import (
    ensure_login,
    check_login_elements,
    check_login_status,
    check_page_status,
    wait_for_user_action,
)

# ── CLI 参数解析
from .cli import parse_args

# ── 状态管理
from .state import (
    TimeStats,
    time_stats,
    StepManager,
    step_manager,
)

# ── 交互菜单
from .menu import (
    print_header,
    print_step_hint,
    show_main_menu,
    show_position_mode_menu,
    input_custom_position,
    show_position_menu,
    show_city_menu,
    ask_crawl_count,
    ask_detail_option,
    ask_sleep_option,
    ask_filter_options,
    show_summary_and_confirm,
    list_all_positions,
    list_all_cities,
)

# ── 爬取引擎
from .crawler import (
    process_job_list,
    should_skip_remaining_pages,
    crawl_jobs_by_position,
    crawl_jobs_by_query,
    get_single_job_detail,
    crawl_job_details,
    execute_crawl_iteration,
    print_crawl_summary,
    run_crawl_process,
    run_crawl_cli,
)

# ── 入口
from .cli import parse_args
from .auth import ensure_login
from .crawler import run_crawl_process, run_crawl_cli
from .data_loader import update_json_data
from .menu import list_all_positions, list_all_cities

# ── 计时埋点
# 埋点绝不能因为自己失败而带崩爬取，所以导入和调用两层都兜住。
# scripts/ 通常已在 sys.path（入口是 scripts/boss_post_interactive.py），
# 换个 cwd 或被别处 import 时兜底补一次路径。
try:
    import stage_timer
except ImportError:                                          # pragma: no cover
    import os as _os
    import sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    try:
        import stage_timer
    except ImportError:
        stage_timer = None


@contextmanager
def _crawl_timing(run_dir, name='crawl'):
    """给整轮爬取计时。

    没传 --run-dir 或埋点模块缺失时退化成空操作 —— 计时是诊断手段，不该成为
    爬取的前置条件。stage_timer.stage 内部已经保证异常照抛、但落盘 status=error，
    所以「爬到一半崩了」也能在 report 里看见耗时。
    """
    if not run_dir or stage_timer is None:
        yield
        return
    with stage_timer.stage(run_dir, name):
        yield


def main():
    """主入口函数"""
    args = parse_args()
    run_dir = getattr(args, 'run_dir', None)

    # 处理查询类命令
    if args.update_data:
        update_json_data()
        return

    if args.ensure_login:
        ensure_login()
        return

    if args.list_positions:
        list_all_positions()
        return

    if args.list_cities:
        list_all_cities()
        return

    # 如果提供了岗位和城市参数，走 CLI 模式
    if args.positions and args.cities:
        with _crawl_timing(run_dir):
            run_crawl_cli(args)
        return

    # 否则走原有交互式流程
    while True:
        choice = show_main_menu()

        if choice == 1:
            with _crawl_timing(run_dir):
                run_crawl_process()
        elif choice == 2:
            update_json_data()
        elif choice == 3:
            print("\n再见！")
            break

        print("\n" + "-" * 50)


__all__ = [
    # Config
    "PER_PAGE", "PER_PAGE_TIME", "PER_DETAIL_TIME", "WAIT_TIMEOUT",
    "ENCODING", "IS_WINDOWS",
    "DUP_PAGE_RATIO", "DUP_PAGE_MIN_LINKS",
    "JOB_TYPE_MAP", "SALARY_MAP", "EXPERIENCE_MAP", "DEGREE_MAP", "SCALE_MAP",
    "FILTER_MAPS", "FILTER_PARAM_NAMES", "FILTER_LABELS",
    "CSV_FIELDS", "co", "USER_DATA_DIR", "ASSETS_DIR",
    "SleepConfig", "sleep_config",
    # Utils
    "_expand_arg", "resolve_filter_values", "build_filter_query_string",
    "format_filter_display", "detect_encoding", "ensure_output_dir",
    "parse_multi_selection", "estimate_time",
    # Data loader
    "load_position_data", "load_city_data", "update_json_data",
    "find_positions_by_name", "find_cities_by_name",
    "load_existing_links", "init_csv_file",
    # Auth
    "ensure_login", "check_login_elements", "check_login_status",
    "check_page_status", "wait_for_user_action",
    # CLI
    "parse_args",
    # State
    "TimeStats", "time_stats", "StepManager", "step_manager",
    # Menu
    "print_header", "print_step_hint", "show_main_menu",
    "show_position_mode_menu", "input_custom_position",
    "show_position_menu", "show_city_menu", "ask_crawl_count",
    "ask_detail_option", "ask_sleep_option", "ask_filter_options",
    "show_summary_and_confirm", "list_all_positions", "list_all_cities",
    # Crawler
    "process_job_list", "should_skip_remaining_pages",
    "crawl_jobs_by_position", "crawl_jobs_by_query",
    "get_single_job_detail", "crawl_job_details",
    "execute_crawl_iteration", "print_crawl_summary",
    "run_crawl_process", "run_crawl_cli",
    # Entry
    "main",
]
