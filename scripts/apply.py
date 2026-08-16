#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""执行投递。**这是整条流水线里唯一不可逆的一步。**

所以它是独立的一条命令，而且默认只演练：不给 --yes 就把「会投给谁、发什么招呼语、
带哪张图」全打出来然后退出，一个浏览器动作都不做。流水线（pipeline.py）跑到材料生成
就停，永远不会自动走到这里。

发出去的消息撤不回、HR 已读撤不回、BOSS 每天还有沟通条数上限 —— 所以下面这些检查
一个都不能省，而且 --yes 也不会跳过它们（只有各自的显式开关能跳）：

- 招呼语缺失 → 拒绝。auto_apply_jobs 拿不到招呼语会用内置默认文案，那等于拿一次
  珍贵的沟通机会去发一句通用寒暄。
- 附件图缺失或体检不过 → 拒绝（--skip-verify 才跳过）。空白图或半张图发出去，
  比不发附件更糟。
- 招呼语前 15 个字是寒暄 → 只告警。HR 在列表里只看得到前 15 个字，但这是文案质量
  问题，不该挡住用户明确要投的动作。

用法：
    python scripts/apply.py <run_dir>                  # 演练：先列出可选公司，再打印计划
    python scripts/apply.py <run_dir> --yes            # 真投
    python scripts/apply.py <run_dir> --yes --only 1,3 # 按序号只投这两个
    python scripts/apply.py <run_dir> --yes --company 百度,棱镜数聚   # 按公司名只投这两家
    python scripts/apply.py <run_dir> --yes --no-image # 不带附件
    python scripts/apply.py <run_dir> --yes --image my.png   # 整批共用一张图

退出码：0 = 演练完成或全部投出，1 = 前置检查不通过（什么都没投），
3 = 投了但有失败/仅部分成功的岗位。
"""

import os
import sys
import json
import glob
import argparse
import subprocess
import unicodedata
from types import SimpleNamespace

from write_application_md import sanitize, load_jobs, resolve_greeting, resolve_csv_row, merge
from render_images import parse_only, resolve_name, job_dir_name
from resume_matcher import profile_path, deliver_dir, apply_log_path

# 投递入口在**模块层**导入，不在 main() 里 —— 局部 import 会遮蔽模块属性，
# 让 test_apply_gate.py 换不掉它，于是「验证闸门」的测试反而会真的打开浏览器去投递。
try:
    from resume_matcher import auto_apply_jobs
    from resume_matcher.deep_analysis import deserialize_profile
    _APPLY_IMPORT_ERROR = None
except ImportError as _exc:                          # pragma: no cover
    auto_apply_jobs = None
    deserialize_profile = None
    _APPLY_IMPORT_ERROR = _exc

# 只为了 has_wasted_preview 才导它。auto_apply 顶层 import DrissionPage 时有 HAS_DRISSION
# 兜底，正常不会失败；万一失败也只是少一条文案告警，不该让演练跑不起来。
try:
    from resume_matcher.auto_apply import has_wasted_preview
except ImportError:                                  # pragma: no cover
    has_wasted_preview = None

_SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# verify_image.py 缺 Pillow 时是 `sys.exit('需要 Pillow：…')`，退出码同样是 1。
# 不区分的话，「没装 Pillow」会被报成「每张图都有问题」，然后拦下一次本该正常的投递。
_PILLOW_MISSING = '需要 Pillow'


def scrub_nulls(data):
    """把 profile.json 里的显式 null 换成同类型空值。

    deserialize_profile 用的是 `data.get('basic_info', {})` —— profile.json 写的是
    显式 `null` 时，默认值**不会**触发，返回的是 None，于是 generate_greeting 里
    `profile.basic_info.get(...)` 直接 AttributeError。同一个坑在这个仓库里踩过两次。
    """
    shapes = {
        'basic_info': dict, 'education': dict, 'experience': dict, 'skills': dict,
        'social_links': dict, 'salary_expectation': dict,
        'projects': list, 'awards': list, 'publications': list, 'keywords': list,
        'raw_text': str,
    }
    out = dict(data or {})
    for key, factory in shapes.items():
        if out.get(key) is None:
            out[key] = factory()
    return out


def load_profile(run_dir):
    path = profile_path(run_dir)
    with open(path, 'r', encoding='utf-8') as f:
        return scrub_nulls(json.load(f))


def find_greeting(run_dir, index):
    """招呼语正文与来源。走 write_application_md.resolve_greeting，两边口径一致。"""
    args = SimpleNamespace(greeting=None, greeting_file=None)
    text, source = resolve_greeting(run_dir, index, args)
    return (text or '').strip(), source


def find_image(run_dir, job, person):
    """该岗位的 <姓名>-<应聘岗位>.png。走 render_images 的同一套目录/命名推导。"""
    dir_name, position = job_dir_name(job)
    base = '%s-%s' % (sanitize(person), sanitize(position))
    path = os.path.join(deliver_dir(run_dir), dir_name, base + '.png')
    return path if os.path.exists(path) and os.path.getsize(path) > 0 else None


def verify_images(paths):
    """跑 verify_image.py 体检。返回 (ok, 输出, 是否因缺 Pillow 而没检)。"""
    script = os.path.join(_SKILL_ROOT, 'scripts', 'verify_image.py')
    bad, lines = [], []
    for path in paths:
        proc = subprocess.run([sys.executable, script, path],
                              capture_output=True, text=True,
                              encoding='utf-8', errors='replace')
        output = ((proc.stdout or '') + (proc.stderr or '')).strip()
        if proc.returncode == 0:
            continue
        if _PILLOW_MISSING in output:
            return True, output, True
        bad.append(path)
        lines.append('  ✗ %s\n%s' % (os.path.basename(path),
                                     '\n'.join('      ' + l for l in output.splitlines())))
    return (not bad), '\n'.join(lines), False


def preview15(text):
    """HR 在消息列表里看得到的那 15 个字。"""
    flat = ' '.join((text or '').split())
    return flat[:15]


# ==================== 按公司选岗 ====================

def company_of(job):
    """岗位所属公司名（原文，未 sanitize）。

    刻意走 job_dir_name 的同一套 resolve_csv_row + merge：`--company` 认的名字、
    演练里列出的名字、deliver/ 下的目录名必须是同一个口径，否则用户照着列表
    抄一遍反而匹配不上。
    """
    csv_row, _ = resolve_csv_row(job)
    row = merge(job, csv_row)
    return str(row.get('公司') or row.get('company') or '未知公司').strip()


def company_index(jobs):
    """[(公司, [序号...])]，按首次出现顺序。同一家公司的多个岗位合成一条。

    同名公司在一轮里出现多次是常态（同一家挂了两个实习岗），所以 --company 天然
    是「一对多」—— 这也是为什么命中多个时要把序号打出来给用户过目。
    """
    order, groups = [], {}
    for index, job in enumerate(jobs, 1):
        name = company_of(job)
        if name not in groups:
            groups[name] = []
            order.append(name)
        groups[name].append(index)
    return [(name, groups[name]) for name in order]


def match_companies(spec, groups):
    """"百度,棱镜" → ([序号...], [说明...])。有一个词没命中就抛 ValueError。

    先全字匹配（忽略大小写与首尾空白），全都没命中才退回子串匹配 —— 公司全称常常
    很长，用户往往只抄简称。

    没命中的词一律让整条命令失败，**不投已命中的那部分**：投递不可撤回，
    「--company 百度,腾讯」里腾讯拼错了却照投百度，用户是在消息发出后才知道的。
    """
    terms, seen = [], set()
    for chunk in str(spec).split(','):
        chunk = chunk.strip()
        if chunk and chunk.casefold() not in seen:
            seen.add(chunk.casefold())
            terms.append(chunk)
    if not terms:
        raise ValueError('--company 里没有有效的公司名')

    picked, notes, missed = [], [], []
    for term in terms:
        key = term.casefold()
        hits = [(name, ix) for name, ix in groups if name.casefold() == key]
        how = '全字'
        if not hits:
            hits = [(name, ix) for name, ix in groups if key in name.casefold()]
            how = '包含'
        if not hits:
            missed.append(term)
            continue
        if how == '包含' or len(hits) > 1 or len(hits[0][1]) > 1:
            notes.append('「%s」%s匹配 → %s' % (term, how, '、'.join(
                '%s(%s)' % (name, ' '.join('#%d' % i for i in ix)) for name, ix in hits)))
        for _, indexes in hits:
            picked.extend(indexes)

    if missed:
        raise ValueError('--company 里这些名字在 qualified_jobs.json 里找不到：%s'
                         % '、'.join(missed))
    return sorted(set(picked)), notes


def _width(text):
    """终端显示宽度：中日韩字符占 2 列。菜单要对齐，len() 在中文名上会短一半。"""
    return sum(2 if unicodedata.east_asian_width(c) in 'WF' else 1 for c in text)


def print_company_menu(jobs, groups):
    """列出 --company / --only 各自能填什么。演练时总是打印，包括材料不齐的岗位。

    例子全部按本轮真实数据拼，直接复制就能用 —— 写成 `--only 1,3,5-7` 这种占位示例，
    用户还得自己回头对着上面的清单换成真序号，多一步就多一次填错的机会。
    """
    total = len(jobs)
    print('\n%s' % ('=' * 60))
    print('  本轮 %d 个岗位、%d 家公司。序号 #1~#%d 就是下面这张表里的编号：'
          % (total, len(groups), total))

    pad = max(_width(name) for name, _ in groups)
    for name, indexes in groups:
        detail = '、'.join('#%d %s' % (i, job_dir_name(jobs[i - 1])[1]) for i in indexes)
        print('    %s%s  %s' % (name, ' ' * (pad - _width(name)), detail))

    # 按公司：第一家 / 前两家
    rows = [('--company "%s"' % groups[0][0], '按公司名投一家，支持简称（子串匹配）')]
    if len(groups) > 1:
        rows.append(('--company "%s,%s"' % (groups[0][0], groups[1][0]),
                     '逗号分隔投多家'))
    multi = next((g for g in groups if len(g[1]) > 1), None)
    if multi:
        rows.append(('--company "%s"' % multi[0],
                     '同名公司的多个岗位会一起选中（这里 = %s）'
                     % ' '.join('#%d' % i for i in multi[1])))

    # 按序号：单个 / 多个 / 区间。区间取末尾一段，保证是本轮真实存在的序号
    rows.append(('--only 1', '按序号投单个，也就是上表的 #1'))
    if total >= 3:
        rows.append(('--only 1,3', '逗号分隔投多个：#1 和 #3'))
        lo = max(1, total - 2)
        rows.append(('--only %d-%d' % (lo, total),
                     '短横线是连续区间：#%d 到 #%d 共 %d 个' % (lo, total, total - lo + 1)))
    elif total == 2:
        rows.append(('--only 1,2', '逗号分隔投多个：#1 和 #2'))

    print('\n  怎么填：')
    width = max(_width(flag) for flag, _ in rows)
    for flag, note in rows:
        print('    %s%s  %s' % (flag, ' ' * (width - _width(flag)), note))
    print('\n  两个参数可以一起用（取交集），再加 --max N 限制总条数。')


def build_plan(run_dir, jobs, indexes, person, want_image, shared_image):
    """每个岗位的投递计划。返回 (plan, blockers, warnings)。"""
    plan, blockers, warnings = [], [], []
    for index in indexes:
        job = jobs[index - 1]
        link = str(job.get('link') or '').strip()
        dir_name, position = job_dir_name(job)

        if not link:
            blockers.append('#%d %s 没有 link，无法打开岗位页' % (index, dir_name))
            continue

        greeting, source = find_greeting(run_dir, index)
        if not greeting:
            blockers.append(
                '#%d %s 没有招呼语（materials/greeting_%d_*.txt 缺失或为空）'
                % (index, dir_name, index))
            continue

        image = None
        if want_image:
            image = shared_image or find_image(run_dir, job, person)
            if not image:
                blockers.append(
                    '#%d %s 没有简历图（先跑 render_images.py，或用 --no-image / --image）'
                    % (index, dir_name))
                continue

        if has_wasted_preview is not None and has_wasted_preview(greeting):
            warnings.append('#%d %s 招呼语前 15 字是寒暄：「%s」'
                            % (index, dir_name, preview15(greeting)))

        plan.append({
            'index': index, 'job': job, 'link': link, 'dir_name': dir_name,
            'position': position, 'greeting': greeting, 'greeting_src': source,
            'image': image,
        })
    return plan, blockers, warnings


def print_plan(plan, dry_run):
    print('\n%s' % ('=' * 60))
    print('  %s：%d 个岗位' % ('演练（不会真投）' if dry_run else '即将投递', len(plan)))
    for item in plan:
        print('\n  #%d %s' % (item['index'], item['dir_name']))
        print('     link     %s' % item['link'])
        print('     前15字   「%s」' % preview15(item['greeting']))
        print('     招呼语   %d 字（来源：%s）' % (len(item['greeting']), item['greeting_src']))
        print('     附件     %s' % (item['image'] or '（不发）'))


def summarize(results):
    """投递结果分档。auto_apply_jobs 的 status: applied / partial / 其他。"""
    applied = [r for r in results if r.get('status') == 'applied']
    partial = [r for r in results if r.get('status') == 'partial']
    failed = [r for r in results if r.get('status') not in ('applied', 'partial')]

    print('\n%s' % ('=' * 60))
    print('  已投出 %d ｜ 仅进输入框未发送 %d ｜ 失败 %d'
          % (len(applied), len(partial), len(failed)))

    attached = [r for r in applied if r.get('attachment_sent')]
    if applied:
        print('  其中带附件成功 %d 个' % len(attached))
    if partial:
        print('\n  ⚠ 下面这些招呼语只填进了输入框、**没有发出去** —— '
              '打开对话框自己按一下回车：')
        for r in partial:
            print('     %s %s' % (r.get('company') or '?', r.get('link') or ''))
    if failed:
        print('\n  ✗ 失败的岗位：')
        for r in failed:
            print('     %s %s  %s' % (r.get('company') or '?',
                                      r.get('status') or '?',
                                      r.get('error') or r.get('reason') or ''))
    return 0 if (not partial and not failed) else 3


def main():
    for stream in (sys.stdout, sys.stderr):        # Windows 控制台是 GBK
        stream.reconfigure(encoding='utf-8', errors='replace')

    ap = argparse.ArgumentParser(
        description='执行投递。不给 --yes 只演练，不做任何浏览器操作。',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='投出去的消息撤不回，所以 --yes 是必需的，且演练是默认行为。\n')
    ap.add_argument('run_dir')
    ap.add_argument('--yes', action='store_true',
                    help='确认真投。不给这个参数就只打印计划')
    ap.add_argument('--only', help='只投这些岗位，如 "1,3,5-7"')
    ap.add_argument('--company',
                    help='只投这些公司，逗号分隔，如 "百度,棱镜数聚"。'
                         '支持简称（子串匹配）；不给 --company 时跑一次演练即可看到可选值')
    ap.add_argument('--max', type=int, help='最多投几个（默认全部选中的）')
    ap.add_argument('--name', help='<姓名>-<应聘岗位> 里的姓名，覆盖 profile.json')
    ap.add_argument('--no-image', action='store_true', help='不发简历附件')
    ap.add_argument('--image', help='整批共用这一张图（用户自己上传的那张）')
    ap.add_argument('--skip-verify', action='store_true',
                    help='跳过 verify_image.py 体检（不建议：空白图发出去比不发更糟）')
    ap.add_argument('--headless', action='store_true',
                    help='无头模式（首次需要扫码登录时别用）')
    args = ap.parse_args()

    run_dir = os.path.abspath(args.run_dir)
    if not os.path.isdir(run_dir):
        print('❌ 运行目录不存在: %s' % run_dir)
        return 1

    try:
        profile_data = load_profile(run_dir)
    except (OSError, ValueError) as exc:
        print('❌ 读不到 profile.json: %s' % exc)
        return 1
    try:
        jobs = load_jobs(run_dir)
    except (OSError, ValueError) as exc:
        print('❌ 读不到 qualified_jobs.json: %s' % exc)
        return 1
    if not jobs:
        print('❌ qualified_jobs.json 是空的，没有岗位可投')
        return 1

    want_image = not args.no_image
    shared_image = None
    if args.image:
        shared_image = os.path.abspath(args.image)
        if not os.path.exists(shared_image):
            print('❌ --image 指定的文件不存在: %s' % shared_image)
            return 1

    # 姓名只在需要按岗位找图时才是必需的
    person = ''
    if want_image and not shared_image:
        try:
            person = resolve_name(profile_data, args.name)
        except ValueError as exc:
            print('❌ %s' % exc)
            print('  或者加 --no-image / --image 跳过按姓名找图')
            return 1

    try:
        indexes = parse_only(args.only, len(jobs))
    except ValueError as exc:
        print('❌ %s' % exc)
        return 1

    # 公司清单：演练时要列给用户看，--company 要拿它匹配。两者都不需要时不算
    # —— company_of 会为每个岗位回查一遍爬取 CSV。
    groups = company_index(jobs) if (args.company or not args.yes) else None

    if args.company:
        try:
            picked, notes = match_companies(args.company, groups)
        except ValueError as exc:
            print('❌ %s' % exc)
            print_company_menu(jobs, groups)
            return 1
        for note in notes:
            print('  · %s' % note)
        chosen = set(picked)
        dropped = [i for i in indexes if i not in chosen]
        indexes = [i for i in indexes if i in chosen]
        if not indexes:
            print('❌ --company 和 --only 的交集是空的，没有岗位可投')
            print_company_menu(jobs, groups)
            return 1
        if args.only and dropped:
            print('  · --only 里的 %s 不属于 --company 指定的公司，已排除'
                  % ' '.join('#%d' % i for i in dropped))

    if args.max is not None and args.max > 0:
        indexes = indexes[:args.max]
    if not indexes:
        print('❌ 没有选中任何岗位')
        return 1

    if groups is not None and not args.company:
        print_company_menu(jobs, groups)

    plan, blockers, warnings = build_plan(
        run_dir, jobs, indexes, person, want_image, shared_image)

    for note in warnings:
        print('  ⚠ %s' % note)
    if blockers:
        print('\n❌ 下面这些岗位缺材料，**一个都不会投**（不做「投一半」）：')
        for note in blockers:
            print('  · %s' % note)
        print('\n  补齐后重跑，或用 --only / --company 只投材料齐全的那几个。')
        return 1
    if not plan:
        print('❌ 没有材料齐全的岗位')
        return 1

    # 图片体检：空白图/半张图必须在投出去之前拦下
    if want_image and not args.skip_verify:
        paths = sorted({item['image'] for item in plan if item['image']})
        if paths:
            print('\n  体检 %d 张简历图 …' % len(paths))
            ok, detail, skipped = verify_images(paths)
            if skipped:
                print('  ⚠ 没装 Pillow，跳过体检（装上更保险：python -m pip install Pillow）')
            elif not ok:
                print('❌ 下面这些图看着不对（空白 / 内容过少 / 底部大片留白）：')
                print(detail)
                print('\n  重新渲染：python scripts/render_images.py "%s"' % run_dir)
                print('  确认没问题就加 --skip-verify。')
                return 1
            else:
                print('  ✅ 全部通过')

    print_plan(plan, dry_run=not args.yes)

    if not args.yes:
        print('\n%s' % ('=' * 60))
        print('  这是演练，**没有投递任何岗位**，也没有打开浏览器。')
        print('  确认无误后加 --yes 真投：')
        cmd = 'python scripts/apply.py "%s" --yes' % run_dir
        if args.only:
            cmd += ' --only %s' % args.only
        if args.company:
            cmd += ' --company "%s"' % args.company
        if args.max:
            cmd += ' --max %d' % args.max
        if args.no_image:
            cmd += ' --no-image'
        if args.image:
            cmd += ' --image "%s"' % args.image
        print('    %s' % cmd)
        if not args.company and groups and len(groups) > 1:
            print('\n  只投其中几家：在上面这条命令后面加 --company "公司名[,公司名…]"')
            print('    可选值就是本次演练开头列出的那些公司名')
        return 0

    # ── 真投 ──
    if auto_apply_jobs is None:
        print('❌ 导入投递模块失败: %s' % _APPLY_IMPORT_ERROR)
        print('  投递需要 DrissionPage：python -m pip install DrissionPage')
        return 1

    selected = [item['job'] for item in plan]
    greetings = {item['link']: item['greeting'] for item in plan}
    images = {item['link']: item['image'] for item in plan if item['image']}

    print('\n  开始投递 %d 个岗位（首次运行需要扫码登录，浏览器会自己打开）…' % len(selected))
    try:
        results = auto_apply_jobs(
            qualified_jobs=selected,
            _profile=deserialize_profile(profile_data),
            max_applications=len(selected),
            headless=args.headless,
            greetings=greetings,
            resume_file_path=images or None,
            output_dir=run_dir,
        )
    except KeyboardInterrupt:
        print('\n⚠ 已中断。已投出的岗位不会撤回；日志见 %s'
              % apply_log_path(run_dir))
        return 3

    if not results:
        print('\n❌ 一个都没投出去（上面有 auto_apply_jobs 的原因）')
        return 1

    code = summarize(results)
    print('\n  明细日志: %s' % apply_log_path(run_dir))
    return code


if __name__ == '__main__':
    sys.exit(main())
