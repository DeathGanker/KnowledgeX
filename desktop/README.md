# 个人知识助手 · 桌面端（Tauri）

给现有的 FastAPI 知识库套一个原生桌面壳 —— **不改动 BS（浏览器）架构**，桌面端复用同一套后端。
`npm run tauri:dev` 会自动拉起后端、再开一个原生窗口加载它。

```
┌─────────── Tauri 窗口（WebView）──────────┐
│   加载 http://127.0.0.1:7346             │
└───────────────┬──────────────────────────┘
                │ beforeDevCommand
                ▼
   start-backend.sh → .venv/bin/python -m web.app
   （WEB_HOST=127.0.0.1  WEB_PORT=7346  DESKTOP_LOCAL=1）
```

`DESKTOP_LOCAL=1` 让后端跳过 token 鉴权（仅绑定 127.0.0.1，本机 webview 独占）；
浏览器模式（`web.command` / `web.app`）不设此变量，token 鉴权照常。两种模式端口不同，可并存。

## 前置依赖（macOS，一次性）

1. **后端 venv**（若还没建，在仓库根目录）：
   ```bash
   python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
   ```
2. **Rust 工具链**（Tauri 必需）：https://rustup.rs
   ```bash
   curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
   ```
3. **Xcode Command Line Tools**：`xcode-select --install`
4. **Node 依赖**：
   ```bash
   cd desktop && npm install
   ```

## 运行

```bash
cd desktop
npm run tauri:dev        # 拉起后端 + 打开桌面窗口（首次会编译 Rust，较慢）
```

或双击 `desktop/桌面端.command`（自动 `npm install` + `tauri:dev`）。

## 文件

| 文件 | 作用 |
|------|------|
| `package.json` | `tauri:dev` / `tauri:build` 脚本 + `@tauri-apps/cli` |
| `start-backend.sh` | Tauri `beforeDevCommand`，前台启动 FastAPI（127.0.0.1:7346，免 token） |
| `src-tauri/tauri.conf.json` | 窗口、`devUrl`、图标、bundle 配置 |
| `src-tauri/src/main.rs` | Tauri 壳：全局快捷键 ⌘⇧C / 托盘 / 桌宠窗口 + 命令 `toggle_pet`·`set_clip_monitor`·`take_clip_image` |
| `src-tauri/icons/` | 应用图标（来自 `assets/branding/icons/`） |
| `dist/index.html` | 仅满足 `frontendDist`，dev 不用到 |

## 桌宠投喂（桌面端特有）

主窗口之外再开一个常驻置顶的「桌宠」小窗（`tauri.conf.json` 的 `pet` 窗口，加载 `/static/pet/`），作零摩擦投喂入口，全部复用现有 `/api/inbox/*` + `/api/jobs/*`，消化管道不改：

- **⌘⇧C**：把剪贴板的链接/文字/图片投喂进收件箱。
- **拖拽**：把链接或文件拖到桌宠身上。
- **复制即投喂**（桌宠顶栏 👁，opt-in 默认关）：后台监听剪贴板，复制即弹「投喂卡」，10s 不点自动消失（不静默囤积）。
- 投喂后桌宠实时显示「抓取→消化→归位→图谱生长」，并发 `knowledgex-digested` 事件通知主界面刷新文件树；主界面顶栏对应有 **🐾 桌宠**（显隐）与 **🕘 记录**（回溯抓取→归位，含原始链接）。
- 剪贴板图片 → 编码 PNG → 上传 → 豆包视觉识别。

> 剪贴板读取放在 Rust 后台线程（插件在主线程读会 panic）。改 `web/static/pet/*` 后记得碰 `tauri.conf.json` 里 pet 窗口 url 及 `index.html` 引用的 `?v=` 版本号，否则 webview 吃旧缓存。透明自由漂浮、复制密码类型跳过、`tauri:build` 打包成 .app 等为后续。

## 改端口

同时改两处：`start-backend.sh` 的 `DESKTOP_PORT` 默认值 与 `tauri.conf.json` 的 `devUrl`。

## 关于 `tauri:build`（打包成 .app/.dmg）

当前仅为 `dev` 配好。打包后的独立 app 需要把 Python 后端做成 **sidecar** 随应用启动
（`tauri.conf.json > bundle > externalBin` + 把 venv/解释器一起打进去），属于后续工作，
现在不影响本地 `tauri:dev` 使用。
