"""投递门户验证码：PIL 绘制字符图形验证码（内存会话，TTL 5 分钟）。

字体探测：优先 DejaVuSans（Linux 常见，ASCII 全）→ Windows arial → 任意 TTF →
PIL 默认位图字体（兜底，丑但可用）。
"""
from __future__ import annotations

import io
import random
import string
import threading
import time
import uuid
from pathlib import Path

CHARS = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"  # 去掉易混淆的 0O1I
_TTL = 300
_store: dict[str, tuple[str, float]] = {}
_lock = threading.Lock()
_font_path_cache: list = []


def _find_font(size: int):
    from PIL import ImageFont
    if not _font_path_cache:
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
            "C:/Windows/Fonts/arial.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ]
        _font_path_cache.append(next((c for c in candidates if Path(c).exists()), ""))
    p = _font_path_cache[0]
    try:
        return ImageFont.truetype(p, size) if p else ImageFont.load_default(size)
    except (OSError, TypeError):
        return ImageFont.load_default()


def _cleanup():
    now = time.time()
    with _lock:
        for k in [k for k, (_, ts) in _store.items() if now - ts > _TTL]:
            _store.pop(k, None)


def generate() -> tuple[str, bytes]:
    """生成验证码：返回 (captcha_id, png_bytes)。"""
    from PIL import Image, ImageDraw
    code = "".join(random.choices(CHARS, k=4))
    cid = uuid.uuid4().hex
    _cleanup()
    with _lock:
        if len(_store) > 500:  # 内存上限保护
            _store.clear()
        _store[cid] = (code, time.time())

    W, H = 132, 44
    img = Image.new("RGB", (W, H), (250, 251, 252))
    d = ImageDraw.Draw(img)
    font = _find_font(30)
    # 干扰线 + 噪点
    for _ in range(4):
        d.line([random.randint(0, W), random.randint(0, H),
                random.randint(0, W), random.randint(0, H)],
               fill=(random.randint(150, 210),) * 3, width=1)
    for _ in range(60):
        d.point((random.randint(0, W - 1), random.randint(0, H - 1)),
                fill=(random.randint(120, 200),) * 3)
    # 字符逐位绘制，带随机偏移/旋转感
    x = 10
    for ch in code:
        dy = random.randint(-3, 5)
        color = (random.randint(20, 90), random.randint(40, 110), random.randint(90, 170))
        d.text((x, 5 + dy), ch, font=font, fill=color)
        x += random.randint(26, 30)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return cid, buf.getvalue()


def verify(captcha_id: str, code: str) -> bool:
    """一次性校验：无论对错都消费掉该 id（防重放/爆破）。"""
    if not captcha_id or not code:
        return False
    with _lock:
        item = _store.pop(captcha_id, None)
    if not item:
        return False
    stored, ts = item
    if time.time() - ts > _TTL:
        return False
    return stored.lower() == str(code).strip().lower()
