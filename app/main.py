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
import threading
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Query, Request, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, config, db
from .extract import llm_parser, loader, ocr_engine, parser
from .services import excel as excel_svc
from .services import lock_auth, model_hub
from .services import applications as app_svc
from .services import talent as talent_svc


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    config.ensure_dirs()
    _start_apply_portal()   # 第二端口：投递门户（config.apply_enabled 可关）
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


def _disposition(fname: str, inline: bool = False) -> dict:
    """RFC 5987：中文文件名必须百分号编码，附 ASCII 回退名。inline=浏览器内预览。"""
    from urllib.parse import quote
    ascii_fallback = re.sub(r"[^\x20-\x7e]", "_", fname) or "download"
    kind = "inline" if inline else "attachment"
    return {"Content-Disposition":
            f"{kind}; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(fname)}"}


# ---------- 锁屏鉴权中间件 ----------
# 锁定启用后，除白名单外的 /api/* 需要 Bearer 令牌；页面静态资源不锁（由前端弹锁屏层）。
_AUTH_WHITELIST = ("/api/health", "/api/auth/", "/api/about")


@app.middleware("http")
async def lock_middleware(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") and not path.startswith(_AUTH_WHITELIST):
        if lock_auth.status()["enabled"]:
            auth = request.headers.get("authorization", "")
            token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
            if not lock_auth.check_token(token):
                return JSONResponse(status_code=401, content={"detail": "locked"})
    return await call_next(request)


# ---------- 锁屏 / 版权 ----------

class PasswordIn(BaseModel):
    password: str


class LockSetupIn(BaseModel):
    password: str
    auto_lock_minutes: int = 30


class LockChangeIn(BaseModel):
    old_password: str
    new_password: str
    auto_lock_minutes: Optional[int] = None


@app.get("/api/auth/status")
def auth_status():
    st = lock_auth.status()
    st["forgot_hint"] = lock_auth.FORGOT_HINT
    return st


@app.post("/api/auth/setup")
def auth_setup(body: LockSetupIn):
    try:
        st = lock_auth.setup(body.password, body.auto_lock_minutes)
        db.log_event("lock", "锁屏密码已设置")
        return st
    except ValueError as e:
        _err(str(e))


@app.post("/api/auth/unlock")
def auth_unlock(body: PasswordIn, request: Request):
    ip = request.client.host if request.client else ""
    try:
        return lock_auth.unlock(body.password, ip)
    except lock_auth.FrozenError as e:
        # 防破解：连续 5 次错误密码冻结 60 秒（429 + 剩余秒数，前端倒计时）
        return JSONResponse(status_code=429,
                            content={"detail": str(e), "retry_after": e.retry_after})
    except PermissionError as e:
        return JSONResponse(status_code=403, content={"detail": str(e)})


@app.get("/api/auth/audit")
def auth_audit(limit: int = Query(50, ge=1, le=200)):
    """锁屏尝试审计（含失败警告与来源 IP，设置页展示）。"""
    return {"items": lock_auth.lock_audit(limit)}


@app.post("/api/auth/change")
def auth_change(body: LockChangeIn):
    try:
        lock_auth.change(body.old_password, body.new_password)
        st = lock_auth.set_auto_lock(body.auto_lock_minutes if body.auto_lock_minutes is not None
                                     else lock_auth.status()["auto_lock_minutes"],
                                     body.new_password)
        db.log_event("lock", "锁屏密码已修改")
        return st
    except PermissionError:
        _err("原密码错误", 403)
    except ValueError as e:
        _err(str(e))


@app.post("/api/auth/disable")
def auth_disable(body: PasswordIn):
    try:
        db.log_event("lock", "锁屏已关闭")
        return lock_auth.disable(body.password)
    except PermissionError:
        _err("密码错误", 403)


@app.get("/api/about")
def about():
    """版权与联系信息（页脚/关于/落地页共用）。"""
    return {"app": config.get().get("app_name"), "version": __version__,
            "copyright": config.COPYRIGHT}


# ---------- 投递门户（第二端口） ----------

def _start_apply_portal():
    cfg = config.get()
    if not cfg.get("apply_enabled", True):
        print("[apply] 投递门户已在配置中关闭")
        return

    def _run():
        import uvicorn as _uv
        from .apply.apply_app import apply_app
        port = int(cfg.get("apply_port", 8301))
        try:
            _uv.run(apply_app, host=cfg.get("host", "0.0.0.0"), port=port,
                    log_level="warning", access_log=False)
        except OSError as e:
            print(f"[apply] 投递门户启动失败（端口 {port} 可能被占用）: {e}")

    threading.Thread(target=_run, daemon=True, name="apply-portal").start()
    print(f"[apply] 投递门户已启动: http://127.0.0.1:{cfg.get('apply_port', 8301)}")


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
    apply_urls = []
    if cfg.get("apply_enabled", True):
        ap = cfg.get("apply_port", 8301)
        apply_urls.append(f"http://127.0.0.1:{ap}")
        if lan != "127.0.0.1":
            apply_urls.append(f"http://{lan}:{ap}")
    return {
        "version": __version__,
        "python": platform.python_version(),
        "os": f"{platform.system()} {platform.release()}",
        "urls": urls,
        "apply_urls": apply_urls,
        "host": cfg["host"], "port": port,
        "open_to_lan": cfg["host"] == "0.0.0.0",
        "db_path": str(config.DB_PATH), "db_size_kb": round(db_size / 1024, 1),
        "upload_dir": str(config.UPLOAD_DIR),
        "ocr": ocr_engine.status(),
        "llm": llm_parser.status(),
        "statuses": db.statuses(),
        "copyright": config.COPYRIGHT,
    }


@app.get("/api/system/resources")
def system_resources():
    """内存/GPU 探测 + 模型档位建议（设置页 AI 区块展示）。"""
    return model_hub.system_resources()


@app.get("/api/statuses")
def get_statuses():
    return {"statuses": db.statuses(), "default": config.DEFAULT_STATUSES}


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
    llm_engine: Optional[str] = None
    llm_api_base: Optional[str] = None
    llm_api_key: Optional[str] = None
    llm_api_model: Optional[str] = None
    llm_gpu_layers: Optional[int] = None
    custom_statuses: Optional[str] = None
    apply_enabled: Optional[bool] = None
    apply_port: Optional[int] = None
    apply_captcha_disabled: Optional[bool] = None
    apply_company_name: Optional[str] = None


@app.get("/api/settings")
def get_settings():
    cfg = {k: v for k, v in config.get().items() if not k.startswith("_")}
    if cfg.get("llm_api_key"):
        cfg["llm_api_key"] = "******"   # 密钥不回显，只示"已设置"
    return cfg


@app.put("/api/settings")
def put_settings(body: SettingsIn):
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    if "host" in patch and patch["host"] not in ("127.0.0.1", "0.0.0.0", "localhost"):
        _err("host 只能是 127.0.0.1（仅本机）或 0.0.0.0（开放端口）")
    for pk in ("port", "apply_port"):
        if pk in patch and not (1024 <= int(patch[pk]) <= 65535):
            _err(f"{pk} 需在 1024-65535 之间")
    if "llm_min_confidence" in patch and not (0 <= float(patch["llm_min_confidence"]) <= 1):
        _err("llm_min_confidence 需在 0~1 之间")
    if "llm_model_path" in patch and patch["llm_model_path"] and not Path(patch["llm_model_path"]).exists():
        _err(f"模型路径不存在: {patch['llm_model_path']}")
    if "llm_engine" in patch and patch["llm_engine"] not in ("local", "api"):
        _err("llm_engine 只能是 local（本地模型）或 api（外部接口）")
    if "llm_api_key" in patch and patch["llm_api_key"] == "******":
        patch.pop("llm_api_key")   # 掩码值＝未修改，忽略
    if "custom_statuses" in patch:
        raw = str(patch["custom_statuses"]).strip()
        if raw:
            items = [s.strip()[:12] for s in re.split(r"[、,，;；/|]", raw) if s.strip()][:12]
            if not items:
                _err("自定义状态格式不正确")
            patch["custom_statuses"] = "、".join(items)
    saved = config.save(patch)
    need_restart = [k for k in ("host", "port", "apply_enabled", "apply_port") if k in patch]
    return {"config": saved, "need_restart": need_restart}


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
            "models_dir": str(config.MODELS_DIR),
            "download": model_hub.progress(),
            "resources": model_hub.system_resources()}


@app.post("/api/llm/download")
def llm_download(body: DownloadIn):
    try:
        return model_hub.start_download(body.model_id, body.source)
    except ValueError as e:
        _err(str(e))


@app.get("/api/llm/download")
def llm_download_progress():
    return model_hub.progress()


class DownloadUrlIn(BaseModel):
    url: str
    filename: str = ""


@app.post("/api/llm/download-url")
def llm_download_url(body: DownloadUrlIn):
    """自定义链接下载 GGUF 模型（服务器支持 Range 时自动 4 线程分段加速）。"""
    try:
        return model_hub.start_download_url(body.url, body.filename)
    except ValueError as e:
        _err(str(e))


@app.post("/api/llm/import")
def llm_import_model(up: UploadFile = File(...)):
    """本地导入 GGUF 模型（流式写盘 + 魔数校验，落盘 data/models/）。"""
    try:
        r = model_hub.import_model_stream(up.filename or "", up.file)
    except ValueError as e:
        _err(str(e))
    db.log_event("model_import", f"导入模型 {r['file']}（{r['size_mb']}MB）")
    return r


@app.get("/api/llm/api-models")
def llm_api_models(base: str = "", key: str = "", use_saved: int = 1):
    """自动获取外部 API 服务的模型列表（代理请求避免浏览器 CORS）。
    use_saved=1 且未传 base/key 时用已保存配置。"""
    cfg = config.get()
    b = base.strip() or (cfg.get("llm_api_base") if use_saved else "")
    k = key.strip() or (cfg.get("llm_api_key") if use_saved else "")
    if k == "******":
        k = cfg.get("llm_api_key", "")   # 前端回传掩码时换真实 Key
    try:
        return {"models": llm_parser.fetch_api_models(b, k)}
    except ValueError as e:
        _err(str(e))


@app.get("/api/llm/usage")
def llm_usage(days: int = Query(30, ge=1, le=365)):
    """token 用量统计：总量 + 按引擎/场景/模型分组 + 最近明细。"""
    with db.db() as conn:
        total = conn.execute(
            "SELECT COUNT(*) calls, COALESCE(SUM(prompt_tokens),0) pt, "
            "COALESCE(SUM(completion_tokens),0) ct, COALESCE(SUM(total_tokens),0) tt, "
            "COALESCE(AVG(elapsed_ms),0) avg_ms FROM ai_usage").fetchone()
        by_engine = [dict(r) for r in conn.execute(
            "SELECT engine, COUNT(*) calls, SUM(total_tokens) tokens FROM ai_usage "
            "GROUP BY engine ORDER BY tokens DESC").fetchall()]
        by_scene = [dict(r) for r in conn.execute(
            "SELECT scene, COUNT(*) calls, SUM(total_tokens) tokens FROM ai_usage "
            "GROUP BY scene ORDER BY tokens DESC").fetchall()]
        by_model = [dict(r) for r in conn.execute(
            "SELECT model, engine, COUNT(*) calls, SUM(total_tokens) tokens FROM ai_usage "
            "GROUP BY model, engine ORDER BY tokens DESC LIMIT 10").fetchall()]
        recent = [dict(r) for r in conn.execute(
            "SELECT time, engine, model, scene, total_tokens, elapsed_ms FROM ai_usage "
            "ORDER BY id DESC LIMIT 20").fetchall()]
    return {
        "total": {"calls": total["calls"], "prompt_tokens": total["pt"],
                  "completion_tokens": total["ct"], "total_tokens": total["tt"],
                  "avg_elapsed_ms": round(total["avg_ms"] or 0)},
        "by_engine": by_engine, "by_scene": by_scene, "by_model": by_model,
        "recent": recent,
    }


class ChatIn(BaseModel):
    messages: list[dict]   # OpenAI 格式 [{role, content}]


@app.post("/api/chat")
def ai_chat(body: ChatIn):
    """AI 助手对话（SSE 流式）：本地 GGUF 或外部 API 引擎，取决于当前配置。"""
    msgs = [m for m in body.messages[-20:]           # 上下文窗口：最近 20 条
            if isinstance(m, dict) and m.get("role") in ("user", "assistant", "system")
            and isinstance(m.get("content"), str) and m.get("content").strip()]
    if not msgs:
        _err("没有有效消息")
    if not llm_parser.status()["enabled"]:
        _err("AI 引擎未就绪：本地引擎需已下载模型，或切换外部 API 引擎并配置完整", 503)

    def gen():
        for kind, payload in llm_parser.chat_stream(msgs):
            yield f"data: {json.dumps({kind: payload}, ensure_ascii=False)}\n\n"
        yield "data: {\"done\": true}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                               headers={"Cache-Control": "no-cache",
                                        "X-Accel-Buffering": "no"})


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
        if body.action == "status" and body.value not in db.statuses():
            _err(f"非法状态值，可选 {db.statuses()}")
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
def get_original_file(tid: int, inline: int = 0):
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
    fname = t.get("source_file") or p.name
    return FileResponse(p, filename=fname,
                        content_disposition_type="inline" if inline else "attachment")


# ---------- 投递审核（管理端） ----------

@app.get("/api/applications")
def list_applications(status: str = "", page: int = 1, size: int = 20):
    return app_svc.list_applications(status, page, size)


@app.get("/api/applications/{aid}")
def get_application(aid: int):
    a = app_svc.get_application(aid)
    if not a:
        _err("投递记录不存在", 404)
    a.pop("ip", None)
    return a


class ReviewIn(BaseModel):
    action: str            # approve | reject
    reason: str = ""


@app.post("/api/applications/{aid}/review")
def review_application(aid: int, body: ReviewIn):
    try:
        a = app_svc.review_application(aid, body.action, body.reason)
    except LookupError as e:
        _err(str(e), 404)
    except ValueError as e:
        _err(str(e))
    return a


@app.post("/api/applications/{aid}/to-talent")
def application_to_talent(aid: int):
    """同意入选并一键转入人才库（自动解析其简历文件 + 表单字段合并）。"""
    try:
        t = app_svc.application_to_talent(aid)
    except LookupError as e:
        _err(str(e), 404)
    except ValueError as e:
        _err(str(e))
    return t


@app.get("/api/applications/{aid}/file")
def get_application_file(aid: int, inline: int = 1):
    a = app_svc.get_application(aid)
    if not a:
        _err("投递记录不存在", 404)
    p = Path(a.get("file_path") or "")
    if not a.get("file_path") or not p.exists():
        _err("该投递没有简历文件", 404)
    try:
        p.resolve().relative_to(config.APPLICATION_DIR.resolve())
    except ValueError:
        _err("非法文件路径", 403)
    return FileResponse(p, filename=a.get("file_name") or p.name,
                        content_disposition_type="inline" if inline else "attachment")


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


class EventsDeleteIn(BaseModel):
    ids: Optional[list[int]] = None   # None=清空全部


@app.delete("/api/events")
def delete_events(body: EventsDeleteIn):
    n = db.clear_events(body.ids)
    db.log_event("events_clear", f"删除操作日志 {n} 条")
    return {"deleted": n}


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
