"""检索：问题 → top-k 相关笔记块（numpy 余弦相似度暴力检索）"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from web.rag import embedder, index


@dataclass(frozen=True)
class Hit:
    score: float
    note_path: str
    note_title: str
    section: str
    text: str
    source_type: str = "vector"  # "vector" | "graph_neighbor"


def search(query: str, top_k: int = 8) -> list[Hit]:
    """对查询求 embedding，跟索引算余弦相似度，返回 top-k。"""
    vecs, chunks = index.load_index()
    if vecs.shape[0] == 0:
        return []

    q_vec = np.array(embedder.embed_one(query), dtype=np.float32)
    q_norm = np.linalg.norm(q_vec)
    if q_norm == 0:
        return []
    q_vec = q_vec / q_norm

    # 索引向量已归一化，点积即余弦相似度
    sims = vecs @ q_vec  # (N,)
    k = min(top_k, len(chunks))
    top_idx = np.argpartition(-sims, k - 1)[:k]
    top_idx = top_idx[np.argsort(-sims[top_idx])]

    hits: list[Hit] = []
    for i in top_idx:
        c = chunks[int(i)]
        hits.append(
            Hit(
                score=float(sims[int(i)]),
                note_path=c["note_path"],
                note_title=c["note_title"],
                section=c.get("section", ""),
                text=c["text"],
            )
        )
    return hits
