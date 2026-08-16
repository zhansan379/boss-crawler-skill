#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
常量定义、筛选参数映射、Chrome 配置、Sleep 配置
"""

import os
import random
import time

from DrissionPage import ChromiumOptions

# 尝试导入Windows键盘检测模块
try:
    import msvcrt
    IS_WINDOWS = True
except ImportError:
    IS_WINDOWS = False

# ==================== 常量 ====================

PER_PAGE = 15  # 每页数据条数
PER_PAGE_TIME = 3  # 每页耗时（秒）
PER_DETAIL_TIME = 5  # 每条详情耗时（秒）
WAIT_TIMEOUT = 300  # 等待用户操作超时（秒）
ENCODING = 'utf-8-sig'  # CSV编码

# 同义关键词的提前退出阈值。
#
# 由来：2026-08-14 复盘一次太原的实跑 —— 5 个关键词爬出 117 行，只有 53 个唯一
# link，54% 是重复。小城市的岗位池就那么大，「AI应用开发」和「大模型应用开发」
# 命中的基本是同一批岗，第 4、5 个关键词几乎整页都是本轮已经采过的。
#
# 判据只数「本轮其他关键词已见过」的那部分（run_dups），不数「旧文件里已有」的。
# 否则续爬会被打断：某文件已有 200 行时，同一关键词的第 1 页 100% 命中旧数据，
# 按总重复率就会立刻 break，再也翻不到后面的新岗位。
DUP_PAGE_RATIO = 0.8  # 单页重复率达到这个比例就跳到下一个关键词
DUP_PAGE_MIN_LINKS = 5  # 少于这么多条的尾页不参与判定，避免误导性的跳过日志

# ==================== 筛选参数映射 ====================

# 工作类型
JOB_TYPE_MAP = {
    '全职': '1901', '1901': '1901',
    '实习': '1902', '1902': '1902',
    '兼职': '1903', '1903': '1903',
}

# 薪资范围
SALARY_MAP = {
    '3K以下': '402', '402': '402',
    '3-5K': '403', '403': '403',
    '5-10K': '404', '404': '404',
    '10-20K': '405', '405': '405',
    '20-50K': '406', '406': '406',
    '50K以上': '407', '407': '407',
}

# 经验要求
EXPERIENCE_MAP = {
    '在校生': '108', '108': '108',
    '应届生': '102', '102': '102',
    '经验不限': '101', '101': '101',
    '1年以内': '103', '103': '103',
    '1-3年': '104', '104': '104',
    '3-5年': '105', '105': '105',
    '5-10年': '106', '106': '106',
    '10年以上': '107', '107': '107',
}

# 学历要求
DEGREE_MAP = {
    '初中及以下': '209', '209': '209',
    '中专/中技': '208', '208': '208',
    '高中': '206', '206': '206',
    '大专': '202', '202': '202',
    '本科': '203', '203': '203',
    '硕士': '204', '204': '204',
    '博士': '205', '205': '205',
}

# 公司规模
SCALE_MAP = {
    '0-20人': '301', '301': '301',
    '20-99人': '302', '302': '302',
    '100-499人': '303', '303': '303',
    '500-999人': '304', '304': '304',
    '1000-9999人': '305', '305': '305',
    '10000人以上': '306', '306': '306',
}

# 聚合映射: filter_key -> 选项 map
FILTER_MAPS = {
    'jobType': JOB_TYPE_MAP,
    'salary': SALARY_MAP,
    'experience': EXPERIENCE_MAP,
    'degree': DEGREE_MAP,
    'scale': SCALE_MAP,
}

# 筛选参数中文名
FILTER_PARAM_NAMES = {
    'jobType': '工作类型',
    'salary': '薪资范围',
    'experience': '经验要求',
    'degree': '学历要求',
    'scale': '公司规模',
}

# 仅供展示用: filter_key -> {code: label}
FILTER_LABELS = {
    'jobType': {'1901': '全职', '1902': '实习', '1903': '兼职'},
    'salary': {'402': '3K以下', '403': '3-5K', '404': '5-10K', '405': '10-20K', '406': '20-50K', '407': '50K以上'},
    'experience': {'108': '在校生', '102': '应届生', '101': '经验不限', '103': '1年以内', '104': '1-3年', '105': '3-5年', '106': '5-10年', '107': '10年以上'},
    'degree': {'209': '初中及以下', '208': '中专/中技', '206': '高中', '202': '大专', '203': '本科', '204': '硕士', '205': '博士'},
    'scale': {'301': '0-20人', '302': '20-99人', '303': '100-499人', '304': '500-999人', '305': '1000-9999人', '306': '10000人以上'},
}

# CSV字段
# 末尾 5 列均来自详情 API（无 -d 时为空）。新增列只能追加在末尾：
# init_csv_file 会按此顺序迁移旧文件的表头。
CSV_FIELDS = [
    'link', '职位', '城市', '区域', '商圈', '地址', '公司', '薪资', '经验', '学历',
    '领域', '性质', '规模', '技能标签', '福利标签', '位置', '岗位要求和职责', '公司信息',
    '已失效', '代招',
    'HR活跃度', 'HR在线', 'HR职位', 'HR姓名', 'HR公司'
]

# ==================== 路径配置 ====================

# 技能根目录: scripts/boss_crawler/config.py → 上溯 3 层到 boss-crawler/
_SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ASSETS_DIR = os.path.join(_SKILL_ROOT, 'assets')

# ==================== Chrome 配置 ====================

co = ChromiumOptions()
# 不手动指定浏览器路径，让 DrissionPage 自动查找 Chrome

# 显式启用图片加载
co.no_imgs(False)

# 持久化 Chrome 用户数据目录，使登录状态跨脚本运行保留
USER_DATA_DIR = os.path.join(ASSETS_DIR, 'chrome_user_data')
os.makedirs(USER_DATA_DIR, exist_ok=True)
co.set_argument('--user-data-dir', USER_DATA_DIR)

# 隐藏自动化标识，避免网站限制
co.set_argument('--disable-blink-features=AutomationControlled')

# ==================== Sleep 配置 ====================


class SleepConfig:
    """Sleep时间配置管理

    风控对抗的核心是**随机抖动**：固定间隔（哪怕隔得久）是机器指纹，BOSS 直聘大约
    200 个详情就触发风控。这里的真实等待 = base + uniform(0, jitter)，节奏不可预测
    才像人。详情页串行爬，base 不能太低——0.5s 是当初 API 直调时代的残留，现在走
    浏览器必须让出来。
    """
    def __init__(self):
        self.enabled = True  # 是否开启sleep等待
        self.page_interval = 3  # 列表页之间的基础等待（秒）
        self.detail_interval = 3  # 详情页之间的基础等待（秒）——走浏览器，0.5s 会触发风控
        self.scroll_wait = 2  # 滚动后的等待时间（秒）
        self.login_wait = 2  # 登录检测后的等待时间（秒）
        self.retry_wait = 2  # 重试等待时间（秒）
        self.api_wait = 5  # API响应等待时间（秒）
        self.jitter = 2.0  # 随机抖动上限（秒）：真实等待 = base + uniform(0, jitter)
        self.detail_batch = 30  # 每 N 个详情请求后进入一次分批冷却
        self.batch_cooldown = 30  # 分批冷却时长（秒）

    def sleep(self, sleep_type='page'):
        """
        根据配置执行sleep（带随机抖动）
        sleep_type: 'page', 'detail', 'scroll', 'login', 'retry', 'api', 'batch'
        """
        if not self.enabled:
            return

        intervals = {
            'page': self.page_interval,
            'detail': self.detail_interval,
            'scroll': self.scroll_wait,
            'login': self.login_wait,
            'retry': self.retry_wait,
            'api': self.api_wait,
            'batch': self.batch_cooldown,
        }

        base = intervals.get(sleep_type, 2)
        time.sleep(base + random.uniform(0, self.jitter))

    def set_enabled(self, enabled):
        """设置是否开启sleep"""
        self.enabled = enabled

    def set_intervals(self, page=None, detail=None, scroll=None, login=None, retry=None, api=None):
        """设置各类型等待时间"""
        if page is not None:
            self.page_interval = page
        if detail is not None:
            self.detail_interval = detail
        if scroll is not None:
            self.scroll_wait = scroll
        if login is not None:
            self.login_wait = login
        if retry is not None:
            self.retry_wait = retry
        if api is not None:
            self.api_wait = api


# 全局Sleep配置
sleep_config = SleepConfig()
