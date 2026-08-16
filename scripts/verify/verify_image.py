#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""校验渲染出来的简历图片，**替代直接 Read 图片**。

为什么必须有它：`Read` 一张生成的简历 PNG 是本 skill 能干出的最贵的一件事。
2026-08-13 实测（session 3b45c941，投递材料目检环节）：

    Read deliver/<job>/<姓名>-<岗位>.png
      → 509 KB JPEG → 单次请求 638,960 input tokens
      → 上下文 83k 一步顶到 722k，立刻触发 auto-compact
      → 那 639k 占了整场 46 分钟会话全部新增 input token（809k）的 79%

图片是按 base64 进上下文、按字符计费的，所以贵在**文件体积**而不是画面复杂度；
而且压缩救不了——压缩发生在那个已经付过钱的请求之后。所以：目检一律走本脚本，
它只回十几行数字。真要人眼看，把路径告诉用户，让用户自己打开。

查什么：
  - 纯白/纯色空图（渲染失败）
  - 内容行数过少或明显被截断
  - 底部大片留白（导出没等页面铺完）
  - 与同目录 .md 源文件的内容量是否对得上（顺带确认不是别人的简历）

用法：
    python scripts/verify/verify_image.py <图片路径>
    python scripts/verify/verify_image.py <图片路径> --md <源 markdown>   # 默认自动找同名 .md
    python scripts/verify/verify_image.py <run_dir>/applications --all    # 批量查一整轮

退出码：0 = 看起来正常，1 = 有可疑项（逐条打印原因）。
"""

import os
import sys
import glob
import argparse
from collections import Counter

try:
    from PIL import Image
except ImportError:                                   # pragma: no cover
    sys.exit('需要 Pillow：python -m pip install Pillow')

# ── 判定阈值 ──
INK_DELTA = 30           # 与背景色差多少才算「有内容」
MIN_INK_RATIO = 0.005    # 低于此视为空图
MIN_CONTENT_ROWS = 10    # 一份简历怎么也不止 10 行
MAX_TAIL_BLANK = 0.25    # 底部留白超过全图 25% 视为可疑
MIN_ROWS_VS_MD = 0.5     # 内容行数至少要有 md 非空行数的一半
SAMPLE_WIDTH = 200       # 缩到这个宽度再统计，够用且快


def _analyse(path):
    with Image.open(path) as im:
        width, height = im.size
        fmt = im.format
        gray = im.convert('L')
        # 等比缩到 SAMPLE_WIDTH，只为统计，不影响判定语义
        if width > SAMPLE_WIDTH:
            scale = SAMPLE_WIDTH / width
            gray = gray.resize((SAMPLE_WIDTH, max(1, int(height * scale))))
        sw, sh = gray.size
        px = list(gray.getdata())

    # 背景色取众数，而不是假定纯白——简历可能有深色底或色块
    bg = Counter(px).most_common(1)[0][0]

    ink_total = 0
    row_has_ink = []
    for y in range(sh):
        row = px[y * sw:(y + 1) * sw]
        n = sum(1 for v in row if abs(v - bg) > INK_DELTA)
        ink_total += n
        row_has_ink.append(n > 0)

    content_rows = sum(row_has_ink)
    # 首末内容行之间的跨度，用来量首尾留白
    first = next((y for y, ink in enumerate(row_has_ink) if ink), None)
    last = next((y for y in range(sh - 1, -1, -1) if row_has_ink[y]), None)
    head_blank = (first / sh) if first is not None else 1.0
    tail_blank = ((sh - 1 - last) / sh) if last is not None else 1.0

    return {
        'width': width, 'height': height, 'format': fmt,
        'bytes': os.path.getsize(path),
        'bg_gray': bg,
        'ink_ratio': ink_total / (sw * sh),
        'content_rows': content_rows,
        'sample_rows': sh,
        'head_blank': head_blank,
        'tail_blank': tail_blank,
    }


def _md_lines(md_path):
    with open(md_path, encoding='utf-8') as f:
        return sum(1 for line in f if line.strip())


def verify(path, md_path=None):
    """返回 (stats, problems)。problems 为空即通过。"""
    stats = _analyse(path)
    problems = []

    if stats['bytes'] == 0:
        problems.append('文件 0 字节')
    if stats['ink_ratio'] < MIN_INK_RATIO:
        problems.append('几乎是空图（内容像素占比 %.4f%% < %.2f%%）——渲染大概失败了'
                        % (stats['ink_ratio'] * 100, MIN_INK_RATIO * 100))
    if stats['content_rows'] < MIN_CONTENT_ROWS:
        problems.append('内容行数只有 %d（采样 %d 行），像是截断或空白'
                        % (stats['content_rows'], stats['sample_rows']))
    if stats['tail_blank'] > MAX_TAIL_BLANK:
        problems.append('底部留白占 %.0f%%（阈值 %.0f%%）——导出可能没等页面铺完'
                        % (stats['tail_blank'] * 100, MAX_TAIL_BLANK * 100))

    # 与 md 源文件互校：默认找同名 .md
    if md_path is None:
        sibling = os.path.splitext(path)[0] + '.md'
        md_path = sibling if os.path.isfile(sibling) else None
    if md_path and os.path.isfile(md_path):
        lines = _md_lines(md_path)
        stats['md_lines'] = lines
        stats['md_path'] = os.path.basename(md_path)
        # 渲染会折行，所以图里的内容行通常 >= md 行数；明显少就是内容没渲染全
        if lines and stats['content_rows'] < lines * MIN_ROWS_VS_MD:
            problems.append('图里内容行 %d 远少于 md 非空行 %d——内容没渲染全'
                            % (stats['content_rows'], lines))
    else:
        stats['md_lines'] = None

    return stats, problems


def report(path, stats, problems):
    print('%s' % os.path.basename(path))
    print('  尺寸        %d × %d  (%s, %.0f KB)'
          % (stats['width'], stats['height'], stats['format'], stats['bytes'] / 1024))
    print('  内容像素    %.2f%%   背景灰度 %d' % (stats['ink_ratio'] * 100, stats['bg_gray']))
    print('  内容行      %d / %d 采样行' % (stats['content_rows'], stats['sample_rows']))
    print('  留白        顶部 %.0f%%  底部 %.0f%%'
          % (stats['head_blank'] * 100, stats['tail_blank'] * 100))
    if stats.get('md_lines') is not None:
        print('  md 互校     %s：%d 非空行' % (stats['md_path'], stats['md_lines']))
    for p in problems:
        print('  ⚠️  %s' % p)
    if not problems:
        print('  ✅ 未发现异常')


def main():
    for _stream in (sys.stdout, sys.stderr):      # Windows 控制台是 GBK
        _stream.reconfigure(encoding='utf-8', errors='replace')

    ap = argparse.ArgumentParser()
    ap.add_argument('target', help='图片路径，或 --all 时的 applications 目录')
    ap.add_argument('--md', help='用于互校的 markdown 源（默认取同名 .md）')
    ap.add_argument('--all', action='store_true',
                    help='把 target 当作目录，递归查所有 png/jpg')
    args = ap.parse_args()

    if args.all:
        paths = sorted(
            p for ext in ('png', 'jpg', 'jpeg')
            for p in glob.glob(os.path.join(args.target, '**', '*.' + ext), recursive=True)
        )
        if not paths:
            print('%s 下没找到图片' % args.target)
            return 1
    else:
        paths = [args.target]

    bad = 0
    for i, path in enumerate(paths):
        if i:
            print()
        try:
            stats, problems = verify(path, args.md if not args.all else None)
        except Exception as error:                       # 坏文件不该带崩整批
            print('%s\n  ⚠️  打不开：%s' % (os.path.basename(path), error))
            bad += 1
            continue
        report(path, stats, problems)
        bad += bool(problems)

    print('\n%d 张图，%d 张有可疑项' % (len(paths), bad))
    if bad:
        print('注意：不要用 Read 打开图片确认——一张 0.5 MB 的图就是 ~640k token。'
              '要人眼看就把路径给用户。')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
