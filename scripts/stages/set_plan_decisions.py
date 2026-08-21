#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把「某岗位的简历优化计划征询结果」写进 <run_dir>/materials/decision_{i}.json。

用途：简历优化同意闸门（SKILL.md「计划征询」）的落盘件。gen_materials 的阶段①
（--resume-mode plan）先写出每岗位的「调整计划」plan_{i}_{公司}.json；主 agent 在
CLI（AskUserQuestion）里按点征询用户后，用本脚本把「批准了哪些 suggestions /
要不要 AI 优化 / 回退用哪张图」原子落盘；gen_materials 的阶段②（--resume-mode apply）
再读它，**只对已批准的岗位**照已批准的 suggestions 出完整简历。

之所以单开 CLI 而不是让主 agent 手改 decision JSON：结构化决策、且要跟已存在的
plan 文件、序号对齐键保持一致 —— 主循环不该手编结构化 JSON。approved 之后被拒的
字段（must_add 里用户放弃的、should_adjust 里用户保留原样的）**干脆不在**
suggestions 里出现，apply 就绝不会把它们写进简历。

用法：
    python scripts/stages/set_plan_decisions.py <run_dir> --index 3 --approved
    python scripts/stages/set_plan_decisions.py <run_dir> --index 3 \
        --suggestions '{"must_add":[...],"should_adjust":[...],"keywords_to_emphasize":[...]}'
    python scripts/states/set_plan_decisions.py <run_dir> --index 3 --reject \
        --fallback-image "C:/my/简历.png"
    python scripts/stages/set_plan_decisions.py <run_dir> --index 3 --clear

    --approved      用计划自带的全量 suggestions（整套采纳）。
    --suggestions   指定过滤/改写后的 suggestions（用户逐条挑选 or 补了真内容后）。
    --reject        该岗位不用 AI 优化，apply 不生成本岗位简历；可带回退图。
    --clear         移除该岗位的决策（想重新征询时用）。

退出码：0 = 写入成功，1 = 文件缺失/不可写/JSON 解析失败，2 = 用法错误。
"""

import os
import sys
import json
import argparse
import tempfile

for _stream in (sys.stdout, sys.stderr):          # Windows 控制台是 GBK
    try:
        _stream.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, ValueError, OSError):
        pass

_SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from resume_matcher import materials_dir          # noqa: E402


def _atomic_write(path, data):
    """同目录写临时文件再 os.replace，避免半途崩溃留下截断的 decision json。"""
    tmpf = None
    try:
        fd, tmpf = tempfile.mkstemp(dir=os.path.dirname(path),
                                    prefix='.decision-', suffix='.tmp')
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmpf, path)
        tmpf = None
    finally:
        if tmpf and os.path.exists(tmpf):
            try:
                os.remove(tmpf)
            except OSError:
                pass


def main():
    ap = argparse.ArgumentParser(
        description='把某岗位的计划征询结果写进 materials/decision_{i}.json',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('run_dir', help='工作运行目录（其 materials/ 会被写）')
    ap.add_argument('--index', type=int, required=True,
                    help='岗位在 qualified_jobs.json 里的 1-based 序号')
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument('--approved', action='store_true',
                      help='整套采纳：apply 用计划自带的全量 suggestions')
    mode.add_argument('--suggestions', metavar='JSON',
                      help='指定过滤/改写后的 suggestions（dict JSON 字符串，CLI 校验后写盘）')
    mode.add_argument('--reject', action='store_true',
                      help='不用 AI 优化：apply 不生成该岗位简历')
    mode.add_argument('--clear', action='store_true',
                      help='移除该岗位已有决策')
    ap.add_argument('--fallback-image', default='',
                    help='（reject 时）该岗位回退用的图片：自定义简历图或原上传简历文件路径')
    args = ap.parse_args()

    if args.index < 1:
        print('❌ --index 必须是 >=1 的整数（1-based）', file=sys.stderr)
        return 2

    suggestions = None
    if args.suggestions is not None:
        try:
            suggestions = json.loads(args.suggestions)
        except ValueError as exc:
            print('❌ --suggestions 不是合法 JSON：%s' % exc, file=sys.stderr)
            return 1
        if not isinstance(suggestions, dict):
            print('❌ --suggestions 必须是 dict（优化建议对象）', file=sys.stderr)
            return 1

    fallback = args.fallback_image.strip() or None
    if fallback and not args.reject:
        print('⚠ --fallback-image 只在 --reject 时有意义，这里一并落盘；无妨。')

    gen = materials_dir(os.path.abspath(args.run_dir))
    os.makedirs(gen, exist_ok=True)
    path = os.path.join(gen, 'decision_%d.json' % args.index)

    if args.clear:
        if os.path.exists(path):
            os.remove(path)
            print('✅ 已移除 #%d 的决策：%s' % (args.index, path))
        else:
            print('ℹ #%d 本来就没有决策文件。' % args.index)
        return 0

    approved = args.approved or suggestions is not None
    payload = {
        'index': args.index,
        'approved': approved,
        'suggestions': suggestions,
        'fallback_image': fallback,
    }
    _atomic_write(path, payload)

    how = ('整套采纳' if args.approved else
           '逐条（%d 个 suggestion 键）' % len(suggestions) if suggestions else
           '不用 AI 优化')
    print('✅ 已写入 #%d 决策 → %s（%s）%s'
          % (args.index, '采纳' if approved else '不用AI优化', how,
             ('，回退图: %s' % fallback) if fallback else ''))
    return 0


if __name__ == '__main__':
    sys.exit(main())