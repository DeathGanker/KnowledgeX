"""通用网页抓取：readability 提取正文，转 markdown"""
from __future__ import annotations

import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md
from readability import Document

from fetchers.base import FetcherResult, failed, staged


def fetch(url: str, *, max_chars: int = 30000, timeout: int = 30, user_agent: str = "") -> FetcherResult:
    headers = {"User-Agent": user_agent or "Mozilla/5.0"}
    try:
        r = requests.get(url, headers=headers, timeout=timeout)
        r.raise_for_status()
    except requests.HTTPError as e:
        return failed("webpage", url, f"HTTP {e.response.status_code}")
    except requests.RequestException as e:
        return failed("webpage", url, f"请求失败: {e}")

    if "text/html" not in r.headers.get("Content-Type", "").lower() and "<html" not in r.text[:1000].lower():
        return failed("webpage", url, f"非 HTML 内容: {r.headers.get('Content-Type')}")

    try:
        doc = Document(r.text)
        title = (doc.short_title() or doc.title() or url).strip()
        summary_html = doc.summary()
    except Exception as e:
        return failed("webpage", url, f"readability 解析失败: {e}")

    soup = BeautifulSoup(summary_html, "lxml")
    body = md(str(soup), heading_style="ATX").strip()
    if not body:
        return failed("webpage", url, "提取到空内容")
    body = body[:max_chars]

    return staged(
        fetcher="webpage",
        source=url,
        title=title,
        content=f"# {title}\n\n{body}",
        meta={"final_url": r.url, "content_type": r.headers.get("Content-Type")},
    )
