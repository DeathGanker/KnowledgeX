#!/usr/bin/env python3
"""端到端入口：扫描 → 抓取 → 消化 → 归位 → 清理"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict
from datetime import datetime, date
from pathlib import Path

from dotenv import load_dotenv

# 让本目录的相对导入生效（脚本既能 `python process_inbox.py` 直接跑，也能 -m 运行）
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

PIPELINE_DIR = SCRIPT_DIR.parent

from paths import VAULT_ROOT, ensure_vault, load_pipeline_config  # noqa: E402  路径与配置单一来源（见 scripts/paths.py）
from cleanup import apply_cleanup  # noqa: E402
from digest import digest  # noqa: E402
from fetchers import dispatch  # noqa: E402
from place import place  # noqa: E402
from scan import WorkItem, filter_new, scan_inbox  # noqa: E402
from state import ItemRecord, hash_url, load_state, now_iso, save_state, upsert  # noqa: E402


def setup_logging() -> logging.Logger:
    log_dir = PIPELINE_DIR / "logs"
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / f"{date.today().isoformat()}.log"

    logger = logging.getLogger("inbox")
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


def write_staging(item_key: str, fetched_payload: dict) -> Path:
    staging_dir = PIPELINE_DIR / "staging"
    staging_dir.mkdir(exist_ok=True)
    p = staging_dir / f"{item_key}.json"
    p.write_text(json.dumps(fetched_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def _item_key(item: WorkItem) -> str:
    if item.kind == "url":
        return hash_url(item.target)
    return hash_url(f"file://{item.target}")


def run(dry_run: bool = False, no_llm: bool = False, only: str | None = None) -> int:
    logger = setup_logging()
    load_dotenv(PIPELINE_DIR / ".env", override=True)
    config = load_config()
    ensure_vault(VAULT_ROOT)  # 全新克隆/手建目录也保证标准目录在，扫描不落空

    items_state = load_state()
    all_items = scan_inbox(VAULT_ROOT, config["inbox_dir"])
    new_items = filter_new(all_items, items_state)
    if only:
        new_items = [it for it in new_items if only in it.target]

    logger.info(f"扫描到 {len(all_items)} 项，待处理 {len(new_items)} 项")
    if not new_items:
        logger.info("没有新内容要处理。结束。")
        return 0

    if dry_run:
        for it in new_items:
            logger.info(f"[dry-run] {it.fetcher:8s} {it.kind:10s} {it.target}  (from: {it.source_file})")
        return 0

    # 归位白名单/兜底：优先用单一来源 taxonomy（profile.yaml），回退 config.yaml
    from persona import taxonomy_prefixes, taxonomy_default
    allowed_prefixes = taxonomy_prefixes() or config["allowed_placement_prefixes"]
    default_placement = taxonomy_default() or config["default_placement"]

    processed = failed = skipped = 0

    for it in new_items:
        key = _item_key(it)
        logger.info(f"→ [{it.fetcher}] {it.target}")

        # 阶段 1: fetch
        result = dispatch(it.fetcher, it.target, config=config)

        if result.status == "skipped":
            upsert(
                items_state, key,
                url=it.target, source_file=it.source_file, fetcher=it.fetcher,
                raw_line=it.raw_line, status="skipped",
                fetched_at=now_iso(), error=result.error,
            )
            save_state(items_state)
            skipped += 1
            logger.info(f"  ⏭ 跳过: {result.error}")
            continue

        if result.status == "failed":
            upsert(
                items_state, key,
                url=it.target, source_file=it.source_file, fetcher=it.fetcher,
                raw_line=it.raw_line, status="failed",
                fetched_at=now_iso(), error=result.error,
            )
            save_state(items_state)
            failed += 1
            logger.warning(f"  ❌ 抓取失败: {result.error}")
            continue

        staging_path = write_staging(key, asdict(result))
        upsert(
            items_state, key,
            url=it.target, source_file=it.source_file, fetcher=it.fetcher,
            raw_line=it.raw_line, status="staged", title=result.title,
            fetched_at=now_iso(), staging_path=str(staging_path.relative_to(PIPELINE_DIR)),
            error=None,
        )
        save_state(items_state)
        logger.info(f"  ✓ 已抓取 → {staging_path.relative_to(PIPELINE_DIR)}")

        if no_llm:
            logger.info("  --no-llm 模式，跳过消化和归位")
            continue

        # 阶段 2: digest
        markdown, err = digest(result, llm_cfg=config["llm"])
        if err:
            upsert(
                items_state, key,
                status="failed", error=f"LLM 失败: {err}",
            )
            save_state(items_state)
            failed += 1
            logger.warning(f"  ❌ LLM 失败: {err}")
            continue

        # 阶段 3: place
        try:
            output_path = place(
                markdown,
                vault_root=VAULT_ROOT,
                allowed_prefixes=allowed_prefixes,
                default_placement=default_placement,
            )
        except Exception as e:
            upsert(items_state, key, status="failed", error=f"归位失败: {e}")
            save_state(items_state)
            failed += 1
            logger.warning(f"  ❌ 归位失败: {e}")
            continue

        upsert(
            items_state, key,
            status="processed", processed_at=now_iso(),
            output_path=str(output_path.relative_to(VAULT_ROOT)),
        )
        save_state(items_state)
        processed += 1
        logger.info(f"  ✓ 已归位 → {output_path.relative_to(VAULT_ROOT)}")

    # 阶段 4: cleanup
    apply_cleanup(VAULT_ROOT, items_state)

    # 阶段 5: 缺口补全关联 —— 把这次归位的「缺口补全」笔记连到触发它的问答引用的笔记
    processed_by_file: dict[str, list[str]] = {}
    for rec in items_state.values():
        if rec.status == "processed" and rec.source_file and rec.output_path:
            processed_by_file.setdefault(rec.source_file, []).append(rec.output_path)
    try:
        # CLI/子进程的 sys.path 只有 scripts/，要先把仓库根加上才 import 得到 web 包
        if str(PIPELINE_DIR) not in sys.path:
            sys.path.insert(0, str(PIPELINE_DIR))
        from web.rag import graph  # 轻量（json+pathlib），延迟导入避免 CLI 顶层耦合 web
        touched = graph.apply_gap_links(processed_by_file)
        if touched:
            logger.info(f"  🔗 缺口笔记并入图谱：{touched} 条关联边")
        else:
            logger.info("  (本次无缺口笔记需要建立关联)")
    except Exception as e:
        logger.warning(f"  (缺口关联跳过：{e})")

    logger.info(f"完成。processed={processed}, failed={failed}, skipped={skipped}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="KnowledgeX 收件箱处理管道")
    parser.add_argument("--dry-run", action="store_true", help="只扫描列出，不抓取不写")
    parser.add_argument("--no-llm", action="store_true", help="抓取并 staging，但不调 LLM 不归位")
    parser.add_argument("--only", help="子串过滤目标（debug 用）")
    args = parser.parse_args()
    return run(dry_run=args.dry_run, no_llm=args.no_llm, only=args.only)


if __name__ == "__main__":
    sys.exit(main())
