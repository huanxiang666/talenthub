"""简历字段解析器：规则引擎从纯文本中抽取结构化字段（中文简历优化，OCR 文本兼容）。

设计取舍：不用 NLP 模型而用分层规则 —— 可离线、零模型依赖、可解释、可手工修正。
每个字段独立打分，汇总成 parse_confidence，低置信度的由前端标黄提示人工确认。
"""
from __future__ import annotations

import re
from pathlib import Path

# ---------- 常量词典 ----------
EDU_ORDER = ["博士后", "博士", "硕士", "研究生", "本科", "大专", "专科", "中专", "职高", "高中", "初中"]
EDU_NORMALIZE = {"研究生": "硕士", "专科": "大专", "职高": "中专"}

CITIES = [
    "北京", "上海", "广州", "深圳", "杭州", "南京", "苏州", "宁波", "无锡", "常州",
    "天津", "重庆", "成都", "武汉", "西安", "长沙", "郑州", "青岛", "济南", "合肥",
    "福州", "厦门", "东莞", "佛山", "珠海", "中山", "惠州", "温州", "绍兴", "嘉兴",
    "金华", "台州", "南通", "徐州", "扬州", "盐城", "潍坊", "烟台", "石家庄", "太原",
    "沈阳", "大连", "长春", "哈尔滨", "南昌", "南宁", "贵阳", "昆明", "兰州", "乌鲁木齐",
    "呼和浩特", "海口", "银川", "西宁", "拉萨",
]

SKILL_DICT = [
    # IT/研发
    "Java", "Python", "C++", "C#", "Go", "Golang", "Rust", "PHP", "Ruby", "Kotlin",
    "Swift", "JavaScript", "TypeScript", "Node.js", "Vue", "React", "Angular", "Spring",
    "SpringBoot", "Spring Boot", "SpringCloud", "MyBatis", "Django", "Flask", "FastAPI",
    "MySQL", "PostgreSQL", "Oracle", "SQL Server", "Redis", "MongoDB", "Elasticsearch",
    "Kafka", "RabbitMQ", "Docker", "Kubernetes", "K8s", "Jenkins", "Linux", "Nginx",
    "微服务", "分布式", "高并发", "算法", "数据结构", "机器学习", "深度学习", "PyTorch",
    "TensorFlow", "OpenCV", "NLP", "大模型", "LLM", "数据分析", "爬虫", "小程序",
    "Android", "iOS", "Unity", "UE4", "嵌入式", "单片机", "STM32", "FPGA", "PLC",
    "MATLAB", "Simulink", "CAD", "SolidWorks", "UG", "Pro/E", "Creo", "CATIA",
    # 制造/工程/质量
    "精益生产", "六西格玛", "SPC", "FMEA", "APQP", "PPAP", "MSA", "ISO9001", "IATF16949",
    "质量管理", "品质管理", "生产管理", "供应链", "采购", "仓储", "物流", "ERP", "SAP",
    "MES", "WMS", "AutoCAD", "天正", "电气设计", "机械设计", "模具", "注塑", "冲压",
    "焊接", "数控", "CNC", "车床", "铣床", "设备维护", "自动化", "机器人", "视觉检测",
    # 职能/通用
    "财务", "会计", "审计", "税务", "成本管理", "预算", "人力资源", "招聘", "薪酬绩效",
    "劳动法", "市场营销", "品牌", "新媒体运营", "SEO", "SEM", "私域", "直播带货",
    "销售", "大客户", "商务谈判", "项目管理", "PMP", "敏捷开发", "Scrum", "产品经理",
    "需求分析", "原型设计", "Axure", "Figma", "PS", "Photoshop", "AI设计", "平面设计",
    "视频剪辑", "Pr", "办公自动化", "Excel", "PPT", "英语", "日语", "CET-4", "CET-6",
    "雅思", "托福", "驾照", "电工证", "焊工证", "叉车证", "注册会计师", "CPA",
    "一级建造师", "二级建造师", "消防工程师", "执业药师", "教师资格",
]
SKILL_LOWER = {s.lower(): s for s in SKILL_DICT}

SECTION_HEADS = [
    "基本信息", "个人信息", "求职意向", "教育经历", "教育背景", "工作经历", "工作经验",
    "项目经验", "项目经历", "专业技能", "技能特长", "技术栈", "自我评价", "个人优势",
    "个人总结", "自我介绍", "培训经历", "证书", "荣誉证书", "语言能力", "技能",
]

RE_PHONE = re.compile(r"(?:\+?86[\s\-]?)?(1[3-9]\d)[\s\-]?(\d{4})[\s\-]?(\d{4})")
RE_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")
RE_NAME_LABEL = re.compile(r"姓\s*名\s*[:：]?\s*([\u4e00-\u9fa5·]{2,6})")
RE_NAME_SELF = re.compile(r"(?:我叫|我是)\s*([\u4e00-\u9fa5·]{2,6})")
RE_AGE = re.compile(r"(?:年\s*龄|age)\s*[:：]?\s*(\d{2})\s*岁?|(\d{2})\s*岁", re.I)
RE_BIRTH = re.compile(r"(19[89]\d|200\d)\s*年?\s*(?:\d{1,2}\s*月?)?\s*(?:出生|生)")
RE_WORKYEARS = re.compile(
    r"(\d{1,2}(?:\.\d)?)\s*年(?:以上)?(?:[\u4e00-\u9fa5A-Za-z]{0,12}?)经[验历]"
    r"|工作(?:年|时)限\s*[:：]?\s*(\d{1,2}(?:\.\d)?)\s*年?")
RE_SCHOOL = re.compile(r"([\u4e00-\u9fa5]{2,14}?(?:大学|学院|职业技术学校|技工学校|高级中学))")
# 专业：①带标签 ②"学校 专业 学历"同行模式（教育经历行/docx 表格行）。
# 不用 `X专业` 反向模式 —— 实测会误命中"专业技能"前的文字。
RE_MAJOR_LABEL = re.compile(r"(?:主修|所学)?专业\s*[:：]\s*([\u4e00-\u9fa5A-Za-z（）()]{2,20})")
RE_MAJOR_LINE = re.compile(
    r"[\u4e00-\u9fa5]{2,14}?(?:大学|学院|学校|职业技术学校)[\s\t]*"
    r"([\u4e00-\u9fa5A-Za-z（）()]{2,20}?)[\s\t]*"
    r"(?:博士后|博士|硕士|研究生|本科|大专|专科|中专|高中)")
RE_EXPECT_TITLE = re.compile(
    r"(?:期望|意向|应聘|求职|目标)\s*(?:职位|岗位|职务|方向|工种)\s*[:：]\s*([^\n，,;；。：:\t]{2,30})")
RE_EXPECT_SALARY = re.compile(
    r"(?:期望|意向)?(?:薪资|月薪|年薪|薪酬)\s*(?:要求|范围|预期)?\s*[:：]?\s*"
    r"(\d+(?:\.\d+)?\s*[kK千wW万]?\s*[-–~至]\s*\d+(?:\.\d+)?\s*[kK千wW万]?|\d+(?:\.\d+)?\s*[kK千wW万](?:以上)?|面议)")
RE_COMPANY = re.compile(
    r"([\u4e00-\u9fa5A-Za-z0-9（）()]{2,24}?(?:有限公司|股份有限公司|集团有限公司|集团|公司|银行|事务所|研究院|研究所|机械厂|工厂))")
RE_TITLE_WORD = re.compile(
    r"(?:担任|任职|职位|岗位)\s*[:：]?\s*([^\n，,;；。]{2,25})"
    r"|([\u4e00-\u9fa5A-Za-z]{0,8}(?:工程师|经理|总监|主管|专员|架构师|设计师|顾问|讲师|技术员|操作工|会计|出纳|助理|总裁|厂长|部长|科长|班长|组长|开发|程序员|分析师|运营|编辑|翻译|护士|药师|司机|焊工|电工))")
RE_CITY = re.compile(r"(?:现居|居住在?|所在地|所在城市|城市|现所在地|base)\s*[:：]?\s*([\u4e00-\u9fa5]{2,10}?市)", re.I)
RE_GENDER = re.compile(r"性\s*别\s*[:：]?\s*(男|女)")


def _norm(text: str) -> str:
    """全角→半角、统一空白，便于正则命中 OCR 文本。"""
    out = []
    for ch in text:
        code = ord(ch)
        if code == 0x3000:
            out.append(" ")
        elif 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - 0xFEE0))
        else:
            out.append(ch)
    s = "".join(out)
    return re.sub(r"[ \t]+", " ", s)


def _section(text: str, heads: list[str], stop: list[str] | None = None) -> str:
    """取出某个小节标题到下一个小节标题之间的内容。"""
    stop = stop or SECTION_HEADS
    for h in heads:
        m = re.search(rf"(?:^|\n)\s*[#*■◆•\-\s]*{re.escape(h)}[^\n]*\n?", text)
        if not m:
            continue
        rest = text[m.end():]
        end = len(rest)
        for s in stop:
            if s in heads:
                continue
            sm = re.search(rf"(?:^|\n)\s*[#*■◆•\-\s]*{re.escape(s)}", rest)
            if sm and sm.start() < end:
                end = sm.start()
        return rest[:end].strip()
    return ""


def _guess_name(text: str, filename: str) -> str:
    m = RE_NAME_LABEL.search(text)
    if m:
        return m.group(1)
    m = RE_NAME_SELF.search(text)
    if m:
        return m.group(1)
    # 首个"干净"的 2-4 字中文行（简历头部通常就是姓名）
    skip_words = ("简历", "求职", "应聘", "个人", "基本", "联系", "电话", "邮箱", "意向", "自我介绍")
    for raw in text.splitlines()[:12]:
        line = re.sub(r"[#*■◆•\-\s|｜/、,，.。:：()（）]", "", raw)
        if not line:
            continue
        if any(w in line for w in skip_words):
            continue
        if re.fullmatch(r"[\u4e00-\u9fa5·]{2,4}", line):
            return line
    # 文件名兜底：如 "张伟-Java工程师-5年.pdf"
    if filename:
        stem = Path(filename).stem
        for token in re.split(r"[-_—\s|@#.]+", stem):
            token = token.strip()
            if re.fullmatch(r"[\u4e00-\u9fa5·]{2,4}", token) and not any(
                    w in token for w in ("简历", "个人", "应聘")):
                return token
    return ""


def _guess_gender(text: str) -> str:
    m = RE_GENDER.search(text)
    if m:
        return m.group(1)
    head = text[:300]
    m = re.search(r"[|｜/、,，\s]?\s*(男|女)\s*[|｜/、,，]\s*\d{2}\s*岁|[\u4e00-\u9fa5]{2,4}[，,\s]+(男|女)[，,\s|｜/、]", head)
    if m:
        return m.group(1) or m.group(2)
    return ""


def _guess_age(text: str) -> int | None:
    m = RE_AGE.search(text)
    if m:
        return int(m.group(1) or m.group(2))
    m = RE_BIRTH.search(text)
    if m:
        from datetime import date
        return max(0, date.today().year - int(m.group(1)))
    return None


def _guess_education(text: str) -> str:
    m = re.search(r"(?:最高)?学\s*历\s*[:：]?\s*(博士后|博士|硕士|研究生|本科|大专|专科|中专|高中)", text)
    if m:
        return EDU_NORMALIZE.get(m.group(1), m.group(1))
    best, best_idx = "", len(EDU_ORDER)
    for i, e in enumerate(EDU_ORDER):
        if e in text and i < best_idx:
            best, best_idx = e, i
    return EDU_NORMALIZE.get(best, best)


def _guess_city(text: str) -> str:
    m = RE_CITY.search(text[:800])
    if m:
        return m.group(1)
    head = text[:600]
    for c in CITIES:
        if re.search(rf"{c}(?:市)?(?:[，,、|｜/\s]|$)", head):
            return c + "市" if c not in ("北京", "上海", "天津", "重庆") else c
    return ""


def _guess_work_years(text: str) -> float | None:
    best = None
    for m in RE_WORKYEARS.finditer(text[:2500]):
        v = m.group(1) or m.group(2)
        if v:
            f = float(v)
            if 0.5 <= f <= 50 and (best is None or f > best):
                best = f
    return best


def _guess_skills(text: str) -> str:
    found: list[str] = []

    def add(s: str):
        s = s.strip(" .。,，、;；:：/|｜-—*■◆•\t")
        if not s or len(s) > 30:
            return
        key = s.lower()
        for f in found:
            if f.lower() == key:
                return
        found.append(s)

    sec = _section(text, ["专业技能", "技能特长", "技术栈", "技能", "掌握技能", "专业技能/证书"])
    if sec:
        for piece in re.split(r"[、，,;；。\n/|｜]+|(?:\d[.、)])", sec):
            # 去掉"精通/熟练使用/熟悉…"前缀，让词典去重能命中（"精通 Java"→"Java"）
            piece = re.sub(r"^(?:精通|熟练掌握|熟练使用|熟练|熟悉|掌握|了解|擅长)\s*", "", piece.strip())
            if 1 < len(piece) <= 30:
                add(piece)
    # 全文词典扫描（补漏）
    low = text.lower()
    for key, canonical in SKILL_LOWER.items():
        if len(key) <= 2:  # 超短词（如 "ai","ps","go"）要求词边界，避免误报
            if re.search(rf"(?<![a-z]){re.escape(key)}(?![a-z])", low):
                add(canonical)
        elif key in low:
            add(canonical)
    return "、".join(found[:24])


def _guess_company_title(text: str) -> tuple[str, str]:
    """从工作经历小节取最近一段公司与职位（小节内第一次出现≈最近）。"""
    sec = _section(text, ["工作经历", "工作经验", "职业经历", "工作经历/项目经历"]) or text
    company = ""
    m = RE_COMPANY.search(sec)
    if m:
        # 去掉前缀噪音（"至今宁波xx集团" → "宁波xx集团"）
        company = re.sub(r"^(?:至今|迄今|今|在|于|时间|日期)+", "", m.group(1).strip())
    title = ""
    m = re.search(r"(?:担任|任职|职位|岗位)\s*[:：]?\s*([^\n，,;；。]{2,25})", sec)
    if m:
        title = re.split(r"\s{2,}", m.group(1).strip())[0][:25]
    else:
        # 公司行之后 3 行内找职位词
        lines = sec.splitlines()
        for i, line in enumerate(lines):
            if company and company in line:
                window = lines[i:i + 4]
            else:
                continue
            for w in window:
                m2 = RE_TITLE_WORD.search(w)
                if m2 and not re.search(r"\d{4}\s*年|至今|\d{2}[:：]\d{2}", w[:6]):
                    title = (m2.group(1) or m2.group(2) or "").strip()
                    break
            if title:
                break
        if not title:
            m2 = RE_TITLE_WORD.search(sec[:1500])
            if m2:
                title = (m2.group(1) or m2.group(2) or "").strip()
    return company, title


def _guess_summary(text: str) -> str:
    sec = _section(text, ["自我评价", "个人优势", "个人总结", "自我介绍", "本人评价"])
    if sec:
        sec = re.sub(r"\s+", " ", sec)
        return sec[:200]
    return ""


def parse_resume(text: str, filename: str = "") -> dict:
    """主入口：文本 → 结构化字段 dict + confidence(0~1)。"""
    t = _norm(text)
    out: dict = {"raw_text": text.strip()}

    m = RE_PHONE.search(t)
    out["phone"] = "".join(m.groups()) if m else ""
    m = RE_EMAIL.search(t)
    out["email"] = m.group(0) if m else ""
    out["name"] = _guess_name(t, filename)
    out["gender"] = _guess_gender(t)
    out["age"] = _guess_age(t)
    out["education"] = _guess_education(t)
    m = RE_SCHOOL.search(t)
    out["school"] = m.group(1) if m else ""
    m = RE_MAJOR_LABEL.search(t) or RE_MAJOR_LINE.search(t)
    out["major"] = m.group(1).strip() if m else ""
    out["work_years"] = _guess_work_years(t)
    out["city"] = _guess_city(t)
    company, title = _guess_company_title(t)
    out["current_company"] = company
    out["current_title"] = title
    m = RE_EXPECT_TITLE.search(t)
    if m:
        # 截断粘连的后续标签（如"机械设计工程师    期望薪资：…"）
        title = re.split(r"\s{2,}|期望薪资|薪资要求|工作地点|工作城市|月薪", m.group(1))[0].strip()
        out["expected_title"] = title[:30]
    else:
        out["expected_title"] = ""
    m = RE_EXPECT_SALARY.search(t)
    out["expected_salary"] = re.sub(r"\s+", "", m.group(1)) if m else ""
    out["skills"] = _guess_skills(t)
    out["summary"] = _guess_summary(t)

    # 置信度：关键字段加权
    weights = [
        (bool(out["name"]), 0.22), (bool(out["phone"]), 0.16), (bool(out["email"]), 0.10),
        (bool(out["education"]), 0.10), (out["work_years"] is not None, 0.08),
        (bool(out["skills"]), 0.10), (bool(out["current_company"]), 0.09),
        (bool(out["current_title"]), 0.05), (bool(out["city"]), 0.05),
        (bool(out["expected_title"] or out["summary"]), 0.05),
    ]
    out["parse_confidence"] = round(sum(w for ok, w in weights if ok), 3)
    return out
