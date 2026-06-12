"""知识缺口补全：AI 推荐 GitHub 仓库 → 校验防幻觉 → 勾选后写入收件箱。

全库 RAG 问答发现「内容不足」时，据当前问题让豆包推荐相关 GitHub 仓库，GitHub API 校验
真实存在（过滤 LLM 幻觉）。用户勾选后把链接写进 00-收件箱/，之后点「处理收件箱」由现有
管道（github fetcher → digest → place）异步抓取消化归位——避免在问答里同步阻塞、耗时。

复用：chat._llm_client（豆包）。GitHub 解析/鉴权 helper 内联（零 scripts 依赖）。
"""
from __future__ import annotations

import json
import os
import re
from datetime import date
from typing import Optional

import requests

from web.config import PIPELINE_CONFIG, VAULT_ROOT, llm_extra_body
from web import chat


# 与 scripts/fetchers/github.py 同款解析；内联避免依赖 scripts 裸导入
_GITHUB_REPO_RE = re.compile(r"github\.com/([^/]+)/([^/#?]+)", re.IGNORECASE)


def _parse_repo(repo_or_url: str) -> Optional[tuple[str, str]]:
    """owner/repo 或完整 URL → (owner, repo)。"""
    raw = (repo_or_url or "").strip()
    if not raw:
        return None
    if "github.com" not in raw:
        raw = f"https://github.com/{raw}"
    m = _GITHUB_REPO_RE.search(raw)
    if not m:
        return None
    return m.group(1), m.group(2).rstrip(".git")


def _gh_headers() -> dict:
    h = {"Accept": "application/vnd.github+json", "User-Agent": "obsidian-knowledge-gap"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


_GAP_SUGGEST_SYSTEM = """你是知识库补缺口助手。用户在自己的知识库里问了一个问题，但库里内容不足。\
请据问题推荐若干**最相关的 GitHub 开源仓库**，这些仓库的源码/文档能补上这个知识缺口。

请输出**严格的 JSON**（不要 markdown 代码块包裹）：
{
  "repos": [
    {
      "repo": "owner/repo（真实存在的 GitHub 仓库，格式必须是 owner/repo）",
      "reason": "一句话：这个仓库能补什么缺口、为什么相关"
    }
  ]
}

要求：
- 只推荐你有把握真实存在的知名/活跃仓库，宁缺毋滥（3-5 个）
- repo 必须是 owner/repo 形式，不要带 https、不要带 .git
- 全部用中文（repo 名除外），只输出 JSON"""


def _verify_repo(repo_or_url: str) -> Optional[dict]:
    """GitHub API 轻量校验仓库真实存在，返回元数据或 None（不拉 README）。"""
    parsed = _parse_repo(repo_or_url)
    if not parsed:
        return None
    owner, repo = parsed
    api = f"https://api.github.com/repos/{owner}/{repo}"
    try:
        r = requests.get(api, headers=_gh_headers(), timeout=15)
        if r.status_code != 200:
            return None
        meta = r.json()
    except requests.RequestException:
        return None
    return {
        "owner": owner,
        "repo": repo,
        "full_name": f"{owner}/{repo}",
        "url": f"https://github.com/{owner}/{repo}",
        "stars": meta.get("stargazers_count"),
        "language": meta.get("language"),
        "description": meta.get("description") or "",
    }


def suggest_gaps(question: str, recalled_titles: list[str] | None = None) -> list[dict]:
    """据问题 + 召回标题，豆包推荐 GitHub 仓库并 GitHub API 校验，返回 verified 候选。

    每个候选：{owner, repo, full_name, url, stars, language, description, reason}
    """
    recalled = recalled_titles or []
    recalled_text = "\n".join(f"- {t}" for t in recalled) if recalled else "（无）"
    user_input = (
        f"## 用户的问题\n{question}\n\n"
        f"## 知识库里已有但不足以回答的笔记\n{recalled_text}\n\n"
        "请推荐能补上这个缺口的 GitHub 仓库。"
    )

    client, llm_cfg = chat._llm_client()
    try:
        resp = client.chat.completions.create(
            model=llm_cfg["model"],
            extra_body=llm_extra_body(llm_cfg),
            messages=[
                {"role": "system", "content": _GAP_SUGGEST_SYSTEM},
                {"role": "user", "content": user_input},
            ],
            temperature=0.3,
            max_tokens=800,
            stream=False,
        )
        text = (resp.choices[0].message.content or "").strip()
        m = re.search(r"\{.*\}", text, re.DOTALL)
        data = json.loads(m.group(0)) if m else {}
    except Exception as e:
        raise RuntimeError(f"推荐仓库失败: {e}")

    candidates: list[dict] = []
    seen: set[str] = set()
    for item in (data.get("repos") or []):
        if not isinstance(item, dict):
            continue
        verified = _verify_repo(str(item.get("repo") or ""))
        if not verified or verified["full_name"] in seen:
            continue
        seen.add(verified["full_name"])
        verified["reason"] = str(item.get("reason") or "").strip()
        candidates.append(verified)
    return candidates


def collect_to_inbox(repos: list[dict], question: str = "") -> dict:
    """把选中的仓库链接写进收件箱的一个 .md 文件，下次「处理收件箱」即抓取消化归位。

    文件名取自触发它的问答内容（而非固定名），便于在收件箱里辨认是哪次缺口补的。
    返回 {"written": n, "file": <相对路径>, "repos": [full_name...]}。
    """
    if not repos:
        return {"written": 0, "file": None, "repos": []}

    inbox_dir = PIPELINE_CONFIG.get("inbox_dir", "00-收件箱")
    inbox_path = VAULT_ROOT / inbox_dir
    inbox_path.mkdir(parents=True, exist_ok=True)

    today = date.today().isoformat()
    # 问题清洗成可做文件名/标题的短串（去非法字符、截断）
    safe_q = re.sub(r"[\\/:*?\"<>|\n\r\t]", "", (question or "").strip())[:30].strip()
    topic = safe_q or "知识补缺"
    lines = [
        f"# 缺口补全 · {topic}（{today}）",
        "",
        f"> 由全库问答「{topic}」的知识缺口生成。点顶栏「处理收件箱」即按现有管道抓取 → 消化 → 归位。",
        "",
    ]
    written: list[str] = []
    for r in repos:
        full_name = r.get("full_name") or ""
        url = r.get("url") or (f"https://github.com/{full_name}" if full_name else "")
        if not url:
            continue
        reason = (r.get("reason") or "").strip()
        lines.append(f"- [{full_name}]({url})" + (f" — {reason}" if reason else ""))
        written.append(full_name)

    if not written:
        return {"written": 0, "file": None, "repos": []}

    # 文件名：日期前缀（沿用 vault「日期 标题」惯例，便于排序）+ 问答主题；重名追加 -2/-3
    base = f"{today} 缺口补全-{safe_q}" if safe_q else f"知识缺口补全-{today}"
    target = inbox_path / f"{base}.md"
    n = 2
    while target.exists():
        target = inbox_path / f"{base}-{n}.md"
        n += 1
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return {
        "written": len(written),
        "file": str(target.relative_to(VAULT_ROOT)),
        "repos": written,
    }
