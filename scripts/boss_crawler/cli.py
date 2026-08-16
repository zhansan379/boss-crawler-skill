#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
命令行参数解析
"""

import argparse

from .utils import entry_cmd


def parse_args():
    """解析命令行参数"""
    # 示例里的路径按 cwd 算（entry_cmd），不写死。从仓库根目录跑 --help 时，写死的
    # `python boss_post_interactive.py …` 这七行没有一行粘得动 —— 而 docs/cli.md 恰恰
    # 是教人从根目录跑的。
    entry = entry_cmd()
    parser = argparse.ArgumentParser(
        description='BOSS直聘岗位爬取工具 - 支持命令行参数非交互式运行',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
示例:
  # 列出可用岗位
  {e} --list-positions

  # 列出可用城市
  {e} --list-cities

  # 更新数据
  {e} --update-data

  # 按关键词爬取（搜索模式）
  {e} -m custom -p "Python,数据分析" -c "北京,上海" -n 20 -d -y

  # 按岗位名称爬取（列表模式）
  {e} -m list -p "Python开发工程师" -c "北京" -n 10

  # 带筛选条件的精准搜索
  {e} -m custom -p "Python后端" -c "北京" -n 20 -e "3-5年" -deg "本科" -s "20-50K" -d -y

  # 交互式模式（不带参数或参数不完整时）
  {e}
        '''.format(e=entry)
    )

    # 查询类参数
    parser.add_argument('--update-data', '-u', action='store_true',
                        help='更新岗位和城市数据后退出')
    parser.add_argument('--ensure-login', action='store_true',
                        help='打开浏览器并确保登录 BOSS 直聘。自动检测登录状态（无需按回车），'
                             '登录状态持久化保存，后续爬取无需重复登录')
    parser.add_argument('--list-positions', action='store_true',
                        help='列出所有可用岗位名称后退出')
    parser.add_argument('--list-cities', action='store_true',
                        help='列出所有可用城市名称后退出')

    # 爬取参数
    parser.add_argument('--run-dir', dest='run_dir', default=None,
                        help='运行目录（assets/<时间戳>/）。传了就把整轮爬取耗时写进该目录的 '
                             'run_timings.jsonl，供 stage_timer.py report 排行；不传则不计时')
    parser.add_argument('--mode', '-m', choices=['list', 'custom'], default='list',
                        help='岗位选择模式: list=从岗位列表选择(默认), custom=关键词搜索')
    parser.add_argument('--position', '-p', action='append', dest='positions', default=None,
                        help='岗位名称(list模式)或搜索关键词(custom模式)，可多次指定或用逗号分隔')
    parser.add_argument('--city', '-c', action='append', dest='cities', default=None,
                        help='城市名称或代码，可多次指定或用逗号分隔，使用 "all" 选择全部城市')
    parser.add_argument('--count', '-n', type=int, default=0,
                        help='每个城市每个岗位的最大爬取数量（默认不限制）')
    parser.add_argument('--detail', '-d', action='store_true',
                        help='同时爬取岗位详情')
    parser.add_argument('--no-sleep', action='store_true',
                        help='关闭请求等待时间（默认开启）')
    parser.add_argument('--yes', '-y', action='store_true',
                        help='跳过确认，直接开始爬取')

    # 筛选参数
    parser.add_argument('--jobType', '-j', action='append', dest='job_types', default=None,
                        help='工作类型筛选，可多次指定或用逗号分隔。可选: 全职, 实习, 兼职')
    parser.add_argument('--salary', '-s', action='append', dest='salaries', default=None,
                        help='薪资范围筛选，可多次指定或用逗号分隔。可选: 3K以下, 3-5K, 5-10K, 10-20K, 20-50K, 50K以上')
    parser.add_argument('--experience', '-e', action='append', dest='experiences', default=None,
                        help='经验要求筛选，可多次指定或用逗号分隔。可选: 经验不限, 应届生, 1年以内, 1-3年, 3-5年, 5-10年, 10年以上, 在校生')
    parser.add_argument('--degree', '-deg', action='append', dest='degrees', default=None,
                        help='学历要求筛选，可多次指定或用逗号分隔。可选: 大专, 本科, 硕士, 博士, 高中, 中专/中技, 初中及以下')
    parser.add_argument('--scale', action='append', dest='scales', default=None,
                        help='公司规模筛选，可多次指定或用逗号分隔。可选: 0-20人, 20-99人, 100-499人, 500-999人, 1000-9999人, 10000人以上')

    args = parser.parse_args()
    return args
