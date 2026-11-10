#!/usr/bin/env python3
"""生成演示/测试样本：文字版 PDF、扫描版 PDF（验证 OCR）、Word 简历、外部名单 xlsx。

开发机（Linux）运行一次即可，样本随项目分发，Windows 用户直接拿来试导入。
用法: python scripts/make_samples.py
"""
from __future__ import annotations


from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"
SAMPLES.mkdir(exist_ok=True)

# 字体策略（实测坑，勿改回）：
#  - fpdf2 生成带文字层的 PDF 只能用 TrueType(glyf) 字体；Noto Sans CJK 是 CFF(OTTO)，
#    fpdf2 嵌出来文字层为空 —— 文字版 PDF 用 arphic uming / WPS TrueType 字体。
#  - PIL 渲染扫描图对 OTF/CFF 无碍，用 Noto Sans CJK Bold（更美观，OCR 已验证识别率）。
#  - DroidSansFallbackFull.ttf 实测缺 ASCII 数字/英文字形，两类都不可用。
TTF_CANDIDATES = [   # 供 fpdf2（必须 TrueType + ASCII + CJK）
    "/usr/share/fonts/truetype/arphic/uming.ttc",
    "/usr/share/fonts/wps-office/HYZYB5.ttf",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
]
RENDER_CANDIDATES = [  # 供 PIL 渲染扫描图（OTF 也可）
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    *TTF_CANDIDATES,
]


def _faces(path: str):
    from fontTools.ttLib import TTCollection, TTFont
    if path.lower().endswith(".ttc"):
        return list(enumerate(TTCollection(path, lazy=True).fonts))
    return [(0, TTFont(path, lazy=True))]


def _covers(f) -> bool:
    cmap = f.getBestCmap()
    return bool(cmap) and ord("8") in cmap and 0x4E2D in cmap


def _is_truetype(f) -> bool:
    return f.sfntVersion in ("\x00\x01\x00\x00", "true", "ttcf")


def find_ttf_font() -> tuple[str, int]:
    """给 fpdf2 找 TrueType 字体，返回 (路径, face 序号)。"""
    for path in TTF_CANDIDATES:
        if not Path(path).exists():
            continue
        for i, f in _faces(path):
            if _covers(f) and _is_truetype(f):
                return path, i
    raise SystemExit("找不到 TrueType 中文字体（fpdf2 用）；请修改 TTF_CANDIDATES")


def find_render_font() -> tuple[str, int]:
    """给 PIL 找渲染字体，返回 (路径, face 序号)。"""
    for path in RENDER_CANDIDATES:
        if not Path(path).exists():
            continue
        for i, f in _faces(path):
            if _covers(f):
                return path, i
    raise SystemExit("找不到渲染用中文字体；请修改 RENDER_CANDIDATES")


def ensure_ttf() -> Path:
    """fpdf2 需要单字体 ttf；从 ttc 抽出对应字面存到 /tmp。"""
    path, num = find_ttf_font()
    if not path.lower().endswith(".ttc"):
        return Path(path)
    out = Path("/tmp") / f"tp_sample_font_{Path(path).stem}_{num}.ttf"
    if not out.exists():
        from fontTools.ttLib import TTFont
        TTFont(path, fontNumber=num).save(str(out))
    return out




RESUME_ZHANG = """张伟
男 | 32岁 | 8年工作经验 | 现居杭州市
电话：13812345678
邮箱：zhangwei.dev@example.com

求职意向
期望职位：高级Java开发工程师
期望薪资：25-35K

教育经历
2012.09 - 2016.06   浙江工业大学   计算机科学与技术   本科

专业技能
精通 Java、Spring Boot、SpringCloud 微服务架构
熟练使用 MySQL、Redis、Kafka、Elasticsearch
熟悉 Docker、Kubernetes、Jenkins 持续集成部署

工作经历
2020.03 - 至今   杭州云启科技有限公司   担任 高级Java开发工程师
负责交易中台核心服务重构，接口 QPS 提升 3 倍
2016.07 - 2020.02   宁波智联信息技术有限公司   Java开发工程师
参与电商订单系统开发与维护

自我评价
8年后端开发经验，主导过高并发分布式系统架构设计，带过 5 人小组。
"""

RESUME_LI = """李娜
女 | 36岁 | 电话：13957654321
邮箱：lina.qa@example.com
现居宁波市

求职意向
期望职位：质量经理

教育经历
2008.09 - 2015.06  宁波大学  机械工程  硕士

专业技能
六西格玛黑带、SPC、FMEA、MSA、ISO9001、IATF16949 内审员

工作经历
2015.07 - 至今  宁波浩华制造集团  担任 质量主管
负责注塑与装配车间质量管理，客诉率下降 42%

自我评价
10年制造业质量管理经验，擅长体系搭建与团队培养。
"""

RESUME_WANG = {
    "name": "王强", "gender": "男", "age": "29岁", "phone": "13734567890",
    "email": "wangqiang.me@example.com", "city": "苏州市",
    "title": "机械设计工程师", "years": "6年",
}


def make_text_pdf():
    from fpdf import FPDF
    pdf = FPDF(format="A4")
    pdf.add_page()
    pdf.add_font("cjk", "", str(ensure_ttf()))
    pdf.set_font("cjk", size=11)
    for line in RESUME_ZHANG.splitlines():
        pdf.cell(w=0, h=7, text=line or " ", new_x="LMARGIN", new_y="NEXT")
    out = SAMPLES / "张伟-Java开发工程师-8年.pdf"
    pdf.output(str(out))
    print("生成", out)


def make_scanned_pdf():
    """文字渲染成图片再装进 PDF —— 无文字层，导入时必然触发 OCR。"""
    from PIL import Image, ImageDraw, ImageFont
    font_path, font_num = find_render_font()
    img = Image.new("RGB", (1240, 1754), "white")  # A4 @150dpi
    d = ImageDraw.Draw(img)
    f_big = ImageFont.truetype(font_path, 46, index=font_num)
    f = ImageFont.truetype(font_path, 28, index=font_num)
    lines = RESUME_LI.splitlines()
    y = 90
    for i, line in enumerate(lines):
        if not line.strip():
            y += 18
            continue
        d.text((110, y), line, font=f_big if i == 0 else f, fill=(20, 20, 20))
        y += 66 if i == 0 else 46
    png = Path("/tmp/tp_scan_li.png")
    img.save(png, dpi=(150, 150))
    from fpdf import FPDF
    pdf = FPDF(format="A4")
    pdf.add_page()
    pdf.image(str(png), x=0, y=0, w=210)
    out = SAMPLES / "李娜-质量经理-扫描版.pdf"
    pdf.output(str(out))
    png.unlink(missing_ok=True)
    print("生成", out)


def make_docx():
    from docx import Document
    doc = Document()
    r = RESUME_WANG
    doc.add_heading(r["name"], level=0)
    doc.add_paragraph(f'{r["gender"]} | {r["age"]} | {r["years"]}工作经验 | 现居{r["city"]}')
    doc.add_paragraph(f'电话：{r["phone"]}    邮箱：{r["email"]}')
    doc.add_heading("求职意向", level=1)
    doc.add_paragraph(f'期望职位：{r["title"]}    期望薪资：15-20K')
    doc.add_heading("教育经历", level=1)
    table = doc.add_table(rows=2, cols=4)
    table.style = "Table Grid"
    hdr = table.rows[0].cells
    for i, t in enumerate(["时间", "学校", "专业", "学历"]):
        hdr[i].text = t
    row = table.rows[1].cells
    for i, t in enumerate(["2015.09-2019.06", "合肥工业大学", "机械设计制造及其自动化", "本科"]):
        row[i].text = t
    doc.add_heading("专业技能", level=1)
    doc.add_paragraph("熟练使用 SolidWorks、AutoCAD、UG 进行非标设备设计")
    doc.add_paragraph("熟悉气动/电动元器件选型、公差分析、DFM")
    doc.add_paragraph("了解电气原理图，能配合电控完成整机调试")
    doc.add_heading("工作经历", level=1)
    doc.add_paragraph("2019.07 - 至今   苏州精工装备有限公司   担任 机械设计师")
    doc.add_paragraph("主导 3 条自动化装配线机械设计，均已交付验收")
    doc.add_heading("自我评价", level=1)
    doc.add_paragraph("6年非标自动化设计经验，动手能力强，可接受短期出差调试。")
    out = SAMPLES / "王强-机械设计工程师.docx"
    doc.save(str(out))
    print("生成", out)


def make_xlsx():
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "外部名单"
    # 故意用别名表头（手机号码/电子邮箱/最高学历/公司/岗位），验证自动映射
    ws.append(["姓名", "手机号码", "电子邮箱", "最高学历", "工作年限", "公司", "岗位", "技能", "状态", "备注"])
    ws.append(["张伟", "13812345678", "zhangwei.dev@example.com", "本科", 8,
               "杭州云启科技有限公司", "高级Java开发工程师", "Java、Spring Boot、Redis",
               "跟进中", "3月招聘会认识，意向明确"])          # ← 与库里张伟电话相同 → 触发合并
    ws.append(["陈晓东", "13698765432", "chenxd@example.com", "大专", 4,
               "宁波某某电器有限公司", "设备技术员", "PLC、设备维护、电工证", "潜在", ""])
    ws.append(["赵敏", "13566667777", "zhaomin@example.com", "硕士", 3,
               "", "算法工程师", "Python、机器学习、PyTorch、NLP", "潜在", "猎头推荐，薪资期望偏高"])
    out = SAMPLES / "外部人才名单.xlsx"
    wb.save(str(out))
    print("生成", out)


if __name__ == "__main__":
    make_text_pdf()
    make_scanned_pdf()
    make_docx()
    make_xlsx()
    print("全部样本生成完毕 →", SAMPLES)
