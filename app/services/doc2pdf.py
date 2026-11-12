"""docx / txt / md → PDF 转换（简历在线预览用，纯 Python 无 LibreOffice 依赖）。

- python-docx 提取段落+表格（保持文档顺序）→ fpdf2 排版为 PDF
- 中文字体自动探测（Windows 雅黑/宋体 → Linux uming/wqy → macOS PingFang）
- 转换结果缓存为 <原文件>.preview.pdf，源文件更新后自动重转
- 任何失败抛 ValueError（调用方回退"下载原件"提示，绝不让预览崩服务）
"""
from __future__ import annotations

from pathlib import Path

_FONT_CANDIDATES = [
    # Windows
    "C:/Windows/Fonts/msyh.ttc",      # 微软雅黑
    "C:/Windows/Fonts/simsun.ttc",    # 宋体
    "C:/Windows/Fonts/simhei.ttf",    # 黑体
    # Linux
    "/usr/share/fonts/truetype/arphic/uming.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    # macOS
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
]
_font_cache: list[str] = []


def _find_cjk_font() -> str:
    if not _font_cache:
        _font_cache.append(next((f for f in _FONT_CANDIDATES if Path(f).exists()), ""))
    return _font_cache[0]


def preview_pdf_path(src: str | Path) -> Path:
    """转换产物的缓存路径（与源文件同目录）。"""
    src = Path(src)
    return src.with_suffix(src.suffix + ".preview.pdf")


def needs_convert(src: str | Path) -> bool:
    src = Path(src)
    out = preview_pdf_path(src)
    return (not out.exists()) or out.stat().st_mtime < src.stat().st_mtime


def convert_to_pdf(src: str | Path) -> Path:
    """转换并返回 PDF 路径；已缓存且新鲜则直接返回。失败抛 ValueError。"""
    src = Path(src)
    out = preview_pdf_path(src)
    if out.exists() and out.stat().st_mtime >= src.stat().st_mtime:
        return out
    ext = src.suffix.lower()
    if ext == ".docx":
        lines = _docx_lines(src)
    elif ext in (".txt", ".md"):
        lines = _text_lines(src)
    else:
        raise ValueError(f"格式 {ext} 不支持转 PDF 预览")
    if not any(ln.strip() for ln in lines):
        raise ValueError("文档没有可转换的文字内容（可能是纯图片文档）")
    _render_pdf(lines, out)
    return out


def _docx_lines(src: Path) -> list[str]:
    from docx import Document
    from docx.document import Document as _Doc
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = Document(str(src))

    def iter_blocks(parent):
        body = parent.element.body
        for child in body.iterchildren():
            if child.tag == qn("w:p"):
                yield Paragraph(child, parent)
            elif child.tag == qn("w:tbl"):
                yield Table(child, parent)

    lines: list[str] = []
    for block in iter_blocks(doc):
        if isinstance(block, Paragraph):
            lines.append(block.text)
        else:
            for row in block.rows:
                cells = [c.text.replace("\n", " ").strip() for c in row.cells]
                # 合并单元格去重（docx 合并格会重复出现）
                dedup: list[str] = []
                for c in cells:
                    if not dedup or dedup[-1] != c:
                        dedup.append(c)
                lines.append("  |  ".join(dedup))
    return lines


def _text_lines(src: Path) -> list[str]:
    raw = src.read_bytes()
    for enc in ("utf-8", "gb18030", "big5", "utf-16"):
        try:
            return raw.decode(enc).splitlines()
        except (UnicodeDecodeError, UnicodeError):
            continue
    return raw.decode("utf-8", "replace").splitlines()


def _render_pdf(lines: list[str], out: Path) -> None:
    from fpdf import FPDF
    from fpdf.enums import XPos, YPos

    font_path = _find_cjk_font()
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    if font_path:
        try:
            pdf.add_font("cjk", "", font_path)
            pdf.set_font("cjk", size=10.5)
        except Exception:
            pdf.set_font("Helvetica", size=10.5)   # 无中文时至少保证不崩
    else:
        pdf.set_font("Helvetica", size=10.5)
    for ln in lines:
        txt = ln.rstrip()
        if not txt.strip():
            pdf.ln(4)
            continue
        # new_x=LMARGIN：每行结束回到左边距（fpdf2 默认停在 cell 右侧，w=0 时下一行宽度为 0 会崩）
        pdf.multi_cell(0, 6, txt, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    tmp = out.with_suffix(".pdf.tmp")
    pdf.output(str(tmp))
    tmp.replace(out)
