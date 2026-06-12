"""用户画像（persona）单一来源加载器。

消化（scripts/digest.py，同目录 `from persona import`）与问答
（web/chat.py，`from scripts.persona import`）都从这里取同一份画像，
渲染成一段「服务对象画像」文本，注入各自的 system prompt。

模块名特意用 persona 而非 profile —— 避开 Python 标准库 `profile` 撞名。
零重依赖：只用 yaml + pathlib。
放在 scripts/ 是因为消化管道以 scripts/ 为 sys.path 根（见 process_inbox.py）。
"""
from __future__ import annotations

from pathlib import Path

import yaml

# scripts/persona.py → parent=scripts/，parent.parent=.pipeline（profile.yaml 所在）
PIPELINE_DIR = Path(__file__).resolve().parent.parent
PROFILE_FILE = PIPELINE_DIR / "profile.yaml"

# 缺文件 / 缺字段时的安全默认（与 profile.yaml 初始值一致）
# 缺文件 / 缺字段时的安全默认 —— 中性占位（不含任何个人信息）。
# 用户首次运行时 profile.yaml 尚不存在，靠这份默认让消化/问答先能跑；
# 引导用户在「画像」里构建专属画像后即覆盖（见 is_configured / save_profile）。
_DEFAULT_PERSONA: dict = {
    "role": "技术学习者 / 开发者",
    "working_style": "关注技术原理与工程落地，偏好可复现、能上手的资料",
    "cares_about": ["技术选型", "工程实现", "可落地性"],
    "interests": ["开源项目", "工程实践"],
    "dislikes": ["过时信息", "纯理论无落地", "营销话术"],
    "extra": "",
}


def is_configured() -> bool:
    """用户是否已构建过专属画像（profile.yaml 存在且 persona.role 非空）。
    首次运行返回 False —— 前端据此弹出引导，让用户构建画像。"""
    try:
        raw = yaml.safe_load(PROFILE_FILE.read_text(encoding="utf-8")) or {}
    except (FileNotFoundError, yaml.YAMLError):
        return False
    persona = raw.get("persona") or {}
    return isinstance(persona, dict) and bool((persona.get("role") or "").strip())


def load_profile() -> dict:
    """读 profile.yaml，返回 persona 字典；缺文件/解析失败/缺字段都回退默认。"""
    persona = dict(_DEFAULT_PERSONA)
    try:
        raw = yaml.safe_load(PROFILE_FILE.read_text(encoding="utf-8")) or {}
        loaded = raw.get("persona") or {}
        if isinstance(loaded, dict):
            for k, default in _DEFAULT_PERSONA.items():
                v = loaded.get(k)
                if v:  # 空字符串/空列表/None 都回退默认
                    persona[k] = v
    except (FileNotFoundError, yaml.YAMLError):
        pass
    return persona


def _join(items) -> str:
    if isinstance(items, (list, tuple)):
        return "、".join(str(x) for x in items if x)
    return str(items or "")


def render_persona() -> str:
    """把 persona 渲染成一段紧凑的「服务对象画像」文本块，供 system prompt 注入。"""
    p = load_profile()
    lines = [
        "### 服务对象画像（重要 —— 决定写什么、不写什么）",
        "",
        f"- 角色：{p['role']}",
        f"- 工作方式：{p['working_style']}",
        f"- 关心：{_join(p['cares_about'])}",
        f"- 兴趣：{_join(p['interests'])}",
        f"- **不想看**：{_join(p['dislikes'])}",
    ]
    extra = str(p.get("extra") or "").strip()
    if extra:
        lines.append("")
        lines.append(extra)
    return "\n".join(lines)


# 字段白名单：字符串字段 + 列表字段
_STR_FIELDS = ("role", "working_style", "extra")
_LIST_FIELDS = ("cares_about", "interests", "dislikes")

_PROFILE_HEADER = """\
# 用户画像（persona）—— 单一来源
# 被消化（scripts/digest.py）和问答（web/chat.py）同源读取，由 persona.py 渲染注入各 system prompt。
# 改这里 → 消化下次运行即生效；问答每次调用实时读取（无需重启）。
# 可在 Web 顶栏「画像」配置页通过 AI 引导式交互重新生成。
"""


def _clean_profile(persona: dict) -> dict:
    """只取白名单字段、校验类型，返回规范化 persona（缺失字段回退默认）。"""
    out = dict(_DEFAULT_PERSONA)
    persona = persona or {}
    for k in _STR_FIELDS:
        v = persona.get(k)
        if isinstance(v, str) and v.strip():
            out[k] = v.strip()
        elif k == "extra":
            out[k] = str(v or "").strip()
    for k in _LIST_FIELDS:
        v = persona.get(k)
        if isinstance(v, (list, tuple)):
            items = [str(x).strip() for x in v if str(x).strip()]
            if items:
                out[k] = items
    return out


def _load_raw() -> dict:
    """读整个 profile.yaml（含 persona + taxonomy 段）；失败回退空 dict。"""
    try:
        return yaml.safe_load(PROFILE_FILE.read_text(encoding="utf-8")) or {}
    except (FileNotFoundError, yaml.YAMLError):
        return {}


def _write_profile_file(persona: dict, taxonomy: dict) -> None:
    """把 persona + taxonomy 一起写回 profile.yaml（带头部注释）。"""
    body = yaml.safe_dump(
        {"persona": persona, "taxonomy": taxonomy},
        allow_unicode=True, sort_keys=False, default_flow_style=False,
    )
    PROFILE_FILE.write_text(_PROFILE_HEADER + "\n" + body, encoding="utf-8")


def save_profile(persona: dict) -> dict:
    """保存画像（写回 profile.yaml，保留现有 taxonomy 段）。返回规范化 persona。"""
    clean = _clean_profile(persona)
    _write_profile_file(clean, load_taxonomy())
    return clean


# ---------------- 目录体系（taxonomy）----------------

_DEFAULT_TAXONOMY_DEFAULT = "01-笔记/文献"


def _config_fallback_taxonomy() -> dict:
    """profile.yaml 无 taxonomy 时，从 config.yaml 的 allowed_placement_prefixes 回退（无 desc）。"""
    try:
        cfg = yaml.safe_load((PIPELINE_DIR / "config.yaml").read_text(encoding="utf-8")) or {}
        prefixes = cfg.get("allowed_placement_prefixes") or []
        default = cfg.get("default_placement") or _DEFAULT_TAXONOMY_DEFAULT
        return {"dirs": [{"path": p, "desc": ""} for p in prefixes], "default": default}
    except (FileNotFoundError, yaml.YAMLError):
        return {"dirs": [], "default": _DEFAULT_TAXONOMY_DEFAULT}


def _clean_taxonomy(tax: dict) -> dict:
    """规范化 taxonomy：dirs 每项需有非空 path（desc 可空），default 为字符串。"""
    tax = tax or {}
    dirs = []
    seen = set()
    for d in (tax.get("dirs") or []):
        if not isinstance(d, dict):
            continue
        path = str(d.get("path") or "").strip().rstrip("/")
        if not path or path in seen:
            continue
        seen.add(path)
        dirs.append({"path": path, "desc": str(d.get("desc") or "").strip()})
    default = str(tax.get("default") or "").strip().rstrip("/") or _DEFAULT_TAXONOMY_DEFAULT
    return {"dirs": dirs, "default": default}


def load_taxonomy() -> dict:
    """读 taxonomy；profile.yaml 无该段时回退 config.yaml。"""
    raw = _load_raw().get("taxonomy")
    if raw and isinstance(raw, dict) and raw.get("dirs"):
        return _clean_taxonomy(raw)
    return _config_fallback_taxonomy()


def save_taxonomy(tax: dict) -> dict:
    """保存目录体系（写回 profile.yaml，保留现有 persona 段）。返回规范化 taxonomy。"""
    clean = _clean_taxonomy(tax)
    _write_profile_file(load_profile(), clean)
    return clean


def taxonomy_prefixes() -> list[str]:
    """归位白名单：所有目录 path。"""
    return [d["path"] for d in load_taxonomy()["dirs"]]


def taxonomy_default() -> str:
    """归位兜底目录。"""
    return load_taxonomy()["default"]


def render_taxonomy() -> str:
    """渲染成「可选目录及语义边界」文本块，注入消化 prompt，让 LLM 准确归位。"""
    tax = load_taxonomy()
    lines = ["可选目录（请据笔记主题选最贴切的一个，写进 frontmatter 的 placement 字段）："]
    for d in tax["dirs"]:
        suffix = f" —— {d['desc']}" if d["desc"] else ""
        lines.append(f"- {d['path']}{suffix}")
    lines.append(f"\n拿不准时归到兜底目录：{tax['default']}")
    return "\n".join(lines)
