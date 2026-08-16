#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""核对 gen_materials.py 的产物齐不齐：每个岗位该有的招呼语和简历在不在。

`gen_materials.py` 是同步跑完的 —— 它返回了就代表不会再有新产物落盘，所以这里
只做一次性快照，不轮询、不等待。判据是「文件存在且非空」：并发写入时可能先出现
一个 0 字节的壳子，放行会把空简历送进渲染。

两个消费者：`pipeline.py` 的 materials 阶段跑完直接调 `check()`；命令行下单独跑
本脚本看「缺谁的哪一项」，缺的用 `gen_materials.py --only <序号>` 补。

用法：
    python scripts/check_artifacts.py <run_dir>                  # 招呼语 + 简历都查
    python scripts/check_artifacts.py <run_dir> --greeting       # 只查招呼语
    python scripts/check_artifacts.py <run_dir> --kinds resume   # 同上，另一种写法

不带任何开关时两者都查。按 gen_materials 的 skip 规则，`--greeting-mode skip`
不产招呼语、`--resume-mode skip` 不产简历，此时用开关缩小范围。

退出码：0 = 齐全，1 = 有缺失（缺失项打印在 stdout）。
"""

import os
import sys
import json
import argparse


def _load_jobs(run_dir):
    path = os.path.join(run_dir, 'qualified_jobs.json')
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    # 容忍被包一层的情况
    if isinstance(data, dict):
        data = data.get('jobs') or data.get('data') or []
    return data


def parse_only(spec, total):
    """--only 1,3,5-7 → 1-based 序号集合；spec 为空时返回 None（表示全部）。

    住在这个模块里是因为它没有任何依赖：gen_materials.py 从这里 import，
    materials 阶段的「生成哪些」和「核对哪些」于是共用同一份解析，不会各自漂移。
    越界序号在这里是**错误**而不是静默丢弃 —— 核对产物时序号对不上就是调用方写错了。
    （render_images.py / apply.py 另有一份宽松版：那边岗位数会随 --limit 变，
    越界要容忍，语义不同，故意不合并。）
    """
    if not spec:
        return None
    picked = set()
    for chunk in str(spec).replace(' ', '').split(','):
        if not chunk:
            continue
        if '-' in chunk:
            lo, _, hi = chunk.partition('-')
            try:
                picked.update(range(int(lo), int(hi) + 1))
            except ValueError:
                raise ValueError('--only 无法解析: %s' % chunk)
        else:
            try:
                picked.add(int(chunk))
            except ValueError:
                raise ValueError('--only 无法解析: %s' % chunk)
    bad = sorted(i for i in picked if i < 1 or i > total)
    if bad:
        raise ValueError('--only 里的序号超出 1..%d 范围: %s' % (total, bad))
    return picked


def check(run_dir, kinds, jobs=None, only=None):
    """核对产物齐不齐。返回 (jobs, 已落盘, 缺失)。

    `only` 是 1-based 序号集合（`--only` 解析后的结果），给 `None` 表示核对全部。
    传了它就只核对这几个序号 —— materials/render 带 `--only` 跑时，其余岗位本来就
    没打算生成，把它们算成「缺失」会让调用方（pipeline.py）把一次成功的部分运行
    误判成部分失败并退 3。
    """
    if jobs is None:
        jobs = _load_jobs(run_dir)
    gen = os.path.join(run_dir, 'generated')
    # 目录可能整个不存在——那就是全缺，而不是崩掉
    existing = os.listdir(gen) if os.path.isdir(gen) else []

    missing, found = [], []
    for i, job in enumerate(jobs, 1):
        if only is not None and i not in only:
            continue
        company = job.get('公司') or job.get('company') or '?'
        for kind in kinds:
            prefix = '%s_%d_' % (kind, i)
            # 非空才算落盘：可能刚 open() 出一个 0 字节的壳子，
            # 此时放行会把空简历送进渲染
            hits = []
            for name in existing:
                if not name.startswith(prefix):
                    continue
                try:
                    if os.path.getsize(os.path.join(gen, name)) > 0:
                        hits.append(name)
                except OSError:
                    pass          # 正在被写/刚被删
            if hits:
                found.append(hits[0])
            else:
                missing.append('%s #%d (%s)' % (kind, i, company))
    return jobs, found, missing


def main():
    for _stream in (sys.stdout, sys.stderr):      # Windows 控制台是 GBK
        _stream.reconfigure(encoding='utf-8', errors='replace')

    ap = argparse.ArgumentParser(
        description='核对 generated/ 下的招呼语与简历产物齐不齐（一次性快照）')
    ap.add_argument('run_dir')
    ap.add_argument('--greeting', action='store_true', help='只查招呼语')
    ap.add_argument('--resume', action='store_true', help='只查简历')
    ap.add_argument('--kinds', nargs='+', metavar='KIND',
                    choices=['greeting', 'resume'],
                    help='要查的产物种类（默认两者都查）')
    ap.add_argument('--only', metavar='SPEC',
                    help='只核对这些 1-based 序号，如 "1,3,5-7"。'
                         'materials/render 带 --only 跑过时必须给同一份，'
                         '否则没打算生成的岗位会被算成缺失')
    args = ap.parse_args()

    kinds = list(args.kinds or [])
    for name, on in (('greeting', args.greeting), ('resume', args.resume)):
        if on and name not in kinds:
            kinds.append(name)
    if not kinds:
        kinds = ['greeting', 'resume']

    try:
        jobs = _load_jobs(args.run_dir)
    except (OSError, ValueError) as exc:
        print('❌ 读不到 qualified_jobs.json: %s' % exc)
        return 1
    try:
        only = parse_only(args.only, len(jobs))
    except ValueError as exc:
        print('❌ %s' % exc)
        return 2

    jobs, found, missing = check(args.run_dir, kinds, jobs=jobs, only=only)
    expected = len(only) if only is not None else len(jobs or [])
    print('%d 个岗位%s × %s → 齐全需 %d 个产物'
          % (expected,
             '（--only 选中，共 %d 个）' % len(jobs or []) if only is not None else '',
             '/'.join(kinds), expected * len(kinds)))
    for name in found:
        print('  ✅ %s' % name)
    for name in missing:
        print('  ❌ 缺失：%s' % name)

    if missing:
        print('\n缺 %d 个。只补缺的这几个：' % len(missing))
        print('  python scripts/gen_materials.py "%s" --only <序号>' % args.run_dir)
        print('已有产物会自动跳过，不会被覆盖（要覆盖得显式 --force）。')
        return 1

    print('\n产物齐全。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
