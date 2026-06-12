"""动态边图：问答共现驱动的双链生长

核心理念（神经元模型）：
- 双链不预建，而是问答时"长出来"
- 一次全库问答，LLM 答案引用的几篇笔记 [来源N] = 真实使用的连接
- 这几篇之间建突触（边），同一对反复被问 → 权重++（突触增强）
- 边渲染到每篇笔记的「🔗 相关笔记」章节，按权重排序
- 没被任何问题连起来的笔记 = 孤立节点（大概率过时/没消化，这是 feature）
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from itertools import combinations
from pathlib import Path

from web.config import PIPELINE_DIR, VAULT_ROOT


GRAPH_FILE = PIPELINE_DIR / "rag_index" / "link_graph.json"
SECTION_MARKER = "## 🔗 相关笔记"
SECTION_RE = re.compile(r"\n*##\s+🔗\s+相关笔记.*?(?=\n##\s|\Z)", re.DOTALL)
SEP = "|||"
MAX_Q_PER_EDGE = 5     # 每条边记录最近几个共现问题
MAX_LINKS_PER_NOTE = 12  # 一篇笔记最多渲染多少条关联（按权重取前 N）


def _key(a: str, b: str) -> str:
    return SEP.join(sorted([a, b]))


def load() -> dict:
    if GRAPH_FILE.exists():
        try:
            return json.loads(GRAPH_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"edges": {}}


def save(g: dict) -> None:
    GRAPH_FILE.parent.mkdir(exist_ok=True)
    tmp = GRAPH_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(g, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(GRAPH_FILE)


def add_cooccurrence(paths: list[str], question: str) -> dict:
    """对一组共现笔记两两建边/加权。返回 {new_edges, strengthened, affected_paths}。"""
    uniq = sorted(set(p for p in paths if p))
    if len(uniq) < 2:
        return {"new_edges": [], "strengthened": [], "affected_paths": []}

    g = load()
    q = (question or "").strip()[:60]
    ts = datetime.now().isoformat(timespec="seconds")
    new_edges: list[tuple[str, str]] = []
    strengthened: list[tuple[str, str]] = []

    for a, b in combinations(uniq, 2):
        k = _key(a, b)
        e = g["edges"].get(k)
        if e is None:
            e = {"count": 0, "questions": [], "created": ts}
            new_edges.append((a, b))
        else:
            strengthened.append((a, b))
        e["count"] += 1
        e["last"] = ts
        if q:
            e["questions"] = [q] + [x for x in e.get("questions", []) if x != q]
            e["questions"] = e["questions"][:MAX_Q_PER_EDGE]
        g["edges"][k] = e

    save(g)
    return {
        "new_edges": new_edges,
        "strengthened": strengthened,
        "affected_paths": uniq,
    }


def neighbors(path: str) -> list[dict]:
    """返回某篇笔记的所有邻居，按权重降序。"""
    g = load()
    out: list[dict] = []
    for k, e in g["edges"].items():
        a, b = k.split(SEP)
        other = b if a == path else (a if b == path else None)
        if other is None:
            continue
        out.append({
            "path": other,
            "count": e.get("count", 1),
            "questions": e.get("questions", []),
        })
    out.sort(key=lambda x: -x["count"])
    return out


def _link_name(path: str) -> str:
    """双链名 = 文件名去 .md（Obsidian [[...]] 匹配文件名）。"""
    return Path(path).stem


def render_to_notes(paths: list[str]) -> None:
    """把这些笔记的「🔗 相关笔记」章节按当前边图重写。"""
    for p in paths:
        full = VAULT_ROOT / p
        if not full.exists():
            continue
        nbrs = neighbors(p)[:MAX_LINKS_PER_NOTE]
        text = full.read_text(encoding="utf-8")
        text = SECTION_RE.sub("", text).rstrip()

        if not nbrs:
            full.write_text(text + "\n", encoding="utf-8")
            continue

        lines = [f"\n\n{SECTION_MARKER}\n"]
        for n in nbrs:
            name = _link_name(n["path"])
            cnt = n["count"]
            weight = "●" * min(cnt, 5)  # 视觉权重
            q = n["questions"][0] if n["questions"] else ""
            ctx = f" · 「{q}」" if q else ""
            lines.append(f"- [[{name}]] `{weight}`{cnt}次{ctx}")
        full.write_text(text + "\n".join(lines) + "\n", encoding="utf-8")


def stats() -> dict:
    g = load()
    edges = g["edges"]
    nodes = set()
    for k in edges:
        a, b = k.split(SEP)
        nodes.add(a)
        nodes.add(b)
    total_weight = sum(e.get("count", 1) for e in edges.values())
    return {
        "edges": len(edges),
        "connected_notes": len(nodes),
        "total_cooccurrence": total_weight,
    }


def reset() -> None:
    """重置边图（从零开始）。"""
    save({"edges": {}})


def rename_note_path(old: str, new: str) -> int:
    """笔记被移动后，把边图里所有含 old 路径的边 key 改成 new（双链正文不动）。

    边 key 形如 a|||b（path 排序后拼接）。移动只换路径不换文件名，
    故 [[文件名]] 正文双链与邻居「🔗 相关笔记」章节都不受影响，只需同步路径键。
    返回受影响的边数。与已有 new 边冲突时合并（count 相加、questions 去重合并）。
    """
    if not old or not new or old == new:
        return 0
    g = load()
    edges = g.get("edges", {})
    affected = 0
    new_edges: dict = {}
    for k, e in edges.items():
        a, b = k.split(SEP)
        if a == old:
            a = new
        if b == old:
            b = new
        nk = _key(a, b)
        if nk != k:
            affected += 1
        if nk in new_edges:
            # 合并到已存在的边
            ex = new_edges[nk]
            ex["count"] = ex.get("count", 1) + e.get("count", 1)
            merged_q = list(dict.fromkeys((ex.get("questions", []) + e.get("questions", []))))
            ex["questions"] = merged_q[:MAX_Q_PER_EDGE]
            ex["last"] = max(ex.get("last", ""), e.get("last", ""))
        else:
            new_edges[nk] = e
    if affected:
        g["edges"] = new_edges
        save(g)
    return affected


# ---------------- 双向清除某篇笔记的双链 ----------------

def _strip_link_line(rel_path: str, link_name: str) -> bool:
    """只在「🔗 相关笔记」章节里删掉指向 link_name 的整行（避免误删正文双链）。

    覆盖 linker.py（向量召回）写入但不在边图里的链接。命中并改动才回写。
    返回是否有改动。
    """
    full = VAULT_ROOT / rel_path
    if not full.exists():
        return False
    text = full.read_text(encoding="utf-8")
    m = SECTION_RE.search(text)
    if not m:
        return False

    section = m.group(0)
    needle_exact = f"[[{link_name}]]"
    needle_alias = f"[[{link_name}|"
    kept: list[str] = []
    removed = False
    for line in section.splitlines():
        if needle_exact in line or needle_alias in line:
            removed = True
            continue
        kept.append(line)
    if not removed:
        return False

    # 章节里除了 marker 标题外没有任何双链了 → 整段删掉
    has_link = any("[[" in ln for ln in kept)
    new_section = "" if not has_link else "\n".join(kept)
    new_text = (text[: m.start()] + new_section + text[m.end():]).rstrip() + "\n"
    full.write_text(new_text, encoding="utf-8")
    return True


def unlink_note(path: str) -> dict:
    """以 path 为中心，双向彻底清除其所有双链。

    1) 删边图里所有含 path 的边，重渲染邻居（自动去掉指回 path 的链接）
    2) 文本兜底扫描全 vault，删掉任何 🔗 章节里指向 path 的残链
    3) 删 path 自身的整个 🔗 章节
    """
    target_stem = Path(path).stem

    # 1) 边图：收集邻居 → 删边 → 重渲染邻居
    g = load()
    nbr_paths = [n["path"] for n in neighbors(path)]
    kept_edges = {k: e for k, e in g["edges"].items() if path not in k.split(SEP)}
    if len(kept_edges) != len(g["edges"]):
        g["edges"] = kept_edges
        save(g)
    if nbr_paths:
        render_to_notes(nbr_paths)

    # 2) 文本兜底：扫全 vault 删残链（含向量建链、或图中已不存在的旧邻居）
    scanned_removed: list[str] = []
    for full in VAULT_ROOT.rglob("*.md"):
        if any(part.startswith(".") for part in full.parts):
            continue
        rel = str(full.relative_to(VAULT_ROOT))
        if rel == path:
            continue
        if _strip_link_line(rel, target_stem):
            scanned_removed.append(rel)

    # 3) 清自己：整段删掉 🔗 章节
    self_full = VAULT_ROOT / path
    self_cleared = False
    if self_full.exists():
        text = self_full.read_text(encoding="utf-8")
        new_text = SECTION_RE.sub("", text).rstrip() + "\n"
        if new_text != text:
            self_full.write_text(new_text, encoding="utf-8")
            self_cleared = True

    return {
        "target": path,
        "self_cleared": self_cleared,
        "neighbors_cleared": nbr_paths,
        "scanned_removed": scanned_removed,
    }


# ---------------- 图谱导出（D3 力导向可视化） ----------------

_DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\s+")


def _graph_label(path: str) -> str:
    """节点显示名 = 文件名去日期前缀、去 .md（与前端文件树同款规则）。"""
    return _DATE_PREFIX_RE.sub("", Path(path).stem)


def _flatten_tree(nodes: list[dict]) -> list[str]:
    """递归把 files.list_tree() 的树展平成所有文件相对路径。"""
    out: list[str] = []
    for n in nodes:
        if n.get("is_dir"):
            out.extend(_flatten_tree(n.get("children", [])))
        else:
            out.append(n["path"])
    return out


def export_graph(scope: str = "connected") -> dict:
    """导出双链图给前端可视化。

    scope="connected"：只含 link_graph 里有边的节点（聚焦主图）。
    scope="all"：含全 vault 可见笔记，无边的为孤立散点（degree=0）。
    返回 {nodes:[{id,label,group,degree}], edges:[{source,target,weight}], scope, stats}。
    """
    g = load()
    edges_raw = g.get("edges", {})

    degree: dict[str, int] = {}
    edges: list[dict] = []
    for k, e in edges_raw.items():
        a, b = k.split(SEP)
        edges.append({"source": a, "target": b, "weight": e.get("count", 1)})
        degree[a] = degree.get(a, 0) + 1
        degree[b] = degree.get(b, 0) + 1

    if scope == "all":
        from web import files
        node_ids = set(_flatten_tree(files.list_tree()))
        node_ids.update(degree.keys())  # 兜底：边里出现但树没列到的也算上
    else:
        node_ids = set(degree.keys())

    nodes = [
        {
            "id": p,
            "label": _graph_label(p),
            "group": p.split("/", 1)[0] if "/" in p else "",
            "degree": degree.get(p, 0),
        }
        for p in sorted(node_ids)
    ]
    # 只保留两端都在节点集内的边
    edges = [e for e in edges if e["source"] in node_ids and e["target"] in node_ids]

    return {
        "nodes": nodes,
        "edges": edges,
        "scope": scope,
        "stats": {"nodes": len(nodes), "edges": len(edges), "connected": len(degree)},
    }
