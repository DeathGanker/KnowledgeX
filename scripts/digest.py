"""LLM 消化：把 staging 原始材料 → 结构化中文 markdown 笔记"""
from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv
from openai import OpenAI

from fetchers.base import FetcherResult
from paths import llm_extra_body
from persona import render_persona, render_taxonomy


PIPELINE_DIR = Path(__file__).resolve().parent.parent
PROMPT_FILE = PIPELINE_DIR / "prompts" / "literature_zh.md"
load_dotenv(PIPELINE_DIR / ".env", override=True)


def _load_prompt_template() -> tuple[str, str]:
    """从 prompts/literature_zh.md 拆出 SYSTEM 和 USER 段。"""
    raw = PROMPT_FILE.read_text(encoding="utf-8")
    # 按 `## SYSTEM` / `## USER（每次调用时附上）` 拆分
    parts = re.split(r"^## (SYSTEM|USER[^\n]*)\s*$", raw, flags=re.MULTILINE)
    # parts 形如 ['前言', 'SYSTEM', '<system body>', 'USER（...）', '<user body>']
    system_body = ""
    user_body = ""
    for i in range(1, len(parts), 2):
        header = parts[i].strip()
        body = parts[i + 1].strip()
        if header == "SYSTEM":
            system_body = body
        elif header.startswith("USER"):
            user_body = body
    if not system_body or not user_body:
        raise RuntimeError("prompts/literature_zh.md 缺少 SYSTEM 或 USER 段")
    return system_body, user_body


def _build_messages(fetched: FetcherResult) -> list[dict]:
    system, user_tpl = _load_prompt_template()
    # 注入单一来源的用户画像（替换 literature_zh.md 里的 __PERSONA__ 占位符）
    system = system.replace("__PERSONA__", render_persona())
    # 注入单一来源的目录体系（让 LLM 看到所有可选目录+语义边界，准确归位）
    user = user_tpl.format(
        source=fetched.source,
        fetcher=fetched.fetcher,
        date=date.today().isoformat(),
        taxonomy=render_taxonomy(),
        content=fetched.content,
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def digest(fetched: FetcherResult, *, llm_cfg: dict) -> tuple[str, Optional[str]]:
    """返回 (markdown_text, error)。error 为 None 表示成功。"""
    if not fetched.ok or fetched.status != "staged":
        return "", f"无效的 staging 输入: status={fetched.status}, error={fetched.error}"

    api_key_env = llm_cfg.get("api_key_env", "")
    api_key = os.environ.get(api_key_env)
    if not api_key:
        return "", f"环境变量 {api_key_env} 未设置（请检查 .pipeline/.env）"

    timeout = llm_cfg.get("timeout_seconds", 120)
    http_client = None
    if llm_cfg.get("bypass_system_proxy", False):
        # macOS 系统代理（Clash 等）通过 CFNetwork 传播，trust_env=False 拦不住，
        # 必须用 mounts={"all://": None} 显式声明所有协议都不走任何代理。
        http_client = httpx.Client(
            trust_env=False,
            mounts={"all://": None},
            timeout=timeout,
        )

    client = OpenAI(
        base_url=llm_cfg["base_url"],
        api_key=api_key,
        timeout=timeout,
        http_client=http_client,
    )
    messages = _build_messages(fetched)

    try:
        resp = client.chat.completions.create(
            model=llm_cfg["model"],
            extra_body=llm_extra_body(llm_cfg),
            messages=messages,
            temperature=llm_cfg.get("temperature", 0.3),
            max_tokens=llm_cfg.get("max_tokens", 8000),
        )
    except Exception as e:
        return "", f"LLM 调用失败: {e}"

    text = (resp.choices[0].message.content or "").strip()
    # 容错 1：有时模型会在 frontmatter 前加 ```markdown 包裹
    if not text.startswith("---"):
        m = re.search(r"---\n.*", text, re.DOTALL)
        if m:
            text = m.group(0)
        else:
            return "", "LLM 输出未以 frontmatter 开头，疑似格式不合规"
    # 容错 2：模型尾部偶尔多带一个 ``` 闭合标记，剔除
    text = re.sub(r"\n+`{3,}\s*$", "\n", text).rstrip() + "\n"
    return text, None
