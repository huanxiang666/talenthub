"""人才库业务服务：CRUD、搜索、批量、统计、合并去重。SQL 全部参数化，排序字段白名单。"""
from __future__ import annotations

import json
import re
from datetime import date

from .. import db

# 可写字段（白名单，防任意列注入）
WRITABLE = {
    "name", "gender", "age", "phone", "email", "city", "education", "school",
    "major", "work_years", "current_company", "current_title", "expected_title",
    "expected_salary", "skills", "source", "source_file", "stored_path",
    "status", "rating", "tags", "summary", "raw_text", "extract_method",
    "parse_confidence",
}
SORTABLE = {
    "created_at", "updated_at", "name", "rating", "status", "work_years",
    "parse_confidence", "id",
}

MERGE_STRATEGIES = ("skip", "fill", "overwrite", "always_new")


def _clean(data: dict) -> dict:
    out = {}
    for k, v in data.items():
        if k not in WRITABLE:
            continue
        if v is None or v == "":
            out[k] = None if k in ("age", "work_years", "parse_confidence") else ""
            continue
        if k in ("age",):
            try:
                out[k] = int(float(v))
            except (TypeError, ValueError):
                out[k] = None
        elif k in ("work_years", "parse_confidence"):
            try:
                out[k] = float(v)
            except (TypeError, ValueError):
                out[k] = None
        else:
            out[k] = str(v).strip()
    if out.get("status") and out["status"] not in db.STATUS_LIST:
        out["status"] = "潜在"
    if out.get("rating") and out["rating"].upper() in db.RATING_LIST:
        out["rating"] = out["rating"].upper()
    elif out.get("rating") is not None and out.get("rating", "") not in db.RATING_LIST:
        out["rating"] = ""
    if out.get("phone"):
        out["phone"] = re.sub(r"[^\d+]", "", str(out["phone"]))[:20]
    if out.get("email"):
        # 邮箱统一小写入库：去重键之一，SQLite BINARY 排序大小写敏感，
        # 不归一会导致 CaseTest@x.com 与 casetest@x.com 判为两个人（复核阻断项#1）
        out["email"] = str(out["email"]).strip().lower()[:254]
    return out


def row_to_dict(row) -> dict:
    d = dict(row)
    try:
        d["notes"] = json.loads(d.get("notes") or "[]")
    except json.JSONDecodeError:
        d["notes"] = []
    return d


# ---------- 查询 ----------

def list_talents(q: str = "", status: str = "", rating: str = "", education: str = "",
                 skill: str = "", tag: str = "", sort: str = "created_at",
                 order: str = "desc", page: int = 1, size: int = 20) -> dict:
    where, params = [], []
    if q:
        like = f"%{q.strip()}%"
        where.append("(name LIKE ? OR phone LIKE ? OR email LIKE ? OR skills LIKE ? "
                     "OR current_company LIKE ? OR current_title LIKE ? OR expected_title LIKE ? "
                     "OR school LIKE ? OR major LIKE ? OR city LIKE ? OR tags LIKE ? OR summary LIKE ?)")
        params += [like] * 12
    if status:
        where.append("status = ?")
        params.append(status)
    if rating:
        where.append("rating = ?")
        params.append(rating.upper())
    if education:
        where.append("education = ?")
        params.append(education)
    if skill:
        where.append("skills LIKE ?")
        params.append(f"%{skill.strip()}%")
    if tag:
        where.append("tags LIKE ?")
        params.append(f"%{tag.strip()}%")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    sort_col = sort if sort in SORTABLE else "created_at"
    order_sql = "DESC" if order.lower() != "asc" else "ASC"
    page = max(1, int(page))
    size = min(200, max(1, int(size)))
    with db.db() as conn:
        total = conn.execute(f"SELECT COUNT(*) c FROM talents {where_sql}", params).fetchone()["c"]
        rows = conn.execute(
            f"SELECT * FROM talents {where_sql} ORDER BY {sort_col} {order_sql}, id DESC LIMIT ? OFFSET ?",
            params + [size, (page - 1) * size]).fetchall()
    return {"items": [row_to_dict(r) for r in rows], "total": total, "page": page, "size": size}


def search_all(q: str = "", status: str = "", rating: str = "", education: str = "",
               skill: str = "", tag: str = "", sort: str = "created_at",
               order: str = "desc") -> list[dict]:
    """按筛选条件取全量（导出用，不分页）。"""
    result = list_talents(q=q, status=status, rating=rating, education=education,
                          skill=skill, tag=tag, sort=sort, order=order,
                          page=1, size=200)
    all_items = list(result["items"])
    total = result["total"]
    page = 2
    while len(all_items) < total:  # 超过单页上限时分批取完
        more = list_talents(q=q, status=status, rating=rating, education=education,
                            skill=skill, tag=tag, sort=sort, order=order,
                            page=page, size=200)
        if not more["items"]:
            break  # 并发删除导致 total 漂移时防死循环
        all_items.extend(more["items"])
        page += 1
    return all_items


def get_talent(tid: int) -> dict | None:
    with db.db() as conn:
        row = conn.execute("SELECT * FROM talents WHERE id = ?", (tid,)).fetchone()
    return row_to_dict(row) if row else None


# ---------- 写入 ----------

def create_talent(data: dict) -> dict:
    clean = _clean(data)
    clean.setdefault("name", "")
    if not clean.get("name"):
        raise ValueError("姓名不能为空")
    ts = db.now()
    clean["created_at"] = ts
    clean["updated_at"] = ts
    clean.setdefault("notes", None)
    notes = clean.pop("notes", None)
    cols = ", ".join(clean.keys()) + (", notes" if notes is not None else "")
    marks = ", ".join(["?"] * len(clean)) + (", ?" if notes is not None else "")
    vals = list(clean.values()) + ([json.dumps(notes, ensure_ascii=False)] if notes is not None else [])
    with db.db() as conn:
        cur = conn.execute(f"INSERT INTO talents ({cols}) VALUES ({marks})", vals)
        new_id = cur.lastrowid
    db.log_event("create", f"新增人才 #{new_id} {clean.get('name','')}")
    return get_talent(new_id)


def update_talent(tid: int, patch: dict) -> dict | None:
    clean = _clean(patch)
    if not clean:
        return get_talent(tid)
    clean["updated_at"] = db.now()
    sets = ", ".join(f"{k} = ?" for k in clean)
    with db.db() as conn:
        exists = conn.execute("SELECT id FROM talents WHERE id = ?", (tid,)).fetchone()
        if not exists:
            return None
        conn.execute(f"UPDATE talents SET {sets} WHERE id = ?", list(clean.values()) + [tid])
    db.log_event("update", f"更新人才 #{tid}")
    return get_talent(tid)


def add_note(tid: int, text: str) -> dict | None:
    t = get_talent(tid)
    if not t:
        return None
    notes = t.get("notes") or []
    notes.append({"time": db.now(), "text": text.strip()[:500]})
    with db.db() as conn:
        conn.execute("UPDATE talents SET notes = ?, updated_at = ? WHERE id = ?",
                     (json.dumps(notes[-100:], ensure_ascii=False), db.now(), tid))
    return get_talent(tid)


def delete_talents(ids: list[int]) -> int:
    if not ids:
        return 0
    marks = ", ".join("?" * len(ids))
    with db.db() as conn:
        cur = conn.execute(f"DELETE FROM talents WHERE id IN ({marks})", ids)
        n = cur.rowcount
    db.log_event("delete", f"删除人才 {ids} 共 {n} 条")
    return n


# ---------- 合并去重 ----------

def find_duplicate(phone: str = "", email: str = "", name: str = "") -> dict | None:
    """按 电话 > 邮箱 > 姓名 优先级查重复，返回已存在的记录。"""
    with db.db() as conn:
        if phone:
            row = conn.execute("SELECT * FROM talents WHERE phone = ? LIMIT 1", (phone,)).fetchone()
            if row:
                return row_to_dict(row)
        if email:
            row = conn.execute("SELECT * FROM talents WHERE email = ? LIMIT 1",
                               (email.lower(),)).fetchone()
            if row:
                return row_to_dict(row)
        if name:
            row = conn.execute("SELECT * FROM talents WHERE name = ? LIMIT 1", (name,)).fetchone()
            if row:
                return row_to_dict(row)
    return None


def upsert_with_strategy(data: dict, strategy: str = "skip") -> tuple[str, int | None]:
    """导入合并核心。strategy: skip 跳过 / fill 补空 / overwrite 覆盖 / always_new 总是新增。
    返回 (动作, id)。动作 ∈ inserted|updated|skipped。"""
    if strategy not in MERGE_STRATEGIES:
        raise ValueError(f"未知合并策略: {strategy}，可选 {MERGE_STRATEGIES}")
    clean = _clean(data)
    if not clean.get("name") and not clean.get("phone") and not clean.get("email"):
        raise ValueError("记录缺少姓名/电话/邮箱，无法入库")
    if strategy == "always_new":
        rec = create_talent(clean)
        return "inserted", rec["id"]

    dup = find_duplicate(clean.get("phone", ""), (clean.get("email") or "").lower(),
                         clean.get("name", ""))
    if dup is None:
        rec = create_talent(clean)
        return "inserted", rec["id"]
    if strategy == "skip":
        return "skipped", dup["id"]

    patch = {}
    for k, v in clean.items():
        if k in ("created_at", "updated_at", "stored_path", "source_file"):
            continue
        old = dup.get(k)
        empty_old = old is None or (isinstance(old, str) and not old.strip())
        if strategy == "fill" and empty_old and v not in (None, ""):
            patch[k] = v
        elif strategy == "overwrite" and v not in (None, "") and old != v:
            patch[k] = v
    if not patch:
        return "skipped", dup["id"]
    update_talent(dup["id"], patch)
    return "updated", dup["id"]


# ---------- 统计 ----------

def stats() -> dict:
    with db.db() as conn:
        total = conn.execute("SELECT COUNT(*) c FROM talents").fetchone()["c"]
        today = date.today()
        month_start = f"{today.year:04d}-{today.month:02d}-01"
        month_new = conn.execute(
            "SELECT COUNT(*) c FROM talents WHERE created_at >= ?", (month_start,)).fetchone()["c"]
        high = conn.execute(
            "SELECT COUNT(*) c FROM talents WHERE rating IN ('A','B') AND status NOT IN ('已归档','不合适')"
        ).fetchone()["c"]
        following = conn.execute(
            "SELECT COUNT(*) c FROM talents WHERE status = '跟进中'").fetchone()["c"]
        edu_rows = conn.execute(
            "SELECT CASE WHEN education = '' OR education IS NULL THEN '未填写' ELSE education END k, "
            "COUNT(*) c FROM talents GROUP BY k").fetchall()
        status_rows = conn.execute(
            "SELECT status k, COUNT(*) c FROM talents GROUP BY status").fetchall()
        rating_rows = conn.execute(
            "SELECT CASE WHEN rating = '' OR rating IS NULL THEN '未评级' ELSE rating END k, "
            "COUNT(*) c FROM talents GROUP BY k").fetchall()
        source_rows = conn.execute(
            "SELECT CASE WHEN source = '' OR source IS NULL THEN '未标注' ELSE source END k, "
            "COUNT(*) c FROM talents GROUP BY k ORDER BY c DESC LIMIT 8").fetchall()
        skill_rows = conn.execute(
            "SELECT skills FROM talents WHERE skills != '' AND skills IS NOT NULL").fetchall()
        month_rows = conn.execute(
            "SELECT substr(created_at,1,7) m, COUNT(*) c FROM talents "
            "WHERE created_at != '' GROUP BY m ORDER BY m DESC LIMIT 12").fetchall()
        recent = conn.execute(
            "SELECT * FROM talents ORDER BY created_at DESC, id DESC LIMIT 8").fetchall()

    # 技能计数
    counter: dict[str, int] = {}
    for r in skill_rows:
        for s in re.split(r"[、,，;；/|]+", r["skills"] or ""):
            s = s.strip()
            if s:
                counter[s] = counter.get(s, 0) + 1
    top_skills = sorted(counter.items(), key=lambda kv: -kv[1])[:10]

    # 近 12 个月补齐（无数据月份记 0）
    months: list[str] = []
    y, m = today.year, today.month
    for _ in range(12):
        months.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    months.reverse()
    m_map = {r["m"]: r["c"] for r in month_rows}
    trend = [{"month": mm, "count": m_map.get(mm, 0)} for mm in months]

    def pairs(rows):
        return [{"name": r["k"], "value": r["c"]} for r in sorted(rows, key=lambda x: -x["c"])]

    return {
        "total": total, "month_new": month_new, "high_potential": high, "following": following,
        "by_education": pairs(edu_rows),
        "by_status": [{"name": s, "value": next((r["c"] for r in status_rows if r["k"] == s), 0)}
                      for s in db.STATUS_LIST],
        "by_rating": pairs(rating_rows),
        "by_source": pairs(source_rows),
        "top_skills": [{"name": k, "value": v} for k, v in top_skills],
        "monthly_trend": trend,
        "recent": [row_to_dict(r) for r in recent],
    }
