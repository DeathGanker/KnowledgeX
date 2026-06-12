"""vault 文件浏览：列树、读笔记、解析 frontmatter、渲染 markdown"""
from __future__ import annotations

import re
import shutil
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

import yaml
from markdown_it import MarkdownIt
from mdit_py_plugins.front_matter import front_matter_plugin
from mdit_py_plugins.tasklists import tasklists_plugin

from web.config import VAULT_ROOT, VISIBLE_DIRS


FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)

_md = (
    MarkdownIt("default", {"html": False, "linkify": False, "typographer": True})
    .use(front_matter_plugin)
    .use(tasklists_plugin)
    .enable("table")
    .enable("strikethrough")
)


@dataclass(frozen=True)
class TreeNode:
    name: str            # 显示名
    path: str            # 相对 vault 根的路径
    is_dir: bool
    children: tuple = () # 仅目录有


def _jsonable(value: Any) -> Any:
    """递归把 yaml 解析出的 date/datetime 转成 ISO 字符串，便于 JSON 序列化。"""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


def _resolve_safe(rel_path: str) -> Path:
    """阻止路径穿越，确保只能访问 vault 内的文件。"""
    rel_path = (rel_path or "").lstrip("/")
    full = (VAULT_ROOT / rel_path).resolve()
    if not str(full).startswith(str(VAULT_ROOT.resolve())):
        raise ValueError(f"非法路径: {rel_path}")
    return full


def list_tree() -> list[dict]:
    """列出 vault 根下可见目录的完整 markdown 文件树。"""
    nodes: list[TreeNode] = []
    for top in VISIBLE_DIRS:
        top_path = VAULT_ROOT / top
        if not top_path.exists() or not top_path.is_dir():
            continue
        nodes.append(_walk(top_path))
    return [_node_to_dict(n) for n in nodes]


def _walk(path: Path) -> TreeNode:
    rel = str(path.relative_to(VAULT_ROOT))
    if path.is_file():
        return TreeNode(name=path.name, path=rel, is_dir=False)
    children: list[TreeNode] = []
    for child in sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if child.name.startswith("."):
            continue
        if child.is_file() and child.suffix.lower() not in (".md", ".html"):
            continue
        children.append(_walk(child))
    return TreeNode(name=path.name, path=rel, is_dir=True, children=tuple(children))


def _node_to_dict(node: TreeNode) -> dict:
    d = asdict(node)
    d["children"] = [_node_to_dict(c) for c in node.children]
    return d


def read_note(rel_path: str) -> dict:
    """读笔记，返回 frontmatter + 原文 + 渲染 HTML + source URL（若有）"""
    p = _resolve_safe(rel_path)
    if not p.exists() or not p.is_file():
        raise FileNotFoundError(rel_path)
    text = p.read_text(encoding="utf-8")

    # HTML 方案（04-项目/*.html）：原样返回给前端 iframe 渲染，不做 markdown 处理
    if p.suffix.lower() == ".html":
        return {
            "path": str(p.relative_to(VAULT_ROOT)),
            "name": p.name,
            "frontmatter": {},
            "body": text,
            "html": "",
            "raw_html": text,
            "is_html": True,
            "source": None,
            "is_github": False,
        }

    fm: dict = {}
    body = text
    m = FRONTMATTER_RE.match(text)
    if m:
        try:
            fm = _jsonable(yaml.safe_load(m.group(1)) or {})
        except yaml.YAMLError:
            fm = {}
        body = m.group(2)

    html = _md.render(body)
    source: Optional[str] = fm.get("source") if isinstance(fm, dict) else None
    is_github = bool(source and "github.com" in source.lower())

    return {
        "path": str(p.relative_to(VAULT_ROOT)),
        "name": p.name,
        "frontmatter": fm,
        "body": body,
        "html": html,
        "source": source,
        "is_github": is_github,
    }


def append_section(rel_path: str, section_md: str) -> dict:
    """追加内容到笔记末尾，返回更新后的笔记。"""
    p = _resolve_safe(rel_path)
    if not p.exists() or not p.is_file():
        raise FileNotFoundError(rel_path)
    existing = p.read_text(encoding="utf-8")
    sep = "" if existing.endswith("\n") else "\n"
    p.write_text(existing + sep + section_md.rstrip() + "\n", encoding="utf-8")
    return read_note(rel_path)


def create_note(rel_path: str, content: str) -> dict:
    """新建笔记（用于灵感闪念）。父目录不存在会自动建。"""
    p = _resolve_safe(rel_path)
    if p.exists():
        raise FileExistsError(rel_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return read_note(str(p.relative_to(VAULT_ROOT)))


def save_note(rel_path: str, body: str) -> dict:
    """覆盖写已存在 markdown 笔记的正文，原 frontmatter 原样保留。

    前端轻量编辑用：用户只编辑正文，不触碰 YAML frontmatter（避免写坏）。
    无 frontmatter 的文件（如收件箱）body 即整文件。
    """
    p = _resolve_safe(rel_path)
    if not p.exists() or not p.is_file():
        raise FileNotFoundError(rel_path)
    if p.suffix.lower() != ".md":
        raise ValueError("仅支持编辑 markdown 笔记")
    original = p.read_text(encoding="utf-8")
    m = FRONTMATTER_RE.match(original)
    fm_block = f"---\n{m.group(1)}\n---\n" if m else ""
    p.write_text(fm_block + body.rstrip() + "\n", encoding="utf-8")
    return read_note(rel_path)


def move_note(rel_path: str, target_dir: str) -> str:
    """把笔记移动到 target_dir 下（文件名不变，重名追加 -2/-3…）。

    返回移动后的新相对路径。文件名不变 → Obsidian 双链 [[文件名]] 不断。
    若目标目录与当前目录相同，原样返回。
    """
    src = _resolve_safe(rel_path)
    if not src.exists() or not src.is_file():
        raise FileNotFoundError(rel_path)

    dst_dir = _resolve_safe(target_dir)
    if dst_dir.exists() and not dst_dir.is_dir():
        raise ValueError(f"目标不是目录: {target_dir}")

    # 已在目标目录：无需移动
    if src.parent.resolve() == dst_dir.resolve():
        return str(src.relative_to(VAULT_ROOT))

    dst_dir.mkdir(parents=True, exist_ok=True)

    # 重名处理：foo.md → foo-2.md → foo-3.md
    stem, suffix = src.stem, src.suffix
    dst = dst_dir / src.name
    n = 2
    while dst.exists():
        dst = dst_dir / f"{stem}-{n}{suffix}"
        n += 1

    shutil.move(str(src), str(dst))
    return str(dst.relative_to(VAULT_ROOT))
