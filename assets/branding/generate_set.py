#!/usr/bin/env python3
"""把 icon_master_1024.png 切成 Tauri 需要的全套图标（PNG / .icns / .ico）。

输出到 ./icons/，文件名与 `tauri icon` 默认产物一致，可直接拷进
src-tauri/icons/ 供 `npm run tauri:dev` / `tauri:build` 使用。
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

HERE = Path(__file__).parent
SRC = HERE / "icon_master_1024.png"
OUT = HERE / "icons"
OUT.mkdir(exist_ok=True)

master = Image.open(SRC).convert("RGBA")


def resize(px: int) -> Image.Image:
    return master.resize((px, px), Image.LANCZOS)


def save(img: Image.Image, name: str):
    img.save(OUT / name)
    print(f"  ✓ {name:24} {img.size[0]}x{img.size[1]}")


# --- tauri.conf.json 直接引用的 4 个（dev 必需）---------------------------
save(resize(32), "32x32.png")
save(resize(128), "128x128.png")
save(resize(256), "128x128@2x.png")
save(resize(512), "icon.png")

# --- Windows Store / UWP 方形徽标（tauri icon 也会产出）-------------------
square = {
    "Square30x30Logo.png": 30,
    "Square44x44Logo.png": 44,
    "Square71x71Logo.png": 71,
    "Square89x89Logo.png": 89,
    "Square107x107Logo.png": 107,
    "Square142x142Logo.png": 142,
    "Square150x150Logo.png": 150,
    "Square284x284Logo.png": 284,
    "Square310x310Logo.png": 310,
    "StoreLogo.png": 50,
}
for name, px in square.items():
    save(resize(px), name)

# --- Windows .ico（多分辨率）---------------------------------------------
ico = OUT / "icon.ico"
master.save(ico, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
                        (128, 128), (256, 256)])
print(f"  ✓ {'icon.ico':24} multi-res")

# --- macOS .icns ----------------------------------------------------------
icns = OUT / "icon.icns"
# Pillow 要求方形 RGBA；提供 1024 源即可，内部生成各档
master.save(icns)
print(f"  ✓ {'icon.icns':24} multi-res")

print(f"\n全套已生成 → {OUT}")
