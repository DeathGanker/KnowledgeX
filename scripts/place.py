"""归位：解析 frontmatter，按 placement 字段写入目标目录"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Optional

import yaml


FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)
TITLE_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)

INVALID_FN_CHARS = re.compile(r'[\/\\:*?"<>|]')


def parse_frontmatter(markdown: str) -> tuple[dict, str]:
    m = FRONTMATTER_RE.match(markdown)
    if not m:
        raise ValueError("markdown 缺少 frontmatter")
    fm = yaml.safe_load(m.group(1)) or {}
    body = m.group(2)
    return fm, body


def extract_title(markdown_body: str) -> Optional[str]:
    m = TITLE_RE.search(markdown_body)
    return m.group(1).strip() if m else None


def sanitize_filename(name: str, max_len: int = 80) -> str:
    cleaned = INVALID_FN_CHARS.sub("", name).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:max_len] or "未命名"


def resolve_placement(fm: dict, *, allowed_prefixes: list[str], default: str) -> str:
    placement = (fm.get("placement") or "").strip().rstrip("/")
    if not placement:
        return default
    for prefix in allowed_prefixes:
        if placement == prefix or placement.startswith(prefix + "/"):
            return placement
    return default


def place(
    markdown: str,
    *,
    vault_root: Path,
    allowed_prefixes: list[str],
    default_placement: str,
) -> Path:
    fm, body = parse_frontmatter(markdown)
    title = extract_title(body) or fm.get("title") or "未命名笔记"
    target_dir_rel = resolve_placement(fm, allowed_prefixes=allowed_prefixes, default=default_placement)

    target_dir = vault_root / target_dir_rel
    target_dir.mkdir(parents=True, exist_ok=True)

    date_str = str(fm.get("date") or date.today().isoformat())
    safe_title = sanitize_filename(title)
    filename = f"{date_str} {safe_title}.md"
    target = target_dir / filename

    # 重名处理：追加 -2, -3 ...
    counter = 2
    while target.exists():
        target = target_dir / f"{date_str} {safe_title} -{counter}.md"
        counter += 1

    target.write_text(markdown, encoding="utf-8")
    return target
