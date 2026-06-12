"""图片 / 视频 → 火山方舟（豆包）多模态：Files API 上传 + Responses API 理解 → 文字材料。

参考自 LocateAnything/ark_client.py 跑通的视频流程：
  上传(/files) → 轮询 active → /responses(input_image|input_video + input_text) → 取 output 文本
不本地 OCR，直接用模型读图/读视频，产出供 digest 整理成笔记的原始材料。
需在 .env 配 VISION_MODEL（豆包视觉/视频接入点 ID）；未配则跳过该文件。
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import requests

from fetchers.base import FetcherResult, failed, skipped, staged

_VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"}
_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
    ".gif": "image/gif", ".bmp": "image/bmp",
    ".mp4": "video/mp4", ".mov": "video/quicktime", ".m4v": "video/x-m4v",
    ".webm": "video/webm", ".avi": "video/x-msvideo", ".mkv": "video/x-matroska",
}

_EXTRACT_PROMPT = (
    "请把这个素材里的内容尽量完整地转写成中文文字材料：画面/页面中的文字、关键信息、数据、"
    "流程步骤、要点、场景与结论都提取出来，分点组织，供后续整理成知识笔记。"
    "直接输出材料本身，不要客套话、不要复述本提示。"
)


def fetch(file_path: str, *, config: dict) -> FetcherResult:
    vc = (config or {}).get("vision", {}) or {}
    model = (vc.get("model") or "").strip()
    base_url = (vc.get("base_url") or "https://ark.cn-beijing.volces.com/api/v3").strip().rstrip("/")
    api_key = os.environ.get(vc.get("api_key_env", "ARK_API_KEY"), "").strip()

    if not model or model.startswith("your-"):
        return skipped("media", file_path, "未配置视觉接入点（在 .env 设 VISION_MODEL）")
    if not api_key:
        return skipped("media", file_path, "未设置 ARK_API_KEY")

    p = Path(file_path)
    if not p.exists():
        return failed("media", file_path, "文件不存在")

    ext = p.suffix.lower()
    is_video = ext in _VIDEO_EXTS
    content_type = "input_video" if is_video else "input_image"
    mime = _MIME.get(ext, "application/octet-stream")
    headers = {"Authorization": f"Bearer {api_key}"}
    timeout = int(vc.get("timeout_seconds", 300))

    try:
        # 1) 上传到 Files API（视频附抽帧 fps）
        with open(p, "rb") as fh:
            files = {"file": (p.name, fh, mime)}
            data = {"purpose": "user_data"}
            if is_video:
                data["preprocess_configs[video][fps]"] = str(vc.get("fps", 1.0))
            r = requests.post(f"{base_url}/files", headers=headers, files=files, data=data, timeout=timeout)
        r.raise_for_status()
        file_id = r.json().get("id")
        if not file_id:
            return failed("media", file_path, f"上传未返回 file id：{r.text[:200]}")

        # 2) 轮询到 active（服务端抽帧/预处理）
        deadline = time.time() + int(vc.get("active_timeout_seconds", 180))
        while True:
            st = requests.get(f"{base_url}/files/{file_id}", headers=headers, timeout=30).json().get("status", "")
            if st == "active":
                break
            if st in ("failed", "error", "expired"):
                return failed("media", file_path, f"文件预处理失败 status={st}")
            if time.time() > deadline:
                return failed("media", file_path, "文件未在限定时间内就绪（视频偏大？）")
            time.sleep(3)

        # 3) Responses API 理解
        payload = {
            "model": model,
            "input": [{"role": "user", "content": [
                {"type": content_type, "file_id": file_id},
                {"type": "input_text", "text": _EXTRACT_PROMPT},
            ]}],
            "max_output_tokens": int(vc.get("max_output_tokens", 4096)),
        }
        rr = requests.post(f"{base_url}/responses",
                           headers={**headers, "Content-Type": "application/json"},
                           json=payload, timeout=timeout)
        rr.raise_for_status()
        body = rr.json()
    except Exception as e:
        return failed("media", file_path, f"视觉模型调用失败：{e}")

    # 4) 取 output 文本
    parts: list[str] = []
    for item in body.get("output", []):
        if item.get("type") == "message":
            for c in item.get("content", []):
                if c.get("type") == "output_text":
                    parts.append(c.get("text", ""))
    text = "\n".join(parts).strip()
    if not text:
        return failed("media", file_path, "视觉模型未返回可用内容")

    kind = "视频" if is_video else "图片"
    content = f"# {p.stem}\n\n> 来源：本地{kind}「{p.name}」，经豆包视觉模型识别\n\n{text}"
    return staged(fetcher="media", source=file_path, title=p.stem, content=content,
                  meta={"media_type": "video" if is_video else "image", "file_id": file_id})
