#!/usr/bin/env python3
"""TalentHub 企业人才库 — 统一启动入口（Windows / Linux / macOS 通用）。

用法:
  python server.py                  # 按 config.json 启动
  python server.py --port 9000      # 临时指定端口
  python server.py --host 0.0.0.0   # 开放端口，局域网可访问
  python server.py --no-open        # 不自动弹浏览器
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402


def _port_busy(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        # 检查监听侧：bind 能成功说明端口空闲
        try:
            s.bind((host if host != "0.0.0.0" else "", port))
            return False
        except OSError:
            return True


def _lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("223.5.5.5", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def main():
    ap = argparse.ArgumentParser(description="TalentHub 企业人才库服务")
    ap.add_argument("--host", help="监听地址：127.0.0.1 仅本机 / 0.0.0.0 开放端口")
    ap.add_argument("--port", type=int, help="监听端口")
    ap.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    ap.add_argument("--wait-port-free", type=int, default=0,
                    help="启动前最多等待 N 秒让旧进程释放端口（热重启用）")
    args = ap.parse_args()

    cfg = config.get()
    host = args.host or cfg["host"]
    port = int(args.port or cfg["port"])

    if args.wait_port_free > 0:
        deadline = time.time() + args.wait_port_free
        while _port_busy(host, port) and time.time() < deadline:
            time.sleep(0.3)

    if _port_busy(host, port):
        print(f"[错误] 端口 {port} 已被占用或不可用。换个端口启动: python server.py --port {port + 1}"
              f"\n       或修改 config.json 里的 port 字段。")
        if os.name == "nt":
            print("       Windows 提示：该端口也可能落在系统保留段（Hyper-V/WSL 动态保留），"
                  "可用命令查看: netsh int ipv4 show excludedportrange protocol=tcp")
        sys.exit(2)

    auto_open = cfg.get("auto_open_browser", True) and not args.no_open
    url_local = f"http://127.0.0.1:{port}"
    lan = _lan_ip()

    bar = "─" * 56
    from app import __version__
    print(f"""
┌{bar}┐
│  TalentHub 企业人才库  v{__version__}                          │
│  本机访问:  {url_local:<43}│
│  局域网:    {('http://' + lan + ':' + str(port)) if host == '0.0.0.0' else '（未开放端口，设置中改 host=0.0.0.0）':<43}│
│  数据目录:  {str(config.DATA_DIR)[:43]:<43}│
│  按 Ctrl+C 停止服务                                         │
└{bar}┘""")

    if auto_open:
        def _open():
            for _ in range(40):  # 等服务真正起来再弹页面
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.4):
                        break
                except OSError:
                    time.sleep(0.25)
            webbrowser.open(url_local)
        threading.Thread(target=_open, daemon=True).start()

    import uvicorn
    if getattr(sys, "frozen", False):
        # 冻结模式：直接传 app 对象（import string 在 PyInstaller 下依赖模块名可解析，传对象最稳）
        from app.main import app as fastapi_app
        uvicorn.run(fastapi_app, host=host, port=port, log_level="info", access_log=False)
    else:
        uvicorn.run("app.main:app", host=host, port=port, log_level="info", access_log=False)


if __name__ == "__main__":
    main()
