"""微信公众号文章抓取：mp.weixin.qq.com

抓取兜底链（参考 OpenWiki，解决新版文章 #js_content 为空导致的"抓取失败"）：
  1. #js_content 正文 div —— 传统文章
  2. content_noencode JS 变量 —— 新版把正文放在 JS 字符串里（十六进制转义），反转义后是 HTML
  3. og:description 元信息 —— appmsg_type=9 等短分享/卡片
  否则诚实失败（留收件箱可重试），不造一条只有标题的废笔记。
"""
from __future__ import annotations

import re

import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md

from fetchers.base import FetcherResult, failed, staged


def _js_unescape(s: str) -> str:
    r"""还原微信 content_noencode 里的 JS 字符串转义。

    主力是 \xNN（正文按 UTF-8 字节逐个十六进制转义），兼顾 \uNNNN 和 \/、\\。
    \xNN 还原后是 latin-1 字节视图，需再 encode('latin-1').decode('utf-8') 还原中文。
    """
    s = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), s)
    s = re.sub(r"\\x([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), s)
    s = s.replace("\\/", "/").replace("\\\\", "\\")
    if any("\x80" <= c <= "\xff" for c in s):
        try:
            s = s.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return s


def _extract_noencode(html: str) -> str:
    """从页面 JS 里抠出 content_noencode 的正文 HTML（新版微信文章）。

    形如 content_noencode: '...\\x3cp\\x3e...'  或  content_noencode = JsDecode('...')。
    正文里真正的引号已被 \\x27 之类转义，故可安全地匹配到下一个同类裸引号。
    """
    m = re.search(
        r"content_noencode\s*[:=]\s*(?:JsDecode\()?\s*(['\"])(.*?)\1",
        html,
        re.S,
    )
    if m and m.group(2):
        return _js_unescape(m.group(2))
    return ""


def _node_to_md(node_html: str, max_chars: int) -> str:
    """把一段微信正文 HTML（含 data-src 图片）转 markdown。"""
    soup = BeautifulSoup(node_html, "lxml")
    for img in soup.find_all("img"):
        data_src = img.get("data-src")
        if data_src:
            img["src"] = data_src
    return md(str(soup), heading_style="ATX").strip()[:max_chars]


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

    html = r.text
    soup = BeautifulSoup(html, "lxml")

    title_node = soup.find("h1", class_="rich_media_title") or soup.find("h2", class_="rich_media_title")
    title = title_node.get_text(strip=True) if title_node else ""
    if not title:
        og_title = soup.find("meta", attrs={"property": "og:title"})
        title = (og_title.get("content", "").strip() if og_title else "") or "微信推文"

    author = ""
    author_node = soup.find("a", id="js_name") or soup.find("span", class_="rich_media_meta_text")
    if author_node:
        author = author_node.get_text(strip=True)

    body, via = "", ""

    # 1) 传统 #js_content 正文
    content_node = soup.find(id="js_content")
    if content_node is not None:
        body = _node_to_md(str(content_node), max_chars)
        if body:
            via = "js_content"

    # 2) content_noencode JS 变量（新版文章）
    if not body:
        raw = _extract_noencode(html)
        if raw:
            body = _node_to_md(raw, max_chars)
            if body:
                via = "content_noencode"

    # 3) og:description（短分享/卡片，至少有一段摘要）
    if not body:
        og_desc = soup.find("meta", attrs={"property": "og:description"})
        desc = (og_desc.get("content", "").strip() if og_desc else "")
        if desc:
            body, via = desc[:max_chars], "og:description"

    if not body:
        return failed(
            "wechat",
            url,
            "未抓到正文（#js_content / content_noencode / og:description 均为空，可能需登录/反爬或链接失效）",
        )

    return staged(
        fetcher="wechat",
        source=url,
        title=title,
        content=f"# {title}\n\n**公众号**: {author or '(未知)'}\n\n{body}",
        meta={"author": author, "via": via},
    )
