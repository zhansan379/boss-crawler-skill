#!/usr/bin/env python3
"""ShowCV 内嵌产物的本地静态服务。

和 `python -m http.server` 的区别只有一点，但很关键：ShowCV 是纯客户端 SPA，
`/export`、`/delete`、`/s/{id}` 这些路径在磁盘上并不存在，靠前端在挂载前解析
location 来分流。裸 http.server 会直接 404，所以这里补了 fallback。
"""

from __future__ import annotations

import argparse
import mimetypes
import os
import socket
import sys
import urllib.error
import urllib.request
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# scripts/showcv/serve.py → 上溯三级到 skill 根
SKILL_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROOT = SKILL_ROOT / 'app'
DEFAULT_PORT = 3090
PORT_SPAN = 10

# ShowCV 仓库的 Express API 占 3070、Vite dev server 占 3080。
# 用户可能同时在跑那个仓库，抢了会让两边都起不来。
RESERVED_PORTS = {3070, 3080}

# Windows 上 Python 默认按控制台代码页（cp936）写 stdout，中文提示会变乱码
for _stream in (sys.stdout, sys.stderr):
    _stream.reconfigure(encoding='utf-8')

# 本机 Python 的 mimetypes 认不出这几个，不注册会退成 application/octet-stream
mimetypes.add_type('font/woff2', '.woff2')
mimetypes.add_type('font/ttf', '.ttf')
mimetypes.add_type('font/otf', '.otf')


class SpaHandler(SimpleHTTPRequestHandler):
    """不存在且不带扩展名的路径一律回 index.html。"""

    def translate_path(self, path: str) -> str:
        local = super().translate_path(path)
        if os.path.exists(local):
            return local
        # 带扩展名说明是资源请求，让它照常 404 —— 静默回 index.html 会把
        # "字体没拷全" 这类问题伪装成 200，反而更难查
        if Path(local).suffix:
            return local
        return str(Path(self.directory) / 'index.html')

    def log_message(self, *args) -> None:
        # 一次页面加载有几十个请求（含 14MB 字体），打日志会把后台任务输出刷爆
        pass


class Server(ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self) -> None:
        # Windows 独有：不设这个，别的进程能用 SO_REUSEADDR 把我们已绑的端口抢走
        exclusive = getattr(socket, 'SO_EXCLUSIVEADDRUSE', None)
        if exclusive is not None:
            self.socket.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        super().server_bind()


def is_listening(host: str, port: int, timeout: float = 0.35) -> bool:
    """端口上有人在听就返回 True。

    不能靠 bind 失败来判断占用：Windows 上只要占用方设了 SO_REUSEADDR
    （`python -m http.server` 就是这样），我们的 bind 会"成功"，
    但请求全落到对方那里 —— 表面启动正常，实际服务的是别人的内容。
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def probe(host: str, port: int, timeout: float = 1.0) -> bool:
    """判断该端口上跑的是否已经是 ShowCV 服务。

    简历数据存在 localStorage，而 localStorage 按 origin 隔离 —— 端口一漂移，
    上次存的简历就"消失"了。所以重复启动时优先复用同端口的旧服务，而不是换端口。
    """
    try:
        with urllib.request.urlopen(f'http://{host}:{port}/', timeout=timeout) as response:
            head = response.read(4096).decode('utf-8', 'ignore')
    except (urllib.error.URLError, OSError):
        return False
    return 'ShowCV' in head


def bind(root: Path, host: str, port: int) -> tuple[Server, int]:
    """从 port 往上找一个真正空闲的端口。"""
    handler = partial(SpaHandler, directory=str(root))
    last_error: OSError | None = None
    for candidate in range(port, port + PORT_SPAN):
        if candidate in RESERVED_PORTS or is_listening(host, candidate):
            continue
        try:
            return Server((host, candidate), handler), candidate
        except OSError as error:
            last_error = error
    raise SystemExit(f'端口 {port}~{port + PORT_SPAN - 1} 都不可用：{last_error}')


def main() -> None:
    parser = argparse.ArgumentParser(description='ShowCV 静态产物服务')
    parser.add_argument('--root', default=str(DEFAULT_ROOT), help='产物目录，默认 skill 内 app/')
    parser.add_argument('--port', type=int, default=DEFAULT_PORT, help='起始端口，默认 3090')
    parser.add_argument('--host', default='127.0.0.1', help='绑定地址，默认仅本机')
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if not (root / 'index.html').is_file():
        raise SystemExit(
            f'{root / "index.html"} 不存在。\n'
            '产物应随仓库一起提交；确实缺失时用 scripts/showcv/sync_app.py '
            '从 ShowCV 仓库的 dist/ 重新同步。'
        )

    if probe(args.host, args.port):
        # 服务已经在跑，复用它并退出 —— 换端口会丢掉旧 origin 里的简历
        print(f'SHOWCV_READY http://{args.host}:{args.port}', flush=True)
        print('复用已在运行的服务，本进程直接退出', file=sys.stderr, flush=True)
        return

    httpd, port = bind(root, args.host, args.port)
    # 首行固定格式，调用方据此拿到实际端口
    print(f'SHOWCV_READY http://{args.host}:{port}', flush=True)
    print(f'root={root}', file=sys.stderr, flush=True)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == '__main__':
    main()
