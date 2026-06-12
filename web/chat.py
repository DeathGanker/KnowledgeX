"""问答核心：豆包 LLM + DeepWiki MCP function calling + SSE 流式输出

模式：
  - note     ：仅基于当前笔记内容回答（不调任何工具）
  - deepwiki ：强制调 DeepWiki 工具（要求笔记有 github source）
  - auto     ：默认，LLM 自主决定是否调 DeepWiki
"""
from __future__ import annotations

import json
import os
import re
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Generator, Optional

import httpx
from openai import OpenAI

from web import files
from web.config import PIPELINE_CONFIG, llm_extra_body
from web.rag import graph, graph_retriever, retriever
from web.rag import index as rag_index
from scripts.persona import (
    render_persona,
    render_taxonomy,
    load_taxonomy,
    taxonomy_prefixes,
    taxonomy_default,
)


# 回答里的引用标注：[来源N] / [来源 N] / 来源N
CITATION_RE = re.compile(r"来源\s*(\d+)")


MODE_NOTE = "note"
MODE_DEEPWIKI = "deepwiki"
MODE_AUTO = "auto"

MAX_TOOL_ROUNDS = 6  # 最多 N 轮，最后一轮强制 tool_choice=none 收尾


# ----------------------- DeepWiki MCP HTTP client -----------------------

class DeepWikiClient:
    """MCP Streamable HTTP 调用，无 session 无 auth。"""

    def __init__(self, endpoint: Optional[str] = None, timeout: Optional[int] = None):
        cfg = PIPELINE_CONFIG.get("deepwiki", {}) or {}
        self.endpoint = endpoint or cfg.get("endpoint", "https://mcp.deepwiki.com/mcp")
        self.timeout = timeout or cfg.get("timeout_seconds", 90)
        self._id = 0
        # ⚠️ 与国内 LLM 客户端相反：DeepWiki 是海外服务，需要「走代理」才能从国内访问，
        # 绝不能像 _llm_client 那样 trust_env=False 把代理关掉（那正是之前「没有通」的根因）。
        # 优先用 config.yaml 里 deepwiki.proxy；否则默认 trust_env=True，读取 https_proxy 等环境变量。
        proxy = cfg.get("proxy")
        if proxy:
            try:
                self._client = httpx.Client(proxy=proxy, trust_env=False, timeout=self.timeout)
            except TypeError:  # 老版本 httpx 用 proxies=
                self._client = httpx.Client(proxies=proxy, trust_env=False, timeout=self.timeout)
        else:
            self._client = httpx.Client(timeout=self.timeout)  # trust_env 默认 True，吃环境代理

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _post(self, method: str, params: Optional[dict] = None) -> dict:
        payload = {"jsonrpc": "2.0", "id": self._next_id(), "method": method}
        if params is not None:
            payload["params"] = params
        r = self._client.post(
            self.endpoint,
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        r.raise_for_status()
        # 响应总是 SSE 格式，解析第一个 data: 行
        for line in r.text.split("\n"):
            if line.startswith("data: "):
                obj = json.loads(line[6:])
                if "error" in obj:
                    raise RuntimeError(f"DeepWiki MCP 错误: {obj['error']}")
                return obj.get("result", {})
        raise RuntimeError(f"DeepWiki 响应格式异常: {r.text[:200]}")

    def call_tool(self, name: str, args: dict) -> str:
        """执行 MCP 工具，返回纯文本结果。"""
        result = self._post("tools/call", {"name": name, "arguments": args})
        # MCP 工具结果在 content 数组里，每项 {type: "text", text: "..."}
        parts = []
        for item in result.get("content", []):
            if item.get("type") == "text":
                parts.append(item.get("text", ""))
        return "\n".join(parts) if parts else json.dumps(result, ensure_ascii=False)


# ----------------------- 工具定义 -----------------------

DEEPWIKI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "deepwiki_ask",
            "description": (
                "对一个 GitHub 仓库提问，由 DeepWiki 基于源码层面的 wiki 给出 AI 回答。"
                "适合：架构、模块、设计、对比、特定函数实现等具体问题。"
                "repoName 格式必须是 owner/repo（如 'getzep/graphiti'）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "repoName": {
                        "type": "string",
                        "description": "GitHub 仓库 owner/repo",
                    },
                    "question": {
                        "type": "string",
                        "description": "中文问题",
                    },
                },
                "required": ["repoName", "question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "deepwiki_structure",
            "description": "获取 GitHub 仓库 wiki 的章节目录（先看有什么再深入）",
            "parameters": {
                "type": "object",
                "properties": {
                    "repoName": {"type": "string", "description": "owner/repo"},
                },
                "required": ["repoName"],
            },
        },
    },
]


# ----------------------- LLM 调用 + 流式 -----------------------

def _llm_client() -> tuple[OpenAI, dict]:
    llm_cfg = PIPELINE_CONFIG["llm"]
    api_key = os.environ[llm_cfg["api_key_env"]]
    http_client = None
    if llm_cfg.get("bypass_system_proxy", False):
        http_client = httpx.Client(
            trust_env=False, mounts={"all://": None}, timeout=llm_cfg.get("timeout_seconds", 120)
        )
    client = OpenAI(
        base_url=llm_cfg["base_url"], api_key=api_key, http_client=http_client,
        timeout=llm_cfg.get("timeout_seconds", 120),
    )
    return client, llm_cfg


def _sse(event: str, data: dict) -> str:
    """格式化一条 SSE 事件。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _build_system_prompt(note: dict, mode: str) -> str:
    """根据模式构造 system prompt。"""
    has_gh = bool(note.get("is_github"))
    role = (
        render_persona()
        + "\n\n你是上面这位用户的私人智囊。对用户的笔记内容做深度回答，"
        "用中文、有信息密度、不要客套话。"
    )

    if mode == MODE_NOTE:
        tool_hint = "本次回答只用笔记内容作为上下文，不要调用任何工具。"
    elif mode == MODE_DEEPWIKI:
        if not has_gh:
            tool_hint = "笔记不含 GitHub 链接，DeepWiki 不可用，请只用笔记内容回答。"
        else:
            tool_hint = (
                "本次必须使用 deepwiki_ask 工具调取源码层面信息再回答。"
                f"目标仓库：{_extract_repo(note.get('source', ''))}"
            )
    else:  # auto
        if has_gh:
            tool_hint = (
                "如果用户问题涉及具体源码、架构、模块实现，调用 deepwiki_ask 拿源码层面的内容再回答。"
                "如果问题就是笔记里能回答的，直接基于笔记回答。"
                f"该笔记关联仓库：{_extract_repo(note.get('source', ''))}"
            )
        else:
            tool_hint = "本笔记不是 GitHub 仓库，只用笔记内容回答。"

    note_block = (
        f"\n\n## 当前笔记内容\n\n标题: {note.get('name')}\n"
        f"路径: {note.get('path')}\n"
        f"frontmatter: {json.dumps(note.get('frontmatter', {}), ensure_ascii=False)}\n\n"
        f"---笔记正文---\n{note.get('body', '')}"
    )
    return f"{role}\n\n{tool_hint}{note_block}"


def _extract_repo(url: str) -> str:
    m = re.search(r"github\.com/([^/]+)/([^/#?]+)", url or "", re.IGNORECASE)
    if not m:
        return ""
    return f"{m.group(1)}/{m.group(2).rstrip('.git')}"


def stream_chat(question: str, note_path: str, mode: str) -> Generator[str, None, None]:
    """主入口：流式 SSE 输出。
    事件类型：
      start    : 开始
      delta    : LLM 文本增量
      tool_call: LLM 决定调工具
      tool_done: 工具返回
      error    : 出错
      end      : 全部完成
    """
    try:
        note = files.read_note(note_path)
    except Exception as e:
        yield _sse("error", {"message": f"读笔记失败: {e}"})
        yield _sse("end", {})
        return

    client, llm_cfg = _llm_client()
    deepwiki = DeepWikiClient()

    # 强制 deepwiki 模式但笔记不是 github 的情况：降级
    effective_mode = mode
    if mode == MODE_DEEPWIKI and not note.get("is_github"):
        effective_mode = MODE_NOTE
        yield _sse("info", {"message": "笔记不含 GitHub 链接，已降级为「仅笔记上下文」模式"})

    # tool_choice：none 强制不用工具；auto 让 LLM 决定；required 强制必须用
    tools_param: Optional[list] = None
    tool_choice = "none"
    if effective_mode == MODE_AUTO and note.get("is_github"):
        tools_param = DEEPWIKI_TOOLS
        tool_choice = "auto"
    elif effective_mode == MODE_DEEPWIKI:
        tools_param = DEEPWIKI_TOOLS
        tool_choice = "required"

    messages = [
        {"role": "system", "content": _build_system_prompt(note, effective_mode)},
        {"role": "user", "content": question},
    ]

    yield _sse("start", {"mode": effective_mode, "note": note.get("name")})

    for round_idx in range(MAX_TOOL_ROUNDS):
        # 最后一轮强制不让用工具，让 LLM 必须用现有上下文回答
        is_final_round = round_idx == MAX_TOOL_ROUNDS - 1
        round_tool_choice = "none" if is_final_round else (tool_choice if tools_param else None)
        round_tools = None if is_final_round else tools_param
        try:
            stream = client.chat.completions.create(
                model=llm_cfg["model"],
                extra_body=llm_extra_body(llm_cfg),
                messages=messages,
                tools=round_tools,
                tool_choice=round_tool_choice,
                temperature=llm_cfg.get("temperature", 0.3),
                max_tokens=llm_cfg.get("max_tokens", 4000),
                stream=True,
            )
        except Exception as e:
            yield _sse("error", {"message": f"LLM 调用失败: {e}"})
            yield _sse("end", {})
            return

        # 累积本轮的 content 和 tool_calls
        content_buf = ""
        tool_calls: dict[int, dict] = {}  # index -> {id, name, args_str}
        finish_reason = None

        for chunk in stream:
            choice = chunk.choices[0] if chunk.choices else None
            if not choice:
                continue
            delta = choice.delta

            # 文本增量
            if delta.content:
                content_buf += delta.content
                yield _sse("delta", {"text": delta.content})

            # 工具调用增量（流式拼装）
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    entry = tool_calls.setdefault(idx, {"id": "", "name": "", "args": ""})
                    if tc.id:
                        entry["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            entry["name"] = tc.function.name
                        if tc.function.arguments:
                            entry["args"] += tc.function.arguments

            if choice.finish_reason:
                finish_reason = choice.finish_reason

        # 这轮结束，看是否要调工具
        if finish_reason == "tool_calls" and tool_calls:
            # 把 LLM 的 assistant 消息加入 messages（带 tool_calls）
            assistant_msg = {
                "role": "assistant",
                "content": content_buf or None,
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {"name": tc["name"], "arguments": tc["args"]},
                    }
                    for tc in tool_calls.values()
                ],
            }
            messages.append(assistant_msg)

            # 执行每个工具
            for tc in tool_calls.values():
                yield _sse("tool_call", {"name": tc["name"], "args": tc["args"]})
                try:
                    args = json.loads(tc["args"] or "{}")
                    if tc["name"] == "deepwiki_ask":
                        result_text = deepwiki.call_tool("ask_question", args)
                    elif tc["name"] == "deepwiki_structure":
                        result_text = deepwiki.call_tool("read_wiki_structure", args)
                    else:
                        result_text = f"未知工具: {tc['name']}"
                    tool_failed = False
                except Exception as e:
                    result_text = f"工具调用失败: {e}"
                    tool_failed = True
                    # 让失败可见（多为海外 DeepWiki 的网络/代理问题），不要静默降级
                    yield _sse("error", {
                        "message": (
                            f"DeepWiki 调用失败：{e}。"
                            "DeepWiki 是海外服务，请确认代理可用——"
                            "在 config.yaml 的 deepwiki.proxy 填代理地址，或给后端设 https_proxy 环境变量。"
                        )
                    })

                yield _sse(
                    "tool_done",
                    {"name": tc["name"], "ok": not tool_failed, "result_preview": result_text[:300]},
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result_text,
                    }
                )

            # 工具调用完，下一轮不再强制 required（避免无限循环）
            tool_choice = "auto"
            continue

        # 正常结束
        yield _sse("end", {"finish_reason": finish_reason or "stop"})
        return

    # 理论上 final_round 强制 none 应该会 stop，到这里说明仍在 tool_calls，强行收尾
    yield _sse("end", {"finish_reason": "max_rounds"})


# ----------------------- 全库 RAG 问答 -----------------------

def _build_rag_system() -> str:
    """全库 RAG 问答的 system prompt：注入单一来源画像 + 检索回答要求。"""
    return (
        render_persona()
        + "\n\n你是上面这位用户的私人知识库智囊。下面给你从用户知识库中语义检索到的若干笔记片段，"
        "请基于这些片段回答用户的问题。\n\n"
        "要求：\n"
        "- 只基于提供的片段回答，不要编造片段里没有的内容\n"
        "- 用中文、信息密度高、不要客套话\n"
        "- 综合多篇笔记给出有洞察的回答（对比、归纳、关联），不要简单罗列\n"
        "- 在回答中用 [来源N] 的方式标注引用了哪几篇笔记（N 是下方每篇笔记的编号）\n"
        "- 如果检索到的内容不足以回答，诚实说明\"知识库里关于这个问题的内容不足\"，并说说缺什么\n"
    )


def stream_rag_chat(
    question: str, top_k: int = 8, history: list[dict] | None = None,
    graph_expand: bool = True,
) -> Generator[str, None, None]:
    """全库 RAG 问答：惰性切片 → 检索 → 图邻居扩展 → 流式回答 → 解析引用建突触。"""
    yield _sse("start", {"mode": "rag"})

    # 0. 惰性自动切片：搭便车，问答前确保改动过的笔记已重新索引（无变化时秒级）
    try:
        st = rag_index.build_index(force=False)
        if st.get("chunks_new", 0) or st.get("notes_reindexed", 0):
            yield _sse("info", {"message": f"自动索引：{st['notes_reindexed']} 篇更新"})
    except Exception:
        pass  # 索引失败不阻塞问答

    # 1. 向量检索
    try:
        hits = retriever.search(question, top_k=top_k)
    except Exception as e:
        yield _sse("error", {"message": f"检索失败: {e}"})
        yield _sse("end", {})
        return

    if not hits:
        yield _sse("error", {"message": "知识库索引为空，请先重建索引"})
        yield _sse("end", {})
        return

    # 1.5 图邻居扩展：在向量命中之上沿图谱边扩充邻居笔记
    graph_hits: list = []
    if graph_expand:
        try:
            graph_hits = graph_retriever.expand_neighbors(hits, question)
        except Exception:
            pass  # 图扩展失败不阻塞问答

    # 合并：向量命中 + 图邻居命中，图邻居补在末尾
    all_hits = hits + graph_hits

    # 2. 按笔记聚合（保持检索顺序，note_path 首次出现定义笔记编号 n=1..M）
    #    —— 编号笔记级，让回答里的 [来源N] 与前端来源卡片一一对应
    note_groups: list[dict] = []
    by_path: dict[str, dict] = {}
    for h in all_hits:
        g = by_path.get(h.note_path)
        if g is None:
            g = {
                "n": len(note_groups) + 1,
                "note_path": h.note_path,
                "note_title": h.note_title,
                "score": round(h.score, 3),
                "source_type": h.source_type,
                "chunks": [],
            }
            by_path[h.note_path] = g
            note_groups.append(g)
        else:
            # 同一笔记多个 hit，source_type 取最优（vector > graph_neighbor）
            if h.source_type == "vector":
                g["source_type"] = "vector"
        g["chunks"].append({
            "section": h.section,
            "score": round(h.score, 3),
            "text": h.text,
        })

    # 推送来源（笔记级、带编号 + source_type 区分向量/图邻居）+ 召回明细
    yield _sse("sources", {"hits": [
        {"n": g["n"], "note_path": g["note_path"], "note_title": g["note_title"],
         "score": g["score"], "chunk_count": len(g["chunks"]),
         "source_type": g.get("source_type", "vector")}
        for g in note_groups
    ]})
    yield _sse("recall", {"notes": [
        {"n": g["n"], "note_path": g["note_path"], "note_title": g["note_title"],
         "source_type": g.get("source_type", "vector"),
         "chunks": [{"section": c["section"], "score": c["score"], "text_preview": c["text"][:240]}
                    for c in g["chunks"]]}
        for g in note_groups
    ]})

    # 3. 拼 context（笔记级：每篇一个 [来源n]，合并该笔记命中的片段）
    context_blocks = []
    for g in note_groups:
        parts = []
        for c in g["chunks"]:
            seg = (f"（{c['section']}）" if c["section"] else "") + c["text"]
            parts.append(seg)
        context_blocks.append(f"【来源{g['n']}: {g['note_title']}】\n" + "\n\n".join(parts))
    context = "\n\n".join(context_blocks)

    messages = [{"role": "system", "content": _build_rag_system()}]
    # 多轮上下文：带上最近几轮对话（最多 4 轮，避免 token 膨胀）
    for h in (history or [])[-8:]:
        role = h.get("role")
        content = (h.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append(
        {"role": "user", "content": f"知识库检索到的片段：\n\n{context}\n\n---\n\n我的问题：{question}"}
    )

    # 4. LLM 流式
    client, llm_cfg = _llm_client()
    try:
        stream = client.chat.completions.create(
            model=llm_cfg["model"],
            extra_body=llm_extra_body(llm_cfg),
            messages=messages,
            temperature=llm_cfg.get("temperature", 0.3),
            max_tokens=llm_cfg.get("max_tokens", 4000),
            stream=True,
        )
    except Exception as e:
        yield _sse("error", {"message": f"LLM 调用失败: {e}"})
        yield _sse("end", {})
        return

    answer_buf = ""
    for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta.content:
            answer_buf += delta.content
            yield _sse("delta", {"text": delta.content})

    # 5. 解析回答里真正引用的 [来源N]（N 是笔记编号）→ 这几篇之间长突触
    cited_ns: list[int] = []
    try:
        cited_idx = sorted({int(n) for n in CITATION_RE.findall(answer_buf)})
        cited_paths: list[str] = []
        for i in cited_idx:
            if 1 <= i <= len(note_groups):
                cited_ns.append(i)
                cited_paths.append(note_groups[i - 1]["note_path"])

        if len(cited_paths) >= 2:
            result = graph.add_cooccurrence(cited_paths, question)
            graph.render_to_notes(result["affected_paths"])
            # 告诉前端长了哪些突触
            def _name(p):
                from pathlib import Path
                return Path(p).stem
            yield _sse("links", {
                "new": [[_name(a), _name(b)] for a, b in result["new_edges"]],
                "strengthened": [[_name(a), _name(b)] for a, b in result["strengthened"]],
            })
    except Exception:
        pass  # 建链失败不影响问答结果

    # cited：被回答真正引用的笔记编号，前端据此高亮来源卡片
    yield _sse("end", {"finish_reason": "stop", "cited": cited_ns})


# ----------------------- 画像 AI 引导式提炼 -----------------------

_PROFILE_DRAFT_SYSTEM = """你是一位用户画像分析师。用户通过引导式问答提供了关于自己的零散、口语化的回答，\
你要把它们提炼成一份规范的「用户画像」，用于个性化一个 AI 知识管理助手（决定消化笔记和问答时写什么、不写什么）。

请输出**严格的 JSON**（不要 markdown 代码块包裹），字段如下：
{
  "role": "一句话角色定位（职业/方向）",
  "working_style": "一句话工作方式",
  "cares_about": ["关心的点", "..."],
  "interests": ["兴趣", "..."],
  "dislikes": ["不想看/反感的内容类型", "..."]
}

要求：
- 基于用户回答提炼、合理润色补全，但不要凭空编造与用户回答无关的内容
- 列表字段每项简短（2-8 字），3-6 项为宜
- 全部用中文
- 只输出 JSON，不要任何解释文字"""


def stream_profile_draft(answers: dict) -> Generator[str, None, None]:
    """把引导各维度的零散回答提炼成规范 persona JSON，SSE 流式输出。

    事件：start → delta（提炼过程文本）→ profile（结构化结果）→ end；出错发 error。
    """
    yield _sse("start", {"mode": "profile_draft"})

    # 把各维度回答拼成给 LLM 的输入
    labels = {
        "role": "职业 / 角色方向",
        "working_style": "工作方式",
        "cares_about": "关心什么",
        "interests": "兴趣",
        "dislikes": "不想看什么",
    }
    lines = []
    for k, label in labels.items():
        v = answers.get(k)
        if isinstance(v, (list, tuple)):
            v = "、".join(str(x) for x in v if str(x).strip())
        v = (str(v or "")).strip()
        if v:
            lines.append(f"- {label}：{v}")
    user_input = "用户的回答：\n" + ("\n".join(lines) if lines else "（用户没填，请给一个通用知识工作者的合理默认画像）")

    client, llm_cfg = _llm_client()
    messages = [
        {"role": "system", "content": _PROFILE_DRAFT_SYSTEM},
        {"role": "user", "content": user_input},
    ]

    try:
        stream = client.chat.completions.create(
            model=llm_cfg["model"],
            extra_body=llm_extra_body(llm_cfg),
            messages=messages,
            temperature=0.4,
            max_tokens=1000,
            stream=True,
        )
    except Exception as e:
        yield _sse("error", {"message": f"提炼失败: {e}"})
        yield _sse("end", {})
        return

    buf = ""
    for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta.content:
            buf += delta.content
            yield _sse("delta", {"text": delta.content})

    # 解析 JSON（容错：剥离可能的 ```json 包裹）
    try:
        text = buf.strip()
        m = re.search(r"\{.*\}", text, re.DOTALL)
        persona = json.loads(m.group(0)) if m else {}
        yield _sse("profile", {"persona": persona})
    except Exception as e:
        yield _sse("error", {"message": f"解析画像失败: {e}"})

    yield _sse("end", {"finish_reason": "stop"})


# ----------------------- 目录体系 AI 推荐 -----------------------

def _flatten_note_paths(nodes: list[dict]) -> list[str]:
    """递归把 files.list_tree() 展平成所有笔记相对路径。"""
    out: list[str] = []
    for n in nodes:
        if n.get("is_dir"):
            out.extend(_flatten_note_paths(n.get("children", [])))
        else:
            out.append(n["path"])
    return out


def _note_distribution() -> list[tuple[str, int]]:
    """统计各目录下的笔记数（按所在目录聚合，降序）。"""
    counter: Counter = Counter()
    for p in _flatten_note_paths(files.list_tree()):
        counter[str(Path(p).parent)] += 1
    return counter.most_common()


_TAXONOMY_SUGGEST_SYSTEM = """你是知识库目录架构师。根据用户画像、现有目录体系和笔记实际分布，\
给出一套优化后的目录体系，用于指导 AI 消化笔记时把每篇准确归位。

请输出**严格的 JSON**（不要 markdown 代码块包裹），字段如下：
{
  "dirs": [
    {"path": "02-领域/模型与RAG", "desc": "该目录收纳什么，一句话语义边界"},
    ...
  ],
  "default": "兜底目录的 path（拿不准的笔记落这里）"
}

要求：
- path 必须沿用现有顶层目录前缀（如 01-笔记/、02-领域/、03-资源/ 等），保持两级结构，不要凭空造新顶层
- desc 一句话讲清这个目录收什么、不收什么，让 AI 据此判断归位
- 贴合用户画像的关注领域：可合并冗余目录、拆分过载目录、补充画像里关心但缺失的领域
- 8-14 个目录为宜；default 选一个通用兜底目录（通常是文献/笔记类）
- 全部用中文，只输出 JSON，不要任何解释文字"""


def stream_taxonomy_suggest(persona_override: Optional[dict] = None) -> Generator[str, None, None]:
    """据画像 + 现有目录 + 笔记分布，AI 推荐优化后的目录体系，SSE 流式输出。

    事件：start → delta（推荐过程文本）→ taxonomy（结构化结果）→ end；出错发 error。
    persona_override：前端可传入未保存的画像草稿覆盖；为空则读 profile.yaml。
    """
    yield _sse("start", {"mode": "taxonomy_suggest"})

    # 画像：优先用前端传入的草稿，否则渲染当前画像
    if persona_override and isinstance(persona_override, dict):
        from scripts.persona import _clean_profile  # 复用白名单清洗
        p = _clean_profile(persona_override)
        persona_text = "\n".join([
            f"- 角色：{p['role']}",
            f"- 工作方式：{p['working_style']}",
            f"- 关心：{'、'.join(p['cares_about'])}",
            f"- 兴趣：{'、'.join(p['interests'])}",
            f"- 不想看：{'、'.join(p['dislikes'])}",
        ])
    else:
        persona_text = render_persona()

    # 现有目录体系
    cur_tax = load_taxonomy()
    cur_lines = [f"- {d['path']}" + (f"：{d['desc']}" if d.get("desc") else "") for d in cur_tax["dirs"]]
    cur_text = "\n".join(cur_lines) if cur_lines else "（暂无）"

    # 笔记分布
    dist = _note_distribution()
    dist_text = "\n".join(f"- {d}: {c} 篇" for d, c in dist) if dist else "（暂无笔记）"

    user_input = (
        f"## 用户画像\n{persona_text}\n\n"
        f"## 现有目录体系\n{cur_text}\n\n"
        f"## 笔记实际分布（目录 → 笔记数）\n{dist_text}\n\n"
        f"（现有兜底目录：{cur_tax['default']}）\n\n"
        "请据此给出优化后的目录体系 JSON。"
    )

    client, llm_cfg = _llm_client()
    messages = [
        {"role": "system", "content": _TAXONOMY_SUGGEST_SYSTEM},
        {"role": "user", "content": user_input},
    ]

    try:
        stream = client.chat.completions.create(
            model=llm_cfg["model"],
            extra_body=llm_extra_body(llm_cfg),
            messages=messages,
            temperature=0.4,
            max_tokens=1500,
            stream=True,
        )
    except Exception as e:
        yield _sse("error", {"message": f"推荐失败: {e}"})
        yield _sse("end", {})
        return

    buf = ""
    for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta.content:
            buf += delta.content
            yield _sse("delta", {"text": delta.content})

    # 解析 JSON（容错：剥离可能的 ```json 包裹）
    try:
        text = buf.strip()
        m = re.search(r"\{.*\}", text, re.DOTALL)
        taxonomy = json.loads(m.group(0)) if m else {}
        yield _sse("taxonomy", {"taxonomy": taxonomy})
    except Exception as e:
        yield _sse("error", {"message": f"解析目录建议失败: {e}"})

    yield _sse("end", {"finish_reason": "stop"})


# ----------------------- 单篇笔记重新归类建议（非流式） -----------------------

_PLACEMENT_SUGGEST_SYSTEM = """你是知识库归类助手。给定一篇笔记和一套目录体系，\
判断这篇笔记最该归到哪个目录，并给出简短理由。

请输出**严格的 JSON**（不要 markdown 代码块包裹）：
{
  "placement": "从下方目录列表里选一个 path（必须完全一致）",
  "reason": "一句话理由（为什么归这里）"
}

要求：
- placement 必须严格等于给定目录列表里的某个 path，不要自创
- reason 简短具体，扣住笔记主题与目录语义边界
- 全部用中文，只输出 JSON"""


def suggest_placement(note: dict) -> dict:
    """据 taxonomy 给单篇笔记建议归类目录 + 理由（非流式）。

    返回 {"target_dir": <path>, "reason": <str>}。
    placement 不在白名单内时回退兜底目录。
    """
    prefixes = taxonomy_prefixes()
    default_dir = taxonomy_default()

    title = note.get("name", "")
    fm = note.get("frontmatter", {}) or {}
    cur_placement = fm.get("placement", "")
    tags = fm.get("tags", [])
    body_preview = (note.get("body", "") or "")[:1200]

    user_input = (
        f"## 目录体系（必须从中选一个 path）\n{render_taxonomy()}\n\n"
        f"## 待归类笔记\n"
        f"标题：{title}\n"
        f"当前 placement：{cur_placement or '（未知）'}\n"
        f"标签：{('、'.join(tags) if isinstance(tags, list) else tags) or '（无）'}\n\n"
        f"正文摘要：\n{body_preview}\n\n"
        "请判断它最该归到哪个目录。"
    )

    client, llm_cfg = _llm_client()
    try:
        resp = client.chat.completions.create(
            model=llm_cfg["model"],
            extra_body=llm_extra_body(llm_cfg),
            messages=[
                {"role": "system", "content": _PLACEMENT_SUGGEST_SYSTEM},
                {"role": "user", "content": user_input},
            ],
            temperature=0.2,
            max_tokens=400,
            stream=False,
        )
        text = (resp.choices[0].message.content or "").strip()
        m = re.search(r"\{.*\}", text, re.DOTALL)
        data = json.loads(m.group(0)) if m else {}
    except Exception as e:
        raise RuntimeError(f"归类建议失败: {e}")

    placement = str(data.get("placement") or "").strip().rstrip("/")
    reason = str(data.get("reason") or "").strip()
    if placement not in prefixes:
        placement = default_dir
        if not reason:
            reason = "未匹配到合适目录，建议归入兜底目录。"
    return {"target_dir": placement, "reason": reason}
