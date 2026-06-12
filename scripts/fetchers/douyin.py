"""抖音占位：首版不支持视频转文字，统一返回 skipped"""
from __future__ import annotations

from fetchers.base import FetcherResult, skipped


def fetch(url: str) -> FetcherResult:
    return skipped(
        "douyin",
        url,
        "抖音视频暂未支持。需要下载视频 + whisper 转文字，将在 v2 加入。",
    )
