#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
BOSS直聘交互式岗位爬虫

支持命令行菜单选择、多选岗位/城市、自定义搜索、数量限制、
时间预估、登录检测、数据去重、详情爬取。
支持返回上一步重新选择、展示已选信息。

此文件为薄入口，实际逻辑已拆分至 boss_crawler/ 包。
"""
import os
import sys

_SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _SCRIPTS)

from boss_crawler import main

if __name__ == '__main__':
    # Windows 控制台是 GBK，爬虫的中文/emoji 进度输出会抛 UnicodeEncodeError
    # 并带崩整轮采集。在入口统一改掉，调用方不必每条命令加 PYTHONIOENCODING。
    for _stream in (sys.stdout, sys.stderr):
        _stream.reconfigure(encoding='utf-8', errors='replace')
    main()
