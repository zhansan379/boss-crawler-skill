#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
爬取/匹配参数预设 —— assets/preferences.json 的读写。

为什么要有这个文件：
2026-08-14 复盘一次实跑：Stage 1 加两道关占了 12 分钟（全程 30%），大部分不是
计算而是交互往返 —— 全程停了 6 次问用户。其中「城市/关键词/学历/模式/TopN」
这几项对同一个人来说几乎不变，每轮重问一遍纯属浪费。存成预设后，回访用户
Stage 1 一次不用问，这是回访路径上最大的一笔省时。

设计取舍（2026-08-14 与用户确认）：
  只存一份，覆盖式更新   —— 一个人找工作时的城市和关键词基本稳定，多份的成本
                            （每轮先决定用哪份）会把省下的那一轮又加回来。
  永不过期，只报年龄     —— show 里打印「74 天前存的」，由用户自己决定要不要
                            打断。不做自动失效。

安全边界（重要，别删）：
预设里永远没有「投递范围」「招呼语」「是否发送图片」这三类字段，save 的 CLI
也不提供对应 flag。preferences.json 是用户能手改的文件，所以 load() 丢弃白名单
外的一切键 —— 否则往文件里塞一个 "auto_apply": true 就成了绕过 7g 发送前确认的
配置开关。7g 不读任何配置文件，这一条由 test_preferences.py 锁住。

用法：
  python scripts/preferences.py show          # 有预设 → 打印 + 退出 0；没有 → 退出 1
  python scripts/preferences.py save --city 太原 --keywords "AI应用开发,Python" \
        --match-mode deep --top 10 --degree 本科 --count 20
  python scripts/preferences.py crawl-args    # 打印 boss_post_interactive.py 的完整命令
  python scripts/preferences.py clear
"""

import argparse
import json
import os
import sys
from datetime import datetime, date

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _assets_dir():
    """assets 目录。测试用 BOSS_SKILL_ASSETS 顶掉，避免碰真实配置。"""
    return os.environ.get('BOSS_SKILL_ASSETS') or os.path.join(SKILL_ROOT, 'assets')


def prefs_path():
    return os.path.join(_assets_dir(), 'preferences.json')


# ==================== 白名单 ====================

# 三组允许的键。不在这里的键会被 load() 丢弃 —— 见模块开头的「安全边界」。
LIST_KEYS = ('keywords', 'cities', 'degree', 'experience', 'salary', 'job_type', 'scale')
INT_KEYS = ('count', 'top_n', 'min_count')
STR_KEYS = ('mode', 'match_mode')
BOOL_KEYS = ('detail',)
META_KEYS = ('saved_at', 'version')

ALLOWED_KEYS = LIST_KEYS + INT_KEYS + STR_KEYS + BOOL_KEYS + META_KEYS

SCHEMA_VERSION = 1

# 中文标签，只用于 show 的输出
LABELS = {
    'cities': '城市', 'keywords': '关键词', 'mode': '爬取模式', 'count': '每关键词条数',
    'detail': '爬详情', 'match_mode': '匹配模式', 'top_n': 'TopN', 'min_count': '最低岗位数',
    'degree': '学历', 'experience': '经验', 'salary': '薪资',
    'job_type': '工作类型', 'scale': '公司规模',
}


def _split(value):
    """"a,b" 或 ["a","b"] → ["a", "b"]，去空去空白。"""
    if value is None:
        return []
    if isinstance(value, str):
        parts = value.split(',')
    elif isinstance(value, (list, tuple)):
        parts = []
        for item in value:
            parts.extend(str(item).split(','))
    else:
        parts = [str(value)]
    return [p.strip() for p in parts if p.strip()]


def _coerce(data):
    """把任意来源的 dict 收敛成白名单内、类型正确的预设。

    未知键直接丢弃（不报错、不透传）。类型不对的值当作缺失，
    因为这文件是用户手改得到的，宁可少一个字段也不要把脏值带进爬取命令。
    """
    out = {}

    for key in LIST_KEYS:
        values = _split(data.get(key))
        if values:
            out[key] = values

    for key in INT_KEYS:
        raw = data.get(key)
        if raw is None or raw == '':
            continue
        try:
            number = int(raw)
        except (TypeError, ValueError):
            continue
        if number > 0:
            out[key] = number

    for key in STR_KEYS:
        raw = data.get(key)
        if isinstance(raw, str) and raw.strip():
            out[key] = raw.strip()

    for key in BOOL_KEYS:
        raw = data.get(key)
        if isinstance(raw, bool):
            out[key] = raw

    for key in META_KEYS:
        raw = data.get(key)
        if raw not in (None, ''):
            out[key] = raw

    return out


# ==================== 读写 ====================

def load():
    """读预设。没有、读不动、或损坏 → 返回 {}（当作「没有预设」，不抛异常）。

    损坏也返回 {} 是刻意的：预设是为了省一轮提问，它自己坏掉时正确的降级是
    「回去问用户」，而不是把整轮跑崩。
    """
    path = prefs_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    return _coerce(data)


def save(**kwargs):
    """写预设（覆盖式）。返回落盘后的 dict。"""
    prefs = _coerce(kwargs)
    prefs['saved_at'] = date.today().isoformat()
    prefs['version'] = SCHEMA_VERSION

    path = prefs_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(prefs, f, ensure_ascii=False, indent=2)
    return prefs


def clear():
    """删掉预设。返回是否真的删了。"""
    path = prefs_path()
    if os.path.exists(path):
        os.remove(path)
        return True
    return False


def age_days(prefs):
    """预设存了多少天。取不到日期时返回 None。"""
    raw = prefs.get('saved_at')
    if not raw:
        return None
    try:
        saved = datetime.strptime(str(raw), '%Y-%m-%d').date()
    except ValueError:
        return None
    return (date.today() - saved).days


# ==================== 命令行拼装 ====================

def _quote(value):
    """给命令行值加引号。含双引号的值在 save 时就被拒了，这里不必转义。"""
    return '"%s"' % value


def crawl_args(prefs):
    """把预设翻译成 boss_post_interactive.py 的参数串。

    存在的意义：预设有 10 个字段要映射到 -p -c -n -d -deg -e -s -j --scale，
    让 Claude 每轮手拼一次，错一次就是一次白爬。这里是唯一的拼装点。
    """
    if not prefs:
        return ''

    parts = ['-m', prefs.get('mode') or 'custom']

    if prefs.get('keywords'):
        parts += ['-p', _quote(','.join(prefs['keywords']))]
    if prefs.get('cities'):
        parts += ['-c', _quote(','.join(prefs['cities']))]
    if prefs.get('count'):
        parts += ['-n', str(prefs['count'])]

    for key, flag in (('job_type', '-j'), ('salary', '-s'),
                      ('experience', '-e'), ('degree', '-deg'),
                      ('scale', '--scale')):
        if prefs.get(key):
            parts += [flag, _quote(','.join(prefs[key]))]

    # -d 默认开：没有详情的 CSV 匹配质量差得多（JD 是空的）。
    # -y 恒开：Claude 驱动的运行读不到 stdin，交互确认在这一层没有意义 ——
    # 真正的确认关在 7g，那一道不受预设影响。
    if prefs.get('detail', True):
        parts.append('-d')
    parts.append('-y')

    return ' '.join(parts)


def crawl_command(prefs):
    args = crawl_args(prefs)
    return 'python scripts/boss_post_interactive.py %s' % args if args else ''


# ==================== 子命令 ====================

def cmd_show(_args):
    prefs = load()
    if not prefs:
        print('[无预设] 尚未保存过爬取参数')
        print('  首轮走合并提问（城市 + 关键词 + 匹配模式 + TopN 一次问完），')
        print('  确认后用 preferences.py save 存下来，下一轮就不必再问。')
        return 1

    print('已保存的爬取/匹配预设:')
    for key in ('cities', 'keywords', 'mode', 'count', 'min_count', 'detail',
                'match_mode', 'top_n', 'degree', 'experience',
                'salary', 'job_type', 'scale'):
        if key not in prefs:
            continue
        value = prefs[key]
        if isinstance(value, list):
            value = ', '.join(value)
        elif isinstance(value, bool):
            value = '是' if value else '否'
        print('  %-12s %s' % (LABELS.get(key, key) + ':', value))

    days = age_days(prefs)
    if days is None:
        print('  %-12s %s' % ('保存于:', prefs.get('saved_at', '未知')))
    else:
        when = '今天' if days == 0 else '%d 天前' % days
        print('  %-12s %s（%s）' % ('保存于:', prefs['saved_at'], when))

    command = crawl_command(prefs)
    if command:
        print('\n爬取命令:')
        print('  %s' % command)

    # 预设永不过期。年龄只是报出来给用户自己判断，不在这里变成一道关。
    return 0


def cmd_save(args):
    for value in (args.keywords or '') + (args.city or ''):
        if '"' in value:
            print('[错误] 参数里不能含双引号: %s' % value)
            return 1

    prefs = save(
        mode=args.mode,
        keywords=args.keywords,
        cities=args.city,
        count=args.count,
        detail=None if args.no_detail is None else (not args.no_detail),
        match_mode=args.match_mode,
        top_n=args.top,
        min_count=args.min_count,
        degree=args.degree,
        experience=args.experience,
        salary=args.salary,
        job_type=args.job_type,
        scale=args.scale,
    )
    print('[OK] 预设已保存到 %s' % prefs_path())
    print('  %d 个字段' % len([k for k in prefs if k not in META_KEYS]))
    command = crawl_command(prefs)
    if command:
        print('  爬取命令: %s' % command)
    return 0


def cmd_crawl_args(_args):
    prefs = load()
    if not prefs:
        print('[无预设] 没有可用的爬取参数', file=sys.stderr)
        return 1
    if not prefs.get('keywords') or not prefs.get('cities'):
        print('[不完整] 预设缺关键词或城市，无法拼出爬取命令', file=sys.stderr)
        return 1
    print(crawl_command(prefs))
    return 0


def cmd_clear(_args):
    if clear():
        print('[OK] 预设已删除')
        return 0
    print('[无预设] 没有可删的文件')
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        description='爬取/匹配参数预设（assets/preferences.json）',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subs = parser.add_subparsers(dest='command')

    subs.add_parser('show', help='打印预设；无预设时退出码 1').set_defaults(func=cmd_show)

    p_save = subs.add_parser('save', help='保存预设（覆盖式）')
    p_save.add_argument('--city', '-c', action='append', help='城市，可重复或逗号分隔')
    p_save.add_argument('--keywords', '-p', action='append', help='关键词，可重复或逗号分隔')
    p_save.add_argument('--mode', '-m', choices=['custom', 'list'], default='custom')
    p_save.add_argument('--count', '-n', type=int, help='每关键词每城市的条数上限')
    p_save.add_argument('--no-detail', action='store_true', default=None,
                        help='不爬详情（默认爬，匹配质量依赖 JD）')
    p_save.add_argument('--match-mode', choices=['quick', 'deep'], help='匹配模式')
    p_save.add_argument('--top', type=int, help='deep 模式的预筛选条数')
    p_save.add_argument('--min-count', type=int, help='最低岗位数量：爬完后实际条数低于此值时，主代理应停下来问用户')
    p_save.add_argument('--degree', '-deg', action='append')
    p_save.add_argument('--experience', '-e', action='append')
    p_save.add_argument('--salary', '-s', action='append')
    p_save.add_argument('--job-type', '-j', action='append')
    p_save.add_argument('--scale', action='append')
    p_save.set_defaults(func=cmd_save)

    subs.add_parser('crawl-args', help='打印完整爬取命令').set_defaults(func=cmd_crawl_args)
    subs.add_parser('clear', help='删除预设').set_defaults(func=cmd_clear)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, 'func', None):
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == '__main__':
    for _stream in (sys.stdout, sys.stderr):      # Windows 控制台是 GBK
        _stream.reconfigure(encoding='utf-8', errors='replace')
    sys.exit(main())
