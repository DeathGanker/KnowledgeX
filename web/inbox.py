"""收件箱处理：在 Web 端触发 process_inbox 管道，SSE 流式转发日志

完全复用 scripts/process_inbox.py（子进程运行），不改管道逻辑、不碰其导入路径。
process_inbox 的 logger 带 StreamHandler，子进程 stdout 即逐行日志。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import date, datetime
from typing import Iterator

from web.config import PIPELINE_CONFIG, PIPELINE_DIR, VAULT_ROOT


SCRIPT = PIPELINE_DIR / "scripts" / "process_inbox.py"

# 可上传的本地文件类型：
#  - 文档 pdf/doc/docx → pdf/docx fetcher 本地抽文本
#  - 图片/视频 → media fetcher 走豆包多模态识别（需配 VISION_MODEL）
_DOC_EXTS = {".pdf", ".doc", ".docx"}
_MEDIA_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp",
               ".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"}
_UPLOAD_EXTS = _DOC_EXTS | _MEDIA_EXTS
_UPLOAD_MAX_BYTES = 300 * 1024 * 1024  # 300MB（视频可能较大）


def save_uploaded_to_inbox(filename: str, data: bytes, use_model: bool = False) -> dict:
    """把上传的本地文件存进收件箱目录，之后「处理收件箱」抽取/识别 → 消化 → 归位。

    use_model 仅对 PDF/Word 生效：True 表示用户选了「大模型识别」（更准、能读扫描件/图表），
    此时写一个同名 .fetch 旁标，scan.py 据此把该文档改派到 media（豆包多模态）而非本地抽取。
    图片/视频本来就走 media，与该开关无关。
    """
    name = (filename or "").strip()
    ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
    if ext not in _UPLOAD_EXTS:
        raise ValueError(f"暂不支持的文件类型：{ext or '无扩展名'}（支持 PDF / Word / 图片 / 视频）")
    if not data:
        raise ValueError("文件为空")
    if len(data) > _UPLOAD_MAX_BYTES:
        raise ValueError(f"文件过大（>{_UPLOAD_MAX_BYTES // 1024 // 1024}MB）")

    stem = re.sub(r'[\\/:*?"<>|]', "", name.rsplit(".", 1)[0]).strip() or "上传文件"
    inbox_dir = PIPELINE_CONFIG.get("inbox_dir", "00-收件箱")
    inbox_path = VAULT_ROOT / inbox_dir
    inbox_path.mkdir(parents=True, exist_ok=True)

    target = inbox_path / f"{stem}{ext}"
    n = 2
    while target.exists():
        target = inbox_path / f"{stem}-{n}{ext}"
        n += 1
    target.write_bytes(data)

    is_doc = ext in _DOC_EXTS
    parser = "本地抽取"
    if is_doc and use_model:
        # 旁标以 "." 开头，scan 会跳过它本身，仅作为路由提示
        (inbox_path / ("." + target.name + ".fetch")).write_text("media", encoding="utf-8")
        parser = "大模型识别"
    elif not is_doc:
        parser = "大模型识别"  # 图片/视频固定走多模态

    return {"file": str(target.relative_to(VAULT_ROOT)), "name": target.name, "parser": parser}


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
