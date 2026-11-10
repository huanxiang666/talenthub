"""简历字段解析器 v2：规则引擎从纯文本抽取结构化字段（中文简历优化，OCR 文本兼容）。

设计取舍：分层规则做主力（离线、零模型依赖、可解释），非标准简历由可选的本地
mini 大模型（llm_parser.py）兜底补全。两层都不可用时仍输出规则结果 + 低置信度标记。

v2 增强（针对非标准简历识别率）：
- 新增字段：微信/籍贯/政治面貌/毕业年份/证书/语言能力/多段工作经历/项目经验
- 姓名：支持「张三丨Java工程师」「张三 | 8年经验」等分隔符行、括号备注行
- 电话：手机号优先，无手机时回退固话（区号-号码）
- 学历：工学学士/理学硕士/MBA 等学位表述归一
- 工作年限：显式表述缺失时，由工作经历最早年份推算
- 证书/语言：词典扫描（CET/驾照/建造师/教师资格/日语N1…）
- 工作经历：全文扫描「时间区间 + 公司 + 职位」段落，输出 JSON 列表
"""
from __future__ import annotations

import json
import re
from datetime import date
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
    "呼和浩特", "海口", "银川", "西宁", "拉萨", "慈溪", "余姚", "义乌", "昆山", "张家港",
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
    "视频剪辑", "Pr", "办公自动化", "Excel", "PPT",
]
SKILL_LOWER = {s.lower(): s for s in SKILL_DICT}

# 证书词典：(规范名, 匹配正则[对 lower 后文本])
CERT_DICT = [
    ("CET-4", r"cet\s*-?\s*4|大学英语四级|英语四级"),
    ("CET-6", r"cet\s*-?\s*6|大学英语六级|英语六级"),
    ("英语专业四级", r"tem\s*-?\s*4|英语专业四级"),
    ("英语专业八级", r"tem\s*-?\s*8|英语专业八级"),
    ("机动车驾驶证", r"驾照|机动车驾驶证|(?<![a-z0-9])c1(?![a-z0-9])"),
    ("教师资格证", r"教师资格证|教师资格"),
    ("注册会计师", r"注册会计师|(?<![a-z])cpa(?![a-z])"),
    ("一级建造师", r"一级建造师"),
    ("二级建造师", r"二级建造师"),
    ("消防工程师", r"消防工程师"),
    ("执业药师", r"执业药师"),
    ("护士执业证", r"护士执业|护士资格"),
    ("电工证", r"电工证|电工操作证"),
    ("焊工证", r"焊工证"),
    ("叉车证", r"叉车证"),
    ("PMP", r"(?<![a-z])pmp(?![a-z])"),
    ("软考证书", r"软件水平考试|软考(中级|高级)?"),
    ("六西格玛黑带", r"六西格玛黑带"),
    ("六西格玛绿带", r"六西格玛绿带"),
    ("雅思", r"雅思\s*\d(?:\.\d)?|(?<![a-z])ielts(?![a-z])"),
    ("托福", r"托福\s*\d+|(?<![a-z])toefl(?![a-z])"),
    ("日语N1", r"日语\s*[nN]1|jlpt\s*n1"),
    ("日语N2", r"日语\s*[nN]2|jlpt\s*n2"),
    ("会计职称", r"(初级|中级|高级)会计师?职称?"),
    ("人力资源管理师", r"人力资源管理师"),
]

# 语言词典：(语言名, 触发正则, 等级提取正则)
LANG_DICT = [
    ("英语", r"英语|english", r"英语\s*(CET\s*-?\s*[46]|四级|六级|良好|熟练|流利|一般|听说读写)"),
    ("日语", r"日语|japanese", r"日语\s*([nN][12]|[一二三]级|良好|熟练)"),
    ("韩语", r"韩语|朝鲜语|korean", r"韩语\s*(TOPIK\s*\d|良好|熟练)?"),
    ("法语", r"法语|french", r"法语\s*(良好|熟练|DELF)?"),
    ("德语", r"德语|german", r"德语\s*(良好|熟练)?"),
    ("粤语", r"粤语|广东话|cantonese", None),
    ("方言", r"闽南语|温州话|上海话", None),
]

SECTION_HEADS = [
    "基本信息", "个人信息", "求职意向", "教育经历", "教育背景", "工作经历", "工作经验",
    "项目经验", "项目经历", "专业技能", "技能特长", "技术栈", "自我评价", "个人优势",
    "个人总结", "自我介绍", "培训经历", "证书", "荣誉证书", "资格证书", "语言能力",
    "语言能力/证书", "技能", "主要项目", "获奖情况", "荣誉奖项",
]

RE_PHONE = re.compile(r"(?:\+?86[\s\-]?)?(1[3-9]\d)[\s\-]?(\d{4})[\s\-]?(\d{4})")
RE_TEL = re.compile(r"(?<!\d)(0\d{2,3})[\s\-]?(\d{7,8})(?!\d)")
RE_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")
RE_NAME_LABEL = re.compile(r"姓\s*名\s*[:：]?\s*([\u4e00-\u9fa5·]{2,6})")
RE_NAME_SELF = re.compile(r"(?:我叫|我是)\s*([\u4e00-\u9fa5·]{2,6})")
RE_AGE = re.compile(r"(?:年\s*龄|age)\s*[:：]?\s*(\d{2})\s*岁?|(\d{2})\s*岁", re.I)
RE_BIRTH = re.compile(r"(19[789]\d|200\d)\s*[年./\-]?\s*(?:\d{1,2}\s*月?)?\s*(?:出生|生)")
RE_WORKYEARS = re.compile(
    r"(?<![\d.])(\d{1,2}(?:\.\d)?)\s*年(?:以上)?(?:[\u4e00-\u9fa5A-Za-z]{0,12}?)经[验历]"
    r"|工作(?:年|时)限\s*[:：]?\s*(?<![\d.])(\d{1,2}(?:\.\d)?)\s*年?")
# 中文数字年限："两年审计经验" / "三年工作经验"
RE_WORKYEARS_CN = re.compile(r"([一两二三四五六七八九十]{1,3})\s*年(?:以上)?[\u4e00-\u9fa5]{0,12}?经[验历]")
CN_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
RE_SCHOOL = re.compile(r"([\u4e00-\u9fa5]{2,14}?(?:大学|学院|职业技术学校|技工学校|高级中学))")
# 专业：①带标签 ②"学校 专业 学历"同行模式（教育经历行/docx 表格行）。
# 不用 `X专业` 反向模式 —— 实测会误命中"专业技能"前的文字。
RE_MAJOR_LABEL = re.compile(r"(?:主修|所学)?专业\s*[:：]\s*([\u4e00-\u9fa5A-Za-z（）()]{2,20})")
RE_MAJOR_LINE = re.compile(
    r"[\u4e00-\u9fa5]{2,14}?(?:大学|学院|学校|职业技术学校)[\s\t]*"
    r"([\u4e00-\u9fa5A-Za-z（）()]{2,20}?)[\s\t]*"
    r"(?:博士后|博士|硕士|研究生|本科|大专|专科|中专|高中)")
# 学位在前的顺序：「南京理工大学 工学学士 信息管理与信息系统」
RE_MAJOR_DEGREE_FIRST = re.compile(
    r"[\u4e00-\u9fa5]{2,14}?(?:大学|学院|学校|职业技术学校)[\s\t]+"
    r"(?:工学|理学|文学|医学|管理学|经济学|法学|教育学|历史学|哲学|农学)?(?:学士|硕士|博士)[\s\t]+"
    r"([\u4e00-\u9fa5A-Za-z（）()]{2,20})")
RE_EXPECT_TITLE = re.compile(
    r"(?:期望|意向|应聘|求职|目标)\s*(?:职位|岗位|职务|方向|工种)\s*[:：]?\s*是?\s*([^\n，,;；。：:\t]{2,30})")
RE_EXPECT_SALARY = re.compile(
    r"(?:期望|意向)?(?:薪资|月薪|年薪|薪酬)\s*(?:要求|范围|预期)?\s*[:：]?\s*"
    r"(\d+(?:\.\d+)?\s*[kK千wW万]?\s*[-–~至]\s*\d+(?:\.\d+)?\s*[kK千wW万]?|\d+(?:\.\d+)?\s*[kK千wW万](?:以上)?|面议)"
    r"|(?:希望|期望|想要)\s*(\d+(?:\.\d+)?\s*[kK千wW万])\s*(?:往上|以上|起|左右)?")
RE_COMPANY = re.compile(
    r"([\u4e00-\u9fa5A-Za-z0-9（）()]{2,24}?(?:有限公司|股份有限公司|集团有限公司|集团|公司|银行|事务所|研究院|研究所|机械厂|工厂|医院|学校|超市|酒店))")
RE_TITLE_WORD = re.compile(
    r"(?:担任|任职|职位|岗位)\s*[:：]?\s*([^\n，,;；。]{2,25})"
    r"|([\u4e00-\u9fa5A-Za-z]{0,8}(?:工程师|经理|总监|主管|专员|架构师|设计师|顾问|讲师|教师|技术员|操作工|会计|出纳|助理|总裁|厂长|部长|科长|班长|组长|开发|程序员|分析师|运营|编辑|翻译|护士|医师|药师|司机|焊工|电工|钳工|普工|店员|店长|保安|厨师))")
RE_CITY = re.compile(
    r"(?:现居|居住在?|目前住在?|住在|生活在|所在地|所在城市|城市|现所在地|工作地点|意向城市|期望城市|base)\s*[:：]?\s*([\u4e00-\u9fa5]{2,10}?)(?:市|[，,。\n|｜]|$)", re.I)
RE_GENDER = re.compile(r"性\s*别\s*[:：]?\s*(男|女)")
RE_WECHAT = re.compile(r"微\s*信(?:号)?\s*[:：]?\s*([A-Za-z][A-Za-z0-9_\-]{4,19}|1[3-9]\d{9})|微信同(?:手机)?号?")
RE_HOMETOWN = re.compile(r"(?:籍\s*贯|老\s*家|户\s*籍)\s*[:：]?\s*([\u4e00-\u9fa5]{2,12}?)(?:[，,。;；\n|｜/]|$)")
RE_POLITICAL = re.compile(r"(中共党员|预备党员|共青团员|民主党派|无党派人士|党员|团员|群众)")
RE_PERIOD = re.compile(
    r"(20\d{2}|19\d{2})\s*[./\-年]?\s*(\d{1,2})?\s*月?\s*"
    r"(?:[-–—~至到]+|一直[\u4e00-\u9fa5]{0,4}到)\s*"
    r"(至今|迄今|今|现在|当前|(?:20\d{2}|19\d{2})\s*[./\-年]?\s*(?:\d{1,2})?\s*月?)")
RE_GRAD_YEAR = re.compile(r"(19\d{2}|20\d{2})\s*[./\-年]?\s*(?:\d{1,2}\s*月?)?\s*(?:毕业|graduation)", re.I)
# 学位动词式专业表述：「在安徽财经大学念的会计学」
RE_MAJOR_VERB = re.compile(
    r"(?:大学|学院|学校)\s*(?:念|读|主修|修读?|学习|就读)的?\s*"
    r"([\u4e00-\u9fa5A-Za-z（）()]{2,20}?)(?=[，,。；;\n\s]|专业|拿|获|学位)")


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
    # 常见误抓黑名单：非姓名的两字行（民族/政治面貌/婚育等）
    BLACKLIST = {"党员", "群众", "团员", "汉族", "回族", "满族", "未婚", "已婚", "离异",
                 "男", "女", "简历", "求职", "应聘", "自荐", "本人", "人才", "应届生"}
    m = RE_NAME_LABEL.search(text)
    if m:
        return m.group(1)
    m = RE_NAME_SELF.search(text)
    if m and m.group(1) not in BLACKLIST:
        return m.group(1)
    # 首个"干净"的 2-4 字中文行（简历头部通常就是姓名）
    skip_words = ("简历", "求职", "应聘", "个人", "基本", "联系", "电话", "邮箱", "意向", "自我介绍", "关于")
    for raw in text.splitlines()[:12]:
        # ① 整行剔除标点后是纯 2-4 字中文
        line = re.sub(r"[#*■◆•\-\s|｜丨/、,，.。:：()（）]", "", raw)
        if line and not any(w in line for w in skip_words) and re.fullmatch(r"[\u4e00-\u9fa5·]{2,4}", line):
            if line not in BLACKLIST:
                return line
        # ② 分隔符行首 token：「张三丨Java工程师」「张伟 | 8年经验 | 本科」
        if any(w in raw for w in skip_words):
            continue
        tokens = [x for x in re.split(r"[|｜丨/·\t]+", raw.strip()) if x.strip()]
        if tokens:
            first = re.sub(r"[\s#*■◆•\-]", "", tokens[0])
            if re.fullmatch(r"[\u4e00-\u9fa5]{2,4}", first) and len(tokens) >= 2 and first not in BLACKLIST:
                return first
        # ③ 行首姓名 + 括号/逗号备注：「李四（男，28岁）」「刘婷婷，女，1996年生」
        m2 = re.match(r"\s*([\u4e00-\u9fa5]{2,4})\s*[（(，,]", raw)
        if m2 and m2.group(1) not in BLACKLIST and not any(w in m2.group(1) for w in skip_words):
            return m2.group(1)
    # 文件名兜底：如 "张伟-Java工程师-5年.pdf"
    if filename:
        stem = Path(filename).stem
        for token in re.split(r"[-_—\s|@#.]+", stem):
            token = token.strip()
            if re.fullmatch(r"[\u4e00-\u9fa5·]{2,4}", token) and token not in BLACKLIST and not any(
                    w in token for w in ("简历", "个人", "应聘")):
                return token
    return ""


def _guess_gender(text: str) -> str:
    m = RE_GENDER.search(text)
    if m:
        return m.group(1)
    head = text[:300]
    m = re.search(r"[|｜丨/、,，\s]?\s*(男|女)\s*[|｜/、,，]\s*\d{2}\s*岁|[\u4e00-\u9fa5]{2,4}[，,\s（(]+(男|女)[，,\s）)|｜/、]", head)
    if m:
        return m.group(1) or m.group(2)
    return ""


def _guess_age(text: str) -> int | None:
    m = RE_AGE.search(text)
    if m:
        return int(m.group(1) or m.group(2))
    m = RE_BIRTH.search(text)
    if m:
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
    if best:
        return EDU_NORMALIZE.get(best, best)
    # 学位表述归一：工学学士→本科、理学硕士/MBA→硕士
    if re.search(r"(?:工学|理学|文学|医学|管理学|经济学|法学)?学士| bachelor", text, re.I):
        return "本科"
    if re.search(r"(?:工学|理学|文学|医学|管理学|经济学|法学)?硕士学位|(?<![a-z])m(?:ba|sc|eng)(?![a-z])", text, re.I):
        return "硕士"
    return ""


def _guess_city(text: str) -> str:
    def _std(c: str) -> str:
        return c if c in ("北京", "上海", "天津", "重庆") else c + "市"
    m = RE_CITY.search(text[:800])
    if m:
        raw = m.group(1).strip()
        for c in CITIES:  # 归一到已知城市（"苏州工业园区"→"苏州市"）
            if raw.startswith(c) or c in raw:
                return _std(c)
        return raw if raw.endswith("市") else raw
    # 兜底：逐行扫城市名，跳过籍贯/出生行（"老家安徽合肥"不是现居城市）
    for line in text.splitlines()[:15]:
        if re.search(r"籍贯|老家|户籍|出生", line):
            continue
        for c in CITIES:
            if re.search(rf"{c}(?:市)?(?:[，,、|｜/\s]|$)", line):
                return _std(c)
    return ""


def _strip_prefix_noise(s: str) -> str:
    """剥离机构名前被正则连带捕获的前缀噪音词（"年在安徽财经大学"→"安徽财经大学"，
    "先在合肥安泰会计师事务所"→"合肥安泰会计师事务所"，"跳到上海立信…"→"上海立信…"）。"""
    return re.sub(
        r"^(?:年|月|在|于|到|从|自|先|曾|后来|之前|此后|其后|毕业后|入职|加入|就职于?|时间|日期|至今|迄今|今"
        r"|跳到|跳槽至?|转去|转到|去了|进入|后到|随后|之后)+", "", s.strip())


def _strip_title_noise(s: str) -> str:
    """剥离职位前噪音（"做税务顾问"→"税务顾问"，"是财务经理"→"财务经理"）。"""
    return re.sub(r"^(?:是|做|担任|出任|任职|任|当)+[:：]?\s*", "", s.strip())


def _guess_work_years(text: str, work_history: list[dict]) -> float | None:
    best = None
    for m in RE_WORKYEARS.finditer(text[:2500]):
        v = m.group(1) or m.group(2)
        if v:
            f = float(v)
            if 0.5 <= f <= 50 and (best is None or f > best):
                best = f
    # 中文数字年限（"做了两年审计经验"）
    for m in RE_WORKYEARS_CN.finditer(text[:2500]):
        cn = m.group(1)
        f = float(CN_NUM.get(cn[0], 0)) if len(cn) == 1 else float(
            (CN_NUM.get(cn[0], 0) * 10 + CN_NUM.get(cn[-1], 0)) if cn[0] == "十" or len(cn) > 1 else 0)
        if cn == "十":
            f = 10.0
        elif len(cn) == 2 and cn[0] == "十":
            f = float(10 + CN_NUM.get(cn[1], 0))
        elif len(cn) == 2 and cn[1] == "十":
            f = float(CN_NUM.get(cn[0], 0) * 10)
        if f and 0.5 <= f <= 50 and (best is None or f > best):
            best = f
    if best is not None:
        return best
    # 无显式表述 → 由工作经历最早开始年份推算
    starts = []
    for seg in work_history:
        m = re.match(r"(19|20)\d{2}", seg.get("period", ""))
        if m:
            starts.append(int(seg["period"][:4]))
    if starts:
        inferred = date.today().year - min(starts)
        if 0 <= inferred <= 45:
            return float(max(inferred, 0.5)) if inferred > 0 else None
    return None


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


def _guess_certificates(text: str) -> str:
    low = text.lower()
    sec = _section(text, ["证书", "荣誉证书", "资格证书", "职业证书", "语言能力/证书"])
    scope = (sec + "\n" + text).lower()  # 小节优先，但全文兜底扫
    found = []
    for name, pattern in CERT_DICT:
        if re.search(pattern, scope if not sec else sec.lower()) or (not sec and re.search(pattern, low)):
            if name not in found:
                found.append(name)
    return "、".join(found[:10])


def _guess_languages(text: str) -> str:
    found = []
    for lang, trigger, level_re in LANG_DICT:
        if not re.search(trigger, text, re.I):
            continue
        level = ""
        if level_re:
            m = re.search(level_re, text, re.I)
            if m:
                level = re.sub(r"\s+", "", m.group(1) if m.groups() else m.group(0))
                if level.lower() == lang.lower():
                    level = ""
        entry = f"{lang}（{level}）" if level else lang
        if entry not in found:
            found.append(entry)
    return "、".join(found[:6])


def _guess_company_title(text: str) -> tuple[str, str]:
    """从工作经历小节取最近一段公司与职位（小节内第一次出现≈最近）。"""
    sec = _section(text, ["工作经历", "工作经验", "职业经历", "工作经历/项目经历"]) or text
    company = ""
    m = RE_COMPANY.search(sec)
    if m:
        company = _strip_prefix_noise(m.group(1))
    title = ""
    m = re.search(r"(?:担任|任职|职位|岗位)\s*[:：]?\s*([^\n，,;；。]{2,25})", sec)
    if m:
        title = _strip_title_noise(re.split(r"\s{2,}", m.group(1).strip())[0][:25])
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
                    title = _strip_title_noise((m2.group(1) or m2.group(2) or "").strip())
                    break
            if title:
                break
        if not title:
            m2 = RE_TITLE_WORD.search(sec[:1500])
            if m2:
                title = _strip_title_noise((m2.group(1) or m2.group(2) or "").strip())
    return company, title


def _guess_work_history(text: str) -> list[dict]:
    """全文扫描「时间区间 → 公司 → 职位」，输出多段工作经历（最多 6 段，按出现顺序）。"""
    segs: list[dict] = []
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        pm = RE_PERIOD.search(line)
        if not pm:
            continue
        # 排除教育段落行（"2012.09—2016.06 南京理工大学 …"不是工作经历）
        if re.search(r"大学|学院|学校|职业技术学校|主修|专业[:：]|学历", line) and not RE_COMPANY.search(line):
            continue
        period = re.sub(r"\s+", "", pm.group(0))
        # 公司：同行优先，其次下两行
        company = ""
        cm = RE_COMPANY.search(line[pm.end():]) or RE_COMPANY.search(line)
        if cm:
            company = _strip_prefix_noise(cm.group(1))
        else:
            for j in range(idx + 1, min(idx + 3, len(lines))):
                cm = RE_COMPANY.search(lines[j])
                if cm:
                    company = _strip_prefix_noise(cm.group(1))
                    break
        # 职位：同行公司之后的文本 → 下两行的职位词 → 「担任」标签
        title = ""
        rest = line[pm.end():]
        if company and company in rest:
            after = rest.split(company, 1)[1]
            tm = RE_TITLE_WORD.search(after)
            if tm:
                title = _strip_title_noise((tm.group(1) or tm.group(2) or "").strip())
        if not title:
            for j in range(idx, min(idx + 3, len(lines))):
                tm = RE_TITLE_WORD.search(lines[j])
                if tm:
                    cand = _strip_title_noise((tm.group(1) or tm.group(2) or "").strip())
                    if cand and not re.fullmatch(r"[\d./\-年至今月\s]+", cand):
                        title = cand
                        break
        if company or title:
            segs.append({"period": period, "company": company, "title": title[:30]})
        if len(segs) >= 6:
            break
    # 去重（同公司同区间）
    seen, out = set(), []
    for s in segs:
        key = (s["period"], s["company"])
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out


def _guess_graduate_year(text: str) -> int | None:
    m = RE_GRAD_YEAR.search(text)
    if m:
        y = int(m.group(1))
        if 1970 <= y <= date.today().year + 2:
            return y
    sec = _section(text, ["教育经历", "教育背景", "教育"])
    if sec:
        # 精准锚定：只从含"大学/学院/学校"的行提取年份（避免非标小节把工作经历年份混入）
        years: list[int] = []
        for line in sec.splitlines():
            if re.search(r"大学|学院|学校|职业技术学校", line):
                years += [int(y) for y in re.findall(r"(?:19|20)\d{2}", line)]
        if not years:
            # 无学校行 → 退而取小节首个非空行
            for line in sec.splitlines():
                if line.strip():
                    years = [int(y) for y in re.findall(r"(?:19|20)\d{2}", line)]
                    break
        years = [y for y in years if 1970 <= y <= date.today().year + 2]
        if years:
            return max(years)
    # 全文兜底：小节标题抓不到时（"读书情况"等非标标题），直接找含学校名的行
    for line in text.splitlines():
        if re.search(r"大学|学院|职业技术学校", line):
            ys = [int(y) for y in re.findall(r"(?:19|20)\d{2}", line)]
            ys = [y for y in ys if 1970 <= y <= date.today().year + 2]
            if ys:
                return max(ys)
    return None


def _guess_summary(text: str) -> str:
    sec = _section(text, ["自我评价", "个人优势", "个人总结", "自我介绍", "本人评价"])
    if sec:
        sec = re.sub(r"\s+", " ", sec)
        return sec[:200]
    return ""


def _guess_wechat(text: str) -> str:
    m = RE_WECHAT.search(text)
    if not m:
        return ""
    if m.group(1):
        return m.group(1)
    return "同手机号"  # "微信同号"场景


def parse_resume(text: str, filename: str = "") -> dict:
    """主入口：文本 → 结构化字段 dict + confidence(0~1)。"""
    t = _norm(text)
    out: dict = {"raw_text": text.strip()}

    m = RE_PHONE.search(t)
    if m:
        out["phone"] = "".join(m.groups())
    else:
        m = RE_TEL.search(t)  # 固话兜底（非标准简历常见只留座机）
        out["phone"] = f"{m.group(1)}-{m.group(2)}" if m else ""
    m = RE_EMAIL.search(t)
    if m:
        out["email"] = m.group(0).lower()
    else:
        # 非标写法归一：「tingting.liu#163.com(#换成@)」「xxx（at）qq.com」
        m2 = re.search(r"[A-Za-z0-9._%+\-]+(?:#|（at）|\(at\)|\[at\])[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+", t)
        if m2:
            fixed = re.sub(r"#|（at）|\(at\)|\[at\]", "@", m2.group(0), count=1)
            out["email"] = fixed.lower()
        else:
            out["email"] = ""
    out["name"] = _guess_name(t, filename)
    out["gender"] = _guess_gender(t)
    out["age"] = _guess_age(t)
    out["education"] = _guess_education(t)
    m = RE_SCHOOL.search(t)
    out["school"] = _strip_prefix_noise(m.group(1)) if m else ""
    m = RE_MAJOR_LABEL.search(t) or RE_MAJOR_DEGREE_FIRST.search(t) or RE_MAJOR_LINE.search(t) or RE_MAJOR_VERB.search(t)
    out["major"] = m.group(1).strip() if m else ""
    out["city"] = _guess_city(t)
    work_history = _guess_work_history(t)
    out["work_years"] = _guess_work_years(t, work_history)
    company, title = _guess_company_title(t)
    # current = 最近一段：优先工作经历里「至今」的段落（非标简历小节名抓不准时的可靠锚点）
    cur_seg = next((s for s in work_history
                    if re.search(r"至今|迄今|现在|今$", s.get("period", "")) and s.get("company")), None)
    anchor_now = bool(cur_seg)
    if cur_seg:
        company = cur_seg["company"]
        title = cur_seg.get("title") or title
    else:
        # work_history 也没抓到 → 找含"至今/现在/目前"的行里的公司（当前雇主强信号）
        for line in t.splitlines():
            if re.search(r"至今|迄今|现在|目前", line):
                cm = RE_COMPANY.search(line)
                if cm:
                    company = _strip_prefix_noise(cm.group(1))
                    anchor_now = True
                    break
    # 内部标记（_ 开头，不入库）：告知 LLM 融合层"当前公司来自强锚点，勿覆盖"
    out["_anchor_now"] = anchor_now
    out["current_company"] = company
    out["current_title"] = title
    m = RE_EXPECT_TITLE.search(t)
    if not m:
        # 非标表述兜底：「想找一份前端架构师的工作」
        m = re.search(r"想找(?:一份|个)?\s*([\u4e00-\u9fa5A-Za-z]{2,20}?)\s*(?:的?工作|的?岗位)", t)
    if m:
        # 截断粘连的后续标签（如"机械设计工程师    期望薪资：…"）
        et = re.split(r"\s{2,}|期望薪资|薪资要求|工作地点|工作城市|月薪", m.group(1))[0].strip()
        out["expected_title"] = et[:30]
    else:
        out["expected_title"] = ""
    m = RE_EXPECT_SALARY.search(t)
    if m:
        sal = m.group(1) or m.group(2) or ""
        out["expected_salary"] = re.sub(r"\s+", "", sal)
    else:
        out["expected_salary"] = ""
    out["skills"] = _guess_skills(t)
    out["certificates"] = _guess_certificates(t)
    out["languages"] = _guess_languages(t)
    out["summary"] = _guess_summary(t)
    out["wechat"] = _guess_wechat(t)
    m = RE_HOMETOWN.search(t)
    out["hometown"] = m.group(1).strip() if m else ""
    m = RE_POLITICAL.search(t)
    out["political_status"] = m.group(1) if m else ""
    out["graduate_year"] = _guess_graduate_year(t)
    out["work_history"] = json.dumps(work_history, ensure_ascii=False) if work_history else ""
    proj = _section(t, ["项目经验", "项目经历", "主要项目", "项目描述"])
    out["project_experience"] = re.sub(r"\n{2,}", "\n", proj)[:500] if proj else ""

    # 置信度：关键字段加权（总和 = 1.0）
    weights = [
        (bool(out["name"]), .18), (bool(out["phone"]), .13), (bool(out["email"]), .08),
        (bool(out["education"]), .08), (out["work_years"] is not None, .06),
        (bool(out["skills"]), .08), (bool(out["current_company"]), .07),
        (bool(out["current_title"]), .04), (bool(out["city"]), .03),
        (bool(out["expected_title"] or out["summary"]), .04),
        (bool(out["school"]), .04), (bool(out["work_history"]), .05),
        (bool(out["certificates"] or out["languages"]), .03), (bool(out["major"]), .04),
        (bool(out["gender"]) or out["age"] is not None, .03),
    ]
    out["parse_confidence"] = round(sum(w for ok, w in weights if ok), 3)
    return out
