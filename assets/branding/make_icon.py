#!/usr/bin/env python3
"""生成「个人知识助手」应用图标 —— 知识星座（卫星笔记汇聚成发光中枢）。

设计语言取自 Web 端调色板：dusk 紫 (#7c3aed/#c4b5fd)、sunset 橙 (#ff7a17/#ffc285)、
breeze 蓝 (#a0c3ec)，深色 squircle 底。主题呼应"问答越多、神经连接越密"。

用法：
    python make_icon.py                # 生成 icon_master_1024.png
渲染采用 2x 超采样后 LANCZOS 缩小，边缘平滑。
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

OUT = Path(__file__).parent
SS = 2                      # 超采样倍率
SIZE = 1024 * SS
HALF = SIZE // 2

# ---- 调色板（与 web/static/css/app.css 同源）-------------------------------
BG_TOP = (38, 26, 64)       # 深紫
BG_BOT = (10, 9, 16)        # 近黑
GLOW_WARM = (255, 122, 23)  # sunset 橙
HUB = (255, 138, 38)
HUB_CORE = (255, 224, 188)
VIOLET = (196, 181, 253)    # twilight
BREEZE = (160, 195, 236)
DUSK = (140, 95, 230)


def lerp(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(len(a)))


def rounded_mask(size: int, radius_frac: float, margin_frac: float) -> Image.Image:
    """macOS 风格圆角方块遮罩（含外边距留白）。"""
    m = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(m)
    mg = int(size * margin_frac)
    r = int(size * radius_frac)
    d.rounded_rectangle([mg, mg, size - mg, size - mg], radius=r, fill=255)
    return m


def vertical_gradient(size: int, top, bot) -> Image.Image:
    grad = Image.new("RGB", (1, size))
    px = grad.load()
    for y in range(size):
        px[0, y] = lerp(top, bot, y / (size - 1))
    return grad.resize((size, size))


def radial_glow(size: int, center, color, radius, intensity=1.0) -> Image.Image:
    """柔光圆斑（RGBA）。"""
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    cx, cy = center
    d.ellipse([cx - radius, cy - radius, cx + radius, cy + radius],
              fill=color + (int(255 * intensity),))
    return layer.filter(ImageFilter.GaussianBlur(radius * 0.55))


def glowing_node(canvas: Image.Image, center, color, r, glow_color=None,
                 glow_mult=2.6, core=None):
    """带光晕的节点：先在独立层画光晕模糊，再叠实心圆 + 高光核。"""
    glow_color = glow_color or color
    cx, cy = center
    glow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gr = r * glow_mult
    gd.ellipse([cx - gr, cy - gr, cx + gr, cy + gr], fill=glow_color + (170,))
    glow = glow.filter(ImageFilter.GaussianBlur(gr * 0.5))
    canvas.alpha_composite(glow)

    d = ImageDraw.Draw(canvas)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color + (255,))
    if core:
        cr = r * 0.42
        d.ellipse([cx - cr, cy - cr, cx + cr, cy + cr], fill=core + (255,))


def main():
    # 1) 底：渐变 + 暖色径向辉光
    base = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    base.paste(vertical_gradient(SIZE, BG_TOP, BG_BOT), (0, 0))
    base.alpha_composite(radial_glow(SIZE, (HALF, HALF),
                                     GLOW_WARM, int(SIZE * 0.30), 0.42))
    base.alpha_composite(radial_glow(SIZE, (int(SIZE * 0.28), int(SIZE * 0.30)),
                                     DUSK, int(SIZE * 0.30), 0.28))

    # 2) 卫星节点（围绕中枢的知识笔记），坐标基于 1024 设计，乘 SS
    hub = (512, 512)
    sats = [
        ((512, 196), VIOLET, 40),
        ((786, 360), BREEZE, 46),
        ((806, 678), VIOLET, 38),
        ((560, 838), BREEZE, 42),
        ((236, 742), VIOLET, 44),
        ((196, 408), BREEZE, 40),
        ((360, 300), DUSK, 30),
        ((680, 560), DUSK, 28),
    ]
    hub = tuple(v * SS for v in hub)
    sats = [((x * SS, y * SS), c, r * SS) for (x, y), c, r in sats]

    # 3) 连线（笔记 → 中枢），在模糊层上画出柔和"神经"连接
    edges = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    ed = ImageDraw.Draw(edges)
    for (pt, col, _r) in sats:
        ed.line([pt, hub], fill=col + (165,), width=max(2, int(6 * SS)))
    # 卫星之间少量横向连接，丰富"网络感"
    ring_links = [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 0)]
    for a, b in ring_links:
        ed.line([sats[a][0], sats[b][0]], fill=(120, 110, 160, 60),
                width=max(1, int(2.4 * SS)))
    edges = edges.filter(ImageFilter.GaussianBlur(1.2 * SS))
    base.alpha_composite(edges)

    # 4) 卫星节点
    for (pt, col, r) in sats:
        glowing_node(base, pt, col, r, glow_mult=2.2)

    # 5) 中枢（sunset 橙 + 暖白核）—— 视觉主角
    glowing_node(base, hub, HUB, int(96 * SS), glow_color=GLOW_WARM,
                 glow_mult=3.0, core=HUB_CORE)
    # 中枢外圈细描，增强"汇聚"焦点
    d = ImageDraw.Draw(base)
    rr = int(96 * SS)
    d.ellipse([hub[0] - rr - 10 * SS, hub[1] - rr - 10 * SS,
               hub[0] + rr + 10 * SS, hub[1] + rr + 10 * SS],
              outline=HUB_CORE + (90,), width=int(3 * SS))

    # 6) 套圆角方块遮罩
    mask = rounded_mask(SIZE, radius_frac=0.225, margin_frac=0.085)
    out = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    out.paste(base, (0, 0), mask)

    # 顶部细微高光，增加立体玻璃感
    hi = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    hd = ImageDraw.Draw(hi)
    mg = int(SIZE * 0.085)
    hd.rounded_rectangle([mg, mg, SIZE - mg, int(SIZE * 0.46)],
                         radius=int(SIZE * 0.2), fill=(255, 255, 255, 16))
    hi = hi.filter(ImageFilter.GaussianBlur(8 * SS))
    out.alpha_composite(Image.composite(hi, Image.new("RGBA", out.size, (0, 0, 0, 0)), mask))

    # 7) 缩小输出
    final = out.resize((1024, 1024), Image.LANCZOS)
    p = OUT / "icon_master_1024.png"
    final.save(p)
    print(f"✓ {p}  ({final.size[0]}x{final.size[1]})")


if __name__ == "__main__":
    main()
