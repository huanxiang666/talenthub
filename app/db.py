"""SQLite 数据层：schema 建表、连接管理（每请求一连接 + WAL），字段字典统一定义。"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime

from . import config

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS talents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL DEFAULT '',
    gender TEXT DEFAULT '',
    age INTEGER,
    phone TEXT DEFAULT '',
    email TEXT DEFAULT '',
    city TEXT DEFAULT '',
    education TEXT DEFAULT '',
    school TEXT DEFAULT '',
    major TEXT DEFAULT '',
    work_years REAL,
    current_company TEXT DEFAULT '',
    current_title TEXT DEFAULT '',
    expected_title TEXT DEFAULT '',
    expected_salary TEXT DEFAULT '',
    skills TEXT DEFAULT '',
    source TEXT DEFAULT '',
    source_file TEXT DEFAULT '',
    stored_path TEXT DEFAULT '',
    status TEXT DEFAULT '潜在',
    rating TEXT DEFAULT '',
    tags TEXT DEFAULT '',
    summary TEXT DEFAULT '',
    raw_text TEXT DEFAULT '',
    extract_method TEXT DEFAULT '',
    parse_confidence REAL DEFAULT 0,
    notes TEXT DEFAULT '[]',
    created_at TEXT DEFAULT '',
    updated_at TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_talents_phone ON talents(phone);
CREATE INDEX IF NOT EXISTS idx_talents_email ON talents(email);
CREATE INDEX IF NOT EXISTS idx_talents_name  ON talents(name);
CREATE INDEX IF NOT EXISTS idx_talents_status ON talents(status);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    time TEXT DEFAULT '',
    action TEXT DEFAULT '',
    detail TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL DEFAULT '',          -- 8位查询码（投递人凭 手机号+查询码 查状态/修改）
    name TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    email TEXT DEFAULT '',
    expected_title TEXT DEFAULT '',
    referrer TEXT DEFAULT '',               -- 推荐人（企业内部投递场景）
    cover_letter TEXT DEFAULT '',           -- 自我介绍/留言
    file_name TEXT DEFAULT '',
    file_path TEXT DEFAULT '',
    status TEXT DEFAULT '待审核',            -- 待审核 / 已同意 / 已拒绝
    reject_reason TEXT DEFAULT '',
    talent_id INTEGER,                      -- 转人才库后的记录 id
    ip TEXT DEFAULT '',
    created_at TEXT DEFAULT '',
    updated_at TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_applications_phone ON applications(phone);
CREATE INDEX IF NOT EXISTS idx_applications_status ON applications(status);

CREATE TABLE IF NOT EXISTS ai_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    time TEXT DEFAULT '',
    engine TEXT DEFAULT '',          -- local | api
    model TEXT DEFAULT '',           -- 模型名（本地文件名或 API 模型名）
    scene TEXT DEFAULT '',           -- resume_parse | chat | test
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    elapsed_ms INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS lock_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    time TEXT DEFAULT '',
    ip TEXT DEFAULT '',
    ok INTEGER DEFAULT 0,            -- 0=失败 1=成功
    detail TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_lock_attempts_time ON lock_attempts(time);
"""

# 性能索引（万级数据筛选/排序不卡）：覆盖常用筛选列与排序列
PERF_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_talents_rating ON talents(rating)",
    "CREATE INDEX IF NOT EXISTS idx_talents_education ON talents(education)",
    "CREATE INDEX IF NOT EXISTS idx_talents_created ON talents(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_talents_work_years ON talents(work_years)",
    "CREATE INDEX IF NOT EXISTS idx_talents_graduate_year ON talents(graduate_year)",
    "CREATE INDEX IF NOT EXISTS idx_talents_skills ON talents(skills)",
    "CREATE INDEX IF NOT EXISTS idx_talents_tags ON talents(tags)",
    "CREATE INDEX IF NOT EXISTS idx_events_id_desc ON events(id DESC)",
]

# 增量迁移列（老库 ALTER TABLE 补列；已存在时忽略 Duplicate column 错误）
MIGRATE_COLUMNS = [
    ("wechat", "TEXT DEFAULT ''"),
    ("hometown", "TEXT DEFAULT ''"),
    ("political_status", "TEXT DEFAULT ''"),
    ("graduate_year", "INTEGER"),
    ("certificates", "TEXT DEFAULT ''"),
    ("languages", "TEXT DEFAULT ''"),
    ("work_history", "TEXT DEFAULT ''"),   # JSON: [{period, company, title}]
    ("project_experience", "TEXT DEFAULT ''"),
]

# 人才字段（导出/导入/表单共用同一份定义，防止三处漂移）
FIELDS = [
    ("name", "姓名"), ("gender", "性别"), ("age", "年龄"), ("phone", "电话"),
    ("email", "邮箱"), ("wechat", "微信"), ("city", "城市"), ("hometown", "籍贯"),
    ("political_status", "政治面貌"), ("education", "学历"), ("school", "毕业院校"),
    ("major", "专业"), ("graduate_year", "毕业年份"), ("work_years", "工作年限"),
    ("current_company", "当前公司"), ("current_title", "当前职位"),
    ("expected_title", "期望职位"), ("expected_salary", "期望薪资"),
    ("skills", "技能"), ("certificates", "证书"), ("languages", "语言能力"),
    ("status", "状态"), ("rating", "评级"), ("tags", "标签"),
    ("source", "来源"), ("summary", "简介"), ("work_history", "工作经历"),
    ("project_experience", "项目经验"), ("source_file", "源文件"),
    ("extract_method", "提取方式"), ("parse_confidence", "解析置信度"),
    ("created_at", "创建时间"), ("updated_at", "更新时间"),
]

STATUS_LIST = ["潜在", "跟进中", "已邀约", "已面试", "已录用", "不合适", "已归档"]  # 默认状态（兼容旧引用）
RATING_LIST = ["A", "B", "C", "D"]
APPLICATION_STATUSES = ["待审核", "已同意", "已拒绝"]


def statuses() -> list[str]:
    """当前生效状态列表（配置可自定义）。"""
    return config.get_statuses()


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        config.ensure_dirs()
        conn = sqlite3.connect(str(config.DB_PATH), check_same_thread=False, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        _local.conn = conn
    return conn


@contextmanager
def db():
    """事务上下文：正常提交，异常回滚并上抛（严禁吞错误）。"""
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def init_db() -> None:
    with db() as conn:
        conn.executescript(SCHEMA)
        # 增量迁移：老库补新列（v1.1 新增 微信/籍贯/政治面貌/毕业年份/证书/语言/工作经历/项目经验）
        for col, decl in MIGRATE_COLUMNS:
            try:
                conn.execute(f"ALTER TABLE talents ADD COLUMN {col} {decl}")
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise
        # 幂等迁移：存量邮箱归一为小写（去重键大小写不敏感的前提，2026-11 复核阻断项#1）
        conn.execute("UPDATE talents SET email = lower(email) WHERE email <> lower(email)")
        # 性能索引（万级数据筛选/排序）
        for stmt in PERF_INDEXES:
            conn.execute(stmt)


def record_ai_usage(engine: str, model: str, scene: str,
                    prompt_tokens: int, completion_tokens: int, elapsed_ms: int) -> None:
    """记录一次 AI 调用的 token 用量（本地/外部引擎统一入口）。"""
    with db() as conn:
        conn.execute(
            "INSERT INTO ai_usage(time, engine, model, scene, prompt_tokens, completion_tokens, total_tokens, elapsed_ms) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (now(), engine, model[:80], scene[:20], int(prompt_tokens or 0),
             int(completion_tokens or 0), int(prompt_tokens or 0) + int(completion_tokens or 0),
             int(elapsed_ms or 0)))


def record_lock_attempt(ip: str, ok: bool, detail: str = "") -> None:
    """锁屏尝试审计（成功/失败都记，含来源 IP）。"""
    with db() as conn:
        conn.execute("INSERT INTO lock_attempts(time, ip, ok, detail) VALUES(?,?,?,?)",
                     (now(), ip[:45], 1 if ok else 0, detail[:200]))
        # 审计表同样裁剪，保留最近 1000 条
        conn.execute("DELETE FROM lock_attempts WHERE id NOT IN "
                     "(SELECT id FROM lock_attempts ORDER BY id DESC LIMIT 1000)")


MAX_EVENTS = 500  # 事件日志裁剪上限，防长期运行无限膨胀


def log_event(action: str, detail: str = "") -> None:
    with db() as conn:
        conn.execute("INSERT INTO events(time, action, detail) VALUES(?,?,?)",
                     (now(), action, detail))
        conn.execute("DELETE FROM events WHERE id NOT IN "
                     "(SELECT id FROM events ORDER BY id DESC LIMIT ?)", (MAX_EVENTS,))


def clear_events(ids: list[int] | None = None) -> int:
    """删除操作日志：ids=None 清空全部，否则删除指定 id。返回删除条数。"""
    with db() as conn:
        if ids is None:
            cur = conn.execute("DELETE FROM events")
        else:
            if not ids:
                return 0
            marks = ",".join("?" * len(ids))
            cur = conn.execute(f"DELETE FROM events WHERE id IN ({marks})", ids)
        return cur.rowcount
