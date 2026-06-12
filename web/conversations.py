"""对话记录持久化：单一滚动会话，自动恢复，按需导出成笔记。

设计（沿用项目惯例，仿 web/jobs.py）：
- 一段对话一个 JSON 文件，存 .pipeline/conversations/。
- note tab：每篇笔记一段（按 note_path 哈希）；vault tab：全局一段（vault.json）。
- 整条会话每轮覆盖写（前端是会话期的 source of truth，幂等）。
- 全部写经 _LOCK + 原子 .tmp→replace()；读容忍坏 JSON 返回空。
- 惰性读，不全量预载（note 会话可能很多）；无需启动 init、无迁移。
"""
from __future__ import annotations

import json
import threading
import time

from web.config import PIPELINE_DIR

CONV_DIR = PIPELINE_DIR / "conversations"
_LOCK = threading.Lock()
_MAX_MESSAGES = 200          # 超限从头按「用户+助手」成对丢弃，数组不以悬空 assistant 开头


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def _conv_path(kind: str, note_path: str | None):
    """key → 文件名。note 路径含 / 和中文，必须哈希。"""
    if kind == "vault":
        return CONV_DIR / "vault.json"
    if kind == "note":
        if not note_path:
            raise ValueError("note 会话必须带 note_path")
        from scripts.state import hash_url
        return CONV_DIR / f"note_{hash_url(note_path)}.json"
    raise ValueError(f"未知 kind: {kind}")


def _truncate(messages: list[dict]) -> list[dict]:
    """超长成对截断：从头丢弃，保证不以 assistant 开头（避免悬空回答）。"""
    if len(messages) <= _MAX_MESSAGES:
        return messages
    cut = messages[-_MAX_MESSAGES:]
    if cut and cut[0].get("role") == "assistant":
        cut = cut[1:]
    return cut


def load_conversation(kind: str, note_path: str | None = None) -> dict:
    """惰性读文件；缺失/坏 JSON 返回空会话（坏文件改名 .broken.json）。"""
    path = _conv_path(kind, note_path)
    empty = {"version": 1, "kind": kind, "note_path": note_path, "updated_at": None, "messages": []}
    with _LOCK:
        if not path.exists():
            return empty
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("messages"), list):
                raise ValueError("schema 不符")
            return data
        except Exception:
            try:
                path.replace(path.with_suffix(".broken.json"))
            except Exception:
                pass
            return empty


def save_conversation(kind: str, note_path: str | None, messages: list[dict]) -> dict:
    """整条会话覆盖写（校验 + 截断 + 原子写）。"""
    path = _conv_path(kind, note_path)  # 同时校验 kind / note_path
    if not isinstance(messages, list):
        raise ValueError("messages 必须是数组")
    messages = _truncate(messages)
    payload = {
        "version": 1,
        "kind": kind,
        "note_path": note_path,
        "updated_at": _now(),
        "messages": messages,
    }
    with _LOCK:
        CONV_DIR.mkdir(exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)
    return {"saved": True, "messages": len(messages)}


def delete_conversation(kind: str, note_path: str | None = None) -> dict:
    """清空对话：删除磁盘文件。"""
    path = _conv_path(kind, note_path)
    with _LOCK:
        existed = path.exists()
        if existed:
            path.unlink()
    return {"deleted": existed}
