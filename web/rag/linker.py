"""自动建双链：基于 RAG 向量找语义相关笔记，写 ## 🔗 相关笔记 章节

笔记级向量 = 该笔记所有 chunk 向量的均值。
对每篇笔记找 top-k 最相似的其他笔记（相似度 > 阈值），
LLM 为每条生成一句话关联理由，幂等写入笔记末尾。
"""
from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import httpx
import numpy as np
from openai import OpenAI

from web.config import PIPELINE_CONFIG, VAULT_ROOT
from web.rag import index


SECTION_MARKER = "## 🔗 相关笔记"
# 匹配整个 🔗 相关笔记 章节（到下一个 ## 或文件尾）
SECTION_RE = re.compile(r"\n*##\s+🔗\s+相关笔记.*?(?=\n##\s|\Z)", re.DOTALL)


@dataclass(frozen=True)
class Related:
    note_path: str
    link_name: str   # 双链用的文件名（不含 .md）
    title: str       # H1 标题
    score: float
    reason: str = ""


def build_note_vectors() -> tuple[dict[str, np.ndarray], dict[str, str]]:
    """从 chunk 索引聚合出笔记级向量（chunk 均值并归一化）。"""
    vecs, chunks = index.load_index()
    if vecs.shape[0] == 0:
        return {}, {}

    note_idxs: dict[str, list[int]] = defaultdict(list)
    note_titles: dict[str, str] = {}
    for i, c in enumerate(chunks):
        note_idxs[c["note_path"]].append(i)
        note_titles.setdefault(c["note_path"], c["note_title"])

    note_vecs: dict[str, np.ndarray] = {}
    for path, idxs in note_idxs.items():
        v = vecs[idxs].mean(axis=0)
        n = np.linalg.norm(v)
        if n > 0:
            v = v / n
        note_vecs[path] = v
    return note_vecs, note_titles


def find_related(
    target_path: str,
    note_vecs: dict[str, np.ndarray],
    note_titles: dict[str, str],
    *,
    top_k: int = 5,
    threshold: float = 0.45,
) -> list[Related]:
    if target_path not in note_vecs:
        return []
    target = note_vecs[target_path]
    scored: list[Related] = []
    for path, v in note_vecs.items():
        if path == target_path:
            continue
        s = float(target @ v)
        if s >= threshold:
            scored.append(
                Related(
                    note_path=path,
                    link_name=Path(path).stem,
                    title=note_titles.get(path, Path(path).stem),
                    score=round(s, 3),
                )
            )
    scored.sort(key=lambda r: r.score, reverse=True)
    return scored[:top_k]


def _llm_client() -> tuple[OpenAI, dict]:
    llm_cfg = PIPELINE_CONFIG["llm"]
    api_key = os.environ[llm_cfg["api_key_env"]]
    http_client = None
    if llm_cfg.get("bypass_system_proxy", False):
        http_client = httpx.Client(trust_env=False, mounts={"all://": None}, timeout=llm_cfg.get("timeout_seconds", 120))
    client = OpenAI(base_url=llm_cfg["base_url"], api_key=api_key, http_client=http_client, timeout=llm_cfg.get("timeout_seconds", 120))
    return client, llm_cfg


def _note_brief(rel_path: str, max_chars: int = 300) -> str:
    """取笔记 H1 + 一句话理解 / 开头，给 LLM 做关联判断。"""
    text = (VAULT_ROOT / rel_path).read_text(encoding="utf-8")
    m = re.match(r"^---\n.*?\n---\n(.*)$", text, re.DOTALL)
    body = m.group(1) if m else text
    return body[:max_chars].strip()


_LINE_RE = re.compile(r"^\s*(\d+)\s+(keep|skip)\b\s*(.*)$", re.IGNORECASE)


def _parse_judgement(text: str) -> dict[int, tuple[bool, str]]:
    """解析行格式判定：'序号 keep/skip 理由'。比 JSON 鲁棒得多。"""
    result: dict[int, tuple[bool, str]] = {}
    for line in (text or "").splitlines():
        m = _LINE_RE.match(line)
        if not m:
            continue
        idx = int(m.group(1))
        keep = m.group(2).lower() == "keep"
        reason = m.group(3).strip().lstrip("：:").strip()
        result[idx] = (keep, reason)
    return result


def generate_reasons(
    target_path: str, target_title: str, candidates: list[Related], *, client, llm_cfg
) -> list[Related]:
    """LLM 判断每条候选是否有实质关联：过滤无关的，对保留的生成理由。

    向量召回会带回"表面相似但实质无关"的候选（vault 稀疏时尤甚），
    让 LLM 当过滤网。用行格式而非 JSON 输出，规避 reasoning 模型 JSON 不稳定的问题。
    """
    if not candidates:
        return []
    brief = _note_brief(target_path)
    cand_lines = "\n".join(f"{i+1}. {c.title}" for i, c in enumerate(candidates))
    prompt = (
        f"目标笔记：《{target_title}》\n"
        f"内容摘要：{brief}\n\n"
        f"候选相关笔记：\n{cand_lines}\n\n"
        "逐条判断每个候选是否和目标笔记有**实质关联**（同类可对比 / 上下游互补 / "
        "同一技术领域 / 解决相似问题）。仅仅\"都是开源\"\"都是 AI\"这种泛共性**不算**，判 skip。\n\n"
        "输出格式：每个候选一行，格式为 `序号 keep 理由` 或 `序号 skip`。\n"
        "理由 20 字内。不要输出任何其他内容。示例：\n"
        "1 keep 同类多智能体工具可对比\n"
        "2 skip\n"
        "3 keep 上下游数据互补"
    )

    judgement: dict[int, tuple[bool, str]] = {}
    for _ in range(2):  # 偶发解析不全则重试一次
        try:
            resp = client.chat.completions.create(
                model=llm_cfg["model"],
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=600,
            )
            judgement = _parse_judgement(resp.choices[0].message.content or "")
            if judgement:  # 至少解析到一条就接受
                break
        except Exception:
            judgement = {}

    # 完全解析失败：降级保留全部（无理由），别让笔记孤立
    if not judgement:
        return candidates

    out: list[Related] = []
    for i, c in enumerate(candidates, 1):
        if i not in judgement:
            out.append(c)  # 没判到的保留
            continue
        keep, reason = judgement[i]
        if keep:
            out.append(
                Related(
                    note_path=c.note_path, link_name=c.link_name, title=c.title,
                    score=c.score, reason=reason,
                )
            )
        # skip → 丢弃
    return out


def write_related_section(rel_path: str, related: list[Related]) -> None:
    """幂等写入：先删旧 🔗 章节，再追加新的。"""
    full = VAULT_ROOT / rel_path
    text = full.read_text(encoding="utf-8")
    # 删除已有的 🔗 相关笔记 章节
    text = SECTION_RE.sub("", text).rstrip()

    if not related:
        full.write_text(text + "\n", encoding="utf-8")
        return

    lines = [f"\n\n{SECTION_MARKER}\n"]
    for r in related:
        reason = f" — {r.reason}" if r.reason else ""
        lines.append(f"- [[{r.link_name}]]{reason}")
    full.write_text(text + "".join(s if s.startswith("\n") else "\n" + s for s in lines) + "\n", encoding="utf-8")


def link_all(
    *,
    top_k: int = 5,
    threshold: float = 0.45,
    with_reasons: bool = True,
    progress: Optional[Callable[[str], None]] = None,
) -> dict:
    """主入口：给所有已索引笔记建双链。返回统计。"""
    def log(m: str):
        if progress:
            progress(m)

    note_vecs, note_titles = build_note_vectors()
    if not note_vecs:
        log("索引为空，请先重建 RAG 索引")
        return {"notes": 0, "links": 0}

    client = llm_cfg = None
    if with_reasons:
        client, llm_cfg = _llm_client()

    total_links = 0
    paths = sorted(note_vecs.keys())
    for idx, path in enumerate(paths, 1):
        related = find_related(path, note_vecs, note_titles, top_k=top_k, threshold=threshold)
        if with_reasons and related:
            related = generate_reasons(path, note_titles[path], related, client=client, llm_cfg=llm_cfg)
        write_related_section(path, related)
        total_links += len(related)
        log(f"[{idx}/{len(paths)}] {note_titles[path][:30]} → {len(related)} 条关联")

    # 建双链只改了不参与 embedding 的 🔗 章节，刷新 manifest 避免下次启动白白重索引
    try:
        index.touch_manifest(paths)
    except Exception:
        pass

    stats = {"notes": len(paths), "links": total_links}
    log(f"完成：{stats}")
    return stats
