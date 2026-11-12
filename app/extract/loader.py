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


def extract_file(path: str | Path, progress_cb=None) -> ExtractResult:
    """progress_cb: 可选回调 fn(step_text)，导入进度实时显示当前操作。"""
    p = Path(path)
    ext = p.suffix.lower()
    if ext == ".pdf":
        return _extract_pdf(p, progress_cb)
    if ext == ".docx":
        return _extract_docx(p, progress_cb)
    if ext == ".doc":
        raise ValueError("暂不支持老式 .doc 二进制格式，请在 Word/WPS 中另存为 .docx 或 .pdf 后导入")
    if ext in IMAGE_EXTS:
        return _extract_image(p, progress_cb)
    if ext in TEXT_EXTS:
        text = p.read_text(encoding="utf-8", errors="replace")
        return ExtractResult(text=text.strip(), method="text", pages=1)
    raise ValueError(f"不支持的文件类型: {ext or p.name}（支持 pdf/docx/txt/md/png/jpg）")


def _extract_pdf(p: Path, progress_cb=None) -> ExtractResult:
    """PDF 提取（v1.5 页级混合）：每页独立判断——文字层字符足够直读（快、零 OCR 成本），
    缺字的页（扫描页）才渲染 OCR。文字版 PDF 全程不碰 OCR；混合版各页各走各的。"""
    import pdfplumber

    cfg = config.get()
    ocr_on = cfg.get("ocr_enabled", True)
    max_pages = int(cfg.get("ocr_max_pages", 30))
    warnings: list[str] = []
    if progress_cb:
        progress_cb("读取 PDF 文字层…")
    page_texts: list[str] = []
    with pdfplumber.open(str(p)) as pdf:
        n_pages = len(pdf.pages)
        for pi, page in enumerate(pdf.pages):
            page_texts.append(page.extract_text() or "")
            if progress_cb and n_pages > 1:
                progress_cb(f"读取 PDF 文字层（第 {pi + 1}/{n_pages} 页）…")

    text_pages = [i for i, t in enumerate(page_texts) if len(t.strip()) >= PDF_TEXT_THRESHOLD]
    scan_pages = [i for i, t in enumerate(page_texts) if len(t.strip()) < PDF_TEXT_THRESHOLD]

    if not scan_pages or not ocr_on:
        full = "\n".join(page_texts[i].strip() for i in range(n_pages) if page_texts[i].strip())
        if scan_pages and not ocr_on:
            warnings.append(f"第 {'、'.join(str(i + 1) for i in scan_pages[:8])} 页疑似扫描页但 OCR 已关闭，该部分未识别")
        return ExtractResult(text=full.strip(), method="pdf_text", pages=n_pages, warnings=warnings)

    # 混合/扫描：仅对缺字页 OCR
    if n_pages > max_pages:
        warnings.append(f"共 {n_pages} 页，超过上限 {max_pages} 页，仅处理前 {max_pages} 页")
    import pypdfium2 as pdfium
    from . import ocr_engine

    ocr_map: dict[int, str] = {}
    low_conf = 0
    doc = pdfium.PdfDocument(str(p))
    try:
        ocr_targets = [x for x in scan_pages if x < max_pages]
        for seq_i, i in enumerate(ocr_targets):
            if progress_cb:
                progress_cb(f"OCR 识别扫描页第 {i + 1} 页（共 {len(ocr_targets)} 页需识别，文字页已直读跳过）…")
            bitmap = doc[i].render(scale=200 / 72)   # 200 DPI 精度/速度平衡点
            lines = ocr_engine.ocr_image(bitmap.to_pil())
            ocr_map[i] = "\n".join(ln["text"] for ln in lines)
            low_conf += sum(1 for ln in lines if ln["score"] < 0.7)
    finally:
        doc.close()
    if low_conf:
        warnings.append(f"OCR 有 {low_conf} 行置信度低于 0.7，建议人工核对")
    parts = []
    for i in range(min(n_pages, max_pages)):
        t = page_texts[i].strip() if i in set(text_pages) else ocr_map.get(i, "").strip()
        if t:
            parts.append(t)
    full = "\n".join(parts).strip()
    if not full:
        warnings.append("未提取到任何文字（可能是空白/加密扫描件）")
    method = "pdf_ocr" if not text_pages else "pdf_mixed"
    return ExtractResult(text=full, method=method, ocr_used=True, pages=n_pages, warnings=warnings)


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


def _extract_docx(p: Path, progress_cb=None) -> ExtractResult:
    """docx 深度提取（v1.5 重写）：
    1. 全文档流遍历 XML——正文段落、表格、**文本框（w:txbxContent）**一次抓全
       （大量简历模板把姓名/电话放文本框或页眉，python-docx 的 paragraph.text 抓不到）
    2. 页眉/页脚（含首页/偶数页变体）
    3. 文本仍缺关键特征（姓名/电话都找不到）→ 内嵌图片 OCR 补全（联系方式截图场景）
    """
    from docx import Document
    from docx.oxml.ns import qn

    doc = Document(str(p))
    warnings: list[str] = []

    # --- 文档流遍历：按 w:p 聚行，w:t 追加（文本框嵌套的 w:p 自然成行，无重复）---
    lines: list[str] = []
    buf: list[str] = []

    def _flush():
        if buf:
            t = "".join(buf).strip()
            if t:
                lines.append(t)
            buf.clear()

    W_P, W_T, W_TAB, W_BR, W_TBL, W_TR, W_TC = (qn("w:p"), qn("w:t"), qn("w:tab"),
                                                qn("w:br"), qn("w:tbl"), qn("w:tr"), qn("w:tc"))
    # 表格内的段落不断行：表格行按单元格 tab 拼接成一行
    # （简历教育/工作段常用表格排版，"2015.09-2019.06\t合肥工业大学\t专业\t本科"必须同行，
    #   拆散会导致毕业年份等跨列模式全部失配）
    tbl_paras = set()
    for tbl in doc.element.body.iter(W_TBL):
        for pp in tbl.iter(W_P):
            tbl_paras.add(id(pp))
    for el in doc.element.body.iter():
        tag = el.tag
        if tag == W_P:
            if id(el) not in tbl_paras:
                _flush()
        elif tag == W_T:
            buf.append(el.text or "")
        elif tag in (W_TAB, W_BR):
            buf.append(" ")
        elif tag == W_TC:
            buf.append("\t")
        elif tag == W_TR:
            _flush()
    _flush()

    # --- 页眉页脚 ---
    try:
        for sec in doc.sections:
            for hf in (sec.header, sec.footer, sec.first_page_header, sec.first_page_footer):
                if hf is None:
                    continue
                for para in hf.paragraphs:
                    t = para.text.strip()
                    if t and t not in lines:
                        lines.append(t)
                for tbl in hf.tables:
                    for row in tbl.rows:
                        t = "\t".join(c.text.strip() for c in row.cells if c.text.strip())
                        if t and t not in lines:
                            lines.append(t)
    except Exception as e:
        warnings.append(f"页眉页脚提取跳过: {type(e).__name__}")

    text = "\n".join(lines).strip()

    # --- 关键特征缺失 → 内嵌图片 OCR 补全（姓名/电话可能在图片里）---
    import re as _re
    has_phone = bool(_re.search(r"1[3-9]\d{9}", text))
    has_name_hint = bool(_re.search(r"姓\s*名|^[\u4e00-\u9fa5]{2,4}$", text, _re.M))
    cfg = config.get()
    if cfg.get("ocr_enabled", True) and (not has_phone or not has_name_hint):
        try:
            if progress_cb:
                progress_cb("正文缺联系方式特征，OCR 识别文档内嵌图片…")
            img_text = _docx_images_text(doc)
            if img_text:
                # 去重合并：图片里已在文本出现的行跳过
                seen = set(lines)
                extra = [ln for ln in img_text.splitlines() if ln.strip() and ln.strip() not in seen]
                if extra:
                    text = text + "\n" + "\n".join(extra)
                    warnings.append(f"已从文档内嵌图片补充识别 {len(extra)} 行（联系方式等常以图片形式放在简历头部）")
        except Exception as e:
            warnings.append(f"内嵌图片识别跳过: {type(e).__name__}: {e}")

    if not text:
        return _extract_docx_embedded_images(p, doc)
    return ExtractResult(text=text, method="docx", pages=1, warnings=warnings)


def _docx_images_text(doc) -> str:
    """提取 docx 全部内嵌图片并 OCR，拼接文本（跳过超小装饰图）。"""
    from . import ocr_engine
    import io
    from PIL import Image
    out: list[str] = []
    for rel in doc.part.rels.values():
        if "image" not in rel.reltype:
            continue
        try:
            blob = rel.target_part.blob
            im = Image.open(io.BytesIO(blob))
            if im.width < 80 or im.height < 40:   # 图标/分隔线级别的小图跳过
                continue
            for ln in ocr_engine.ocr_image(im):
                if ln["score"] >= 0.5:
                    out.append(ln["text"])
        except Exception:
            continue
    return "\n".join(out)


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


def _extract_image(p: Path, progress_cb=None) -> ExtractResult:
    from . import ocr_engine
    if progress_cb:
        progress_cb("OCR 识别图片简历…")
    lines = ocr_engine.ocr_image(str(p))
    text = "\n".join(ln["text"] for ln in lines).strip()
    low = sum(1 for ln in lines if ln["score"] < 0.7)
    warnings = [f"有 {low} 行识别置信度低于 0.7，建议人工核对"] if low else []
    return ExtractResult(text=text, method="image_ocr", ocr_used=True, pages=1,
                         warnings=warnings)
