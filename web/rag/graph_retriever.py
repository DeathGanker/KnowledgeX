"""图增强检索：在向量检索之上叠加 graph neighbor expansion。

不替代向量检索，而是对向量命中的每篇笔记沿图谱边扩充权重最高的邻居，
取邻居笔记中与查询最相关的片段，合并到检索上下文——让 LLM 看到更广的知识网络。

LightRAG 思路的最小可行版：1-hop neighbor + chunk 级相似度筛选。
"""
from __future__ import annotations

import numpy as np

from web.rag import embedder, graph, index, retriever


def expand_neighbors(
    hits: list[retriever.Hit],
    query_text: str,
    *,
    neighbor_top: int = 3,
) -> list[retriever.Hit]:
    """对向量检索命中的笔记，沿图谱边拉权重最高的邻居，返回补充 Hit。

    去重：邻居 path 若已在向量命中 note_paths 里则跳过。
    片段选择：对邻居笔记的所有 chunk 与 query 算余弦相似度，取得分最高的 chunk。
    score 用邻居边权重归一化（count / max_count）作为置信度标记。
    """
    if not hits:
        return []

    # 向量命中的 note_path 集合（去重用）
    seen_paths: set[str] = {h.note_path for h in hits}

    # 加载索引（用于邻居笔记 chunk 选择）
    vecs, chunks = index.load_index()
    if vecs.shape[0] == 0:
        return []

    # 查询向量
    q_vec = np.array(embedder.embed_one(query_text), dtype=np.float32)
    q_norm = np.linalg.norm(q_vec)
    if q_norm == 0:
        return []
    q_vec = q_vec / q_norm

    # 构建 note_path → chunk indices 映射
    note_chunk_idxs: dict[str, list[int]] = {}
    for i, c in enumerate(chunks):
        note_chunk_idxs.setdefault(c["note_path"], []).append(i)

    # 收集所有邻居 path → 边权重（去重汇总，取最大 count）
    neighbor_weights: dict[str, int] = {}
    for h in hits:
        for nb in graph.neighbors(h.note_path)[:neighbor_top]:
            if nb["path"] in seen_paths:
                continue
            cur = neighbor_weights.get(nb["path"], 0)
            neighbor_weights[nb["path"]] = max(cur, nb.get("count", 1))

    if not neighbor_weights:
        return []

    # 归一化边权重
    max_w = max(neighbor_weights.values())

    # 对每个邻居笔记，从其 chunk 中选与 query 最相关的片段
    out: list[retriever.Hit] = []
    for nb_path, weight in neighbor_weights.items():
        idxs = note_chunk_idxs.get(nb_path)
        if not idxs:
            continue
        # 只算该笔记的 chunk 相似度
        nb_vecs = vecs[idxs]  # (k, dim)
        sims = nb_vecs @ q_vec  # (k,)
        best_local = int(np.argmax(sims))
        best_global_idx = idxs[best_local]
        c = chunks[best_global_idx]

        # score = chunk 相似度 * (边权重归一化) —— 让图信号参与排序
        norm_weight = weight / max_w if max_w > 0 else 1.0
        combined_score = float(sims[best_local]) * (0.5 + 0.5 * norm_weight)

        out.append(
            retriever.Hit(
                score=round(combined_score, 3),
                note_path=c["note_path"],
                note_title=c["note_title"],
                section=c.get("section", ""),
                text=c["text"],
            )
        )

    # 按组合得分降序
    out.sort(key=lambda x: -x.score)
    return out
