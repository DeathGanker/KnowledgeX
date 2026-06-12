#!/usr/bin/env python3
"""一次性导入 GitHub Starred 仓库：复用现有管道（fetcher → digest → place + state.json 防重）

用法：
  # 仅列出，不调 LLM，不写笔记（看看有多少、什么语言）
  python import_github_stars.py <username> --list

  # 真跑（按 starred 时间倒序）
  python import_github_stars.py <username>

  # 跑前 50 个（debug/试跑）
  python import_github_stars.py <username> --limit 50

  # 跳过前 N 个，从某处续跑
  python import_github_stars.py <username> --skip 100 --limit 50

  # 重试之前失败的项
  python import_github_stars.py <username> --retry-failed
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Iterator, Optional

import requests
from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

PIPELINE_DIR = SCRIPT_DIR.parent

from paths import VAULT_ROOT, load_pipeline_config  # noqa: E402  路径与配置单一来源（见 scripts/paths.py）
from digest import digest  # noqa: E402
from fetchers import dispatch  # noqa: E402
from place import place  # noqa: E402
from state import (  # noqa: E402
    ItemRecord,
    hash_url,
    load_state,
    now_iso,
    save_state,
    upsert,
)


def setup_logging() -> logging.Logger:
    log_dir = PIPELINE_DIR / "logs"
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / f"stars-import.log"

    logger = logging.getLogger("stars")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger


def load_config() -> dict:
    # config.yaml + .env 端点覆盖（统一来源：scripts/paths.load_pipeline_config）
    return load_pipeline_config()


def _gh_headers() -> dict:
    h = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "obsidian-inbox-pipeline",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def fetch_all_stars(username: str, *, logger: logging.Logger) -> list[dict]:
    """分页拉取全部 starred 仓库。"""
    all_repos: list[dict] = []
    page = 1
    while True:
        url = f"https://api.github.com/users/{username}/starred"
        params = {"per_page": 100, "page": page}
        r = requests.get(url, headers=_gh_headers(), params=params, timeout=30)
        if r.status_code == 401:
            raise RuntimeError("GitHub API 401：GITHUB_TOKEN 无效或已过期")
        if r.status_code == 403:
            reset = r.headers.get("X-RateLimit-Reset", "?")
            remaining = r.headers.get("X-RateLimit-Remaining", "?")
            raise RuntimeError(
                f"GitHub API 403 限额耗尽。remaining={remaining}, reset={reset}（请加 GITHUB_TOKEN 或等会再跑）"
            )
        if r.status_code == 404:
            raise RuntimeError(f"GitHub 用户 '{username}' 不存在，或 starred 列表为 private")
        r.raise_for_status()

        page_data = r.json()
        if not page_data:
            break
        all_repos.extend(page_data)
        logger.info(f"  · 已拉取第 {page} 页，累计 {len(all_repos)} 个 repo")
        if len(page_data) < 100:
            break
        page += 1
        time.sleep(0.3)  # 礼貌停顿
    return all_repos


def print_summary(repos: list[dict], logger: logging.Logger) -> None:
    logger.info(f"总数: {len(repos)}")
    langs = Counter(r.get("language") or "(无)" for r in repos)
    logger.info(f"按语言 Top 10: {langs.most_common(10)}")

    top_starred = sorted(repos, key=lambda r: r.get("stargazers_count", 0), reverse=True)[:10]
    logger.info("⭐ 最热 10 个:")
    for r in top_starred:
        logger.info(
            f"   {r['stargazers_count']:>7d} ⭐ {r['full_name']:50s} {r.get('language') or '-':12s} {(r.get('description') or '')[:60]}"
        )


def filter_targets(
    repos: list[dict],
    state_items: dict[str, ItemRecord],
    *,
    skip: int,
    limit: Optional[int],
    retry_failed: bool,
) -> list[dict]:
    targets: list[dict] = []
    for r in repos:
        url = r["html_url"]
        key = hash_url(url)
        existing = state_items.get(key)
        if existing:
            if existing.status == "processed":
                continue
            if existing.status == "failed" and not retry_failed:
                continue
            if existing.status == "skipped":
                continue
        targets.append(r)

    if skip:
        targets = targets[skip:]
    if limit is not None:
        targets = targets[:limit]
    return targets


def process_one(
    repo: dict,
    *,
    config: dict,
    items_state: dict[str, ItemRecord],
    logger: logging.Logger,
    no_llm: bool,
) -> str:
    """处理单个 repo。返回 'processed' / 'failed' / 'skipped' / 'staged-only'。"""
    url = repo["html_url"]
    key = hash_url(url)
    logger.info(f"→ {repo['full_name']}  ({repo.get('stargazers_count', 0)} ⭐, {repo.get('language') or '-'})")

    result = dispatch("github", url, config=config)

    if result.status == "skipped":
        upsert(
            items_state, key,
            url=url, source_file="github-stars-import", fetcher="github",
            raw_line=url, status="skipped",
            fetched_at=now_iso(), error=result.error,
        )
        save_state(items_state)
        logger.info(f"  ⏭ 跳过: {result.error}")
        return "skipped"

    if result.status == "failed":
        upsert(
            items_state, key,
            url=url, source_file="github-stars-import", fetcher="github",
            raw_line=url, status="failed",
            fetched_at=now_iso(), error=result.error,
        )
        save_state(items_state)
        logger.warning(f"  ❌ 抓取失败: {result.error}")
        return "failed"

    # 写 staging
    import json
    staging_dir = PIPELINE_DIR / "staging"
    staging_dir.mkdir(exist_ok=True)
    staging_path = staging_dir / f"{key}.json"
    staging_path.write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2), encoding="utf-8")
    upsert(
        items_state, key,
        url=url, source_file="github-stars-import", fetcher="github",
        raw_line=url, status="staged", title=result.title,
        fetched_at=now_iso(),
        staging_path=str(staging_path.relative_to(VAULT_ROOT)),
        error=None,
    )
    save_state(items_state)

    if no_llm:
        return "staged-only"

    markdown, err = digest(result, llm_cfg=config["llm"])
    if err:
        upsert(items_state, key, status="failed", error=f"LLM 失败: {err}")
        save_state(items_state)
        logger.warning(f"  ❌ LLM 失败: {err}")
        return "failed"

    try:
        from persona import taxonomy_prefixes, taxonomy_default
        allowed_prefixes = taxonomy_prefixes() or config["allowed_placement_prefixes"]
        default_placement = taxonomy_default() or config["default_placement"]
        output_path = place(
            markdown,
            vault_root=VAULT_ROOT,
            allowed_prefixes=allowed_prefixes,
            default_placement=default_placement,
        )
    except Exception as e:
        upsert(items_state, key, status="failed", error=f"归位失败: {e}")
        save_state(items_state)
        logger.warning(f"  ❌ 归位失败: {e}")
        return "failed"

    upsert(
        items_state, key,
        status="processed", processed_at=now_iso(),
        output_path=str(output_path.relative_to(VAULT_ROOT)),
    )
    save_state(items_state)
    logger.info(f"  ✓ 已归位 → {output_path.relative_to(VAULT_ROOT)}")
    return "processed"


def run(args: argparse.Namespace) -> int:
    logger = setup_logging()
    load_dotenv(PIPELINE_DIR / ".env", override=True)
    config = load_config()

    if not os.environ.get("GITHUB_TOKEN"):
        logger.warning("⚠️ 未设置 GITHUB_TOKEN，公开 API 限额仅 60 次/小时，可能撑不到结束")

    logger.info(f"拉取 {args.username} 的 starred 列表...")
    repos = fetch_all_stars(args.username, logger=logger)
    print_summary(repos, logger)

    if args.list:
        logger.info("--list 模式结束（未调 LLM、未写笔记）")
        return 0

    items_state = load_state()
    targets = filter_targets(
        repos, items_state,
        skip=args.skip, limit=args.limit, retry_failed=args.retry_failed,
    )

    already_done = len(repos) - len(targets) - args.skip
    if already_done < 0:
        already_done = 0
    logger.info(
        f"本次将处理 {len(targets)} 个（已完成/跳过 {already_done} 个）。"
        f"{'[no-llm: 只抓 staging]' if args.no_llm else ''}"
    )

    if not targets:
        logger.info("无待处理项。结束。")
        return 0

    counters = Counter()
    for i, repo in enumerate(targets, 1):
        logger.info(f"--- [{i}/{len(targets)}] ---")
        try:
            status = process_one(
                repo, config=config, items_state=items_state, logger=logger, no_llm=args.no_llm
            )
            counters[status] += 1
        except KeyboardInterrupt:
            logger.warning("用户中断，state 已保存到上一条。下次重跑会从这里继续。")
            return 130
        except Exception as e:
            logger.error(f"未预期异常: {e}")
            counters["error"] += 1
        # 礼貌停顿，避免对 GitHub / LLM 同时打太密
        time.sleep(1)

    logger.info(f"完成。统计: {dict(counters)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="把 GitHub starred 仓库导入到 Obsidian vault")
    parser.add_argument("username", help="GitHub 用户名")
    parser.add_argument("--list", action="store_true", help="仅列出统计，不抓取不调 LLM")
    parser.add_argument("--no-llm", action="store_true", help="只抓取并 staging，不调 LLM 不归位")
    parser.add_argument("--skip", type=int, default=0, help="跳过前 N 个")
    parser.add_argument("--limit", type=int, help="只跑前 N 个（用于试跑）")
    parser.add_argument("--retry-failed", action="store_true", help="重试之前 failed 的项")
    args = parser.parse_args()
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
