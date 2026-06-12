"""抓取器接口与结果数据结构"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class FetcherResult:
    """抓取阶段产物，会被 digest.py 喂给 LLM。"""
    ok: bool
    fetcher: str
    source: str  # URL 或文件路径
    title: Optional[str] = None
    content: str = ""  # 主体文本（markdown 或纯文本）
    meta: dict = field(default_factory=dict)
    error: Optional[str] = None
    status: str = "staged"  # staged | skipped | failed


def skipped(fetcher: str, source: str, reason: str) -> FetcherResult:
    return FetcherResult(ok=False, fetcher=fetcher, source=source, status="skipped", error=reason)


def failed(fetcher: str, source: str, reason: str) -> FetcherResult:
    return FetcherResult(ok=False, fetcher=fetcher, source=source, status="failed", error=reason)


def staged(fetcher: str, source: str, title: str, content: str, meta: Optional[dict] = None) -> FetcherResult:
    return FetcherResult(
        ok=True,
        fetcher=fetcher,
        source=source,
        title=title,
        content=content,
        meta=meta or {},
        status="staged",
    )
