"""配置管理：config.json 落盘在程序根目录，路径全部绝对锚定，禁止依赖 CWD。"""
from __future__ import annotations

import json
import os
import re
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
APPLICATION_DIR = DATA_DIR / "applications"   # 投递门户收到的简历原件
MODELS_DIR = DATA_DIR / "models"
DB_PATH = DATA_DIR / "talent.db"
LOCK_PATH = DATA_DIR / "lock.json"            # 锁屏密码（独立文件：删除即解锁，见「忘记密码」指引）
STATIC_DIR = BUNDLE_DIR / "app" / "static"

DEFAULT_STATUSES = ["潜在", "跟进中", "已邀约", "已面试", "已录用", "不合适", "已归档"]

# 版权信息（程序内多处展示：侧栏页脚/设置页/启动横幅/投递页/落地页）
COPYRIGHT = {
    "owner": "慈溪八凯八威电子商务经营部",
    "author": "胡利杰 先生",
    "notice": "本软件为原创开发成果。商业使用需获得书面申请授权。",
    "phone": "13208088838",
    "email": "admin@8k8w.com",
}

DEFAULTS = {
    "host": "0.0.0.0",          # 默认开放 0.0.0.0，局域网与本机均可访问
    "port": 8300,
    "auto_open_browser": True,     # 本地运行时自动弹出仪表页面
    "ocr_enabled": True,           # 扫描版文档是否走内置 OCR
    "ocr_max_pages": 30,           # 单份文档最多 OCR 页数（防超大扫描件卡死）
    "upload_keep_original": True,  # 保留上传原件，可回看下载
    "llm_enabled": True,           # AI 智能解析总开关
    "llm_model_path": "",          # 留空自动发现 data/models/*.gguf；也可指定绝对路径
    "llm_min_confidence": 0.75,    # 规则解析置信度低于该值才动用 LLM（批量导入提速）
    "llm_engine": "local",         # local=本地GGUF模型 | api=外部 OpenAI 兼容 API（联网大模型）
    "llm_api_base": "",            # 如 https://api.deepseek.com/v1 （OpenAI 兼容 /chat/completions）
    "llm_api_key": "",             # API 密钥（仅存本地 config.json，设置页掩码显示）
    "llm_api_model": "",           # 如 deepseek-chat / gpt-4o-mini / qwen-plus
    "llm_gpu_layers": 0,           # 0=纯CPU；>0=GPU 卸载层数；-1=自动全部层（需 CUDA 版 llama-cpp-python）
    "custom_statuses": "",         # 自定义状态列表（顿号/逗号分隔）；空 = 默认七状态
    "apply_enabled": True,         # 投递门户（第二端口）开关
    "apply_port": 8301,            # 投递门户端口
    "apply_captcha_disabled": False,  # 关闭投递验证码（仅限完全可信内网；默认开启防滥用）
    "apply_company_name": "慈溪八凯八威电子商务经营部",  # 投递页显示的公司名
    "app_name": "TalentHub 企业人才库",
}

_lock = threading.Lock()


def ensure_dirs() -> None:
    for d in (DATA_DIR, UPLOAD_DIR, EXPORT_DIR, APPLICATION_DIR, MODELS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def get_statuses() -> list[str]:
    """当前生效的人才状态列表（自定义优先，空/损坏回退默认）。"""
    raw = str(load().get("custom_statuses") or "").strip()
    if not raw:
        return list(DEFAULT_STATUSES)
    items = [s.strip() for s in re.split(r"[、,，;；/|]", raw) if s.strip()]
    # 去重保序；至少保留一个状态，且限长防滥用
    seen, out = set(), []
    for s in items[:12]:
        s = s[:12]
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out or list(DEFAULT_STATUSES)


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
