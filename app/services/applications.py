"""投递申请服务：投递门户（第二端口）与管理端（主端口）共用的数据操作。"""
from __future__ import annotations

import json
import re
import secrets

from .. import config, db

MAX_FILE_MB = 30
ALLOWED_EXTS = {".pdf", ".docx", ".doc", ".txt", ".md", ".png", ".jpg", ".jpeg", ".bmp", ".webp"}


def gen_code() -> str:
    """8 位查询码（去易混淆字符）。"""
    alphabet = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
    return "".join(secrets.choice(alphabet) for _ in range(8))


def create_application(data: dict, file_bytes: bytes | None, file_name: str, ip: str) -> dict:
    """创建投递。data: name/phone/email/expected_title/referrer/cover_letter。"""
    name = str(data.get("name") or "").strip()[:30]
    phone = re.sub(r"[^\d+]", "", str(data.get("phone") or ""))[:20]
    if not name:
        raise ValueError("姓名不能为空")
    if not re.fullmatch(r"1[3-9]\d{9}", phone or ""):
        raise ValueError("请填写有效的 11 位手机号")
    email = str(data.get("email") or "").strip().lower()[:254]
    if email and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise ValueError("邮箱格式不正确")
    stored = ""
    saved_name = ""
    if file_bytes:
        from ..main import _safe_name  # 复用主应用的名称消毒（延迟导入防循环）
        saved_name = _safe_name(file_name or "resume")
        ext = re.sub(r".*(\.[A-Za-z0-9]+)$", r"\1", saved_name).lower()
        if ext not in ALLOWED_EXTS:
            raise ValueError(f"简历文件仅支持 {'/'.join(sorted(ALLOWED_EXTS))}")
        if len(file_bytes) > MAX_FILE_MB * 1024 * 1024:
            raise ValueError(f"简历文件超过 {MAX_FILE_MB}MB 限制")
        config.ensure_dirs()
        ts = db.now().replace("-", "").replace(":", "").replace(" ", "_")
        dest = config.APPLICATION_DIR / f"{ts}_{saved_name}"
        dest.write_bytes(file_bytes)
        stored = str(dest)
    code = gen_code()
    ts = db.now()
    with db.db() as conn:
        cur = conn.execute(
            "INSERT INTO applications(code,name,phone,email,expected_title,referrer,"
            "cover_letter,file_name,file_path,status,ip,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (code, name, phone, email,
             str(data.get("expected_title") or "").strip()[:60],
             str(data.get("referrer") or "").strip()[:30],
             str(data.get("cover_letter") or "").strip()[:2000],
             saved_name, stored, "待审核", ip[:45], ts, ts))
        new_id = cur.lastrowid
    db.log_event("apply_submit", f"新投递 #{new_id} {name}（{phone}）")
    return {"id": new_id, "code": code, "status": "待审核"}


def query_application(phone: str, code: str) -> dict | None:
    phone = re.sub(r"[^\d+]", "", str(phone or ""))[:20]
    code = str(code or "").strip().upper()
    if not phone or not code:
        return None
    with db.db() as conn:
        row = conn.execute(
            "SELECT * FROM applications WHERE phone = ? AND code = ? ORDER BY id DESC LIMIT 1",
            (phone, code)).fetchone()
    if not row:
        return None
    d = dict(row)
    d.pop("ip", None)          # 不外泄投递人 IP
    d.pop("file_path", None)   # 不外泄服务器路径
    d["has_file"] = bool(d.get("file_name"))
    return d


def update_application(phone: str, code: str, data: dict,
                       file_bytes: bytes | None, file_name: str) -> dict:
    """投递人修改自己的资料——仅「待审核」状态允许；提供新文件则替换旧文件。"""
    cur = query_application(phone, code)
    if not cur:
        raise PermissionError("手机号或查询码不正确")
    if cur["status"] != "待审核":
        raise ValueError(f"当前状态「{cur['status']}」不允许修改（已审核的申请请联系招聘方）")
    sets, params = [], []
    for field, maxlen in (("name", 30), ("email", 254), ("expected_title", 60),
                          ("referrer", 30), ("cover_letter", 2000)):
        if field in data:
            v = str(data.get(field) or "").strip()[:maxlen]
            if field == "name" and not v:
                raise ValueError("姓名不能为空")
            if field == "email" and v and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", v):
                raise ValueError("邮箱格式不正确")
            sets.append(f"{field} = ?")
            params.append(v.lower() if field == "email" else v)
    if file_bytes:
        from ..main import _safe_name
        saved_name = _safe_name(file_name or "resume")
        ext = re.sub(r".*(\.[A-Za-z0-9]+)$", r"\1", saved_name).lower()
        if ext not in ALLOWED_EXTS:
            raise ValueError(f"简历文件仅支持 {'/'.join(sorted(ALLOWED_EXTS))}")
        if len(file_bytes) > MAX_FILE_MB * 1024 * 1024:
            raise ValueError(f"简历文件超过 {MAX_FILE_MB}MB 限制")
        config.ensure_dirs()
        ts = db.now().replace("-", "").replace(":", "").replace(" ", "_")
        dest = config.APPLICATION_DIR / f"{ts}_{saved_name}"
        dest.write_bytes(file_bytes)
        sets += ["file_name = ?", "file_path = ?"]
        params += [saved_name, str(dest)]
        # 旧文件清理（best-effort）
        old = cur.get("file_path")
    if not sets:
        raise ValueError("没有需要修改的内容")
    sets.append("updated_at = ?")
    params.append(db.now())
    params.append(cur["id"])
    with db.db() as conn:
        conn.execute(f"UPDATE applications SET {', '.join(sets)} WHERE id = ?", params)
    db.log_event("apply_update", f"投递人修改 #{cur['id']}")
    return query_application(phone, code)


# ---------- 管理端 ----------

def list_applications(status: str = "", page: int = 1, size: int = 20) -> dict:
    where, params = [], []
    if status:
        where.append("status = ?")
        params.append(status)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    page, size = max(1, page), min(100, max(1, size))
    with db.db() as conn:
        total = conn.execute(f"SELECT COUNT(*) c FROM applications {where_sql}", params).fetchone()["c"]
        rows = conn.execute(
            f"SELECT * FROM applications {where_sql} ORDER BY "
            "CASE status WHEN '待审核' THEN 0 ELSE 1 END, id DESC LIMIT ? OFFSET ?",
            params + [size, (page - 1) * size]).fetchall()
        pend = conn.execute("SELECT COUNT(*) c FROM applications WHERE status='待审核'").fetchone()["c"]
    return {"items": [dict(r) for r in rows], "total": total,
            "page": page, "size": size, "pending": pend}


def get_application(aid: int) -> dict | None:
    with db.db() as conn:
        row = conn.execute("SELECT * FROM applications WHERE id = ?", (aid,)).fetchone()
    return dict(row) if row else None


def review_application(aid: int, action: str, reason: str = "") -> dict:
    app = get_application(aid)
    if not app:
        raise LookupError("投递记录不存在")
    if action not in ("approve", "reject"):
        raise ValueError("action 只能是 approve / reject")
    if app["status"] != "待审核":
        raise ValueError(f"该申请已处理（{app['status']}），不能重复审核")
    new_status = "已同意" if action == "approve" else "已拒绝"
    with db.db() as conn:
        conn.execute("UPDATE applications SET status=?, reject_reason=?, updated_at=? WHERE id=?",
                     (new_status, str(reason or "")[:500], db.now(), aid))
    db.log_event("apply_review", f"#{aid} {app['name']} → {new_status}")
    return get_application(aid)


def application_to_talent(aid: int) -> dict:
    """同意入选并转入人才库：解析简历文件 + 表单字段合并 → 新建人才。"""
    from ..extract import llm_parser, loader, parser
    from . import talent as talent_svc
    app = get_application(aid)
    if not app:
        raise LookupError("投递记录不存在")
    if app["status"] == "已拒绝":
        raise ValueError("已拒绝的申请不能转入人才库")
    if app.get("talent_id") and talent_svc.get_talent(app["talent_id"]):
        return talent_svc.get_talent(app["talent_id"])

    draft: dict = {}
    warnings: list[str] = []
    fpath = app.get("file_path") or ""
    if fpath and __import__("pathlib").Path(fpath).exists():
        try:
            res = loader.extract_file(fpath)
            if res.text:
                draft = parser.parse_resume(res.text, app.get("file_name") or "")
                cfg = config.get()
                if cfg.get("llm_enabled") and llm_parser.status()["enabled"] and \
                        draft.get("parse_confidence", 0) < float(cfg.get("llm_min_confidence", 0.75)):
                    try:
                        draft, _ = llm_parser.enhance(res.text, draft)
                    except Exception:
                        warnings.append("AI 增强失败，使用规则解析结果")
            else:
                warnings.append("简历文件未提取到文字")
        except Exception as e:
            warnings.append(f"简历解析失败: {type(e).__name__}: {e}")
    else:
        warnings.append("无简历文件或文件已删除，仅按表单信息建档")

    # 表单字段优先于简历解析（投递人自己填的最新）
    draft["name"] = app["name"]
    draft["phone"] = app["phone"]
    if app.get("email"):
        draft["email"] = app["email"]
    if app.get("expected_title"):
        draft["expected_title"] = app["expected_title"]
    draft["source"] = "内部投递"
    tags = [t for t in re.split(r"[、,，]", app.get("tags") or "") if t]
    if app.get("referrer"):
        tags.append(f"推荐人:{app['referrer']}")
    draft["tags"] = "、".join(tags[:8])
    draft["status"] = db.statuses()[0]
    draft["source_file"] = app.get("file_name") or ""
    draft["stored_path"] = fpath if config.get().get("upload_keep_original") else ""
    talent = talent_svc.create_talent(draft)
    with db.db() as conn:
        conn.execute("UPDATE applications SET status='已同意', talent_id=?, updated_at=? WHERE id=?",
                     (talent["id"], db.now(), aid))
    db.log_event("apply_to_talent", f"投递 #{aid} {app['name']} → 人才 #{talent['id']}")
    result = dict(talent)
    result["_apply_warnings"] = warnings
    return result
