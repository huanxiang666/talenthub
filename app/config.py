"""配置管理：config.json 落盘在程序根目录，路径全部绝对锚定，禁止依赖 CWD。"""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

# 双模式路径锚定：
#  - 源码运行：项目根 = app/ 的上一级；数据/配置/静态资源都在项目根下。
#  - PyInstaller 冻结（sys.frozen）：BASE_DIR = exe 所在目录（数据/config.json 写在 exe 旁边，
#    用户可直接看到并备份）；BUNDLE_DIR = 打包资源只读目录（sys._MEIPASS），静态页面从这里读。
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
    BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", BASE_DIR))
else:
    BASE_DIR = Path(__file__).resolve().parent.parent
    BUNDLE_DIR = BASE_DIR

PROJECT_ROOT = BASE_DIR
CONFIG_PATH = BASE_DIR / "config.json"
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
EXPORT_DIR = DATA_DIR / "exports"
DB_PATH = DATA_DIR / "talent.db"
STATIC_DIR = BUNDLE_DIR / "app" / "static"

DEFAULTS = {
    "host": "127.0.0.1",          # 改 0.0.0.0 即"开放端口"，局域网可访问
    "port": 8300,
    "auto_open_browser": True,     # 本地运行时自动弹出仪表页面
    "ocr_enabled": True,           # 扫描版文档是否走内置 OCR
    "ocr_max_pages": 30,           # 单份文档最多 OCR 页数（防超大扫描件卡死）
    "upload_keep_original": True,  # 保留上传原件，可回看下载
    "app_name": "TalentHub 企业人才库",
}

_lock = threading.Lock()


def ensure_dirs() -> None:
    for d in (DATA_DIR, UPLOAD_DIR, EXPORT_DIR):
        d.mkdir(parents=True, exist_ok=True)


def load() -> dict:
    """读取配置；缺失/损坏时回退默认值（损坏文件保留为 .bad 以便排查）。"""
    ensure_dirs()
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                user = json.load(f)
            if isinstance(user, dict):
                cfg.update({k: user[k] for k in DEFAULTS if k in user})
        except (json.JSONDecodeError, OSError) as e:
            bad = CONFIG_PATH.with_suffix(".json.bad")
            try:
                os.replace(CONFIG_PATH, bad)
            except OSError:
                pass
            cfg["_config_error"] = f"config.json 解析失败已备份到 {bad}: {e}"
    return cfg


def save(patch: dict) -> dict:
    """合并保存配置，返回最终配置。只接受 DEFAULTS 里存在的键。"""
    with _lock:
        cfg = load()
        for k, v in patch.items():
            if k in DEFAULTS:
                cfg[k] = v
        tmp = CONFIG_PATH.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({k: cfg[k] for k in DEFAULTS}, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_PATH)
        return cfg


def get() -> dict:
    return load()
