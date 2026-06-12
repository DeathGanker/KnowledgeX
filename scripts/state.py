"""管道状态读写：state.json 单一真相源"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


PIPELINE_DIR = Path(__file__).resolve().parent.parent
STATE_FILE = PIPELINE_DIR / "state.json"


@dataclass(frozen=True)
class ItemRecord:
    url: str
    source_file: str
    fetcher: str
    status: str  # pending | staged | processed | failed | skipped
    output_path: Optional[str] = None
    staging_path: Optional[str] = None
    fetched_at: Optional[str] = None
    processed_at: Optional[str] = None
    error: Optional[str] = None
    title: Optional[str] = None
    raw_line: Optional[str] = None  # cleanup 用：原文中的整行（用于精确删除）


def hash_url(url: str) -> str:
    return hashlib.sha256(url.strip().encode("utf-8")).hexdigest()[:12]


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _load_raw() -> dict:
    if not STATE_FILE.exists():
        return {"items": {}}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        backup = STATE_FILE.with_suffix(".broken.json")
        STATE_FILE.rename(backup)
        return {"items": {}}


def load_state() -> dict[str, ItemRecord]:
    raw = _load_raw()
    items = {}
    for key, payload in raw.get("items", {}).items():
        items[key] = ItemRecord(**payload)
    return items


def save_state(items: dict[str, ItemRecord]) -> None:
    payload = {"items": {k: asdict(v) for k, v in items.items()}}
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)


def upsert(items: dict[str, ItemRecord], key: str, **changes) -> ItemRecord:
    """不可变更新：返回新 ItemRecord 并替换。"""
    existing = items.get(key)
    if existing is None:
        record = ItemRecord(**changes)
    else:
        merged = {**asdict(existing), **changes}
        record = ItemRecord(**merged)
    items[key] = record
    return record
