"""模型下载中心：多源站（hf-mirror / ModelScope / HuggingFace 官方）下载 GGUF 小模型
+ 系统资源探测（内存/GPU），给用户"该下哪档"的明确建议。

- 后台线程流式下载到 data/models/<name>.part → 完整性校验（GGUF 魔数+尺寸下限）→ 原子改名
- 进度可查（downloaded/total/speed）；同一时间只允许一个下载任务
- 目录内所有国内源 URL 均在 2026-11-10 做过 HEAD 实测（200）；HF 官方源国内网络可能不可达属预期
"""
from __future__ import annotations

import shutil
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

from .. import config

# id/文件/展示名/下载体积MB/运行峰值内存MB估算/源站
MODEL_CATALOG = [
    {
        "id": "qwen2.5-0.5b",
        "file": "qwen2.5-0.5b-instruct-q4_k_m.gguf",
        "label": "Qwen2.5-0.5B（极速，中文好）",
        "size_mb": 469, "ram_mb": 1200,
        "urls": {
            "hf-mirror（国内推荐）": "https://hf-mirror.com/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/qwen2.5-0.5b-instruct-q4_k_m.gguf",
            "ModelScope（国内）": "https://modelscope.cn/models/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/master/qwen2.5-0.5b-instruct-q4_k_m.gguf",
            "HuggingFace（国际）": "https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/qwen2.5-0.5b-instruct-q4_k_m.gguf",
        },
    },
    {
        "id": "gemma3-1b",
        "file": "gemma-3-1b-it-Q4_K_M.gguf",
        "label": "Gemma-3-1B（Google，轻量多语）",
        "size_mb": 814, "ram_mb": 1600,
        "urls": {
            "hf-mirror（国内推荐）": "https://hf-mirror.com/unsloth/gemma-3-1b-it-GGUF/resolve/main/gemma-3-1b-it-Q4_K_M.gguf",
            "ModelScope（国内）": "https://modelscope.cn/models/unsloth/gemma-3-1b-it-GGUF/resolve/master/gemma-3-1b-it-Q4_K_M.gguf",
            "HuggingFace（国际）": "https://huggingface.co/unsloth/gemma-3-1b-it-GGUF/resolve/main/gemma-3-1b-it-Q4_K_M.gguf",
        },
    },
    {
        "id": "llama3.2-1b",
        "file": "Llama-3.2-1B-Instruct-Q4_K_M.gguf",
        "label": "Llama-3.2-1B（Meta，英文强/中文一般）",
        "size_mb": 786, "ram_mb": 1500,
        "urls": {
            "hf-mirror（国内推荐）": "https://hf-mirror.com/unsloth/Llama-3.2-1B-Instruct-GGUF/resolve/main/Llama-3.2-1B-Instruct-Q4_K_M.gguf",
            "ModelScope（国内）": "https://modelscope.cn/models/unsloth/Llama-3.2-1B-Instruct-GGUF/resolve/master/Llama-3.2-1B-Instruct-Q4_K_M.gguf",
            "HuggingFace（国际）": "https://huggingface.co/unsloth/Llama-3.2-1B-Instruct-GGUF/resolve/main/Llama-3.2-1B-Instruct-Q4_K_M.gguf",
        },
    },
    {
        "id": "qwen2.5-1.5b",
        "file": "qwen2.5-1.5b-instruct-q4_k_m.gguf",
        "label": "Qwen2.5-1.5B（★推荐，质量/速度均衡，中文好）",
        "size_mb": 1066, "ram_mb": 2200,
        "urls": {
            "hf-mirror（国内推荐）": "https://hf-mirror.com/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf",
            "ModelScope（国内）": "https://modelscope.cn/models/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/master/qwen2.5-1.5b-instruct-q4_k_m.gguf",
            "HuggingFace（国际）": "https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf",
        },
    },
    {
        "id": "qwen2.5-3b",
        "file": "qwen2.5-3b-instruct-q4_k_m.gguf",
        "label": "Qwen2.5-3B（更准，中文好，解析较慢）",
        "size_mb": 2008, "ram_mb": 3500,
        "urls": {
            "hf-mirror（国内推荐）": "https://hf-mirror.com/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf",
            "ModelScope（国内）": "https://modelscope.cn/models/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/master/qwen2.5-3b-instruct-q4_k_m.gguf",
            "HuggingFace（国际）": "https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf",
        },
    },
    {
        "id": "llama3.2-3b",
        "file": "Llama-3.2-3B-Instruct-Q4_K_M.gguf",
        "label": "Llama-3.2-3B（Meta，英文强/中文一般）",
        "size_mb": 1920, "ram_mb": 3300,
        "urls": {
            "hf-mirror（国内推荐）": "https://hf-mirror.com/unsloth/Llama-3.2-3B-Instruct-GGUF/resolve/main/Llama-3.2-3B-Instruct-Q4_K_M.gguf",
            "HuggingFace（国际）": "https://huggingface.co/unsloth/Llama-3.2-3B-Instruct-GGUF/resolve/main/Llama-3.2-3B-Instruct-Q4_K_M.gguf",
        },
    },
    {
        "id": "qwen3-4b",
        "file": "Qwen3-4B-Q4_K_M.gguf",
        "label": "Qwen3-4B（最准，新一代，建议 8GB+ 内存或 GPU）",
        "size_mb": 2500, "ram_mb": 4500,
        "urls": {
            "hf-mirror（国内推荐）": "https://hf-mirror.com/Qwen/Qwen3-4B-GGUF/resolve/main/Qwen3-4B-Q4_K_M.gguf",
            "ModelScope（国内）": "https://modelscope.cn/models/Qwen/Qwen3-4B-GGUF/resolve/master/Qwen3-4B-Q4_K_M.gguf",
            "HuggingFace（国际）": "https://huggingface.co/Qwen/Qwen3-4B-GGUF/resolve/main/Qwen3-4B-Q4_K_M.gguf",
        },
    },
]

_state_lock = threading.Lock()
_state: dict = {"active": False, "file": "", "source": "", "downloaded": 0, "total": 0,
                "speed_bps": 0.0, "error": "", "done_file": ""}
_thread: threading.Thread | None = None


# ---------- 系统资源探测 ----------

def detect_ram() -> dict:
    """总内存/可用内存（MB）。Linux 读 /proc/meminfo；Windows 走 ctypes；macOS sysctl。"""
    import platform as _pf
    total = avail = 0
    try:
        sysname = _pf.system()
        if sysname == "Linux":
            info = {}
            with open("/proc/meminfo", encoding="utf-8") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2:
                        info[parts[0].rstrip(":")] = int(parts[1])  # kB
            total = info.get("MemTotal", 0) // 1024
            avail = info.get("MemAvailable", info.get("MemFree", 0)) // 1024
        elif sysname == "Windows":
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            ms = MEMORYSTATUSEX()
            ms.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms))
            total = ms.ullTotalPhys // 1048576
            avail = ms.ullAvailPhys // 1048576
        elif sysname == "Darwin":
            total = int(subprocess.check_output(["sysctl", "-n", "hw.memsize"], timeout=5)) // 1048576
            avail = total // 2  # macOS 无直接可用值，粗估
    except Exception:
        pass
    return {"total_mb": total, "available_mb": avail}


def detect_gpu() -> dict:
    """NVIDIA GPU 探测（nvidia-smi）。无卡/无驱动返回 available=False。"""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return {"available": False, "name": "", "vram_mb": 0,
                "hint": "未检测到 NVIDIA 显卡或驱动（将使用 CPU 推理）"}
    try:
        out = subprocess.check_output(
            [exe, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            timeout=8).decode().strip().splitlines()
        if not out:
            return {"available": False, "name": "", "vram_mb": 0, "hint": "nvidia-smi 无输出"}
        name, vram = [x.strip() for x in out[0].split(",")[:2]]
        return {"available": True, "name": name, "vram_mb": int(float(vram)),
                "hint": f"检测到 {name}（{vram}MB 显存）：安装 CUDA 版 llama-cpp-python 后，在设置中把 GPU 卸载层数设为 -1 即可加速"}
    except Exception as e:
        return {"available": False, "name": "", "vram_mb": 0, "hint": f"GPU 探测失败: {type(e).__name__}"}


def recommend(ram: dict, gpu: dict) -> dict:
    """按可用内存/GPU 给出档位建议。"""
    avail = ram.get("available_mb", 0) or ram.get("total_mb", 0)
    if gpu.get("available") and gpu.get("vram_mb", 0) >= 6000:
        pick, why = "qwen3-4b", f"检测到 GPU（{gpu['vram_mb']}MB 显存），可跑最大档位并开启 GPU 加速"
    elif avail >= 5500:
        pick, why = "qwen3-4b", f"可用内存约 {avail // 1024}GB，可跑最准的 Qwen3-4B 档"
    elif avail >= 3800:
        pick, why = "qwen2.5-3b", f"可用内存约 {avail // 1024}GB，推荐 3B 档（更准）"
    elif avail >= 2400:
        pick, why = "qwen2.5-1.5b", f"可用内存约 {avail // 1024}GB，推荐 1.5B 档（均衡）"
    elif avail >= 1200:
        pick, why = "qwen2.5-0.5b", f"可用内存约 {avail // 1024}GB，建议 0.5B 轻量档"
    else:
        pick, why = "", "可用内存偏小，建议在设置页改用「外部 API 引擎」（联网大模型，如 DeepSeek），不占本机资源"
    return {"model_id": pick, "reason": why}


def catalog() -> list[dict]:
    """模型目录 + 本地已有模型标记。"""
    models_dir = config.MODELS_DIR
    local = {p.name: p.stat().st_size for p in models_dir.glob("*.gguf")} if models_dir.exists() else {}
    out = []
    for m in MODEL_CATALOG:
        item = dict(m)
        item["downloaded"] = m["file"] in local
        item["local_size_mb"] = round(local.get(m["file"], 0) / 1048576, 1)
        out.append(item)
    known = {m["file"] for m in MODEL_CATALOG}
    for name, size in local.items():
        if name not in known:
            out.append({"id": name, "file": name, "label": f"{name}（用户自行放置）",
                        "size_mb": round(size / 1048576), "ram_mb": 0, "urls": {},
                        "downloaded": True, "local_size_mb": round(size / 1048576, 1)})
    return out


def system_resources() -> dict:
    ram = detect_ram()
    gpu = detect_gpu()
    return {"ram": ram, "gpu": gpu, "recommend": recommend(ram, gpu)}


def progress() -> dict:
    with _state_lock:
        s = dict(_state)
    s["percent"] = round(s["downloaded"] / s["total"] * 100, 1) if s["total"] else 0
    s["downloaded_mb"] = round(s["downloaded"] / 1048576, 1)
    s["total_mb"] = round(s["total"] / 1048576, 1)
    s["speed_mbps"] = round(s["speed_bps"] / 1048576, 2)
    return s


def _download_worker(url: str, dest: Path, declared_mb: int):
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "TalentHub/1.2"})
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
        # 完整性校验：GGUF 魔数 + 尺寸不小于声明的 80%（防半截/错误页落盘）
        with open(tmp, "rb") as f:
            magic = f.read(4)
        real_mb = tmp.stat().st_size / 1048576
        if magic != b"GGUF":
            raise ValueError(f"文件魔数非 GGUF（实际 {magic!r}），下载内容不是模型")
        if declared_mb and real_mb < declared_mb * 0.8:
            raise ValueError(f"文件过小：{real_mb:.0f}MB < 预期 {declared_mb}MB 的 80%，可能下载中断")
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
        raise ValueError(f"未知模型 id: {model_id}")
    url = model["urls"].get(source)
    if not url:
        raise ValueError(f"未知源站: {source}，可选 {list(model['urls'])}")
    config.ensure_dirs()
    dest = config.MODELS_DIR / model["file"]
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        raise ValueError(f"模型已存在：{dest}（无需重复下载）")
    with _state_lock:
        _state.update(active=True, file=model["file"], source=source,
                      downloaded=0, total=model["size_mb"] * 1048576, error="", done_file="")
    _thread = threading.Thread(target=_download_worker, args=(url, dest, model["size_mb"]), daemon=True)
    _thread.start()
    return {"started": True, "file": model["file"], "source": source, "dest": str(dest)}
