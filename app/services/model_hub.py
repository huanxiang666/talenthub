"""模型下载中心：多源站（hf-mirror / ModelScope / HuggingFace 官方）下载 GGUF 小模型
+ 系统资源探测（内存/GPU），给用户"该下哪档"的明确建议。

- 后台线程流式下载到 data/models/<name>.part → 完整性校验（GGUF 魔数+尺寸下限）→ 原子改名
- 进度可查（downloaded/total/speed）；同一时间只允许一个下载任务
- 目录内所有国内源 URL 均在 2026-11-10 做过 HEAD 实测（200）；HF 官方源国内网络可能不可达属预期
"""
from __future__ import annotations

import re
import shutil
import subprocess
import threading
import time
import urllib.parse
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
        "label": "Qwen3-4B（新一代旗舰小杯，建议 8GB+ 内存）",
        "size_mb": 2500, "ram_mb": 4500,
        "urls": {
            "hf-mirror（国内推荐）": "https://hf-mirror.com/Qwen/Qwen3-4B-GGUF/resolve/main/Qwen3-4B-Q4_K_M.gguf",
            "ModelScope（国内）": "https://modelscope.cn/models/Qwen/Qwen3-4B-GGUF/resolve/master/Qwen3-4B-Q4_K_M.gguf",
            "HuggingFace（国际）": "https://huggingface.co/Qwen/Qwen3-4B-GGUF/resolve/main/Qwen3-4B-Q4_K_M.gguf",
        },
    },
    {
        "id": "qwen3-1.7b",
        "file": "Qwen3-1.7B-Q4_K_M.gguf",
        "label": "Qwen3-1.7B（新一代轻量，中文强）",
        "size_mb": 1200, "ram_mb": 2500,
        "urls": {
            "hf-mirror（国内推荐）": "https://hf-mirror.com/unsloth/Qwen3-1.7B-GGUF/resolve/main/Qwen3-1.7B-Q4_K_M.gguf",
            "ModelScope（国内）": "https://modelscope.cn/models/unsloth/Qwen3-1.7B-GGUF/resolve/master/Qwen3-1.7B-Q4_K_M.gguf",
            "HuggingFace（国际）": "https://huggingface.co/unsloth/Qwen3-1.7B-GGUF/resolve/main/Qwen3-1.7B-Q4_K_M.gguf",
        },
    },
    {
        "id": "gemma3-4b",
        "file": "gemma-3-4b-it-Q4_K_M.gguf",
        "label": "Gemma-3-4B（Google 中档，多语言）",
        "size_mb": 2500, "ram_mb": 4200,
        "urls": {
            "hf-mirror（国内推荐）": "https://hf-mirror.com/unsloth/gemma-3-4b-it-GGUF/resolve/main/gemma-3-4b-it-Q4_K_M.gguf",
            "ModelScope（国内）": "https://modelscope.cn/models/unsloth/gemma-3-4b-it-GGUF/resolve/master/gemma-3-4b-it-Q4_K_M.gguf",
            "HuggingFace（国际）": "https://huggingface.co/unsloth/gemma-3-4b-it-GGUF/resolve/main/gemma-3-4b-it-Q4_K_M.gguf",
        },
    },
    {
        "id": "qwen3-8b",
        "file": "Qwen3-8B-Q4_K_M.gguf",
        "label": "Qwen3-8B（准专业级，需 12GB+ 内存或 GPU）",
        "size_mb": 5000, "ram_mb": 7500,
        "urls": {
            "hf-mirror（国内推荐）": "https://hf-mirror.com/unsloth/Qwen3-8B-GGUF/resolve/main/Qwen3-8B-Q4_K_M.gguf",
            "ModelScope（国内）": "https://modelscope.cn/models/unsloth/Qwen3-8B-GGUF/resolve/master/Qwen3-8B-Q4_K_M.gguf",
            "HuggingFace（国际）": "https://huggingface.co/unsloth/Qwen3-8B-GGUF/resolve/main/Qwen3-8B-Q4_K_M.gguf",
        },
    },
    {
        "id": "deepseek-r1-7b",
        "file": "DeepSeek-R1-Distill-Qwen-7B-Q4_K_M.gguf",
        "label": "DeepSeek-R1-Distill-7B（🔥推理模型轻量档，需 8GB+ 内存）",
        "size_mb": 4700, "ram_mb": 7000,
        "urls": {
            "hf-mirror（国内推荐）": "https://hf-mirror.com/unsloth/DeepSeek-R1-Distill-Qwen-7B-GGUF/resolve/main/DeepSeek-R1-Distill-Qwen-7B-Q4_K_M.gguf",
            "ModelScope（国内）": "https://modelscope.cn/models/unsloth/DeepSeek-R1-Distill-Qwen-7B-GGUF/resolve/master/DeepSeek-R1-Distill-Qwen-7B-Q4_K_M.gguf",
            "HuggingFace（国际）": "https://huggingface.co/unsloth/DeepSeek-R1-Distill-Qwen-7B-GGUF/resolve/main/DeepSeek-R1-Distill-Qwen-7B-Q4_K_M.gguf",
        },
    },
    {
        "id": "gemma3-12b",
        "file": "gemma-3-12b-it-Q4_K_M.gguf",
        "label": "Gemma-3-12B（Google 大杯多语言，需 12GB+ 内存或 GPU）",
        "size_mb": 7200, "ram_mb": 10000,
        "urls": {
            "hf-mirror（国内推荐）": "https://hf-mirror.com/unsloth/gemma-3-12b-it-GGUF/resolve/main/gemma-3-12b-it-Q4_K_M.gguf",
            "ModelScope（国内）": "https://modelscope.cn/models/unsloth/gemma-3-12b-it-GGUF/resolve/master/gemma-3-12b-it-Q4_K_M.gguf",
            "HuggingFace（国际）": "https://huggingface.co/unsloth/gemma-3-12b-it-GGUF/resolve/main/gemma-3-12b-it-Q4_K_M.gguf",
        },
    },
    {
        "id": "qwen3-14b",
        "file": "Qwen3-14B-Q4_K_M.gguf",
        "label": "Qwen3-14B（专业级中文旗舰，需 16GB+ 内存或 GPU）",
        "size_mb": 9300, "ram_mb": 13000,
        "urls": {
            "hf-mirror（国内推荐）": "https://hf-mirror.com/unsloth/Qwen3-14B-GGUF/resolve/main/Qwen3-14B-Q4_K_M.gguf",
            "ModelScope（国内）": "https://modelscope.cn/models/unsloth/Qwen3-14B-GGUF/resolve/master/Qwen3-14B-Q4_K_M.gguf",
            "HuggingFace（国际）": "https://huggingface.co/unsloth/Qwen3-14B-GGUF/resolve/main/Qwen3-14B-Q4_K_M.gguf",
        },
    },
    {
        "id": "deepseek-r1-14b",
        "file": "DeepSeek-R1-Distill-Qwen-14B-Q4_K_M.gguf",
        "label": "DeepSeek-R1-Distill-14B（🔥热门推理模型，需 16GB+ 内存或 GPU）",
        "size_mb": 9000, "ram_mb": 12000,
        "urls": {
            "hf-mirror（国内推荐）": "https://hf-mirror.com/unsloth/DeepSeek-R1-Distill-Qwen-14B-GGUF/resolve/main/DeepSeek-R1-Distill-Qwen-14B-Q4_K_M.gguf",
            "ModelScope（国内）": "https://modelscope.cn/models/unsloth/DeepSeek-R1-Distill-Qwen-14B-GGUF/resolve/master/DeepSeek-R1-Distill-Qwen-14B-Q4_K_M.gguf",
            "HuggingFace（国际）": "https://huggingface.co/unsloth/DeepSeek-R1-Distill-Qwen-14B-GGUF/resolve/main/DeepSeek-R1-Distill-Qwen-14B-Q4_K_M.gguf",
        },
    },
    {
        "id": "qwen3-30b-a3b",
        "file": "Qwen3-30B-A3B-Q4_K_M.gguf",
        "label": "Qwen3-30B-A3B（🔥MoE 旗舰，激活仅3B速度快，需 24GB+ 内存或大显存）",
        "size_mb": 18700, "ram_mb": 22000,
        "urls": {
            "hf-mirror（国内推荐）": "https://hf-mirror.com/unsloth/Qwen3-30B-A3B-GGUF/resolve/main/Qwen3-30B-A3B-Q4_K_M.gguf",
            "ModelScope（国内）": "https://modelscope.cn/models/unsloth/Qwen3-30B-A3B-GGUF/resolve/master/Qwen3-30B-A3B-Q4_K_M.gguf",
            "HuggingFace（国际）": "https://huggingface.co/unsloth/Qwen3-30B-A3B-GGUF/resolve/main/Qwen3-30B-A3B-Q4_K_M.gguf",
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
    """按可用内存/GPU 给出档位建议（v1.3：覆盖 0.5B~14B 全谱系）。"""
    avail = ram.get("available_mb", 0) or ram.get("total_mb", 0)
    vram = gpu.get("vram_mb", 0) if gpu.get("available") else 0
    if vram >= 20000 or avail >= 24000:
        pick, why = "qwen3-30b-a3b", f"资源顶配（内存 {avail // 1024}GB" + (f"，显存 {vram // 1024}GB" if vram else "") + "），推荐 Qwen3-30B-A3B MoE 旗舰（激活仅 3B，推理快）"
    elif vram >= 14000 or avail >= 13000:
        pick, why = "qwen3-14b", f"资源充足（内存 {avail // 1024}GB" + (f"，显存 {vram // 1024}GB" if vram else "") + "），推荐 Qwen3-14B 专业级中文旗舰"
    elif vram >= 9000 or avail >= 8500:
        pick, why = "qwen3-8b", f"可用内存约 {avail // 1024}GB" + ("（含 GPU）" if vram else "") + "，推荐 Qwen3-8B 准专业档"
    elif vram >= 6000 or avail >= 5500:
        pick, why = "qwen3-4b", f"可用内存约 {avail // 1024}GB，推荐 Qwen3-4B 新一代旗舰小杯"
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


UA = {"User-Agent": "TalentHub/1.3"}
DL_THREADS = 4          # 多线程分段下载并发数（服务器支持 Range 时启用）
MIN_MT_SIZE = 32 * 1024 * 1024   # 小于 32MB 不值得分段


def _head_info(url: str) -> tuple[int, bool]:
    """HEAD 探测：返回 (content_length, 是否支持 Range 分段)。"""
    req = urllib.request.Request(url, headers=UA, method="HEAD")
    with urllib.request.urlopen(req, timeout=30) as resp:
        size = int(resp.headers.get("Content-Length") or 0)
        ranges = (resp.headers.get("Accept-Ranges") or "").lower() == "bytes"
    return size, ranges


def _download_worker(url: str, dest: Path, declared_mb: int):
    """下载器：优先 4 线程 Range 分段并发（大文件提速明显），不支持则单流回退。
    完成后校验 GGUF 魔数 + 声明尺寸下限，通过才原子改名。"""
    tmp = dest.with_suffix(dest.suffix + ".part")
    t0 = time.time()

    def _bump(n: int):
        now = time.time()
        with _state_lock:
            _state["downloaded"] += n
            if now - t0 > 0.5:
                _state["speed_bps"] = _state["downloaded"] / (now - t0)

    def _seg(start: int, end: int):
        """下载 [start, end] 字节区间并写入 tmp 对应偏移。"""
        req = urllib.request.Request(url, headers={**UA, "Range": f"bytes={start}-{end}"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            with open(tmp, "r+b") as f:
                f.seek(start)
                while True:
                    chunk = resp.read(1024 * 512)
                    if not chunk:
                        break
                    f.write(chunk)
                    _bump(len(chunk))

    try:
        size, ranges = _head_info(url)
        with _state_lock:
            _state.update(active=True, total=size or declared_mb * 1048576,
                          downloaded=0, error="", done_file="")
        multi = ranges and size > MIN_MT_SIZE
        if multi:
            with open(tmp, "wb") as f:
                f.truncate(size)          # 预分配全尺寸，各线程按偏移写入
            step = size // DL_THREADS
            segs = [(i * step, (size - 1) if i == DL_THREADS - 1 else (i + 1) * step - 1)
                    for i in range(DL_THREADS)]
            errs: list[Exception] = []

            def _run(a: int, b: int):
                try:
                    _seg(a, b)
                except Exception as e:    # 收集分段错误，主线程统一抛
                    errs.append(e)
            ts = [threading.Thread(target=_run, args=s) for s in segs]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
            if errs:
                raise errs[0]
        else:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as resp:
                total = int(resp.headers.get("Content-Length") or 0)
                with _state_lock:
                    _state["total"] = total or _state["total"]
                with open(tmp, "wb") as f:
                    while True:
                        chunk = resp.read(1024 * 256)
                        if not chunk:
                            break
                        f.write(chunk)
                        _bump(len(chunk))
        # 完整性校验：GGUF 魔数 + 尺寸下限（防半截/错误页落盘）
        with open(tmp, "rb") as f:
            magic = f.read(4)
        real_mb = tmp.stat().st_size / 1048576
        if magic != b"GGUF":
            raise ValueError(f"文件魔数非 GGUF（实际 {magic!r}），下载内容不是模型文件")
        if declared_mb and real_mb < declared_mb * 0.8:
            raise ValueError(f"文件过小：{real_mb:.0f}MB < 预期 {declared_mb}MB 的 80%，可能下载中断")
        if size and tmp.stat().st_size != size:
            raise ValueError(f"尺寸不符：{tmp.stat().st_size} != {size}，下载不完整")
        tmp.replace(dest)
        with _state_lock:
            _state.update(active=False, done_file=str(dest), error="",
                          speed_bps=_state["downloaded"] / max(time.time() - t0, 0.1))
    except Exception as e:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        with _state_lock:
            _state.update(active=False, error=f"{type(e).__name__}: {e}")


def _begin_download(url: str, dest: Path, declared_mb: int, source: str) -> dict:
    global _thread
    with _state_lock:
        if _state["active"]:
            raise ValueError(f"已有下载进行中：{_state['file']}（{progress()['percent']}%）")
    config.ensure_dirs()
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        raise ValueError(f"模型已存在：{dest.name}（无需重复下载）")
    with _state_lock:
        _state.update(active=True, file=dest.name, source=source,
                      downloaded=0, total=declared_mb * 1048576, error="", done_file="")
    _thread = threading.Thread(target=_download_worker, args=(url, dest, declared_mb), daemon=True)
    _thread.start()
    return {"started": True, "file": dest.name, "source": source, "dest": str(dest)}


def start_download(model_id: str, source: str) -> dict:
    """启动目录模型下载。model_id=目录 id；source=urls 的键名。"""
    model = next((m for m in MODEL_CATALOG if m["id"] == model_id), None)
    if not model:
        raise ValueError(f"未知模型 id: {model_id}")
    url = model["urls"].get(source)
    if not url:
        raise ValueError(f"未知源站: {source}，可选 {list(model['urls'])}")
    return _begin_download(url, config.MODELS_DIR / model["file"], model["size_mb"], source)


def start_download_url(url: str, filename: str = "") -> dict:
    """自定义链接下载（多线程分段）。url 必须 http(s)；filename 缺省从 URL 推断。"""
    url = (url or "").strip()
    if not re.match(r"^https?://", url):
        raise ValueError("仅支持 http/https 直链")
    if not filename:
        tail = url.split("?")[0].rstrip("/").rsplit("/", 1)[-1]
        filename = urllib.parse.unquote(tail)
    filename = Path(filename).name          # 去路径成分（防目录逃逸）
    if not filename.lower().endswith(".gguf"):
        filename += ".gguf"
    filename = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", filename)[:120]
    # HEAD 预检：链接可达 + 拿尺寸（拿不到尺寸用 0，下载后靠魔数校验兜底）
    try:
        size, _ = _head_info(url)
    except Exception as e:
        raise ValueError(f"链接不可达或拒绝 HEAD 请求: {type(e).__name__}: {e}")
    return _begin_download(url, config.MODELS_DIR / filename,
                           round(size / 1048576) if size else 0, "自定义链接")


MAX_IMPORT_GB = 16


def import_model_stream(filename: str, fileobj) -> dict:
    """本地导入 GGUF 模型：流式写盘（大文件不吃内存），首块校验魔数，
    先写 .uploading 临时文件，成功后原子改名。"""
    name = Path(filename or "model.gguf").name
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name)[:120]
    if not name.lower().endswith(".gguf"):
        name += ".gguf"
    config.ensure_dirs()
    dest = config.MODELS_DIR / name
    if dest.exists():
        raise ValueError(f"同名模型已存在：{name}（请先删除或改名）")
    tmp = dest.with_suffix(".gguf.uploading")
    written = 0
    try:
        with open(tmp, "wb") as f:
            first = True
            while True:
                chunk = fileobj.read(1024 * 1024 * 4)
                if not chunk:
                    break
                if first:
                    if chunk[:4] != b"GGUF":
                        raise ValueError(f"文件魔数非 GGUF（实际 {chunk[:4]!r}），不是有效的模型文件")
                    first = False
                written += len(chunk)
                if written > MAX_IMPORT_GB * 1024 * 1024 * 1024:
                    raise ValueError(f"模型文件超过 {MAX_IMPORT_GB}GB 限制")
                f.write(chunk)
        if written == 0:
            raise ValueError("文件内容为空")
        tmp.replace(dest)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return {"imported": True, "file": name, "size_mb": round(written / 1048576, 1),
            "dest": str(dest)}
