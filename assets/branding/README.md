# 品牌图标 · 个人知识助手

「知识星座」—— 卫星笔记沿连线汇聚到中央发光中枢，呼应"问答越多、神经连接越密"。
配色取自 Web 端 `app.css`：dusk 紫 `#7c3aed/#c4b5fd`、sunset 橙 `#ff7a17/#ffc285`、
breeze 蓝 `#a0c3ec`，深色圆角方块底。

## 文件

| 文件 | 用途 |
|------|------|
| `make_icon.py` | 母版生成脚本（Pillow 程序化绘制，2× 超采样）→ `icon_master_1024.png` |
| `icon_master_1024.png` | 1024×1024 母版，改设计只需改 `make_icon.py` 后重跑 |
| `generate_set.py` | 把母版切成 Tauri 全套（PNG / `.icns` / `.ico`）→ `icons/` |
| `icons/` | 成品图标集，文件名与 `tauri icon` 默认产物一致 |

## 重新生成

```bash
python3 -m venv .venv && .venv/bin/pip install Pillow
.venv/bin/python make_icon.py        # 母版
.venv/bin/python generate_set.py     # 全套
```

## 合并进 Tauri 桌面端

`tauri.conf.json` 的 `bundle.icon` 引用 `icons/` 下的 PNG，把本目录 `icons/` 整个拷到桌面端图标目录即可：

```bash
cp assets/branding/icons/* desktop/src-tauri/icons/
```

dev（`npm run tauri:dev`）只需 `32x32.png / 128x128.png / 128x128@2x.png / icon.png`；
`tauri:build` 还会用到 `icon.icns`（macOS）、`icon.ico`（Windows）——已一并生成。

> 在 macOS 上若装了 Tauri CLI，也可直接 `cd desktop && npx tauri icon ../path/icon_master_1024.png`
> 由官方工具重新切图（产物等价）。

## Web 端

`icons/` 的几张已拷到 `web/static/icons/` 并接入 `index.html` / `login.html` 的 favicon。
