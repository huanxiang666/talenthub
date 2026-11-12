"""统一文本提取入口：按扩展名分发，返回 ExtractResult(text, method, ocr_used, pages, warnings)。

支持：.pdf（文字版直读 / 扫描版自动 OCR）、.docx（段落+表格，保持文档顺序）、
.txt/.md、.png/.jpg/.jpeg/.bmp/.webp（直接 OCR）。
LIMIT: 老式二进制 .doc 无法纯 Python 解析，返回明确错误提示转存 .docx/.pdf 后导入；
       目标机器装了 LibreOffice 后可在此处补 soffice --convert-to 自动转换。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .. import config

TEXT_EXTS = {".txt", ".md"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
PDF_TEXT_THRESHOLD = 20  # 整份 PDF 提取字符数低于该值判定为扫描版 → OCR


@dataclass
class ExtractResult:
    text: str
    method: str = ""            # pdf_text | pdf_ocr | docx | image_ocr | text | manual
    ocr_used: bool = False
    pages: int = 0
    warnings: list[str] = field(default_factory=list)


def extract_file(path: str | Path) -> ExtractResult:
    p = Path(path)
    ext = p.suffix.lower()
    if ext == ".pdf":
        return _extract_pdf(p)
    if ext == ".docx":
        return _extract_docx(p)
    if ext == ".doc":
        raise ValueError("暂不支持老式 .doc 二进制格式，请在 Word/WPS 中另存为 .docx 或 .pdf 后导入")
    if ext in IMAGE_EXTS:
        return _extract_image(p)
    if ext in TEXT_EXTS:
        text = p.read_text(encoding="utf-8", errors="replace")
        return ExtractResult(text=text.strip(), method="text", pages=1)
    raise ValueError(f"不支持的文件类型: {ext or p.name}（支持 pdf/docx/txt/md/png/jpg）")


def _extract_pdf(p: Path) -> ExtractResult:
    import pdfplumber

    warnings: list[str] = []
    texts: list[str] = []
    with pdfplumber.open(str(p)) as pdf:
        n_pages = len(pdf.pages)
        for page in pdf.pages:
            texts.append(page.extract_text() or "")
    full = "\n".join(t for t in texts if t).strip()
    cfg = config.get()

    if len(full) >= PDF_TEXT_THRESHOLD or not cfg.get("ocr_enabled", True):
        if len(full) < PDF_TEXT_THRESHOLD:
            warnings.append("文字层几乎为空且 OCR 已在设置中关闭，建议开启 OCR 后重试")
        return ExtractResult(text=full, method="pdf_text", pages=n_pages, warnings=warnings)

    # 扫描版：渲染成图片走内置 OCR
    max_pages = int(cfg.get("ocr_max_pages", 30))
    if n_pages > max_pages:
        warnings.append(f"共 {n_pages} 页，超过 OCR 上限 {max_pages} 页，仅识别前 {max_pages} 页")
    import pypdfium2 as pdfium
    from . import ocr_engine

    doc = pdfium.PdfDocument(str(p))
    ocr_lines: list[str] = []
    low_conf = 0
    try:
        for i in range(min(n_pages, max_pages)):
            page = doc[i]
            bitmap = page.render(scale=200 / 72)  # 200 DPI，识别精度/速度平衡点
            pil = bitmap.to_pil()
            for ln in ocr_engine.ocr_image(pil):
                ocr_lines.append(ln["text"])
                if ln["score"] < 0.7:
                    low_conf += 1
    finally:
        doc.close()
    if low_conf:
        warnings.append(f"有 {low_conf} 行识别置信度低于 0.7，建议人工核对关键字段")
    text = "\n".join(ocr_lines).strip()
    if not text:
        warnings.append("OCR 未识别到任何文字，文件可能是空白页或加密扫描件")
    return ExtractResult(text=text, method="pdf_ocr", ocr_used=True,
                         pages=n_pages, warnings=warnings)


def extract_file_pages(p: str | Path) -> list[ExtractResult]:
    """逐页提取 PDF（多份简历扫描在一个文件里的拆分录入场景）。

    每页独立成一份 ExtractResult：该页文字层字符足够则直读，否则单页渲染 OCR。
    非 PDF 文件返回单元素列表（等价 extract_file）。
    """
    p = Path(p)
    if p.suffix.lower() != ".pdf":
        return [extract_file(p)]
    import pdfplumber

    cfg = config.get()
    ocr_on = cfg.get("ocr_enabled", True)
    max_pages = int(cfg.get("ocr_max_pages", 30))
    page_texts: list[str] = []
    with pdfplumber.open(str(p)) as pdf:
        n_pages = len(pdf.pages)
        for page in pdf.pages:
            page_texts.append(page.extract_text() or "")
    if n_pages > max_pages:
        n_pages = max_pages   # 与整份提取一致的上限保护

    results: list[ExtractResult] = []
    need_ocr_idx = [i for i in range(n_pages) if len(page_texts[i].strip()) < PDF_TEXT_THRESHOLD]
    ocr_texts: dict[int, str] = {}
    if need_ocr_idx and ocr_on:
        import pypdfium2 as pdfium
        from . import ocr_engine
        doc = pdfium.PdfDocument(str(p))
        try:
            for i in need_ocr_idx:
                bitmap = doc[i].render(scale=200 / 72)
                lines = [ln["text"] for ln in ocr_engine.ocr_image(bitmap.to_pil())]
                ocr_texts[i] = "\n".join(lines).strip()
        finally:
            doc.close()
    for i in range(n_pages):
        text = page_texts[i].strip()
        method = "pdf_text"
        ocr_used = False
        warns: list[str] = []
        if len(text) < PDF_TEXT_THRESHOLD:
            if ocr_on:
                text = ocr_texts.get(i, "")
                method, ocr_used = "pdf_ocr", True
                if not text:
                    warns.append(f"第 {i + 1} 页 OCR 未识别到文字（可能是空白页/分隔页）")
            else:
                warns.append(f"第 {i + 1} 页文字层为空且 OCR 已关闭")
        results.append(ExtractResult(text=text, method=method, ocr_used=ocr_used,
                                     pages=1, warnings=warns))
    return results


def _extract_docx(p: Path) -> ExtractResult:
    """按文档真实顺序遍历段落与表格（简历常用表格排版，只读 paragraphs 会丢内容）。"""
    from docx import Document
    from docx.document import Document as _Doc
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = Document(str(p))
    parts: list[str] = []

    def iter_blocks(parent: _Doc):
        body = parent.element.body
        for child in body.iterchildren():
            if child.tag == qn("w:p"):
                yield Paragraph(child, parent)
            elif child.tag == qn("w:tbl"):
                yield Table(child, parent)

    for block in iter_blocks(doc):
        if isinstance(block, Paragraph):
            t = block.text.strip()
            if t:
                parts.append(t)
        else:  # Table：单元格按行拼接，制表符分隔
            for row in block.rows:
                cells = [c.text.strip().replace("\n", " ") for c in row.cells]
                line = "\t".join(c for c in cells if c)
                if line:
                    parts.append(line)
    text = "\n".join(parts).strip()
    if not text:
        # 文本为空的 docx 可能整页是图片 → 抽内嵌图片 OCR
        return _extract_docx_embedded_images(p, doc)
    return ExtractResult(text=text, method="docx", pages=1)


def _extract_docx_embedded_images(p: Path, doc) -> ExtractResult:
    from . import ocr_engine
    lines: list[str] = []
    for rel in doc.part.rels.values():
        if "image" in rel.reltype:
            try:
                blob = rel.target_part.blob
            except Exception:
                continue
            try:
                for ln in ocr_engine.ocr_image(blob):
                    lines.append(ln["text"])
            except Exception as e:
                raise RuntimeError(f"docx 内嵌图片 OCR 失败: {e}") from e
    text = "\n".join(lines).strip()
    return ExtractResult(text=text, method="image_ocr", ocr_used=True, pages=1,
                         warnings=[] if text else ["docx 中无文字也未从内嵌图片识别到内容"])


def _extract_image(p: Path) -> ExtractResult:
    from . import ocr_engine
    lines = ocr_engine.ocr_image(str(p))
    text = "\n".join(ln["text"] for ln in lines).strip()
    low = sum(1 for ln in lines if ln["score"] < 0.7)
    warnings = [f"有 {low} 行识别置信度低于 0.7，建议人工核对"] if low else []
    return ExtractResult(text=text, method="image_ocr", ocr_used=True, pages=1,
                         warnings=warnings)
