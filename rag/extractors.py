"""二进制办公文档与图片的文本提取。

为知识库上传扩展能力：在原有 .md/.txt/.html 基础上，新增
Word/.docx、PDF、Excel/.xlsx、PPT/.pptx、图片(OCR) 的文本提取，
使这些格式也能被切分并进入向量索引。所有提取都做了异常兜底，
单文件失败不影响整批重建。
"""
from __future__ import annotations

from pathlib import Path

from config import DOC_IMAGE_CAPTION

TEXT_SUFFIXES = {".md", ".txt", ".html"}
WORD_SUFFIXES = {".docx"}
PDF_SUFFIXES = {".pdf"}
EXCEL_SUFFIXES = {".xlsx"}
PPT_SUFFIXES = {".pptx"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
LEGACY_BIN = {".doc", ".xls", ".ppt"}  # 老式二进制 Office 格式（尽力提取）

# 上传允许的后缀（含旧版二进制格式，上传成功但尽量提取文本）
ALLOWED_EXTENSIONS = (
    TEXT_SUFFIXES
    | WORD_SUFFIXES
    | PDF_SUFFIXES
    | EXCEL_SUFFIXES
    | PPT_SUFFIXES
    | IMAGE_SUFFIXES
    | LEGACY_BIN
)

# 老二进制格式：允许上传；提取失败时才标注“暂不支持解析”
LEGACY_UNSUPPORTED = set(LEGACY_BIN)

_EXT_LABEL = "/".join(sorted(x.lstrip(".") for x in ALLOWED_EXTENSIONS))


def allowed_ext_label() -> str:
    return _EXT_LABEL


def _read_text(path: Path) -> str:
    data = path.read_bytes()
    for enc in ("utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _docx_image_captions(doc) -> list[str]:
    """提取 Word 内嵌图片的「图注描述」（Alt 文本 / descr）；无描述则返回空串占位。

    遍历正文中所有 wp:inline / wp:anchor 图形节点，读取其 cNvPr / docPr 的 descr，
    让图片也以可检索的图注形式进入知识库，图表类问答更友好（不依赖视觉大模型）。
    """
    caps: list[str] = []
    for node in getattr(doc.element.body, "iter", lambda: [])():
        tag = getattr(node, "tag", "")
        if not (tag.endswith("}inline") or tag.endswith("}anchor")):
            continue
        descr = ""
        for sub in node.iter():
            t = getattr(sub, "tag", "")
            if t.endswith("}cNvPr") or t.endswith("}docPr"):
                descr = (sub.get("descr") or "").strip()
                break
        caps.append(descr)
    return caps[:_IMAGE_CAPTION_LIMIT]


# 单文档提取的图注数量上限，防止海量小图撑爆正文
_IMAGE_CAPTION_LIMIT = 60


def _read_docx(path: Path) -> str:
    import docx

    doc = docx.Document(str(path))
    parts: list[str] = [p.text for p in doc.paragraphs if p.text.strip()]
    for tbl in doc.tables:
        out_rows = []
        for row in tbl.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                out_rows.append(" │ ".join(cells))
        if out_rows:
            parts.append("[表格]\n" + "\n".join(out_rows))
    if DOC_IMAGE_CAPTION:
        for i, cap in enumerate(_docx_image_captions(doc), 1):
            parts.append(f"[图 {i}：{cap}]" if cap else f"[图片 {i}]")
    return "\n".join(parts)


def _read_pdf(path: Path) -> str:
    try:
        import pdfplumber

        with pdfplumber.open(str(path)) as pdf:
            texts = [p.extract_text() for p in pdf.pages]
        joined = "\n".join(t for t in texts if t)
        if joined.strip():
            return joined
    except Exception:  # noqa: BLE001 pdfplumber 失败则回退 pypdf
        pass
    import pypdf

    reader = pypdf.PdfReader(str(path))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _read_xlsx(path: Path) -> str:
    import openpyxl

    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    parts: list[str] = []
    for ws in wb.worksheets:
        rows = []
        for row in ws.iter_rows(values_only=True):
            cells = [str(c).strip() for c in row if c is not None and str(c).strip()]
            if cells:
                rows.append(" │ ".join(cells))
        if rows:
            parts.append(f"[工作表：{ws.title}]\n" + "\n".join(rows))
    wb.close()
    return "\n\n".join(parts)


def _read_pptx(path: Path) -> str:
    from pptx import Presentation

    prs = Presentation(str(path))
    parts: list[str] = []
    for i, slide in enumerate(prs.slides, 1):
        texts = [
            sh.text.strip()
            for sh in slide.shapes
            if getattr(sh, "has_text_frame", False) and sh.text.strip()
        ]
        if texts:
            parts.append(f"[幻灯片 {i}]\n" + "\n".join(texts))
    return "\n\n".join(parts)


# 常见 tesseract 安装位置（跨平台通用路径，不含个人目录；个人机器用环境变量 TESSERACT_CMD 指定）
_TESSERACT_EXES = (
    "/usr/bin/tesseract",
    "/usr/local/bin/tesseract",
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
)
_TESSDATA_DIRS = (
    "/usr/share/tesseract-ocr/5/tessdata",
    "/usr/share/tessdata",
)


def _find_tesseract_exe() -> str:
    """定位 tesseract 可执行文件。

    优先级：环境变量 TESSERACT_CMD > PATH(shutil.which) > 常见安装位置。
    Windows 上 which 常命中 .cmd/.bat 包装，这里尝试解析到同目录的 tesseract.exe；
    若仍无法定位，返回 "tesseract" 交由上层 try 兜底（失败返回空文本，不阻塞上传）。
    """
    import os
    import shutil

    cmd = os.getenv("TESSERACT_CMD")
    if cmd and os.path.exists(cmd):
        return cmd
    found = shutil.which("tesseract")
    if found and os.path.exists(found):
        if found.lower().endswith((".cmd", ".bat")):
            exe = os.path.join(os.path.dirname(found), "tesseract.exe")
            if os.path.exists(exe):
                return exe
        return found
    return next((e for e in _TESSERACT_EXES if os.path.exists(e)), "tesseract")


def _read_image_ocr(path: Path) -> str:
    """图片 OCR：定位可用的 tesseract 与 tessdata；若不可用则返回空文本（上传本身不受影响）。"""
    import os

    if not os.getenv("TESSDATA_PREFIX"):
        for td in _TESSDATA_DIRS:
            if os.path.isdir(td):
                os.environ["TESSDATA_PREFIX"] = td
                break
    exe = _find_tesseract_exe()
    # 默认中文+英文双语言：tesseract 装 chi_sim 后可识别中文；
    # 可用环境变量 TESSERACT_LANGS 覆盖（如只识英文时设为 "eng"）。
    langs = os.getenv("TESSERACT_LANGS", "chi_sim+eng")
    try:
        import pytesseract
        from PIL import Image

        pytesseract.pytesseract.tesseract_cmd = exe
        with Image.open(str(path)) as im:
            return pytesseract.image_to_string(im, lang=langs) or ""
    except Exception:  # noqa: BLE001 OCR 失败不阻塞上传与索引构建
        return ""


def _read_xls_legacy(path: Path) -> str:
    """老式 Excel (.xls)：用 xlrd 精确取各工作表单元格文本。"""
    try:
        import xlrd
    except Exception:  # noqa: BLE001
        xlrd = None
    if xlrd:
        wb = xlrd.open_workbook(str(path), formatting_info=False)
        parts = []
        for sh in wb.sheets():
            rows = []
            for r in range(sh.nrows):
                cells = [str(sh.cell_value(r, c)).strip() for c in range(sh.ncols)]
                cells = [c for c in cells if c]
                if cells:
                    rows.append(" │ ".join(cells))
            if rows:
                parts.append(f"[工作表：{sh.name}]\n" + "\n".join(rows))
        if parts:
            return "\n\n".join(parts)
    # xlrd 缺装或为空：回退 OLE 启发式提取
    return _read_ole_legacy(path)


def _ole_readable_runs(data: bytes) -> str:
    """按 UTF-16LE 扫描流字节，收集连续可读片段（中文/ASCII/全角），过滤二进制噪声。"""
    out: list[str] = []
    run: list[str] = []

    def flush() -> None:
        if len(run) >= 2:
            out.append("".join(run))
        run.clear()

    for i in range(1, len(data) - 1, 2):
        code = data[i - 1] | (data[i] << 8)  # LE
        ok = (
            0x4E00 <= code <= 0x9FFF  # CJK 统一表意
            or 0x3400 <= code <= 0x4DBF  # CJK 扩展 A
            or 0xF900 <= code <= 0xFAFF  # CJK 兼容
            or 0x0020 <= code <= 0x007E  # ASCII 可打印
            or 0xFF00 <= code <= 0xFFEF  # 全角
            or 0x3000 <= code <= 0x303F  # CJK 标点
        )
        if ok:
            run.append(chr(code))
        else:
            flush()
    flush()
    # 过滤二进制对齐噪声：
    # 1) 丢弃「同一字符连续重复≥2」的片段（偶数字节错位常产生如 卋卋 这样的双同码）。
    # 2) 丢弃「字符类别高频互跳」的片段：排版/域格式字节错位会在中文与 ASCII 间逐字符切换
    #    （如 J帀J漀伀J倀儀J帀…），而真实正文（纯中文/纯英文/自然中英混排）切换次数很少。
    return "".join(r for r in out if len(set(r)) > 1 and _alternation_ratio(r) <= 0.5)


def _alternation_ratio(text: str) -> float:
    """返回相邻字符在「CJK 类 / 其他可打印类」间交替的比例；越高越像二进制错位噪声。"""
    def cls(ch: str) -> int:
        o = ord(ch)
        return 1 if (0x3400 <= o <= 0x9FFF or 0xF900 <= o <= 0xFAFF) else 0
    if len(text) < 2:
        return 0.0
    switches = sum(1 for a, b in zip(text, text[1:]) if cls(a) != cls(b))
    return switches / (len(text) - 1)


def _read_ole_legacy(path: Path, streams: list[str] | None = None) -> str:
    """老式 Word/PowerPoint (.doc/.ppt)：读 OLE 流，启发式捞取 UTF-16 可读文本。"""
    import olefile

    if not olefile.isOleFile(str(path)):
        return ""
    ole = olefile.OleFileIO(str(path))
    parts: list[str] = []
    try:
        names = streams or ["/".join(seg) for seg in ole.listdir()]
        for name in names:
            try:
                data = ole.openstream(name).read()
            except Exception:  # noqa: BLE001
                continue
            t = _ole_readable_runs(data).strip()
            if len(t) >= 10:
                parts.append(t)
    finally:
        ole.close()
    return "\n".join(parts)


def _read_doc_legacy(path: Path) -> str:
    """老式 Word (.doc)：正文主要在 WordDocument 流；回退全流。"""
    t = _read_ole_legacy(path, streams=["WordDocument"])
    if len(t) < 10:
        t = _read_ole_legacy(path)
    # 截掉 Word 域/格式尾噪声：正文通常在 PAGE · \\* MERGEFORMAT 域标记前结束，
    # 该标记后的字节多为错位排版码，直接裁掉保留可读主体。
    marker = _doc_body_end_marker(t)
    if marker > 0:
        t = t[:marker]
    return _strip_word_page_residue(t)


def _strip_word_page_residue(text: str) -> str:
    """去掉 .doc 正文末尾的 Word 页码域残留（PAGE / NUMPAGES / 粘连的 PAGE / - N - 页脚）。

    页码域标记有时直接粘连在前一个中文字符后（如「…签章PAGE」，无空格），按空白分词切不出去，
    需用端点锚定正则剥离；只处理 PAGE/NUMPAGES/SECTIONPAGES 与带箭头的 `- N -` 页脚，
    不触碰正文末尾可能是真实数字（如金额、账号）的内容。
    """
    import re

    pat = re.compile(r"(?:PAGE|NUMPAGES|SECTIONPAGES)(?:\s*\\?\*\s*MERGEFORMAT)?\d*$|-{1,4}\s*\d{1,4}\s*-{1,4}$")
    t = text.rstrip()
    while True:
        new = re.sub(pat, "", t, count=1)
        if new == t:
            break
        t = new.rstrip()
    return t


def _doc_body_end_marker(text: str) -> int:
    """定位 .doc 正文截断点：取首个 Word 域结果标记（MERGEFORMAT）的位置，找不到返回 0。"""
    for mk in ("\\* MERGEFORMAT", "MERGEFORMAT"):
        i = text.find(mk)
        if i > 8:
            return i
    return 0


def _read_ppt_legacy(path: Path) -> str:
    """老式 PowerPoint (.ppt)：文本在 PowerPoint Document 流；回退全流。"""
    t = _read_ole_legacy(path, streams=["PowerPoint Document"])
    if len(t) < 10:
        t = _read_ole_legacy(path)
    return t


def extract_text(path: Path) -> str:
    """按后缀分发提取文本；未知或不支持的格式返回空串（不抛异常）。"""
    suffix = path.suffix.lower()
    if suffix in TEXT_SUFFIXES:
        return _read_text(path)
    if suffix in WORD_SUFFIXES:
        try:
            return _read_docx(path)
        except Exception:  # noqa: BLE001
            return ""
    if suffix in PDF_SUFFIXES:
        try:
            return _read_pdf(path)
        except Exception:  # noqa: BLE001
            return ""
    if suffix in EXCEL_SUFFIXES:
        try:
            return _read_xlsx(path)
        except Exception:  # noqa: BLE001
            return ""
    if suffix in PPT_SUFFIXES:
        try:
            return _read_pptx(path)
        except Exception:  # noqa: BLE001
            return ""
    if suffix in IMAGE_SUFFIXES:
        return _read_image_ocr(path)
    if suffix == ".doc":
        try:
            return _read_doc_legacy(path)
        except Exception:  # noqa: BLE001
            return ""
    if suffix == ".xls":
        try:
            return _read_xls_legacy(path)
        except Exception:  # noqa: BLE001
            return ""
    if suffix == ".ppt":
        try:
            return _read_ppt_legacy(path)
        except Exception:  # noqa: BLE001
            return ""
    return ""
