"""微信公众号文章抓取：mp.weixin.qq.com"""
from __future__ import annotations

import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md

from fetchers.base import FetcherResult, failed, staged


def fetch(url: str, *, max_chars: int = 30000, timeout: int = 30, user_agent: str = "") -> FetcherResult:
    headers = {
        "User-Agent": user_agent or "Mozilla/5.0",
        "Referer": "https://mp.weixin.qq.com/",
    }
    try:
        r = requests.get(url, headers=headers, timeout=timeout)
        r.raise_for_status()
    except requests.HTTPError as e:
        return failed("wechat", url, f"HTTP {e.response.status_code}")
    except requests.RequestException as e:
        return failed("wechat", url, f"请求失败: {e}")

    soup = BeautifulSoup(r.text, "lxml")

    title_node = soup.find("h1", class_="rich_media_title") or soup.find("h2", class_="rich_media_title")
    title = (title_node.get_text(strip=True) if title_node else "微信推文")

    author = ""
    author_node = soup.find("a", id="js_name") or soup.find("span", class_="rich_media_meta_text")
    if author_node:
        author = author_node.get_text(strip=True)

    content_node = soup.find(id="js_content")
    if content_node is None:
        return failed("wechat", url, "未找到正文节点 #js_content（可能被反爬/需登录/链接已失效）")

    # 微信文章常用 data-src 而非 src 加载图片，统一替换
    for img in content_node.find_all("img"):
        data_src = img.get("data-src")
        if data_src:
            img["src"] = data_src

    body = md(str(content_node), heading_style="ATX").strip()
    if not body:
        return failed("wechat", url, "正文为空")
    body = body[:max_chars]

    return staged(
        fetcher="wechat",
        source=url,
        title=title,
        content=f"# {title}\n\n**公众号**: {author or '(未知)'}\n\n{body}",
        meta={"author": author},
    )
