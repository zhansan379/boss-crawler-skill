#!/usr/bin/env python3
"""把 ShowCV 仓库的 dist/ 同步到本 skill 的 app/。

app/ 随本仓库一起提交，正常情况下**不需要跑这个脚本**。只有想升级到 ShowCV 的
新版本时才用：先在 ShowCV 仓库根跑 `pnpm build`，再把它的 dist/ 传给 --dist。

    python scripts/showcv/sync_app.py --dist "D:/Project/open-source project/ShowCV/dist"

和上游版本的区别：本仓库和 ShowCV 仓库没有目录包含关系，猜不出源产物在哪，
所以 --dist 是必填的。
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

# scripts/showcv/sync_app.py → 上溯三级到 skill 根
SKILL_ROOT = Path(__file__).resolve().parents[2]
APP_DIR = SKILL_ROOT / 'app'

# Windows 上 Python 默认按控制台代码页（cp936）写 stdout，中文提示会变乱码
for _stream in (sys.stdout, sys.stderr):
    _stream.reconfigure(encoding='utf-8')


def newest_mtime(root: Path, patterns: tuple[str, ...]) -> float:
    times = [path.stat().st_mtime for pattern in patterns for path in root.rglob(pattern)]
    return max(times) if times else 0.0


def main() -> None:
    parser = argparse.ArgumentParser(description='同步 ShowCV 的 dist/ 到本 skill 的 app/')
    parser.add_argument('--dist', required=True, help='ShowCV 仓库的 dist/ 目录')
    parser.add_argument('--force', action='store_true', help='产物比源码旧时也照样同步')
    args = parser.parse_args()

    dist = Path(args.dist).resolve()
    entry = dist / 'index.html'
    if not entry.is_file():
        raise SystemExit(f'{entry} 不存在，先在 ShowCV 仓库根跑 pnpm build')

    # dist/ 的同级 src/ 就是源码目录；不在同级（比如用户拷了份产物过来）就跳过这个检查
    src_dir = dist.parent / 'src'
    if src_dir.is_dir():
        src_mtime = newest_mtime(src_dir, ('*.ts', '*.tsx', '*.css'))
        if src_mtime > entry.stat().st_mtime and not args.force:
            raise SystemExit('dist/ 比 src/ 旧，先跑 pnpm build（或加 --force 跳过检查）')

    if APP_DIR.exists():
        shutil.rmtree(APP_DIR)
    shutil.copytree(dist, APP_DIR)

    size = sum(path.stat().st_size for path in APP_DIR.rglob('*') if path.is_file())
    print(f'已同步 {dist} → {APP_DIR}（{size / 1024 / 1024:.1f} MB）')
    print('注意：app/ 是提交进 git 的，同步后记得 git add app/ 并检查 diff 体积')


if __name__ == '__main__':
    main()
