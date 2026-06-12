"""对话记录持久化：自动恢复，按需导出成笔记。

设计（沿用项目惯例，仿 web/jobs.py）：
- 一段对话一个 JSON 文件，存 .pipeline/conversations/。
- note tab：每篇笔记一段（按 note_path 哈希，固定单段，跟着笔记走）。
- vault / plan tab：**多会话**，每段一个 id —— conversations/{kind}/{session_id}.json，
  支持列表 / 切换 / 新建 / 删除。
- 整条会话每轮覆盖写（前端是会话期的 source of truth，幂等）。
- 全部写经 _LOCK + 原子 .tmp→replace()；读容忍坏 JSON 返回空。
- 惰性读，不全量预载；旧的单段 vault.json 首次 list 时自动迁移成一个会话。
"""
from __future__ import annotations

import json
import re
import threading
import time

from web.config import PIPELINE_DIR

CONV_DIR = PIPELINE_DIR / "conversations"
_LOCK = threading.Lock()
_MAX_MESSAGES = 200          # 超限从头按「用户+助手」成对丢弃，数组不以悬空 assistant 开头
_MULTI_KINDS = {"vault", "plan"}   # 多会话的 tab
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def _safe_id(session_id: str) -> str:
    if not session_id or not _ID_RE.match(session_id):
        raise ValueError(f"非法 session_id: {session_id!r}")
    return session_id


def _derive_title(messages: list[dict]) -> str:
    """会话标题：取第一条用户消息前若干字；空则"新对话"。"""
    for m in messages:
        if m.get("role") == "user":
            t = re.sub(r"\s+", " ", (m.get("text") or "")).strip()
            if t:
                return t[:40]
    return "新对话"


def _conv_path(kind: str, note_path: str | None = None, session_id: str | None = None):
    """key → 文件路径。note 按 note_path 哈希；vault/plan 按 session_id 分文件。"""
    if kind == "note":
        if not note_path:
            raise ValueError("note 会话必须带 note_path")
        from scripts.state import hash_url
        return CONV_DIR / f"note_{hash_url(note_path)}.json"
    if kind in _MULTI_KINDS:
        if not session_id:
            raise ValueError(f"{kind} 会话必须带 session_id")
        return CONV_DIR / kind / f"{_safe_id(session_id)}.json"
    raise ValueError(f"未知 kind: {kind}")


def _truncate(messages: list[dict]) -> list[dict]:
    """超长成对截断：从头丢弃，保证不以 assistant 开头（避免悬空回答）。"""
    if len(messages) <= _MAX_MESSAGES:
        return messages
    cut = messages[-_MAX_MESSAGES:]
    if cut and cut[0].get("role") == "assistant":
        cut = cut[1:]
    return cut


def _read(path) -> dict | None:
    """读单个会话文件；坏 JSON 改名 .broken.json 返回 None。"""
    if not path.exists():
        return None
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
        return None


def _migrate_legacy_vault() -> None:
    """旧版单段 conversations/vault.json → 迁成 vault 多会话里的一个会话（一次性）。"""
    legacy = CONV_DIR / "vault.json"
    if not legacy.exists():
        return
    data = _read(legacy)
    if data and data.get("messages"):
        sid = "legacy-" + time.strftime("%Y%m%d%H%M%S", time.localtime())
        data["kind"] = "vault"
        data["session_id"] = sid
        data.setdefault("title", _derive_title(data["messages"]))
        data.setdefault("updated_at", _now())
        dest = CONV_DIR / "vault" / f"{sid}.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        legacy.unlink()
    except Exception:
        pass


def list_sessions(kind: str) -> list[dict]:
    """列出某个多会话 tab 的所有会话（按更新时间倒序）。"""
    if kind not in _MULTI_KINDS:
        raise ValueError(f"{kind} 不支持多会话")
    with _LOCK:
        if kind == "vault":
            _migrate_legacy_vault()
        d = CONV_DIR / kind
        out = []
        if d.is_dir():
            for p in d.glob("*.json"):
                data = _read(p)
                if data is None:
                    continue
                out.append({
                    "session_id": p.stem,
                    "title": data.get("title") or _derive_title(data.get("messages") or []),
                    "updated_at": data.get("updated_at"),
                    "count": len(data.get("messages") or []),
                })
        out.sort(key=lambda s: s.get("updated_at") or "", reverse=True)
        return out


def load_conversation(kind: str, note_path: str | None = None, session_id: str | None = None) -> dict:
    """惰性读文件；缺失/坏 JSON 返回空会话。"""
    path = _conv_path(kind, note_path, session_id)
    empty = {"version": 1, "kind": kind, "note_path": note_path,
             "session_id": session_id, "title": "新对话", "updated_at": None, "messages": []}
    with _LOCK:
        data = _read(path)
        return data if data is not None else empty


def save_conversation(kind: str, note_path: str | None, messages: list[dict],
                      session_id: str | None = None, title: str | None = None) -> dict:
    """整条会话覆盖写（校验 + 截断 + 原子写）。"""
    path = _conv_path(kind, note_path, session_id)  # 同时校验 kind / note_path / session_id
    if not isinstance(messages, list):
        raise ValueError("messages 必须是数组")
    messages = _truncate(messages)
    payload = {
        "version": 1,
        "kind": kind,
        "note_path": note_path,
        "session_id": session_id,
        "title": (title or _derive_title(messages))[:40],
        "updated_at": _now(),
        "messages": messages,
    }
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)
    return {"saved": True, "messages": len(messages), "title": payload["title"]}


def delete_conversation(kind: str, note_path: str | None = None, session_id: str | None = None) -> dict:
    """清空/删除一段对话：删除磁盘文件。"""
    path = _conv_path(kind, note_path, session_id)
    with _LOCK:
        existed = path.exists()
        if existed:
            path.unlink()
    return {"deleted": existed}
