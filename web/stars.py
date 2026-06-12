"""GitHub Starred 导入：复用现有管道，Web 端 SSE 流式消化入库。

把用户 GitHub starred 仓库经现有管道（github fetcher → digest → place）消化成笔记。
"被动收藏 → 主动消化"的闭环。复用 scripts/import_github_stars.py 的拉取/去重逻辑，
归位用 taxonomy 单一来源（profile.yaml），而非 CLI 里的旧 config 字段。

mode:
  - "latest"：只处理最近 N 个新 star（GitHub 默认按 star 时间倒序）
  - "full"  ：处理全部未消化的 star
state.json 防重：已 processed/skipped 的不重复处理。
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict
from typing import Generator

from web.config import PIPELINE_CONFIG, PIPELINE_DIR, VAULT_ROOT
from web import chat
from web.rag import index as rag_index

# scripts/ 内模块用裸导入，必须先加 sys.path（与 gapfill/process_inbox 同款）
_SCRIPTS_DIR = PIPELINE_DIR / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from import_github_stars import fetch_all_stars, filter_targets  # noqa: E402
from digest import digest  # noqa: E402
from fetchers import dispatch  # noqa: E402
from place import place  # noqa: E402
from state import hash_url, load_state, now_iso, save_state, upsert  # noqa: E402
from persona import taxonomy_prefixes, taxonomy_default  # noqa: E402


class _SilentLogger:
    """fetch_all_stars/filter_targets 要 logger，Web 端不需要它们的日志输出。"""
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass


def _persist_token(token: str) -> None:
    """把 GITHUB_TOKEN 写回 .env（已有则替换），方便下次免填。"""
    env_path = PIPELINE_DIR / ".env"
    line = f"GITHUB_TOKEN={token}"
    try:
        if env_path.exists():
            lines = env_path.read_text(encoding="utf-8").splitlines()
            replaced = False
            for i, ln in enumerate(lines):
                if ln.strip().startswith("GITHUB_TOKEN="):
                    lines[i] = line
                    replaced = True
                    break
            if not replaced:
                lines.append(line)
            env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        else:
            env_path.write_text(line + "\n", encoding="utf-8")
    except Exception:
        pass  # 存盘失败不影响本次导入


def stream_import_stars(
    username: str, mode: str = "latest", limit: int = 30,
    token: str = "", save_token: bool = False,
) -> Generator[str, None, None]:
    """流式导入 starred 仓库：fetch → 过滤去重 → 逐个 dispatch→digest→place。

    token：前端可传入 GITHUB_TOKEN（覆盖环境变量，所有下游 GitHub 请求生效）。
    save_token：True 则把 token 写回 .env 免下次再填。

    事件：start → fetched(total) → repo_start → placed/skipped/error → end(created)。
    单个失败不中断其余。末尾增量重索引。
    """
    # 前端传了 token → 设进环境变量，fetch_all_stars / github fetcher 都从这读
    token = (token or "").strip()
    if token:
        os.environ["GITHUB_TOKEN"] = token
        if save_token:
            _persist_token(token)

    yield chat._sse("start", {"username": username, "mode": mode})

    config = PIPELINE_CONFIG
    logger = _SilentLogger()

    # 1. 拉全部 starred
    try:
        repos = fetch_all_stars(username, logger=logger)
    except Exception as e:
        yield chat._sse("error", {"message": f"拉取 starred 失败: {e}"})
        yield chat._sse("end", {"created": []})
        return

    # 2. 去重过滤（state.json 已处理的跳过）；latest 模式取最近 N 个
    items_state = load_state()
    effective_limit = limit if mode == "latest" else None
    targets = filter_targets(
        repos, items_state, skip=0, limit=effective_limit, retry_failed=False
    )

    yield chat._sse("fetched", {
        "total_stars": len(repos),
        "to_process": len(targets),
        "already_done": len(repos) - len(targets),
    })

    if not targets:
        yield chat._sse("end", {"created": [], "message": "没有新的 star 要处理"})
        return

    # 归位白名单/兜底：用 taxonomy 单一来源（修正 CLI 旧 bug）
    allowed_prefixes = taxonomy_prefixes() or config["allowed_placement_prefixes"]
    default_placement = taxonomy_default() or config["default_placement"]

    staging_dir = PIPELINE_DIR / "staging"
    staging_dir.mkdir(exist_ok=True)

    created: list[dict] = []

    for idx, repo in enumerate(targets, 1):
        url = repo["html_url"]
        full_name = repo.get("full_name", url)
        key = hash_url(url)
        yield chat._sse("repo_start", {
            "i": idx, "total": len(targets), "repo": full_name,
            "stars": repo.get("stargazers_count"), "lang": repo.get("language"),
        })

        # fetch（github fetcher 取元数据 + README）
        result = dispatch("github", url, config=config)

        if result.status == "skipped":
            upsert(items_state, key, url=url, source_file="github-stars-import",
                   fetcher="github", raw_line=url, status="skipped",
                   fetched_at=now_iso(), error=result.error)
            save_state(items_state)
            yield chat._sse("skipped", {"repo": full_name, "message": result.error})
            continue

        if result.status == "failed":
            upsert(items_state, key, url=url, source_file="github-stars-import",
                   fetcher="github", raw_line=url, status="failed",
                   fetched_at=now_iso(), error=result.error)
            save_state(items_state)
            yield chat._sse("error", {"repo": full_name, "message": f"抓取失败: {result.error}"})
            continue

        # staging
        staging_path = staging_dir / f"{key}.json"
        staging_path.write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2), encoding="utf-8")
        upsert(items_state, key, url=url, source_file="github-stars-import",
               fetcher="github", raw_line=url, status="staged", title=result.title,
               fetched_at=now_iso(),
               staging_path=str(staging_path.relative_to(VAULT_ROOT)), error=None)
        save_state(items_state)

        # digest
        try:
            markdown, err = digest(result, llm_cfg=config["llm"])
        except Exception as e:
            err = str(e)
            markdown = ""
        if err:
            upsert(items_state, key, status="failed", error=f"LLM 失败: {err}")
            save_state(items_state)
            yield chat._sse("error", {"repo": full_name, "message": f"消化失败: {err}"})
            continue

        # place（用 taxonomy 归位）
        try:
            output_path = place(
                markdown, vault_root=VAULT_ROOT,
                allowed_prefixes=allowed_prefixes, default_placement=default_placement,
            )
            rel = str(output_path.relative_to(VAULT_ROOT))
        except Exception as e:
            upsert(items_state, key, status="failed", error=f"归位失败: {e}")
            save_state(items_state)
            yield chat._sse("error", {"repo": full_name, "message": f"归位失败: {e}"})
            continue

        upsert(items_state, key, status="processed", processed_at=now_iso(), output_path=rel)
        save_state(items_state)
        created.append({"repo": full_name, "path": rel})
        yield chat._sse("placed", {"repo": full_name, "path": rel})

    # 增量重索引（新笔记进 RAG）
    if created:
        try:
            rag_index.build_index(force=False)
        except Exception:
            pass

    yield chat._sse("end", {"created": created, "count": len(created)})
