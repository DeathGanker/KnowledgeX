"""Word 文档抽取（mammoth → markdown）"""
from __future__ import annotations

from pathlib import Path

from fetchers.base import FetcherResult, failed, staged


def fetch(file_path: str) -> FetcherResult:
    try:
        import mammoth
    except ImportError:
        return failed("docx", file_path, "缺少依赖 mammoth，请 pip install mammoth")

    p = Path(file_path)
    if not p.exists():
        return failed("docx", file_path, "文件不存在")

    title = p.stem
    try:
        with open(p, "rb") as f:
            result = mammoth.convert_to_markdown(f)
        body = (result.value or "").strip()
    except Exception as e:
        return failed("docx", file_path, f"docx 解析失败: {e}")

    if not body:
        return failed("docx", file_path, "解析到空内容")

    return staged(
        fetcher="docx",
        source=file_path,
        title=title,
        content=f"# {title}\n\n{body}",
        meta={"warnings": [m.message for m in result.messages[:5]]},
    )
