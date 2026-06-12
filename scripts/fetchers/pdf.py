"""PDF 文本抽取（pdfplumber）"""
from __future__ import annotations

from pathlib import Path

from fetchers.base import FetcherResult, failed, staged


def fetch(file_path: str, *, max_pages: int = 30) -> FetcherResult:
    try:
        import pdfplumber
    except ImportError:
        return failed("pdf", file_path, "缺少依赖 pdfplumber，请 pip install pdfplumber")

    p = Path(file_path)
    if not p.exists():
        return failed("pdf", file_path, "文件不存在")

    title = p.stem
    parts: list[str] = [f"# {title}"]
    try:
        with pdfplumber.open(p) as pdf:
            total_pages = len(pdf.pages)
            for i, page in enumerate(pdf.pages[:max_pages]):
                text = (page.extract_text() or "").strip()
                if text:
                    parts.append(f"\n## 第 {i+1} 页\n\n{text}")
            truncated = total_pages > max_pages
    except Exception as e:
        return failed("pdf", file_path, f"PDF 解析失败: {e}")

    content = "\n".join(parts)
    if len(content.strip()) <= len(title) + 10:
        return failed("pdf", file_path, "未提取到文本（可能是扫描件，需 OCR）")

    return staged(
        fetcher="pdf",
        source=file_path,
        title=title,
        content=content,
        meta={"total_pages": total_pages, "truncated": truncated},
    )
