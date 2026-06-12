"""VAULT_ROOT（笔记目录）解析 —— web 端与管道脚本共用的单一来源。

历史上 VAULT_ROOT 写死成「.pipeline 的父目录」，因为 .pipeline 嵌在 Obsidian vault 里。
脱离 Obsidian 独立运行后，代码仓库（=.pipeline）和笔记数据应当解耦，于是改为可配置：

优先级：
1) 环境变量 / .env 里的 VAULT_ROOT（支持 ~ 展开）—— 独立部署时手动指定，每台机器各配各的。
2) 兼容旧布局：若 .pipeline 的父目录已经是 vault（含已知笔记目录），沿用之，旧用户零改动。
3) 全新克隆、独立运行：默认 ~/KnowledgeX（首次运行 ensure_vault 自动建标准目录）。

本模块在 import 时幂等加载一次 .pipeline/.env（override=False，不覆盖已设环境变量），
确保无论被 web.config 还是管道脚本先导入，VAULT_ROOT 都能读到 .env 的配置。
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

try:
    from dotenv import load_dotenv
except Exception:  # dotenv 理论上一定在，缺了也不要让路径解析崩
    load_dotenv = None

PIPELINE_DIR = Path(__file__).resolve().parent.parent

if load_dotenv is not None:
    load_dotenv(PIPELINE_DIR / ".env", override=False)

# 标准笔记子目录：首次在新建 vault 下创建，保证文件树非空、管道有处可落。
STANDARD_DIRS = ["00-收件箱", "01-笔记", "02-领域", "03-资源", "04-项目", "05-归档"]
# 判断「父目录是否已是旧 vault」的标志目录
_LEGACY_MARKERS = ("01-笔记", "00-收件箱", "02-领域")


def resolve_vault_root() -> Path:
    env_val = os.environ.get("VAULT_ROOT")
    if env_val and env_val.strip():
        return Path(env_val).expanduser().resolve()
    parent = PIPELINE_DIR.parent
    if any((parent / m).exists() for m in _LEGACY_MARKERS):
        return parent
    return (Path.home() / "KnowledgeX").resolve()


def ensure_vault(root: Path) -> Path:
    """创建 vault 根并幂等补齐标准子目录（已存在的不动）。返回 root 方便链式。

    幂等而非"仅新建时补齐"：用户可能先手动建好目标目录再指过来，
    那种情况也要保证 00-收件箱 等标准目录存在，否则录入/扫描会落空或报错。
    """
    root.mkdir(parents=True, exist_ok=True)
    for d in STANDARD_DIRS:
        (root / d).mkdir(exist_ok=True)
    return root


VAULT_ROOT = resolve_vault_root()


# .env 可覆盖的端点配置：键名 → (config.yaml 段, 段内字段)。
# 账号专属的接入点 ID / 自定义端点放 .env，config.yaml 只留公共默认值与占位，
# 这样 config.yaml 可安全提交、换服务商或换接入点时只动 .env。
_CONFIG_ENV_OVERRIDES = {
    "LLM_BASE_URL": ("llm", "base_url"),
    "LLM_MODEL": ("llm", "model"),
    "EMBEDDING_URL": ("embedding", "url"),
    "EMBEDDING_MODEL": ("embedding", "model"),
}


def load_pipeline_config() -> dict:
    """读 config.yaml 并叠加 .env 的端点覆盖（base_url / model / embedding url+model）。

    web 端与各管道脚本统一走这里，确保「.env 覆盖 config.yaml」的行为一致。
    .env 里对应变量为空/未设时，沿用 config.yaml 的值。
    """
    cfg = yaml.safe_load((PIPELINE_DIR / "config.yaml").read_text(encoding="utf-8")) or {}
    for env_key, (section, field) in _CONFIG_ENV_OVERRIDES.items():
        val = os.environ.get(env_key, "").strip()
        if val:
            cfg.setdefault(section, {})[field] = val
    return cfg
