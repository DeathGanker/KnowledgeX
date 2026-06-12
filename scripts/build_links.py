#!/usr/bin/env python3
"""命令行：给 vault 所有已索引笔记自动建双链。

用法：
  python scripts/build_links.py                  # 默认 top5, 阈值 0.45, 带 AI 理由
  python scripts/build_links.py --top-k 4 --threshold 0.5
  python scripts/build_links.py --no-reasons     # 只建链接不调 LLM（快）
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PIPELINE_DIR = SCRIPT_DIR.parent
# web 包在 .pipeline 下，需把 .pipeline 加入 path
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(PIPELINE_DIR / ".env", override=True)

from web.rag import linker  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="自动给笔记建双链")
    ap.add_argument("--top-k", type=int, default=5, help="每篇最多关联几条")
    ap.add_argument("--threshold", type=float, default=0.45, help="相似度阈值（0-1）")
    ap.add_argument("--no-reasons", action="store_true", help="不调 LLM 生成关联理由（更快）")
    args = ap.parse_args()

    stats = linker.link_all(
        top_k=args.top_k,
        threshold=args.threshold,
        with_reasons=not args.no_reasons,
        progress=lambda m: print(" ", m),
    )
    print(f"\n统计: {stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
