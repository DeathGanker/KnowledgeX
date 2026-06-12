"""抓取器注册表"""
from __future__ import annotations

from typing import Callable

from fetchers import docx as docx_mod
from fetchers import douyin as douyin_mod
from fetchers import github as github_mod
from fetchers import pdf as pdf_mod
from fetchers import webpage as webpage_mod
from fetchers import wechat as wechat_mod
from fetchers.base import FetcherResult, failed


REGISTRY: dict[str, Callable[..., FetcherResult]] = {
    "github": github_mod.fetch,
    "wechat": wechat_mod.fetch,
    "webpage": webpage_mod.fetch,
    "douyin": douyin_mod.fetch,
    "pdf": pdf_mod.fetch,
    "docx": docx_mod.fetch,
}


def dispatch(fetcher_name: str, target: str, *, config: dict) -> FetcherResult:
    fn = REGISTRY.get(fetcher_name)
    if fn is None:
        return failed(fetcher_name, target, f"未知 fetcher: {fetcher_name}")

    if not config.get("fetchers", {}).get(fetcher_name, True):
        from fetchers.base import skipped
        return skipped(fetcher_name, target, f"抓取器 {fetcher_name} 在 config 中已禁用")

    limits = config.get("limits", {})
    timeout = limits.get("http_timeout_seconds", 30)
    user_agent = limits.get("user_agent", "")

    if fetcher_name == "github":
        return fn(target, max_readme_chars=limits.get("github_readme_max_chars", 40000), timeout=timeout)
    if fetcher_name == "webpage":
        return fn(target, max_chars=limits.get("webpage_max_chars", 30000), timeout=timeout, user_agent=user_agent)
    if fetcher_name == "wechat":
        return fn(target, max_chars=limits.get("webpage_max_chars", 30000), timeout=timeout, user_agent=user_agent)
    if fetcher_name == "pdf":
        return fn(target, max_pages=limits.get("pdf_max_pages", 30))
    if fetcher_name == "docx":
        return fn(target)
    if fetcher_name == "douyin":
        return fn(target)

    return failed(fetcher_name, target, f"无适配调用: {fetcher_name}")
