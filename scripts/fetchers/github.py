"""GitHub 仓库抓取：REST API + README"""
from __future__ import annotations

import os
import re
from typing import Optional

import requests

from fetchers.base import FetcherResult, failed, staged


GITHUB_REPO_RE = re.compile(r"github\.com/([^/]+)/([^/#?]+)", re.IGNORECASE)


def _parse_repo(url: str) -> Optional[tuple[str, str]]:
    m = GITHUB_REPO_RE.search(url)
    if not m:
        return None
    owner, repo = m.group(1), m.group(2).rstrip(".git")
    return owner, repo


def _headers() -> dict:
    h = {"Accept": "application/vnd.github+json", "User-Agent": "obsidian-inbox-pipeline"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def fetch(url: str, *, max_readme_chars: int = 40000, timeout: int = 30) -> FetcherResult:
    parsed = _parse_repo(url)
    if not parsed:
        return failed("github", url, f"无法解析 GitHub 仓库路径: {url}")
    owner, repo = parsed

    api = f"https://api.github.com/repos/{owner}/{repo}"
    try:
        r = requests.get(api, headers=_headers(), timeout=timeout)
        r.raise_for_status()
        meta = r.json()
    except requests.HTTPError as e:
        return failed("github", url, f"GitHub API 错误 {e.response.status_code}: {e.response.text[:200]}")
    except requests.RequestException as e:
        return failed("github", url, f"GitHub API 请求失败: {e}")

    readme_url = f"https://api.github.com/repos/{owner}/{repo}/readme"
    readme_text = ""
    try:
        rr = requests.get(
            readme_url,
            headers={**_headers(), "Accept": "application/vnd.github.raw"},
            timeout=timeout,
        )
        if rr.status_code == 200:
            readme_text = rr.text[:max_readme_chars]
    except requests.RequestException:
        readme_text = ""  # README 不强求

    title = f"{owner}/{repo}"
    content_parts = [
        f"# {title}",
        "",
        f"**描述**: {meta.get('description') or '(无)'}",
        f"**Stars**: {meta.get('stargazers_count', 'N/A')} | **Forks**: {meta.get('forks_count', 'N/A')} | **License**: {(meta.get('license') or {}).get('name', 'N/A')}",
        f"**主要语言**: {meta.get('language') or 'N/A'}",
        f"**主页**: {meta.get('homepage') or '(无)'}",
        f"**Topics**: {', '.join(meta.get('topics', [])) or '(无)'}",
        "",
        "## README",
        "",
        readme_text or "(无 README)",
    ]

    return staged(
        fetcher="github",
        source=url,
        title=title,
        content="\n".join(content_parts),
        meta={
            "owner": owner,
            "repo": repo,
            "stars": meta.get("stargazers_count"),
            "language": meta.get("language"),
            "topics": meta.get("topics", []),
            "homepage": meta.get("homepage"),
            "description": meta.get("description"),
        },
    )
