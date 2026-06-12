"""笔记切块：按 ## 章节切，保留笔记标题+章节标题作为上下文"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from web.config import VAULT_ROOT


FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)
H1_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
# 按 ## 切分（含 ## 本身）
SECTION_SPLIT_RE = re.compile(r"(?=^##\s)", re.MULTILINE)


@dataclass(frozen=True)
class Chunk:
    note_path: str       # vault 相对路径
    note_title: str      # 笔记主标题（# 或文件名）
    section: str         # 章节标题（## 后面的文字，可能为空）
    text: str            # 用于 embedding 的完整文本（含标题上下文）
    chunk_id: str        # note_path#section#序号


def _split_long(text: str, max_chars: int) -> list[str]:
    """超长文本按段落切，每块不超过 max_chars。"""
    if len(text) <= max_chars:
        return [text]
    paras = text.split("\n\n")
    chunks: list[str] = []
    cur = ""
    for p in paras:
        if len(cur) + len(p) + 2 > max_chars and cur:
            chunks.append(cur.strip())
            cur = p
        else:
            cur = f"{cur}\n\n{p}" if cur else p
    if cur.strip():
        chunks.append(cur.strip())
    return chunks


def chunk_note(rel_path: str, max_chars: int = 800) -> list[Chunk]:
    """把一篇笔记切成若干 Chunk。"""
    full = VAULT_ROOT / rel_path
    text = full.read_text(encoding="utf-8")

    # 去 frontmatter
    body = text
    m = FRONTMATTER_RE.match(text)
    if m:
        body = m.group(2)

    # 笔记主标题
    h1 = H1_RE.search(body)
    note_title = h1.group(1).strip() if h1 else Path(rel_path).stem

    # 按 ## 切章节
    parts = SECTION_SPLIT_RE.split(body)
    chunks: list[Chunk] = []
    seq = 0

    for part in parts:
        part = part.strip()
        if not part:
            continue
        # 章节标题
        sec_m = re.match(r"^##\s+(.+)", part)
        section = sec_m.group(1).strip() if sec_m else ""
        # 跳过机器生成的关联章节，避免双链反过来污染向量
        if "相关笔记" in section:
            continue
        # 章节正文（去掉 ## 标题行）
        content = re.sub(r"^##\s+.+\n?", "", part).strip() if sec_m else part
        # 跳过 H1 标题独占的那块（没实际内容）
        if not content and not section:
            continue

        for sub in _split_long(content, max_chars):
            if not sub.strip():
                continue
            # embedding 文本带上标题上下文，提升检索命中
            header = f"{note_title}"
            if section:
                header += f" — {section}"
            emb_text = f"{header}\n{sub}"
            chunks.append(
                Chunk(
                    note_path=rel_path,
                    note_title=note_title,
                    section=section,
                    text=emb_text,
                    chunk_id=f"{rel_path}#{seq}",
                )
            )
            seq += 1

    return chunks
