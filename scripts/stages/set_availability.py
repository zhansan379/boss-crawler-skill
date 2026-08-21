#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把「可到岗 / 可实习时长 / 每周出勤」写进 <run_dir>/state/profile.json 的
basic_info.availability。

用途：招呼语防编造的一条链路。到岗三样 = HR 会照此排期入职的承诺，只能从真实输入来。
parse 阶段从简历原文 LLM 提取（没写就是 null）；当它缺位时，由主 agent 用这里把用户在
问询里给的答案落盘——之后 gen_greeting 的动态组装（greeting.st 的「我实际的到岗安排」段）
读它并拼进提示词。之所以单开一个 CLI 而不是让主 agent 手改 profile.json：主循环不该手编
结构化 JSON，那是 get 默认值不生效的坑（profile 里写的是显式 null）。

用法：
    python scripts/stages/set_availability.py <run_dir> --can-start 随时
    python scripts/stages/set_availability.py <run_dir> --duration 6个月 --days-per-week 5天
    python scripts/stages/set_availability.py <run_dir> --clear        # 清空到岗三样

三个字段各自可省略——只写给到的。至少给一个字段（或 --clear）才会动文件。

退出码：0 = 写入成功，1 = 文件缺失或无可写字段，2 = 用法错误。
"""

import os
import sys
import json
import argparse
import tempfile

_SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from resume_matcher import profile_path          # noqa: E402

_KEYS = (('can_start', '可到岗'), ('duration', '可实习时长'), ('days_per_week', '每周出勤'))


def reconfigure_stdout():
    for stream in (sys.stdout, sys.stderr):      # Windows 控制台是 GBK
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError, OSError):
            pass


def _atomic_write(path, data):
    """同目录下写临时文件再 os.replace，避免半途崩溃留下截断的 profile.json。"""
    tmpf = None
    try:
        fd, tmpf = tempfile.mkstemp(dir=os.path.dirname(path), prefix='.profile-', suffix='.tmp')
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmpf, path)
        tmpf = None                              # 已改名，不再需要清理
    finally:
        if tmpf and os.path.exists(tmpf):
            try:
                os.remove(tmpf)
            except OSError:
                pass


def main():
    reconfigure_stdout()
    ap = argparse.ArgumentParser(
        description='把到岗信息写进 <run_dir>/state/profile.json 的 basic_info.availability',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('run_dir', help='工作运行目录（其 state/profile.json 会被写）')
    ap.add_argument('--can-start', default='', help='可到岗（原文，如 随时 / 下周一 / 8月23日）')
    ap.add_argument('--duration', default='', help='可实习时长（如 6个月）')
    ap.add_argument('--days-per-week', dest='days_per_week', default='',
                    help='每周可出勤天数（如 5天）')
    ap.add_argument('--clear', action='store_true', help='清空到岗三样（置 null）')
    args = ap.parse_args()

    values = {'can_start': args.can_start, 'duration': args.duration,
              'days_per_week': args.days_per_week}
    if args.clear:
        values = {k: None for k in values}
    elif not any(v.strip() for v in values.values()):
        print('❌ 没有可写的字段：请给 --can-start / --duration / --days-per-week 至少一个，'
              '或 --clear 清空。', file=sys.stderr)
        return 1

    path = profile_path(os.path.abspath(args.run_dir))
    if not os.path.exists(path):
        print('❌ profile 不存在：%s' % path, file=sys.stderr)
        return 1

    try:
        with open(path, 'r', encoding='utf-8') as f:
            profile = json.load(f)
    except (ValueError, OSError) as exc:
        print('❌ 读 profile 失败：%s' % exc, file=sys.stderr)
        return 1
    if not isinstance(profile, dict):
        print('❌ profile.json 不是对象结构', file=sys.stderr)
        return 1

    bi = profile.setdefault('basic_info', {})
    if not isinstance(bi, dict):
        bi = profile['basic_info'] = {}
    avail = bi.get('availability')
    if not isinstance(avail, dict):
        avail = bi['availability'] = {}
    for key, _label in _KEYS:
        avail[key] = values[key]

    _atomic_write(path, profile)
    shown = '、'.join('%s=%s' % (_label, avail[k]) for k, _label in _KEYS if avail.get(k))
    print('✅ 已把到岗信息写入 %s  →  %s' % (path, shown or '（全部清空）'))
    return 0


if __name__ == '__main__':
    sys.exit(main())