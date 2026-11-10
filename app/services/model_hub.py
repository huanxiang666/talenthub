"""模型下载中心：多源站（hf-mirror / ModelScope / HuggingFace 官方）下载 GGUF 小模型。

- 后台线程流式下载到 data/models/<name>.part → 完成后原子改名
- 进度可查（downloaded/total/speed）；同一时间只允许一个下载任务
- URL 均在 2026-11-10 做过 HEAD 实测（HF 官方源国内网络可能不可达，属预期）
"""
from __future__ import annotations

import threading
import time
import urllib.request
from pathlib import Path

from .. import config

MODEL_CATALOG = [
    {
        "id": "qwen2.5-0.5b",
        "file": "qwen2.5-0.5b-instruct-q4_k_m.gguf",
        "label": "Qwen2.5-0.5B-Instruct（极速版，内存约 +0.6GB，抽取质量中）",
        "size_mb": 370,
        "urls": {
            "hf-mirror（国内推荐）": "https://hf-mirror.com/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/qwen2.5-0.5b-instruct-q4_k_m.gguf",
            "ModelScope（国内）": "https://modelscope.cn/models/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/master/qwen2.5-0.5b-instruct-q4_k_m.gguf",
            "HuggingFace（国际）": "https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/qwen2.5-0.5b-instruct-q4_k_m.gguf",
        },
    },
    {
        "id": "qwen2.5-1.5b",
        "file": "qwen2.5-1.5b-instruct-q4_k_m.gguf",
        "label": "Qwen2.5-1.5B-Instruct（推荐，内存约 +1.5GB，质量/速度均衡）",
        "size_mb": 1066,
        "urls": {
            "hf-mirror（国内推荐）": "https://hf-mirror.com/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf",
            "ModelScope（国内）": "https://modelscope.cn/models/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/master/qwen2.5-1.5b-instruct-q4_k_m.gguf",
            "HuggingFace（国际）": "https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf",
        },
    },
    {
        "id": "qwen2.5-3b",
        "file": "qwen2.5-3b-instruct-q4_k_m.gguf",
        "label": "Qwen2.5-3B-Instruct（更准，内存约 +2.5GB，解析较慢）",
        "size_mb": 2008,
        "urls": {
            "hf-mirror（国内推荐）": "https://hf-mirror.com/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf",
            "ModelScope（国内）": "https://modelscope.cn/models/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/master/qwen2.5-3b-instruct-q4_k_m.gguf",
            "HuggingFace（国际）": "https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf",
        },
    },
]

_state_lock = threading.Lock()
_state: dict = {"active": False, "file": "", "source": "", "downloaded": 0, "total": 0,
                "speed_bps": 0.0, "error": "", "done_file": ""}
_thread: threading.Thread | None = None


def catalog() -> list[dict]:
    """模型目录 + 本地已有模型标记。"""
    models_dir = config.DATA_DIR / "models"
    local = {p.name: p.stat().st_size for p in models_dir.glob("*.gguf")} if models_dir.exists() else {}
    out = []
    for m in MODEL_CATALOG:
        item = dict(m)
        item["downloaded"] = m["file"] in local
        item["local_size_mb"] = round(local.get(m["file"], 0) / 1048576, 1)
        out.append(item)
    # 目录外的本地模型（用户自行放置的 gguf）也列出来
    known = {m["file"] for m in MODEL_CATALOG}
    for name, size in local.items():
        if name not in known:
            out.append({"id": name, "file": name, "label": f"{name}（用户自行放置）",
                        "size_mb": round(size / 1048576, 1), "urls": {},
                        "downloaded": True, "local_size_mb": round(size / 1048576, 1)})
    return out


def progress() -> dict:
    with _state_lock:
        s = dict(_state)
    s["percent"] = round(s["downloaded"] / s["total"] * 100, 1) if s["total"] else 0
    s["downloaded_mb"] = round(s["downloaded"] / 1048576, 1)
    s["total_mb"] = round(s["total"] / 1048576, 1)
    s["speed_mbps"] = round(s["speed_bps"] / 1048576, 2)
    return s


def _download_worker(url: str, dest: Path):
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "TalentHub/1.1"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            with _state_lock:
                _state.update(active=True, total=total, downloaded=0, error="", done_file="")
            t0 = time.time()
            got = 0
            with open(tmp, "wb") as f:
                while True:
                    chunk = resp.read(1024 * 256)
                    if not chunk:
                        break
                    f.write(chunk)
                    got += len(chunk)
                    now = time.time()
                    with _state_lock:
                        _state["downloaded"] = got
                        if now - t0 > 0.5:
                            _state["speed_bps"] = got / (now - t0)
        # 完整性下限校验：GGUF 魔数 + 尺寸不小于目录声明的 80%（防半截/错误页落盘）
        with open(tmp, "rb") as f:
            magic = f.read(4)
        declared = next((m["size_mb"] for m in MODEL_CATALOG if m["file"] == dest.name), 0)
        real_mb = tmp.stat().st_size / 1048576
        if magic != b"GGUF":
            raise ValueError(f"文件魔数非 GGUF（实际 {magic!r}），下载内容不是模型")
        if declared and real_mb < declared * 0.8:
            raise ValueError(f"文件过小：{real_mb:.0f}MB < 预期 {declared}MB 的 80%，可能下载中断")
        tmp.replace(dest)
        with _state_lock:
            _state.update(active=False, done_file=str(dest), error="")
    except Exception as e:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        with _state_lock:
            _state.update(active=False, error=f"{type(e).__name__}: {e}")


def start_download(model_id: str, source: str) -> dict:
    """启动后台下载。model_id=目录 id；source=urls 的键名。"""
    global _thread
    with _state_lock:
        if _state["active"]:
            raise ValueError(f"已有下载进行中：{_state['file']}（{progress()['percent']}%）")
    model = next((m for m in MODEL_CATALOG if m["id"] == model_id), None)
    if not model:
        raise ValueError(f"未知模型 id: {model_id}，可选 {[m['id'] for m in MODEL_CATALOG]}")
    url = model["urls"].get(source)
    if not url:
        raise ValueError(f"未知源站: {source}，可选 {list(model['urls'])}")
    config.ensure_dirs()
    dest = config.DATA_DIR / "models" / model["file"]
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        raise ValueError(f"模型已存在：{dest}（无需重复下载）")
    with _state_lock:
        _state.update(active=True, file=model["file"], source=source,
                      downloaded=0, total=model["size_mb"] * 1048576, error="", done_file="")
    _thread = threading.Thread(target=_download_worker, args=(url, dest), daemon=True)
    _thread.start()
    return {"started": True, "file": model["file"], "source": source, "dest": str(dest)}
