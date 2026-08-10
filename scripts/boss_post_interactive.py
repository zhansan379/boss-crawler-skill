#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
BOSS直聘交互式岗位爬虫

支持命令行菜单选择、多选岗位/城市、自定义搜索、数量限制、
时间预估、登录检测、数据去重、详情爬取。
支持返回上一步重新选择、展示已选信息。

此文件为薄入口，实际逻辑已拆分至 boss_crawler/ 包。
"""
from boss_crawler import main

if __name__ == '__main__':
    main()
