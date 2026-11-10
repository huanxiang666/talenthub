"""电子表格服务：xlsx/csv 导出（带样式）、导入（表头自动映射）、导入模板生成。

导出用 openpyxl 现场构建 BytesIO，不落临时文件；CSV 用 utf-8-sig（Excel 双击不乱码）。
导入表头映射见 COLUMN_ALIASES —— 用户表格列名常见写法都能对上。
"""
from __future__ import annotations

import csv
import io
import re
from datetime import datetime

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .. import db

# 导出列（字段, 表头, 宽度）
EXPORT_COLUMNS = [
    ("name", "姓名", 10), ("gender", "性别", 6), ("age", "年龄", 6),
    ("phone", "电话", 15), ("email", "邮箱", 24), ("wechat", "微信", 14),
    ("city", "城市", 10), ("hometown", "籍贯", 10),
    ("education", "学历", 8), ("school", "毕业院校", 16), ("major", "专业", 14),
    ("graduate_year", "毕业年份", 9),
    ("work_years", "工作年限", 9), ("current_company", "当前公司", 20),
    ("current_title", "当前职位", 14), ("expected_title", "期望职位", 14),
    ("expected_salary", "期望薪资", 12), ("skills", "技能", 36),
    ("certificates", "证书", 22), ("languages", "语言能力", 16),
    ("status", "状态", 9), ("rating", "评级", 6), ("tags", "标签", 14),
    ("source", "来源", 12), ("summary", "简介", 40),
    ("work_history", "工作经历", 50),
    ("created_at", "创建时间", 18), ("updated_at", "更新时间", 18),
]

# 导入表头别名（全部小写比较，去空格/星号）
COLUMN_ALIASES = {
    "name": ["姓名", "名字", "候选人姓名", "人才姓名", "name", "候选人"],
    "gender": ["性别", "gender", "sex"],
    "age": ["年龄", "age"],
    "phone": ["电话", "手机", "手机号", "联系电话", "手机号码", "联系方式", "phone", "mobile", "tel"],
    "email": ["邮箱", "邮件", "电子邮箱", "电子邮件", "email", "e-mail", "mail"],
    "wechat": ["微信", "微信号", "wechat"],
    "city": ["城市", "所在城市", "现居城市", "地点", "工作地", "city", "location"],
    "hometown": ["籍贯", "户籍", "hometown"],
    "political_status": ["政治面貌", "political_status"],
    "education": ["学历", "最高学历", "教育程度", "education"],
    "school": ["毕业院校", "学校", "院校", "毕业学校", "school", "university"],
    "major": ["专业", "所学专业", "major"],
    "graduate_year": ["毕业年份", "毕业时间", "graduate_year"],
    "work_years": ["工作年限", "工作经验", "经验年数", "年限", "年资", "work_years", "experience", "years"],
    "current_company": ["当前公司", "目前公司", "所在公司", "现公司", "公司", "company"],
    "current_title": ["当前职位", "目前职位", "职位", "岗位", "现任职务", "title", "position"],
    "expected_title": ["期望职位", "意向职位", "应聘职位", "求职意向", "expected_title"],
    "expected_salary": ["期望薪资", "薪资要求", "期望月薪", "薪酬要求", "expected_salary", "salary"],
    "skills": ["技能", "专业技能", "技术栈", "擅长技能", "skills", "skill"],
    "certificates": ["证书", "资格证书", "职业证书", "持有证书", "certificates"],
    "languages": ["语言能力", "语言", "外语能力", "languages"],
    "status": ["状态", "人才状态", "跟进状态", "status"],
    "rating": ["评级", "等级", "人才评级", "潜力评级", "rating", "grade"],
    "tags": ["标签", "人才标签", "tags"],
    "source": ["来源", "渠道", "来源渠道", "source", "channel"],
    "summary": ["简介", "自我评价", "个人简介", "摘要", "summary", "intro"],
    "work_history": ["工作经历", "职业经历", "work_history"],
    "project_experience": ["项目经验", "项目经历", "project_experience"],
    "notes_text": ["备注", "跟进记录", "remark", "remarks", "note"],
}

HEADER_FILL = PatternFill("solid", fgColor="0369A1")   # 与前端主题一致（深青蓝）
HEADER_FONT = Font(name="微软雅黑", bold=True, color="FFFFFF", size=11)
BODY_FONT = Font(name="微软雅黑", size=10)
THIN = Side(style="thin", color="D8DEE6")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
ALT_FILL = PatternFill("solid", fgColor="F4F8FB")

STATUS_COLORS = {"潜在": "475569", "跟进中": "0284C7", "已邀约": "9333EA",
                 "已面试": "D97706", "已录用": "059669", "不合适": "DC2626", "已归档": "94A3B8"}
RATING_COLORS = {"A": "059669", "B": "0284C7", "C": "D97706", "D": "94A3B8"}


def _style_sheet(ws, n_cols: int, widths: list[int]):
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(n_cols)}{max(ws.max_row, 1)}"
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for cell in ws[1]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = BORDER
    ws.row_dimensions[1].height = 22
    status_col = next((i for i, c in enumerate(EXPORT_COLUMNS, 1) if c[0] == "status"), None)
    rating_col = next((i for i, c in enumerate(EXPORT_COLUMNS, 1) if c[0] == "rating"), None)
    for r_idx, row in enumerate(ws.iter_rows(min_row=2), start=2):
        for cell in row:
            cell.font = BODY_FONT
            cell.border = BORDER
            cell.alignment = Alignment(vertical="center", wrap_text=False)
            if r_idx % 2 == 0:
                cell.fill = ALT_FILL
        if status_col:
            v = row[status_col - 1].value
            if v in STATUS_COLORS:
                row[status_col - 1].font = Font(name="微软雅黑", size=10, bold=True,
                                                 color=STATUS_COLORS[v])
        if rating_col:
            v = row[rating_col - 1].value
            if v in RATING_COLORS:
                row[rating_col - 1].font = Font(name="微软雅黑", size=10, bold=True,
                                                color=RATING_COLORS[v])


_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _safe_cell(v):
    """防公式注入（CSV/XLSX injection）：简历文本属不可信输入，
    以 = + - @ 开头的字符串加 ' 前缀强制按文本处理，防止导出件在 Excel 中执行公式。"""
    if isinstance(v, str) and v[:1] in _FORMULA_PREFIXES:
        return "'" + v
    return v


def _fmt_cell(key: str, v):
    """导出单元格格式化：work_history JSON→可读文本；work_years 去 .0；防公式注入。"""
    if key == "work_history" and isinstance(v, str) and v.strip().startswith("["):
        import json as _json
        try:
            segs = _json.loads(v)
            v = "；".join(
                " ".join(x for x in (s.get("period", ""), s.get("company", ""),
                                     f"({s.get('title')})" if s.get("title") else "") if x)
                for s in segs if isinstance(s, dict))
        except _json.JSONDecodeError:
            pass  # 非 JSON（用户手填文本）原样导出
    if key == "work_years" and v not in (None, ""):
        try:
            f = float(v)
            if f == int(f):
                v = int(f)
        except (TypeError, ValueError):
            pass
    return "" if v is None else _safe_cell(v)


def export_xlsx(rows: list[dict], title: str = "人才列表") -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = title[:28]
    ws.append([c[1] for c in EXPORT_COLUMNS])
    for r in rows:
        line = [_fmt_cell(key, r.get(key)) for key, _label, _w in EXPORT_COLUMNS]
        ws.append(line)
    _style_sheet(ws, len(EXPORT_COLUMNS), [c[2] for c in EXPORT_COLUMNS])

    # 第二个 sheet：数据概览（导出即带统计，给领导看的规范件）
    ov = wb.create_sheet("数据概览")
    ov.append(["统计项", "数值"])
    ov.append(["导出时间", datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
    ov.append(["人才总数", len(rows)])
    for label, keyf in [("学历", "education"), ("状态", "status"), ("评级", "rating")]:
        counter: dict[str, int] = {}
        for r in rows:
            k = str(r.get(keyf) or "未填写")
            counter[k] = counter.get(k, 0) + 1
        for k, v in sorted(counter.items(), key=lambda kv: -kv[1]):
            ov.append([f"{label}-{k}", v])
    for cell in ov[1]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
    ov.column_dimensions["A"].width = 24
    ov.column_dimensions["B"].width = 22
    for row in ov.iter_rows(min_row=2):
        for cell in row:
            cell.font = BODY_FONT
            cell.border = BORDER

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def export_csv(rows: list[dict]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([c[1] for c in EXPORT_COLUMNS])
    for r in rows:
        w.writerow([_fmt_cell(k, r.get(k)) for k, _l, _w in EXPORT_COLUMNS])
    return buf.getvalue().encode("utf-8-sig")


def template_xlsx() -> bytes:
    """导入模板：表头 + 2 行示例 + 字段说明 sheet。"""
    wb = Workbook()
    ws = wb.active
    ws.title = "人才导入模板"
    headers = [c[1] for c in EXPORT_COLUMNS if c[0] not in ("created_at", "updated_at")]
    ws.append(headers)
    ws.append(["张三（示例行，导入前删除）", "男", 28, "13800001111", "zhangsan@example.com",
               "zhangsan-wx", "杭州市", "浙江杭州", "本科", "浙江工业大学", "计算机科学", 2016, 5,
               "某某科技有限公司", "Java工程师", "高级Java工程师", "18-25K",
               "Java、Spring、MySQL、Redis", "CET-6、PMP", "英语（CET-6）",
               "潜在", "B", "重点跟进", "招聘会", "5年Java后端经验，主导过高并发系统",
               "2015.07-2020.02 某某信息 Java工程师；2020.03-至今 某某科技 高级Java工程师"])
    ws.append(["李四（示例行，导入前删除）", "女", 32, "13900002222", "lisi@example.com",
               "", "宁波市", "浙江宁波", "硕士", "宁波大学", "机械工程", 2015, 8,
               "某某制造集团", "质量主管", "", "面议",
               "六西格玛、SPC、ISO9001", "六西格玛黑带", "英语（CET-4）",
               "跟进中", "A", "", "内推", "8年制造业质量管理经验", ""])
    widths = [c[2] for c in EXPORT_COLUMNS if c[0] not in ("created_at", "updated_at")]
    ws.freeze_panes = "A2"
    for i, w_ in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w_
    for cell in ws[1]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.border = BORDER
        cell.alignment = Alignment(horizontal="center", vertical="center")
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.font = Font(name="微软雅黑", size=10, color="8A94A6", italic=True)
            cell.border = BORDER

    help_ws = wb.create_sheet("填写说明")
    help_ws.append(["字段", "说明"])
    tips = [
        ("姓名", "必填（姓名/电话/邮箱全空的行会被跳过；缺姓名列时自动用电话/邮箱兜底）"),
        ("电话/邮箱", "重复判定主键：电话 > 邮箱 > 姓名，用于数据合并去重；邮箱大小写不敏感"),
        ("状态", "可选值：潜在 / 跟进中 / 已邀约 / 已面试 / 已录用 / 不合适 / 已归档"),
        ("评级", "可选值：A（高潜）/ B（良好）/ C（一般）/ D（暂缓）"),
        ("工作年限", "数字，如 5 或 3.5"),
        ("毕业年份", "四位数字，如 2016"),
        ("技能/证书/语言能力", "多项用「、」分隔，如 Java、Spring、MySQL"),
        ("工作经历", "文本，如 2020.03-至今 某某公司 高级工程师"),
        ("表头行", "列顺序可任意调整；常见别名（手机/联系方式/最高学历等）可自动识别；带 * 号也能识别"),
    ]
    for t in tips:
        help_ws.append(list(t))
    help_ws.column_dimensions["A"].width = 16
    help_ws.column_dimensions["B"].width = 70
    for cell in help_ws[1]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
    for row in help_ws.iter_rows(min_row=2):
        for cell in row:
            cell.font = BODY_FONT
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _norm_header(h) -> str:
    return re.sub(r"[\s*＊:：()（）/\\\-_#]", "", str(h or "")).lower()


def build_column_map(headers: list) -> dict[int, str]:
    """列号(0-based) → 标准字段名。返回 {} 表示一个都没对上。"""
    alias_lookup = {}
    for field, aliases in COLUMN_ALIASES.items():
        for a in aliases:
            alias_lookup[_norm_header(a)] = field
    mapping = {}
    for i, h in enumerate(headers):
        f = alias_lookup.get(_norm_header(h))
        if f:
            mapping[i] = f
    return mapping


def parse_table(data: bytes, filename: str) -> dict:
    """解析上传的 xlsx/csv → {headers, mapping, rows(已映射为标准字段), warnings}。"""
    name = (filename or "").lower()
    warnings: list[str] = []
    if name.endswith(".csv") or name.endswith(".txt"):
        text = None
        for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise ValueError("CSV 编码无法识别（支持 UTF-8 / GB18030 / GBK）")
        reader = csv.reader(io.StringIO(text))
        all_rows = [r for r in reader if any(str(c).strip() for c in r)]
    elif name.endswith(".xlsx") or name.endswith(".xlsm"):
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        ws = wb.active
        all_rows = [[("" if c is None else c) for c in row]
                    for row in ws.iter_rows(values_only=True)]
        all_rows = [r for r in all_rows if any(str(c).strip() for c in r)]
        wb.close()
    elif name.endswith(".xls"):
        raise ValueError("暂不支持老式 .xls 格式，请在 Excel/WPS 中另存为 .xlsx 或 .csv 后导入")
    else:
        raise ValueError("仅支持 .xlsx / .xlsm / .csv 表格文件")

    if len(all_rows) < 2:
        raise ValueError("表格为空或只有表头行，没有可导入的数据")

    headers = [str(h).strip() for h in all_rows[0]]
    mapping = build_column_map(headers)
    if not mapping:
        raise ValueError(f"表头无法识别：{headers}。请使用模板或包含 姓名/电话/邮箱 等列")
    if not any(f == "name" for f in mapping.values()):
        warnings.append("未找到「姓名」列，导入记录将尝试用电话/邮箱兜底标识")

    rows = []
    for raw in all_rows[1:]:
        rec = {}
        for i, field in mapping.items():
            if i < len(raw):
                v = raw[i]
                if isinstance(v, datetime):
                    v = v.strftime("%Y-%m-%d")
                v = "" if v is None else str(v).strip()
                if field == "notes_text":
                    if v:
                        rec.setdefault("_notes", []).append(v)
                elif v or field not in rec:
                    # 多列映射同一字段时（如"电话"+"手机"并存）取非空值，后列空值不覆盖前列
                    rec[field] = v
        if not rec.get("name"):
            # 兑现"缺姓名列用电话/邮箱兜底"的提示，保证无姓名行也能入库
            fallback = rec.get("phone") or rec.get("email")
            if fallback:
                rec["name"] = fallback
        if any(rec.get(k) for k in ("name", "phone", "email")):
            rows.append(rec)
    unmapped = [h for i, h in enumerate(headers) if i not in mapping and h]
    if unmapped:
        warnings.append(f"以下列未识别、将被忽略：{'、'.join(unmapped[:8])}")
    return {"headers": headers, "mapping": {i: f for i, f in mapping.items()},
            "rows": rows, "warnings": warnings,
            "skipped_empty": len(all_rows) - 1 - len(rows)}


def analyze_duplicates(rows: list[dict]) -> list[dict]:
    """对每行做重复分析：{row_index, duplicate_of(id), matched_by}。"""
    from . import talent as talent_svc
    out = []
    seen_local: dict[str, int] = {}
    for i, r in enumerate(rows):
        phone = re.sub(r"[^\d+]", "", r.get("phone", ""))[:20]
        email = (r.get("email") or "").lower()
        name = r.get("name", "")
        dup = talent_svc.find_duplicate(phone, email, name)
        matched_by = ""
        if dup:
            if phone and dup.get("phone") == phone:
                matched_by = "电话"
            elif email and (dup.get("email") or "").lower() == email:
                matched_by = "邮箱"
            else:
                matched_by = "姓名"
        else:
            key = phone or email or name
            if key and key in seen_local:
                dup = {"id": seen_local[key]}
                matched_by = "文件内重复"
            elif key:
                seen_local[key] = -1  # 占位，实际 id 在 commit 后才有
        out.append({"row_index": i, "duplicate_of": (dup or {}).get("id"),
                    "matched_by": matched_by})
    return out
