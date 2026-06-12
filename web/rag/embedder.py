"""豆包多模态 embedding 封装：单条调用 + 线程池并发"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional

import httpx

from web.config import PIPELINE_CONFIG


_emb_cfg = PIPELINE_CONFIG["embedding"]


def _make_client() -> httpx.Client:
    kwargs = {"timeout": 60}
    if _emb_cfg.get("bypass_system_proxy", False):
        kwargs.update({"trust_env": False, "mounts": {"all://": None}})
    return httpx.Client(**kwargs)


def embed_one(text: str, client: Optional[httpx.Client] = None) -> list[float]:
    """对单条文本求 embedding 向量。"""
    own = client is None
    if own:
        client = _make_client()
    try:
        api_key = os.environ[_emb_cfg["api_key_env"]]
        r = client.post(
            _emb_cfg["url"],
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": _emb_cfg["model"],
                "input": [{"type": "text", "text": text}],
            },
        )
        r.raise_for_status()
        data = r.json()
        emb = data.get("data", {}).get("embedding")
        if not emb:
            raise RuntimeError(f"embedding 响应无向量: {str(data)[:200]}")
        return emb
    finally:
        if own:
            client.close()


def embed_many(
    texts: list[str],
    *,
    concurrency: Optional[int] = None,
    progress: Optional[Callable[[int, int], None]] = None,
) -> list[list[float]]:
    """并发对多条文本求 embedding，保持顺序返回。

    progress(done, total) 回调用于报告进度。
    """
    n = len(texts)
    if n == 0:
        return []
    conc = concurrency or _emb_cfg.get("concurrency", 8)
    results: list[Optional[list[float]]] = [None] * n

    client = _make_client()
    done = 0
    try:
        with ThreadPoolExecutor(max_workers=conc) as pool:
            future_to_idx = {
                pool.submit(embed_one, txt, client): i for i, txt in enumerate(texts)
            }
            for fut in as_completed(future_to_idx):
                idx = future_to_idx[fut]
                results[idx] = fut.result()  # 失败会抛，索引流程负责捕获
                done += 1
                if progress:
                    progress(done, n)
    finally:
        client.close()

    return [r for r in results if r is not None]  # 顺序已由 results 列表保证


def dimension() -> int:
    return _emb_cfg.get("dimension", 2048)
