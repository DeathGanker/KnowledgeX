"""灵感反哺：追加到原笔记 / 生成闪念笔记"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from web import files
from web.config import VAULT_ROOT


SECTION_MARKER = "## 💡 衍生思考"
FLASH_DIR = "01-笔记/闪念"
CONV_DIR = "01-笔记/对话"


def append_to_note(note_path: str, question: str, answer: str) -> dict:
    """把一条问答追加到原笔记末尾的「衍生思考」章节。
    如果章节不存在则创建；已存在则在末尾追加新条目。
    """
    note = files.read_note(note_path)
    body = note["body"]

    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = (
        f"\n### {ts}\n\n"
        f"**问**：{question.strip()}\n\n"
        f"{answer.strip()}\n"
    )

    if SECTION_MARKER in body:
        # 已有章节，append 到这个章节后面，下一个 ## 之前
        section_pat = re.compile(
            re.escape(SECTION_MARKER) + r"(.*?)(\n##\s|\Z)", re.DOTALL
        )
        m = section_pat.search(body)
        if m:
            existing_section = m.group(0).rstrip()
            tail = m.group(2)
            new_section = existing_section.rstrip() + entry + (tail if tail.startswith("\n##") else "")
            body = body[: m.start()] + new_section + body[m.end():]
        else:
            body = body.rstrip() + "\n\n" + SECTION_MARKER + entry
    else:
        body = body.rstrip() + "\n\n" + SECTION_MARKER + entry

    return _rewrite_body(note_path, note, body)


def create_flash_note(
    question: str,
    answer: str,
    *,
    note_path: str | None = None,
    sources: list[str] | None = None,
) -> dict:
    """新建闪念笔记到 01-笔记/闪念/。

    来源绑定区分两种场景：
    - 当前笔记问答（note_path 给定）：origin = 那篇笔记
    - 全库问答（sources 给定）：origin = 本次问答真正引用的来源笔记们（双链到它们），
      不再张冠李戴当前打开的无关笔记
    """
    # 确定关联的笔记和 source
    origin_links: list[str] = []
    source_val = ""
    if note_path:
        origin = files.read_note(note_path)
        stem = Path(origin["name"]).stem
        origin_links = [stem]
        source_val = origin.get("source") or stem
    elif sources:
        for sp in sources:
            origin_links.append(Path(sp).stem)
        source_val = "全库问答"
    else:
        source_val = "全库问答"

    backlinks = " ".join(f"[[{s}]]" for s in origin_links) if origin_links else "（无）"

    ts_full = datetime.now().strftime("%Y-%m-%d %H-%M")
    safe_q = re.sub(r"[\\/:*?\"<>|]", "", question.strip())[:50] or "对话灵感"
    filename = f"{ts_full} {safe_q}.md"

    # frontmatter 的 origin（双链字符串加引号防 YAML 误解析为数组）
    origin_fm = f'"{backlinks}"' if origin_links else '""'

    content = (
        "---\n"
        f"tags:\n  - 闪念\n  - 对话灵感\n"
        f"source: {source_val}\n"
        f"date: {datetime.now().strftime('%Y-%m-%d')}\n"
        f"origin: {origin_fm}\n"
        "---\n\n"
        f"# {safe_q}\n\n"
        f"**关联笔记**: {backlinks}\n\n"
        f"**问**：{question.strip()}\n\n"
        "---\n\n"
        f"{answer.strip()}\n"
    )

    rel = f"{FLASH_DIR}/{filename}"
    full = VAULT_ROOT / rel
    if full.exists():
        # 重名（同分钟）：后缀加序号
        i = 2
        while (VAULT_ROOT / f"{FLASH_DIR}/{filename[:-3]} -{i}.md").exists():
            i += 1
        rel = f"{FLASH_DIR}/{filename[:-3]} -{i}.md"

    return files.create_note(rel, content)


def export_to_note(kind: str, note_path: str | None, messages: list[dict]) -> dict:
    """把一段对话导出成可读的 markdown 笔记，存入 01-笔记/对话/。

    完整态留在 .pipeline/conversations/ 的 JSON 里；这里只渲染给人看的问答正文，
    进 vault → 可被 RAG 索引、可双链。命名/同分钟去重/frontmatter 仿 create_flash_note。
    """
    msgs = [m for m in (messages or []) if m.get("text")]
    if not msgs:
        raise ValueError("对话为空，无可导出内容")

    # 首条用户问题作标题
    first_q = next((m["text"] for m in msgs if m.get("role") == "user"), "对话记录")

    # 来源双链：note → 该笔记；vault → 各 assistant 引用来源的 stem 并集
    origin_links: list[str] = []
    if kind == "note" and note_path:
        origin_links = [Path(note_path).stem]
    else:
        seen: set[str] = set()
        for m in msgs:
            if m.get("role") != "assistant":
                continue
            for s in (m.get("sources") or []):
                sp = s.get("note_path") if isinstance(s, dict) else None
                if sp:
                    stem = Path(sp).stem
                    if stem not in seen:
                        seen.add(stem)
                        origin_links.append(stem)
    backlinks = " ".join(f"[[{s}]]" for s in origin_links) if origin_links else "（无）"
    origin_fm = f'"{backlinks}"' if origin_links else '""'

    # 正文：按序渲染 问/答，答附本轮来源
    parts: list[str] = []
    for m in msgs:
        if m.get("role") == "user":
            parts.append(f"**问**：{m['text'].strip()}\n")
        else:
            block = m["text"].strip()
            srcs = [
                Path(s["note_path"]).stem
                for s in (m.get("sources") or [])
                if isinstance(s, dict) and s.get("note_path")
            ]
            if srcs:
                block += "\n\n> 来源：" + " ".join(f"[[{s}]]" for s in srcs)
            parts.append(block + "\n")
    conversation_md = "\n".join(parts)

    ts_full = datetime.now().strftime("%Y-%m-%d %H-%M")
    safe_q = re.sub(r"[\\/:*?\"<>|]", "", first_q.strip())[:50] or "对话记录"
    filename = f"{ts_full} {safe_q}.md"

    content = (
        "---\n"
        f"tags:\n  - 对话\n  - 对话记录\n"
        f"date: {datetime.now().strftime('%Y-%m-%d')}\n"
        f"origin: {origin_fm}\n"
        "---\n\n"
        f"# {safe_q}\n\n"
        f"**关联笔记**: {backlinks}\n\n"
        "---\n\n"
        f"{conversation_md}"
    )

    rel = f"{CONV_DIR}/{filename}"
    if (VAULT_ROOT / rel).exists():
        i = 2
        while (VAULT_ROOT / f"{CONV_DIR}/{filename[:-3]} -{i}.md").exists():
            i += 1
        rel = f"{CONV_DIR}/{filename[:-3]} -{i}.md"

    return files.create_note(rel, content)


def _rewrite_body(note_path: str, original_note: dict, new_body: str) -> dict:
    """保留 frontmatter 重写 body 后回写文件。"""
    # 读原始文件取 frontmatter 原样
    full = VAULT_ROOT / note_path
    original_text = full.read_text(encoding="utf-8")
    m = re.match(r"^(---\n.*?\n---\n)", original_text, re.DOTALL)
    fm_block = m.group(1) if m else ""
    full.write_text(fm_block + new_body, encoding="utf-8")
    return files.read_note(note_path)
