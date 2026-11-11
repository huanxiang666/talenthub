"""投递门户独立应用（第二端口）：面向求职者/内推人的轻量投递页 + API。

与管理端（主端口）共享同一 SQLite 库；本身不含任何人才库数据接口，
锁屏不影响投递门户（外部投递人没有管理密码）。

防滥用三件套：图形验证码（可配置关闭）+ IP 限频 + 同手机号日限。
shortcut: 限频计数存内存（重启清零）——单机内网工具足够；要多实例共享时换 SQLite 表。
"""
from __future__ import annotations

import base64
import time
from collections import deque
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel

from .. import config, db
from ..services import applications as app_svc
from . import captcha

IP_LIMIT_PER_HOUR = 10        # 同 IP 每小时最多提交次数
PHONE_LIMIT_PER_DAY = 3       # 同手机号每天最多提交次数
_ip_hits: dict[str, deque] = {}
_phone_hits: dict[str, deque] = {}

apply_app = FastAPI(title="TalentHub 投递门户", docs_url=None, redoc_url=None)
STATIC = config.STATIC_DIR


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def _rate_check(ip: str, phone: str):
    now = time.time()
    dq = _ip_hits.setdefault(ip, deque())
    while dq and now - dq[0] > 3600:
        dq.popleft()
    if len(dq) >= IP_LIMIT_PER_HOUR:
        raise HTTPException(429, "提交过于频繁，请 1 小时后再试（同一网络地址每小时最多 10 次）")
    dq2 = _phone_hits.setdefault(phone, deque())
    while dq2 and now - dq2[0] > 86400:
        dq2.popleft()
    if len(dq2) >= PHONE_LIMIT_PER_DAY:
        raise HTTPException(429, "该手机号今日提交次数已达上限（3 次/天），请明天再试或联系招聘方")
    return dq, dq2


@apply_app.get("/")
def apply_page():
    return FileResponse(STATIC / "apply.html")


@apply_app.get("/apply-api/info")
def apply_info():
    cfg = config.get()
    return {
        "company": cfg.get("apply_company_name") or config.COPYRIGHT["owner"],
        "captcha_required": not cfg.get("apply_captcha_disabled", False),
        "copyright": config.COPYRIGHT,
        "max_file_mb": app_svc.MAX_FILE_MB,
        "allowed_exts": sorted(app_svc.ALLOWED_EXTS),
    }


@apply_app.get("/apply-api/captcha")
def get_captcha():
    cid, png = captcha.generate()
    b64 = base64.b64encode(png).decode()
    return {"captcha_id": cid, "image": f"data:image/png;base64,{b64}"}


@apply_app.post("/apply-api/submit")
async def submit(request: Request,
                 name: str = Form(...), phone: str = Form(...),
                 email: str = Form(""), expected_title: str = Form(""),
                 referrer: str = Form(""), cover_letter: str = Form(""),
                 captcha_id: str = Form(""), captcha_code: str = Form(""),
                 file: UploadFile | None = File(None)):
    cfg = config.get()
    if not cfg.get("apply_captcha_disabled", False):
        if not captcha.verify(captcha_id, captcha_code):
            raise HTTPException(400, "验证码错误或已过期，请重新输入")
    data = await request.form()
    phone_raw = str(data.get("phone") or "")
    import re
    phone_norm = re.sub(r"[^\d+]", "", phone_raw)[:20]
    ip = _client_ip(request)
    ip_dq, phone_dq = _rate_check(ip, phone_norm)
    file_bytes = None
    if file is not None:
        file_bytes = file.file.read(app_svc.MAX_FILE_MB * 1024 * 1024 + 1)
        if len(file_bytes) > app_svc.MAX_FILE_MB * 1024 * 1024:
            raise HTTPException(413, f"文件超过 {app_svc.MAX_FILE_MB}MB 限制")
    try:
        result = app_svc.create_application(
            {"name": name, "phone": phone, "email": email,
             "expected_title": expected_title, "referrer": referrer,
             "cover_letter": cover_letter},
            file_bytes, file.filename if file else "", ip)
    except ValueError as e:
        raise HTTPException(400, str(e))
    ip_dq.append(time.time())
    phone_dq.append(time.time())
    return result


class QueryIn(BaseModel):
    phone: str
    code: str


@apply_app.post("/apply-api/query")
def query(body: QueryIn):
    rec = app_svc.query_application(body.phone, body.code)
    if not rec:
        raise HTTPException(404, "未找到投递记录：请核对手机号与查询码（区分大小写不敏感）")
    return rec


@apply_app.post("/apply-api/update")
async def update(request: Request,
                 phone: str = Form(...), code: str = Form(...),
                 name: str = Form(""), email: str = Form(""),
                 expected_title: str = Form(""), referrer: str = Form(""),
                 cover_letter: str = Form(""),
                 file: UploadFile | None = File(None)):
    data = await request.form()
    fields = {k: str(data.get(k) or "") for k in ("name", "email", "expected_title",
                                                  "referrer", "cover_letter") if data.get(k) is not None}
    file_bytes = None
    if file is not None and (file.filename or ""):
        file_bytes = file.file.read(app_svc.MAX_FILE_MB * 1024 * 1024 + 1)
        if len(file_bytes) > app_svc.MAX_FILE_MB * 1024 * 1024:
            raise HTTPException(413, f"文件超过 {app_svc.MAX_FILE_MB}MB 限制")
    try:
        rec = app_svc.update_application(phone, code, fields, file_bytes,
                                         file.filename if file else "")
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return rec


@apply_app.get("/apply-api/file/{aid}")
def get_apply_file(aid: int, phone: str = "", code: str = ""):
    """投递人凭 手机号+查询码 在线预览自己提交的简历（inline）。"""
    rec = app_svc.query_application(phone, code)
    if not rec or rec["id"] != aid:
        raise HTTPException(403, "无权查看")
    full = app_svc.get_application(aid)
    p = Path(full.get("file_path") or "")
    if not full.get("file_path") or not p.exists():
        raise HTTPException(404, "该投递没有简历文件")
    try:
        p.resolve().relative_to(config.APPLICATION_DIR.resolve())
    except ValueError:
        raise HTTPException(403, "非法路径")
    return FileResponse(p, filename=full.get("file_name") or p.name,
                        content_disposition_type="inline")


@apply_app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    import traceback
    traceback.print_exc()
    return JSONResponse(status_code=500, content={"detail": f"服务器错误: {type(exc).__name__}"})
