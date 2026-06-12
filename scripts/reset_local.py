"""把本地环境重置到「全新克隆、从未运行」的静默状态，用于反复测试首次体验。

保留：.env（密钥/配置）、config.yaml、profile.yaml.example、全部代码。
清除：
  - 运行态缓存（state.json / rag_index / staging / conversations / logs）—— 总是清
  - profile.yaml —— 清掉后重新触发首次画像引导（--keep-profile 可保留）
  - 笔记库 vault 的标准目录 —— 清空已归位笔记、收件箱重置为起步文件（破坏性！
    默认会问你确认，--keep-vault 完全不动 vault，--yes 跳过确认）

用法：
  .venv/bin/python scripts/reset_local.py            # 交互确认后全量重置
  .venv/bin/python scripts/reset_local.py --keep-vault   # 只重置运行态+画像，不碰笔记
  .venv/bin/python scripts/reset_local.py --yes          # 跳过确认（慎用）
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from paths import PIPELINE_DIR, VAULT_ROOT, STANDARD_DIRS, ensure_vault  # noqa: E402

# 代码仓库目录下的运行态缓存（可再生）
_RUNTIME_DIRS = ["rag_index", "staging", "conversations", "logs"]
_RUNTIME_FILES = ["state.json"]


def _rm_dir(p: Path) -> bool:
    if p.is_dir():
        shutil.rmtree(p)
        p.mkdir()  # 留一个空目录，避免下游 mkdir 时序问题
        return True
    return False


def _rm_file(p: Path) -> bool:
    if p.exists():
        p.unlink()
        return True
    return False


def reset_runtime() -> list[str]:
    done = []
    for name in _RUNTIME_DIRS:
        if _rm_dir(PIPELINE_DIR / name):
            done.append(f"清空 {name}/")
    for name in _RUNTIME_FILES:
        if _rm_file(PIPELINE_DIR / name):
            done.append(f"删除 {name}")
    return done


def reset_profile() -> list[str]:
    return ["删除 profile.yaml（下次启动重新走画像引导）"] if _rm_file(PIPELINE_DIR / "profile.yaml") else []


def reset_vault() -> list[str]:
    done = []
    for d in STANDARD_DIRS:
        p = VAULT_ROOT / d
        if p.is_dir():
            shutil.rmtree(p)
            done.append(f"清空 {d}/")
    ensure_vault(VAULT_ROOT)  # 重建标准目录 + 收件箱起步文件
    done.append("重建标准目录 + 收件箱起步文件（GitHub 链接 / 微信分享）")
    return done


def main() -> int:
    ap = argparse.ArgumentParser(description="把本地环境重置到全新静默状态")
    ap.add_argument("--keep-vault", action="store_true", help="不动笔记库 vault（只重置运行态+画像）")
    ap.add_argument("--keep-profile", action="store_true", help="保留 profile.yaml（不重走画像引导）")
    ap.add_argument("--yes", "-y", action="store_true", help="跳过确认")
    args = ap.parse_args()

    wipe_vault = not args.keep_vault

    print("\n▎本地环境重置 —— 将回到「全新克隆、从未运行」状态")
    print(f"  代码目录 : {PIPELINE_DIR}")
    print(f"  笔记 vault: {VAULT_ROOT}")
    print("\n会执行：")
    print(f"  • 清空运行态缓存：{', '.join(_RUNTIME_DIRS)} + {', '.join(_RUNTIME_FILES)}")
    if not args.keep_profile:
        print("  • 删除 profile.yaml（重新触发画像引导）")
    if wipe_vault:
        print(f"  • ⚠️  清空 vault 的 {len(STANDARD_DIRS)} 个标准目录（含已归位笔记！），收件箱重置为起步文件")
    else:
        print("  • 保留 vault 笔记不动")
    print("\n保留不动：.env、config.yaml、profile.yaml.example、代码")

    if not args.yes:
        prompt = "\n确认重置？" + ("此操作会删除 vault 里的笔记，输入 RESET 继续：" if wipe_vault else "输入 y 继续：")
        ans = input(prompt).strip()
        ok = (ans == "RESET") if wipe_vault else (ans.lower() in ("y", "yes"))
        if not ok:
            print("已取消。")
            return 1

    done = reset_runtime()
    if not args.keep_profile:
        done += reset_profile()
    if wipe_vault:
        done += reset_vault()

    print("\n✓ 重置完成：")
    for line in done:
        print(f"   - {line}")
    print("\n下一步：启动后端（web.command / python -m web.app），首次打开会重新弹出画像引导。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
