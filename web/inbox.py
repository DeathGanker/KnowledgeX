"""收件箱处理：在 Web 端触发 process_inbox 管道，SSE 流式转发日志

完全复用 scripts/process_inbox.py（子进程运行），不改管道逻辑、不碰其导入路径。
process_inbox 的 logger 带 StreamHandler，子进程 stdout 即逐行日志。
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, datetime
from typing import Iterator

from web.config import PIPELINE_CONFIG, PIPELINE_DIR, VAULT_ROOT


SCRIPT = PIPELINE_DIR / "scripts" / "process_inbox.py"


def add_to_inbox(text: str) -> dict:
    """App 内录入：把链接/文字追加到当日收件箱文件 00-收件箱/录入-YYYY-MM-DD.md。

    之后点「处理收件箱」由现有管道抓取（含 URL 的行）→ 消化 → 归位。
    取代手动在 Obsidian 里编辑收件箱文件，是脱离 Obsidian 的关键入口。
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("内容不能为空")

    inbox_dir = PIPELINE_CONFIG.get("inbox_dir", "00-收件箱")
    inbox_path = VAULT_ROOT / inbox_dir
    inbox_path.mkdir(parents=True, exist_ok=True)

    today = date.today().isoformat()
    target = inbox_path / f"录入-{today}.md"
    ts = datetime.now().strftime("%H:%M")
    line = f"- {text}  <!-- {ts} -->\n"

    if target.exists():
        existing = target.read_text(encoding="utf-8")
        sep = "" if existing.endswith("\n") else "\n"
        target.write_text(existing + sep + line, encoding="utf-8")
    else:
        target.write_text(f"# 录入 · {today}\n\n{line}", encoding="utf-8")

    return {"file": str(target.relative_to(VAULT_ROOT)), "added": text}


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def stream_process() -> Iterator[str]:
    """运行收件箱管道，逐行把日志转成 SSE 事件。

    事件：start → log*（每行一条）→ end（带退出码）；异常时发 error。
    """
    yield _sse("start", {"script": str(SCRIPT.relative_to(PIPELINE_DIR))})

    if not SCRIPT.exists():
        yield _sse("error", {"message": f"找不到脚本: {SCRIPT}"})
        yield _sse("end", {"code": -1})
        return

    try:
        proc = subprocess.Popen(
            [sys.executable, "-u", str(SCRIPT)],
            cwd=str(PIPELINE_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            encoding="utf-8",
            errors="replace",
        )
    except Exception as e:  # noqa: BLE001
        yield _sse("error", {"message": f"启动失败: {e}"})
        yield _sse("end", {"code": -1})
        return

    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip("\n")
            if line:
                yield _sse("log", {"line": line})
        code = proc.wait()
    except Exception as e:  # noqa: BLE001
        proc.kill()
        yield _sse("error", {"message": f"处理中断: {e}"})
        yield _sse("end", {"code": -1})
        return

    yield _sse("end", {"code": code})
