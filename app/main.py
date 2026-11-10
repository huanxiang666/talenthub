"""TalentHub FastAPI 主应用：REST API + 静态页面托管。

页面双通道：本机自动弹浏览器（server.py 负责）与局域网开放端口（config.host=0.0.0.0）
访问的是同一套前端与 API —— 即「web 和本地双页面可用」。
"""
from __future__ import annotations

import json
import os
import platform
import re
import socket
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Query, Request, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, config, db
from .extract import llm_parser, loader, ocr_engine, parser
from .services import excel as excel_svc
from .services import model_hub
from .services import talent as talent_svc


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    config.ensure_dirs()
    yield


app = FastAPI(title="TalentHub 企业人才库", version=__version__,
              docs_url="/api/docs", lifespan=lifespan)

STATIC_DIR = config.STATIC_DIR          # 源码=app/static；冻结包=_MEIPASS/app/static
SERVER_PY = config.PROJECT_ROOT / "server.py"
IS_FROZEN = getattr(sys, "frozen", False)
MAX_UPLOAD_MB = 50
MAX_FILES_PER_UPLOAD = 50
RESUME_EXTS = {".pdf", ".docx", ".doc", ".txt", ".md", ".png", ".jpg", ".jpeg", ".bmp", ".webp"}


def _remove_orphan(path: str) -> None:
    """best-effort 清理未被引用的上传原件；失败仅记日志不中断主流程。"""
    if not path:
        return
    try:
        os.remove(path)
    except OSError as e:
        print(f"[warn] 清理上传原件失败 {path}: {e}")


# ---------- 工具 ----------

def _safe_name(name: str) -> str:
    name = Path(name or "file").name
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name)
    return name[:120] or "file"


def _lan_ip() -> str:
    """本机局域网 IP（UDP connect 技巧，不实际发包；失败回退 127.0.0.1）。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("223.5.5.5", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def _read_upload(up: UploadFile) -> bytes:
    data = up.file.read(MAX_UPLOAD_MB * 1024 * 1024 + 1)
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"文件超过 {MAX_UPLOAD_MB}MB 限制: {up.filename}")
    return data


def _err(msg: str, code: int = 400):
    raise HTTPException(code, msg)


def _disposition(fname: str) -> dict:
    """RFC 5987：中文文件名必须百分号编码，附 ASCII 回退名。"""
    from urllib.parse import quote
    ascii_fallback = re.sub(r"[^\x20-\x7e]", "_", fname) or "download"
    return {"Content-Disposition":
            f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(fname)}"}


# ---------- 系统 / 设置 ----------

@app.get("/api/health")
def health():
    return {"ok": True, "version": __version__, "time": db.now()}


@app.get("/api/system")
def system_info(request: Request):
    cfg = config.get()
    port = cfg["port"]
    urls = [f"http://127.0.0.1:{port}"]
    lan = _lan_ip()
    if lan != "127.0.0.1":
        urls.append(f"http://{lan}:{port}")
    db_size = config.DB_PATH.stat().st_size if config.DB_PATH.exists() else 0
    return {
        "version": __version__,
        "python": platform.python_version(),
        "os": f"{platform.system()} {platform.release()}",
        "urls": urls,
        "host": cfg["host"], "port": port,
        "open_to_lan": cfg["host"] == "0.0.0.0",
        "db_path": str(config.DB_PATH), "db_size_kb": round(db_size / 1024, 1),
        "upload_dir": str(config.UPLOAD_DIR),
        "ocr": ocr_engine.status(),
        "llm": llm_parser.status(),
    }


class SettingsIn(BaseModel):
    host: Optional[str] = None
    port: Optional[int] = None
    auto_open_browser: Optional[bool] = None
    ocr_enabled: Optional[bool] = None
    ocr_max_pages: Optional[int] = None
    upload_keep_original: Optional[bool] = None
    llm_enabled: Optional[bool] = None
    llm_model_path: Optional[str] = None
    llm_min_confidence: Optional[float] = None


@app.get("/api/settings")
def get_settings():
    return {k: v for k, v in config.get().items() if not k.startswith("_")}


@app.put("/api/settings")
def put_settings(body: SettingsIn):
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    if "host" in patch and patch["host"] not in ("127.0.0.1", "0.0.0.0", "localhost"):
        _err("host 只能是 127.0.0.1（仅本机）或 0.0.0.0（开放端口）")
    if "port" in patch and not (1024 <= int(patch["port"]) <= 65535):
        _err("端口需在 1024-65535 之间")
    if "llm_min_confidence" in patch and not (0 <= float(patch["llm_min_confidence"]) <= 1):
        _err("llm_min_confidence 需在 0~1 之间")
    if "llm_model_path" in patch and patch["llm_model_path"] and not Path(patch["llm_model_path"]).exists():
        _err(f"模型路径不存在: {patch['llm_model_path']}")
    return config.save(patch)


# ---------- 本地 AI 模型（下载/状态） ----------

class DownloadIn(BaseModel):
    model_id: str
    source: str


@app.get("/api/llm/status")
def llm_status():
    return llm_parser.status()


@app.get("/api/llm/models")
def llm_models():
    return {"models": model_hub.catalog(),
            "models_dir": str(config.DATA_DIR / "models"),
            "download": model_hub.progress()}


@app.post("/api/llm/download")
def llm_download(body: DownloadIn):
    try:
        return model_hub.start_download(body.model_id, body.source)
    except ValueError as e:
        _err(str(e))


@app.get("/api/llm/download")
def llm_download_progress():
    return model_hub.progress()


@app.post("/api/settings/restart")
def restart_service(background: BackgroundTasks):
    """保存配置后热重启服务（换端口/开放端口即时生效）。"""
    def _do_restart():
        if IS_FROZEN:
            # 冻结模式：重执行 exe 自身（sys.executable 即单文件二进制）
            cmd = [sys.executable, "--wait-port-free", "12", "--no-open"]
        else:
            cmd = [sys.executable, str(SERVER_PY), "--wait-port-free", "12", "--no-open"]
        kwargs: dict = {}
        if os.name == "nt":
            DETACHED = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
            kwargs["creationflags"] = DETACHED
        else:
            kwargs["start_new_session"] = True
        env = dict(os.environ)
        if IS_FROZEN:
            # PyInstaller onefile 自 spawn 坑（6.x 实测）：bootloader 给应用进程注入
            # _PYI_APPLICATION_HOME_DIR / _PYI_PARENT_PROCESS_LEVEL / LD_LIBRARY_PATH，
            # 全部指向父进程的 /tmp/_MEIxxxx 解压目录。子进程继承后会误判自己"已解压"，
            # 复用该目录；父进程退出时目录被删 → 子进程静默崩溃。必须全部剥离，
            # 让新二进制的 bootloader 自己完整走一遍解压。
            for k in list(env):
                if k.startswith("_PYI_") or k == "LD_LIBRARY_PATH":
                    env.pop(k)
            # 旧版 PyInstaller 约定：存在 LD_LIBRARY_PATH_ORIG 则恢复原值
            orig = env.pop("LD_LIBRARY_PATH_ORIG", None)
            if orig is not None:
                env["LD_LIBRARY_PATH"] = orig
        subprocess.Popen(cmd, cwd=str(config.PROJECT_ROOT), env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs)
        os._exit(0)
    background.add_task(_do_restart)
    return {"ok": True, "message": "服务正在以新配置重启，约 3 秒后刷新页面"}


# ---------- 统计 ----------

@app.get("/api/stats")
def get_stats():
    return talent_svc.stats()


# ---------- 人才 CRUD ----------

@app.get("/api/talents")
def list_talents(q: str = "", status: str = "", rating: str = "", education: str = "",
                 skill: str = "", tag: str = "", sort: str = "created_at",
                 order: str = "desc", page: int = 1, size: int = 20):
    return talent_svc.list_talents(q, status, rating, education, skill, tag,
                                   sort, order, page, size)


class TalentIn(BaseModel):
    data: dict


@app.post("/api/talents")
def create_talent(body: TalentIn):
    try:
        return talent_svc.create_talent(body.data)
    except ValueError as e:
        _err(str(e))


@app.get("/api/talents/{tid}")
def get_talent(tid: int):
    t = talent_svc.get_talent(tid)
    if not t:
        _err("人才记录不存在", 404)
    return t


@app.put("/api/talents/{tid}")
def update_talent(tid: int, body: TalentIn):
    try:
        t = talent_svc.update_talent(tid, body.data)
    except ValueError as e:
        _err(str(e))
    if t is None:
        _err("人才记录不存在", 404)
    return t


@app.delete("/api/talents/{tid}")
def delete_talent(tid: int):
    n = talent_svc.delete_talents([tid])
    return {"deleted": n}


class NoteIn(BaseModel):
    text: str


@app.post("/api/talents/{tid}/notes")
def add_note(tid: int, body: NoteIn):
    if not body.text.strip():
        _err("跟进内容不能为空")
    t = talent_svc.add_note(tid, body.text)
    if not t:
        _err("人才记录不存在", 404)
    return t


class BatchIn(BaseModel):
    ids: list[int]
    action: str          # delete | status | rating | tag_add
    value: Optional[str] = None


@app.post("/api/talents/batch")
def batch_op(body: BatchIn):
    if not body.ids:
        _err("未选择记录")
    affected = 0
    if body.action == "delete":
        affected = talent_svc.delete_talents(body.ids)
    elif body.action in ("status", "rating"):
        if body.action == "status" and body.value not in db.STATUS_LIST:
            _err(f"非法状态值，可选 {db.STATUS_LIST}")
        if body.action == "rating" and (body.value or "").upper() not in db.RATING_LIST + [""]:
            _err("非法评级值，可选 A/B/C/D 或留空")
        for tid in body.ids:
            if talent_svc.update_talent(tid, {body.action: body.value or ""}):
                affected += 1
    elif body.action == "tag_add":
        tag = (body.value or "").strip()
        if not tag:
            _err("标签不能为空")
        for tid in body.ids:
            t = talent_svc.get_talent(tid)
            if not t:
                continue
            tags = [x for x in re.split(r"[、,，]", t.get("tags") or "") if x.strip()]
            if tag not in tags:
                tags.append(tag)
            talent_svc.update_talent(tid, {"tags": "、".join(tags[:10])})
            affected += 1
    else:
        _err(f"未知批量操作: {body.action}")
    db.log_event("batch", f"{body.action} x{affected}")
    return {"affected": affected}


@app.get("/api/files/{tid}")
def get_original_file(tid: int):
    t = talent_svc.get_talent(tid)
    if not t or not t.get("stored_path"):
        _err("该记录没有关联的原件", 404)
    p = Path(t["stored_path"])
    # 路径逃逸防护：必须位于 uploads 目录内
    try:
        p.resolve().relative_to(config.UPLOAD_DIR.resolve())
    except ValueError:
        _err("非法文件路径", 403)
    if not p.exists():
        _err("原件文件已不存在", 404)
    return FileResponse(p, filename=t.get("source_file") or p.name)


# ---------- 简历导入（PDF/Word/图片 → 解析草稿 → 批量入库） ----------

@app.post("/api/import/resumes")
def import_resumes(files: list[UploadFile] = File(...), force_llm: str = Form("0")):
    """上传简历文件 → 提取文本（必要时 OCR）→ 规则解析 → （低置信度时）本地 LLM 增强
    → 返回草稿（不直接入库，由前端确认后调 /api/talents/bulk，实现"人工确认"）。

    同步 def：整条管线（OCR/LLM）都是 CPU 阻塞操作，交给 FastAPI 线程池执行，
    避免卡死事件循环导致其它请求（健康检查/页面）无响应。
    force_llm=1 时对全部文件强制走 LLM（默认只对规则置信度 < llm_min_confidence 的启用，
    批量导入时避免每份都跑几十秒推理）。"""
    if not files:
        _err("未收到文件")
    if len(files) > MAX_FILES_PER_UPLOAD:
        _err(f"单次最多上传 {MAX_FILES_PER_UPLOAD} 个文件，请分批导入")
    results = []
    cfg = config.get()
    llm_ok = bool(cfg.get("llm_enabled")) and llm_parser.status()["enabled"]
    llm_min = float(cfg.get("llm_min_confidence", 0.75))
    want_llm_all = force_llm == "1"
    llm_errors: list[str] = []
    for up in files:
        fname = _safe_name(up.filename or "")
        ext = Path(fname).suffix.lower()
        item: dict = {"filename": fname, "ok": False}
        if ext not in RESUME_EXTS:
            item["error"] = f"不支持的类型 {ext or '(无扩展名)'}"
            results.append(item)
            continue
        data = _read_upload(up)
        if not data:
            item["error"] = "文件内容为空"
            results.append(item)
            continue
        stored = ""
        try:
            ts = datetime.now().strftime("%Y%m%d%H%M%S%f")
            dest = config.UPLOAD_DIR / f"{ts}_{fname}"
            dest.write_bytes(data)
            stored = str(dest)
            res = loader.extract_file(dest)
            if not res.text:
                item["error"] = "未提取到任何文字；" + "；".join(res.warnings or ["请确认文件内容"])
                item["warnings"] = res.warnings
                _remove_orphan(stored)  # 解析失败的原件不会被任何记录引用，清掉防孤儿堆积
                results.append(item)
                continue
            parsed = parser.parse_resume(res.text, fname)
            llm_used = False
            if llm_ok and (want_llm_all or parsed.get("parse_confidence", 0) < llm_min):
                try:
                    parsed, llm_used = llm_parser.enhance(res.text, parsed)
                except Exception as e:  # LLM 失败不阻断：回退纯规则结果，记警告
                    llm_errors.append(f"{fname}: {type(e).__name__}: {e}")
                    res.warnings.append("AI 增强解析失败，已回退规则解析结果")
            parsed["source_file"] = fname
            parsed["stored_path"] = stored if cfg.get("upload_keep_original") else ""
            if not cfg.get("upload_keep_original"):
                _remove_orphan(stored)
            parsed["extract_method"] = res.method
            parsed["source"] = "简历导入"
            item.update({
                "ok": True, "draft": parsed, "method": res.method,
                "ocr_used": res.ocr_used, "llm_used": llm_used, "pages": res.pages,
                "warnings": res.warnings, "text_length": len(res.text),
            })
        except Exception as e:
            item["error"] = f"{type(e).__name__}: {e}"
            _remove_orphan(stored)
        results.append(item)
    ok_n = sum(1 for r in results if r["ok"])
    llm_n = sum(1 for r in results if r.get("llm_used"))
    db.log_event("import_resumes", f"{len(files)} 个文件，成功解析 {ok_n} 个，AI增强 {llm_n} 个")
    return {"total": len(results), "parsed": ok_n, "results": results,
            "ocr_status": ocr_engine.status(), "llm_status": llm_parser.status(),
            "llm_used_count": llm_n, "llm_errors": llm_errors[:5]}


class BulkIn(BaseModel):
    drafts: list[dict]
    strategy: str = "skip"


@app.post("/api/talents/bulk")
def bulk_insert(body: BulkIn):
    """前端确认后的草稿批量入库，带合并策略（skip/fill/overwrite/always_new）。"""
    if not body.drafts:
        _err("没有待入库的记录")
    if body.strategy not in talent_svc.MERGE_STRATEGIES:
        _err(f"非法合并策略，可选 {list(talent_svc.MERGE_STRATEGIES)}")
    inserted = updated = skipped = 0
    errors = []
    ids = []
    for i, d in enumerate(body.drafts):
        try:
            action, tid = talent_svc.upsert_with_strategy(d, body.strategy)
            ids.append(tid)
            if action == "inserted":
                inserted += 1
            elif action == "updated":
                updated += 1
            else:
                skipped += 1
        except ValueError as e:
            errors.append({"index": i, "name": d.get("name", ""), "error": str(e)})
    db.log_event("bulk_insert", f"入库{inserted} 更新{updated} 跳过{skipped} 失败{len(errors)}")
    return {"inserted": inserted, "updated": updated, "skipped": skipped,
            "errors": errors, "ids": ids}


# ---------- 表格导出 ----------

def _export_rows(request: Request) -> list[dict]:
    p = request.query_params
    return talent_svc.search_all(
        q=p.get("q", ""), status=p.get("status", ""), rating=p.get("rating", ""),
        education=p.get("education", ""), skill=p.get("skill", ""), tag=p.get("tag", ""),
        sort=p.get("sort", "created_at"), order=p.get("order", "desc"))


@app.get("/api/export/excel")
def export_excel(request: Request):
    rows = _export_rows(request)
    data = excel_svc.export_xlsx(rows)
    fname = f"人才库导出_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers=_disposition(fname))


@app.get("/api/export/csv")
def export_csv(request: Request):
    rows = _export_rows(request)
    data = excel_svc.export_csv(rows)
    fname = f"人才库导出_{datetime.now():%Y%m%d_%H%M%S}.csv"
    return Response(data, media_type="text/csv; charset=utf-8", headers=_disposition(fname))


@app.get("/api/export/template")
def export_template():
    data = excel_svc.template_xlsx()
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers=_disposition("人才库导入模板.xlsx"))


@app.get("/api/export/json")
def export_json():
    with db.db() as conn:
        rows = conn.execute("SELECT * FROM talents ORDER BY id").fetchall()
    payload = {"app": "TalentHub", "version": __version__, "exported_at": db.now(),
               "count": len(rows), "talents": [talent_svc.row_to_dict(r) for r in rows]}
    data = json.dumps(payload, ensure_ascii=False, indent=1).encode("utf-8")
    fname = f"人才库备份_{datetime.now():%Y%m%d_%H%M%S}.json"
    return Response(data, media_type="application/json", headers=_disposition(fname))


# ---------- 表格导入 + 数据合并 ----------

@app.post("/api/import/table/preview")
async def import_table_preview(up: UploadFile = File(...)):
    data = _read_upload(up)
    try:
        parsed = excel_svc.parse_table(data, up.filename or "")
    except ValueError as e:
        _err(str(e))
    parsed["duplicates"] = excel_svc.analyze_duplicates(parsed["rows"])
    dup_n = sum(1 for d in parsed["duplicates"] if d["duplicate_of"] is not None)
    parsed["duplicate_count"] = dup_n
    parsed["new_count"] = len(parsed["rows"]) - dup_n
    return parsed


class TableCommitIn(BaseModel):
    rows: list[dict]
    strategy: str = "fill"
    selected: Optional[list[int]] = None   # 行下标白名单；None=全部


@app.post("/api/import/table/commit")
def import_table_commit(body: TableCommitIn):
    if body.strategy not in talent_svc.MERGE_STRATEGIES:
        _err(f"非法合并策略，可选 {list(talent_svc.MERGE_STRATEGIES)}")
    idxs = body.selected if body.selected is not None else range(len(body.rows))
    inserted = updated = skipped = 0
    errors = []
    notes_map: dict[int, list[str]] = {}
    for i in idxs:
        if not (0 <= i < len(body.rows)):
            continue
        row = dict(body.rows[i])
        note_texts = row.pop("_notes", None)
        row.setdefault("source", "表格导入")
        try:
            action, tid = talent_svc.upsert_with_strategy(row, body.strategy)
            if action == "inserted":
                inserted += 1
            elif action == "updated":
                updated += 1
            else:
                skipped += 1
            if tid and note_texts:
                notes_map.setdefault(tid, []).extend(note_texts)
        except ValueError as e:
            errors.append({"index": i, "error": str(e)})
    for tid, texts in notes_map.items():
        for t in texts[:5]:
            talent_svc.add_note(tid, f"[导入备注] {t}")
    db.log_event("import_table", f"策略{body.strategy} 入库{inserted} 更新{updated} 跳过{skipped}")
    return {"inserted": inserted, "updated": updated, "skipped": skipped, "errors": errors}


@app.post("/api/import/json")
async def import_json(up: UploadFile = File(...), strategy: str = "fill"):
    """JSON 备份导入：数据合并（fill/overwrite/skip）或 always_new 全量追加。"""
    if strategy not in talent_svc.MERGE_STRATEGIES:
        _err(f"非法合并策略，可选 {list(talent_svc.MERGE_STRATEGIES)}")
    data = _read_upload(up)
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        _err(f"JSON 解析失败: {e}")
    talents = payload.get("talents") if isinstance(payload, dict) else payload
    if not isinstance(talents, list):
        _err("JSON 结构不对：应为 {talents: [...]} 或直接是记录数组")
    inserted = updated = skipped = 0
    errors = []
    for i, rec in enumerate(talents):
        if not isinstance(rec, dict):
            errors.append({"index": i, "error": "非对象记录"})
            continue
        rec = {k: v for k, v in rec.items() if k in talent_svc.WRITABLE}
        notes = rec.pop("notes", None)
        try:
            action, tid = talent_svc.upsert_with_strategy(rec, strategy)
            if action == "inserted":
                inserted += 1
            elif action == "updated":
                updated += 1
            else:
                skipped += 1
            if tid and isinstance(notes, list):
                for n in notes[-5:]:
                    if isinstance(n, dict) and n.get("text"):
                        talent_svc.add_note(tid, f"[备份合并] {n['text']}")
        except ValueError as e:
            errors.append({"index": i, "error": str(e)})
    db.log_event("import_json", f"策略{strategy} 入库{inserted} 更新{updated} 跳过{skipped}")
    return {"inserted": inserted, "updated": updated, "skipped": skipped,
            "errors": errors[:50], "total": len(talents)}


# ---------- 事件日志 ----------

@app.get("/api/events")
def list_events(limit: int = Query(50, ge=1, le=200)):
    with db.db() as conn:
        rows = conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return {"items": [dict(r) for r in rows]}


# ---------- 静态页面（放最后，兜底路由） ----------

@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")


@app.exception_handler(Exception)
def unhandled_exception_handler(request: Request, exc: Exception):
    """兜底：任何未捕获异常返回结构化 JSON 并打日志，绝不吞错误。"""
    import traceback
    traceback.print_exc()
    return JSONResponse(status_code=500,
                        content={"detail": f"服务器内部错误: {type(exc).__name__}: {exc}"})
