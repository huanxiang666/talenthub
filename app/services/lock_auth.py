"""锁屏鉴权：本地密码锁保护管理端 API 与页面数据。

设计：
- 密码 PBKDF2-HMAC-SHA256（12 万次迭代）加盐哈希，存 data/lock.json（独立文件）。
- 解锁成功签发 HMAC 会话令牌（12 小时有效），前端存 sessionStorage，请求带 Bearer 头。
- 锁定启用后，除白名单（health/auth/静态资源/投递门户）外所有 /api/* 需要有效令牌。
- 忘记密码：停止程序后删除 data/lock.json 即可清除锁（人才数据不受影响）——
  锁屏页与 README 均给出该指引。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path

from .. import config

ITERATIONS = 120_000
TOKEN_TTL_SECONDS = 12 * 3600
MIN_PASSWORD_LEN = 6


def _read() -> dict | None:
    p = config.LOCK_PATH
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _write(data: dict) -> None:
    config.ensure_dirs()
    tmp = config.LOCK_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, config.LOCK_PATH)


def status() -> dict:
    d = _read()
    if not d:
        return {"enabled": False, "auto_lock_minutes": 0, "set": False}
    return {"enabled": bool(d.get("enabled")), "set": True,
            "auto_lock_minutes": int(d.get("auto_lock_minutes", 30))}


def setup(password: str, auto_lock_minutes: int = 30) -> dict:
    """首次设置密码（或重置，需先 disable）。"""
    if len(password) < MIN_PASSWORD_LEN:
        raise ValueError(f"密码至少 {MIN_PASSWORD_LEN} 位")
    if _read() is not None:
        raise ValueError("锁已设置；修改请用 change，清除请先 disable")
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), ITERATIONS)
    _write({"salt": salt, "hash": dk.hex(), "secret": secrets.token_hex(32),
            "enabled": True, "auto_lock_minutes": int(auto_lock_minutes)})
    return status()


def verify_password(password: str) -> bool:
    d = _read()
    if not d:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), d["salt"].encode(), ITERATIONS)
    return hmac.compare_digest(dk.hex(), d["hash"])


# ---------- 防破解：连续失败冻结 ----------
FAIL_LIMIT = 5           # 窗口内连续失败次数上限
FREEZE_SECONDS = 60      # 触发后冻结时长（秒）
_fail_log: dict[str, list[float]] = {}   # ip -> 失败时间戳（进程内存，重启清零）
_fail_lock = threading.Lock()


class FrozenError(Exception):
    def __init__(self, retry_after: int):
        super().__init__(f"密码错误次数过多，已冻结，请 {retry_after} 秒后再试")
        self.retry_after = retry_after


def _frozen_remaining(ip: str) -> int:
    """返回该 IP 还需冻结的秒数（0=未冻结）。"""
    with _fail_lock:
        stamps = _fail_log.get(ip, [])
        recent = [t for t in stamps if time.time() - t < FREEZE_SECONDS]
        _fail_log[ip] = recent
        if len(recent) >= FAIL_LIMIT:
            return int(FREEZE_SECONDS - (time.time() - recent[0])) + 1
        return 0


def _note_fail(ip: str) -> None:
    with _fail_lock:
        _fail_log.setdefault(ip, []).append(time.time())


def _clear_fails(ip: str) -> None:
    with _fail_lock:
        _fail_log.pop(ip, None)


def unlock(password: str, ip: str = "") -> dict:
    """解锁。连续 5 次错误密码冻结该来源 60 秒；每次尝试（含 IP）写入审计表。"""
    from .. import db
    wait = _frozen_remaining(ip)
    if wait > 0:
        # 冻结期的尝试同样记入审计（用户要求：警告记录来源地址）
        try:
            db.record_lock_attempt(ip, False, f"冻结期内尝试解锁被拒（剩余 {wait}s）")
            db.log_event("lock_warning", f"⚠ 冻结期内仍有解锁尝试 来源IP={ip or '未知'}，剩余冻结 {wait}s")
        except Exception:
            pass
        raise FrozenError(wait)
    if not verify_password(password):
        _note_fail(ip)
        left = FAIL_LIMIT - len(_fail_log.get(ip, []))
        try:
            db.record_lock_attempt(ip, False, f"密码错误（冻结前还剩 {max(left, 0)} 次机会）")
            db.log_event("lock_warning", f"⚠ 锁屏密码错误尝试 来源IP={ip or '未知'}"
                         + (f"，再错 {max(left, 0)} 次将冻结 {FREEZE_SECONDS} 秒" if left > 0
                            else f"，已触发 {FREEZE_SECONDS} 秒冻结"))
        except Exception:
            pass  # 审计失败不阻断鉴权主流程
        raise PermissionError(f"密码错误（连续 {FAIL_LIMIT} 次错误将冻结 {FREEZE_SECONDS} 秒）")
    _clear_fails(ip)
    try:
        db.record_lock_attempt(ip, True, "解锁成功")
    except Exception:
        pass
    return {"token": make_token(), "expires_in": TOKEN_TTL_SECONDS,
            "auto_lock_minutes": int((_read() or {}).get("auto_lock_minutes", 30))}


def lock_audit(limit: int = 50) -> list[dict]:
    """最近的锁屏尝试审计（设置页展示警告记录）。"""
    from .. import db
    with db.db() as conn:
        rows = conn.execute("SELECT * FROM lock_attempts ORDER BY id DESC LIMIT ?",
                            (min(limit, 200),)).fetchall()
    return [dict(r) for r in rows]


def make_token() -> str:
    d = _read()
    if not d:
        raise ValueError("锁未设置")
    exp = int(time.time()) + TOKEN_TTL_SECONDS
    payload = str(exp)
    sig = hmac.new(d["secret"].encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    return base64.urlsafe_b64encode(f"{payload}.{sig}".encode()).decode()


def check_token(token: str) -> bool:
    d = _read()
    if not d or not token:
        return False
    try:
        raw = base64.urlsafe_b64decode(token.encode()).decode()
        payload, sig = raw.rsplit(".", 1)
        expect = hmac.new(d["secret"].encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(sig, expect):
            return False
        return int(payload) > time.time()
    except (ValueError, TypeError):
        return False


def change(old_password: str, new_password: str) -> dict:
    if not verify_password(old_password):
        raise PermissionError("原密码错误")
    if len(new_password) < MIN_PASSWORD_LEN:
        raise ValueError(f"新密码至少 {MIN_PASSWORD_LEN} 位")
    d = _read()
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", new_password.encode(), salt.encode(), ITERATIONS)
    d.update(salt=salt, hash=dk.hex())
    _write(d)
    return status()


def disable(password: str) -> dict:
    """关闭锁屏（保留密码哈希可再启用？——直接删除文件，语义干净）。"""
    if not verify_password(password):
        raise PermissionError("密码错误")
    try:
        config.LOCK_PATH.unlink()
    except FileNotFoundError:
        pass
    return {"enabled": False, "set": False, "auto_lock_minutes": 0}


def set_auto_lock(minutes: int, password: str) -> dict:
    if not verify_password(password):
        raise PermissionError("密码错误")
    d = _read()
    d["auto_lock_minutes"] = max(0, int(minutes))  # 0 = 不自动锁
    _write(d)
    return status()


FORGOT_HINT = ("忘记密码：停止程序后删除 data/lock.json 文件再启动即可清除锁屏"
               "（人才数据保存在 data/talent.db，不受影响）")
