"""向量索引：构建/增量更新/加载。用 numpy 存储 + 暴力余弦检索。"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from web.config import PIPELINE_DIR, VAULT_ROOT, PIPELINE_CONFIG
from web.rag import embedder
from web.rag.chunker import Chunk, chunk_note


INDEX_DIR = PIPELINE_DIR / "rag_index"
VECTORS_FILE = INDEX_DIR / "vectors.npy"
CHUNKS_FILE = INDEX_DIR / "chunks.json"
MANIFEST_FILE = INDEX_DIR / "manifest.json"  # note_path -> mtime，用于增量

_emb_cfg = PIPELINE_CONFIG["embedding"]


def _iter_note_paths() -> list[str]:
    """扫描配置的 index_dirs 下所有 .md 文件。"""
    paths: list[str] = []
    for d in _emb_cfg.get("index_dirs", []):
        base = VAULT_ROOT / d
        if not base.exists():
            continue
        for p in sorted(base.rglob("*.md")):
            if any(part.startswith(".") for part in p.parts):
                continue
            paths.append(str(p.relative_to(VAULT_ROOT)))
    return paths


def _load_manifest() -> dict:
    if MANIFEST_FILE.exists():
        return json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
    return {}


def _mtime(rel_path: str) -> float:
    return (VAULT_ROOT / rel_path).stat().st_mtime


def build_index(
    *, force: bool = False, progress: Optional[Callable[[str], None]] = None
) -> dict:
    """构建或增量更新索引。

    force=True 全量重建；否则只对 mtime 变化/新增的笔记重新 embedding。
    返回统计 dict。
    """
    def log(msg: str):
        if progress:
            progress(msg)

    INDEX_DIR.mkdir(exist_ok=True)
    note_paths = _iter_note_paths()
    manifest = {} if force else _load_manifest()

    # 加载已有索引
    existing_chunks: list[dict] = []
    existing_vecs: Optional[np.ndarray] = None
    if not force and VECTORS_FILE.exists() and CHUNKS_FILE.exists():
        existing_vecs = np.load(VECTORS_FILE)
        existing_chunks = json.loads(CHUNKS_FILE.read_text(encoding="utf-8"))

    # 判断哪些笔记需要重新索引
    current_set = set(note_paths)
    changed: list[str] = []
    for rp in note_paths:
        mt = _mtime(rp)
        if manifest.get(rp) != mt:
            changed.append(rp)
    deleted = [rp for rp in manifest.keys() if rp not in current_set]

    log(f"共 {len(note_paths)} 篇笔记，{len(changed)} 篇需要(重新)索引，{len(deleted)} 篇已删除")

    # 保留未变化的 chunk
    keep_chunks: list[dict] = []
    keep_vecs_idx: list[int] = []
    changed_set = set(changed)
    deleted_set = set(deleted)
    chunk_to_vecidx = {c["chunk_id"]: i for i, c in enumerate(existing_chunks)}
    for i, c in enumerate(existing_chunks):
        if c["note_path"] in changed_set or c["note_path"] in deleted_set:
            continue
        keep_chunks.append(c)
        keep_vecs_idx.append(i)

    keep_vecs = (
        existing_vecs[keep_vecs_idx]
        if existing_vecs is not None and keep_vecs_idx
        else np.zeros((0, embedder.dimension()), dtype=np.float32)
    )

    # 对变化的笔记切块 + embedding
    new_chunks: list[Chunk] = []
    for rp in changed:
        try:
            new_chunks.extend(chunk_note(rp, max_chars=_emb_cfg.get("chunk_max_chars", 800)))
        except Exception as e:
            log(f"⚠️ 切块失败 {rp}: {e}")

    new_vecs = np.zeros((0, embedder.dimension()), dtype=np.float32)
    if new_chunks:
        log(f"对 {len(new_chunks)} 个新块求 embedding...")
        texts = [c.text for c in new_chunks]

        done_counter = {"n": 0}
        def emb_progress(done: int, total: int):
            if done - done_counter["n"] >= 20 or done == total:
                done_counter["n"] = done
                log(f"  embedding {done}/{total}")

        vecs_list = embedder.embed_many(
            texts, concurrency=_emb_cfg.get("concurrency", 8), progress=emb_progress
        )
        new_vecs = np.array(vecs_list, dtype=np.float32)

    # 合并
    all_chunks = keep_chunks + [asdict(c) for c in new_chunks]
    if keep_vecs.shape[0] and new_vecs.shape[0]:
        all_vecs = np.vstack([keep_vecs, new_vecs])
    elif new_vecs.shape[0]:
        all_vecs = new_vecs
    else:
        all_vecs = keep_vecs

    # L2 归一化（便于用点积算余弦）
    if all_vecs.shape[0]:
        norms = np.linalg.norm(all_vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        all_vecs = all_vecs / norms

    # 持久化
    np.save(VECTORS_FILE, all_vecs.astype(np.float32))
    CHUNKS_FILE.write_text(
        json.dumps(all_chunks, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    new_manifest = {rp: _mtime(rp) for rp in note_paths}
    MANIFEST_FILE.write_text(
        json.dumps(new_manifest, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    stats = {
        "notes_total": len(note_paths),
        "notes_reindexed": len(changed),
        "notes_deleted": len(deleted),
        "chunks_total": len(all_chunks),
        "chunks_new": len(new_chunks),
        "dimension": all_vecs.shape[1] if all_vecs.shape[0] else 0,
    }
    log(f"索引完成: {stats}")
    return stats


def load_index() -> tuple[np.ndarray, list[dict]]:
    """加载索引，返回 (归一化向量矩阵, chunk 元数据列表)。"""
    if not VECTORS_FILE.exists() or not CHUNKS_FILE.exists():
        return np.zeros((0, embedder.dimension()), dtype=np.float32), []
    vecs = np.load(VECTORS_FILE)
    chunks = json.loads(CHUNKS_FILE.read_text(encoding="utf-8"))
    return vecs, chunks


def index_exists() -> bool:
    return VECTORS_FILE.exists() and CHUNKS_FILE.exists()


def touch_manifest(paths: list[str]) -> None:
    """把指定笔记的当前 mtime 写回 manifest。
    建双链只改了不参与 embedding 的 🔗 章节，向量不变，
    用这个避免下次启动把它们当"已变更"白白重索引。
    """
    manifest = _load_manifest()
    for rp in paths:
        full = VAULT_ROOT / rp
        if full.exists():
            manifest[rp] = full.stat().st_mtime
    MANIFEST_FILE.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8"
    )
