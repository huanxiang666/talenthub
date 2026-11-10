"""内置 OCR 引擎：RapidOCR（PP-OCRv4 模型，ONNX Runtime 推理，中英混排，全离线）。

- 懒加载：首次用到才加载模型（约 1-2s），平时不占内存。
- 线程锁：onnxruntime 会话不允许并发调用，串行化。
"""
from __future__ import annotations

import threading
from typing import Optional

_engine = None
_lock = threading.Lock()
_load_error: Optional[str] = None


def _get_engine():
    global _engine, _load_error
    if _engine is not None:
        return _engine
    with _lock:
        if _engine is not None:
            return _engine
        try:
            from rapidocr_onnxruntime import RapidOCR
            _engine = RapidOCR()
            _load_error = None
        except Exception as e:  # 显式记录并上抛，不吞错误
            _load_error = f"{type(e).__name__}: {e}"
            raise RuntimeError(f"OCR 引擎加载失败: {_load_error}") from e
    return _engine


def status() -> dict:
    """OCR 可用性状态（供设置页/健康检查展示）。不触发模型加载。"""
    import importlib.util
    installed = importlib.util.find_spec("rapidocr_onnxruntime") is not None
    return {
        "installed": installed,
        "loaded": _engine is not None,
        "engine": "RapidOCR (PP-OCRv4, ONNX Runtime, 离线)",
        "load_error": _load_error,
    }


def ocr_image(img) -> list[dict]:
    """对 PIL.Image / ndarray / 文件路径执行 OCR，返回 [{text, score, box}] 按阅读顺序。"""
    engine = _get_engine()
    import numpy as np
    if hasattr(img, "convert"):  # PIL.Image
        arr = np.array(img.convert("RGB"))
    else:
        arr = img
    with _lock:
        result, _elapse = engine(arr)
    lines: list[dict] = []
    for item in result or []:
        box, text, score = item[0], item[1], float(item[2])
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        lines.append({"text": text, "score": score,
                      "x": min(xs), "y": min(ys), "h": max(ys) - min(ys)})
    # 阅读顺序：按 y 分行（半行高容差），行内按 x
    lines.sort(key=lambda d: (d["y"], d["x"]))
    ordered: list[dict] = []
    cur_y: Optional[float] = None
    row: list[dict] = []
    for ln in lines:
        tol = max(8.0, ln["h"] * 0.6)
        if cur_y is not None and abs(ln["y"] - cur_y) > tol:
            row.sort(key=lambda d: d["x"])
            ordered.extend(row)
            row = []
            cur_y = ln["y"]   # 换行必须重置锚点，否则全局 min 会让容差判断失效（复核建议#5）
        else:
            cur_y = ln["y"] if cur_y is None else min(cur_y, ln["y"])
        row.append(ln)
    row.sort(key=lambda d: d["x"])
    ordered.extend(row)
    return ordered
