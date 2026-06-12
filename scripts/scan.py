"""扫描收件箱，提取 URL 和静态文件，识别来源类型，返回 WorkItem 列表"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional


URL_PATTERN = re.compile(r"https?://[^\s一-鿿\)\]\}\>\"\'<>]+", re.UNICODE)
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def _strip_comments_preserve_lines(text: str) -> str:
    """把 <!-- ... --> 内的字符替换为空格，但保留换行 —— 行号和长度不变，URL 正则就找不到注释里的示例链接了。"""
    def repl(m: re.Match) -> str:
        return "".join(ch if ch == "\n" else " " for ch in m.group(0))
    return HTML_COMMENT_RE.sub(repl, text)

# 文件扩展名 → fetcher 名
ATTACHMENT_EXTS = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".doc": "docx",
}


@dataclass(frozen=True)
class WorkItem:
    kind: str  # url | attachment
    target: str  # url 或 文件绝对路径
    source_file: str  # 相对 vault 根的路径
    raw_line: str  # 原文中提取此 item 的整行（处理后用于精准删除）
    fetcher: str  # github / wechat / webpage / douyin / pdf / docx


def classify_url(url: str) -> str:
    """域名 → fetcher 名"""
    lower = url.lower()
    if "github.com" in lower:
        return "github"
    if "mp.weixin.qq.com" in lower:
        return "wechat"
    if "douyin.com" in lower or "iesdouyin.com" in lower:
        return "douyin"
    return "webpage"


def extract_urls_from_text(text: str) -> Iterable[tuple[str, str]]:
    """yield (url, 原始整行) 元组。会先剔除 HTML 注释里的内容，但 raw_line 仍用原文。"""
    orig_lines = text.splitlines()
    stripped_text = _strip_comments_preserve_lines(text)
    stripped_lines = stripped_text.splitlines()
    for orig, stripped in zip(orig_lines, stripped_lines):
        for match in URL_PATTERN.finditer(stripped):
            url = match.group(0).rstrip(".,;:!?。，；：！？")
            yield url, orig


def scan_inbox(vault_root: Path, inbox_dir: str) -> list[WorkItem]:
    inbox_path = vault_root / inbox_dir
    items: list[WorkItem] = []

    if not inbox_path.is_dir():  # 收件箱尚未建立（全新 vault / 用户未录入）→ 视作空，不崩
        return items

    for path in sorted(inbox_path.iterdir()):
        if path.name.startswith("."):
            continue
        rel = str(path.relative_to(vault_root))

        if path.is_file() and path.suffix.lower() == ".md":
            text = path.read_text(encoding="utf-8")
            for url, line in extract_urls_from_text(text):
                items.append(
                    WorkItem(
                        kind="url",
                        target=url,
                        source_file=rel,
                        raw_line=line,
                        fetcher=classify_url(url),
                    )
                )
        elif path.is_file() and path.suffix.lower() in ATTACHMENT_EXTS:
            items.append(
                WorkItem(
                    kind="attachment",
                    target=str(path),
                    source_file=rel,
                    raw_line="",
                    fetcher=ATTACHMENT_EXTS[path.suffix.lower()],
                )
            )

    return items


def filter_new(items: list[WorkItem], state_items: dict) -> list[WorkItem]:
    """剔除 state 中已处理（processed）或已跳过（skipped）的项；failed 的允许重试。"""
    from state import hash_url

    out: list[WorkItem] = []
    for item in items:
        key = hash_url(item.target) if item.kind == "url" else hash_url(f"file://{item.target}")
        existing = state_items.get(key)
        if existing and existing.status in {"processed", "skipped"}:
            continue
        out.append(item)
    return out
