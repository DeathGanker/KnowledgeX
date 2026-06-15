// 个人知识助手 桌面端：Tauri 壳 + 桌宠投喂。
// 主窗口(main) 复用 FastAPI 前端；桌宠窗口(pet) 是「投喂」入口：
//   - 全局快捷键 ⌘/Ctrl+Shift+C：读剪贴板（文本/图片）→ 弹「投喂卡」
//   - 复制自动监听（opt-in，默认关）：后台轮询剪贴板，变化即弹卡
//   - 拖拽文件/链接到桌宠：前端 HTML5 拖放处理
//   - 投喂复用现有后端 /api/inbox/* + /api/jobs/*，消化管道一行不改
// 注意：剪贴板读取必须在「非主线程、事件循环已起」时进行，否则插件会 panic，
//       故所有 read_text/read_image 都丢到后台线程里做。
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::thread;
use std::time::Duration;

use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Emitter, Manager};
use tauri_plugin_clipboard_manager::ClipboardExt;
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};

/// 剪贴板图片暂存：⌘⇧C 抓到图片时编码成 PNG 存这里，pet 点「喂它」时 take 走上传。
struct ClipImage(Mutex<Option<Vec<u8>>>);

/// 复制自动监听：开关 + 最近一次内容（快捷键/监听共用，避免同一内容重复弹卡）。
struct ClipMonitor {
    enabled: AtomicBool,
    last: Mutex<String>,
}

fn classify(trimmed: &str) -> &'static str {
    if (trimmed.starts_with("http://") || trimmed.starts_with("https://"))
        && !trimmed.chars().any(|c| c.is_whitespace())
    {
        "url"
    } else {
        "text"
    }
}

/// 显示桌宠并发「投喂卡」事件（文本/链接）。focus=true 抢焦点（快捷键用）；监听用 false 不打扰。
fn emit_text_capture(app: &AppHandle, trimmed: &str, focus: bool) {
    if let Ok(mut g) = app.state::<ClipMonitor>().last.lock() {
        *g = trimmed.to_string();
    }
    if let Some(pet) = app.get_webview_window("pet") {
        let _ = pet.show();
        if focus {
            let _ = pet.set_focus();
        }
    }
    let preview: String = trimmed.chars().take(240).collect();
    let _ = app.emit(
        "capture-propose",
        serde_json::json!({ "kind": classify(trimmed), "text": trimmed, "preview": preview }),
    );
}

/// RGBA8 → PNG 字节（剪贴板图片是裸 RGBA，上传前编码成 PNG）。
fn rgba_to_png(rgba: &[u8], width: u32, height: u32) -> Result<Vec<u8>, String> {
    let mut out: Vec<u8> = Vec::new();
    {
        let mut encoder = png::Encoder::new(&mut out, width, height);
        encoder.set_color(png::ColorType::Rgba);
        encoder.set_depth(png::BitDepth::Eight);
        let mut writer = encoder.write_header().map_err(|e| e.to_string())?;
        writer.write_image_data(rgba).map_err(|e| e.to_string())?;
    }
    Ok(out)
}

/// 快捷键 ⌘⇧C：后台线程读剪贴板。文本→文本卡；无文本但有图片→编码 PNG 暂存→图片卡；都没有→空。
fn propose_from_clipboard(app: &AppHandle) {
    let app = app.clone();
    thread::spawn(move || {
        let text = app.clipboard().read_text().unwrap_or_default();
        let trimmed = text.trim().to_string();
        if !trimmed.is_empty() {
            emit_text_capture(&app, &trimmed, true);
            return;
        }
        // 无文本 → 试剪贴板图片（显式绑定 clipboard，避免 Image 借用临时量）
        let png = {
            let clip = app.clipboard();
            match clip.read_image() {
                Ok(img) => rgba_to_png(img.rgba(), img.width(), img.height()).ok(),
                Err(_) => None,
            }
        };
        if let Some(bytes) = png {
            if let Ok(mut g) = app.state::<ClipImage>().0.lock() {
                *g = Some(bytes);
            }
            if let Some(pet) = app.get_webview_window("pet") {
                let _ = pet.show();
                let _ = pet.set_focus();
            }
            let _ = app.emit(
                "capture-propose",
                serde_json::json!({ "kind": "image", "text": "", "preview": "📷 剪贴板图片" }),
            );
            return;
        }
        let _ = app.emit("capture-propose", serde_json::json!({ "kind": "empty" }));
    });
}

/// 主界面顶栏按钮：显隐桌宠窗口。放在 Rust 里做，最稳——免去 JS 跨窗口控制的权限问题。
#[tauri::command]
fn toggle_pet(app: AppHandle) {
    if let Some(pet) = app.get_webview_window("pet") {
        if matches!(pet.is_visible(), Ok(true)) {
            let _ = pet.hide();
        } else {
            let _ = pet.show();
            let _ = pet.set_focus();
        }
    }
}

/// 复制自动监听开关（pet 设置里切换，默认关）。
#[tauri::command]
fn set_clip_monitor(state: tauri::State<ClipMonitor>, enabled: bool) {
    state.enabled.store(enabled, Ordering::Relaxed);
}

/// pet 点「喂它」时取走暂存的剪贴板图片 PNG（返回原始字节，JS 收到 ArrayBuffer）。
#[tauri::command]
fn take_clip_image(state: tauri::State<ClipImage>) -> Result<tauri::ipc::Response, String> {
    let bytes = state.0.lock().map_err(|_| "状态锁失败".to_string())?.take();
    bytes
        .map(tauri::ipc::Response::new)
        .ok_or_else(|| "剪贴板图片已失效，请重试".to_string())
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_clipboard_manager::init())
        .manage(ClipImage(Mutex::new(None)))
        .manage(ClipMonitor {
            enabled: AtomicBool::new(false),
            last: Mutex::new(String::new()),
        })
        .invoke_handler(tauri::generate_handler![
            toggle_pet,
            set_clip_monitor,
            take_clip_image
        ])
        .setup(|app| {
            // 全局快捷键：mac = ⌘⇧C / 其它平台 = Ctrl⇧C
            #[cfg(target_os = "macos")]
            let mods = Modifiers::SUPER | Modifiers::SHIFT;
            #[cfg(not(target_os = "macos"))]
            let mods = Modifiers::CONTROL | Modifiers::SHIFT;
            let feed_shortcut = Shortcut::new(Some(mods), Code::KeyC);

            let sc = feed_shortcut.clone();
            app.handle().plugin(
                tauri_plugin_global_shortcut::Builder::new()
                    .with_handler(move |app, shortcut, event| {
                        if shortcut == &sc && event.state() == ShortcutState::Pressed {
                            propose_from_clipboard(app);
                        }
                    })
                    .build(),
            )?;
            app.global_shortcut().register(feed_shortcut)?;

            // 复制自动监听后台线程（默认关；剪贴板读取在此非主线程进行，先 sleep 让事件循环起来）
            let mon_app = app.handle().clone();
            thread::spawn(move || loop {
                thread::sleep(Duration::from_millis(800));
                if !mon_app.state::<ClipMonitor>().enabled.load(Ordering::Relaxed) {
                    continue;
                }
                let text = match mon_app.clipboard().read_text() {
                    Ok(t) => t,
                    Err(_) => continue,
                };
                let trimmed = text.trim().to_string();
                if trimmed.is_empty() {
                    continue;
                }
                let same = mon_app
                    .state::<ClipMonitor>()
                    .last
                    .lock()
                    .map(|g| *g == trimmed)
                    .unwrap_or(false);
                if same {
                    continue;
                }
                emit_text_capture(&mon_app, &trimmed, false);
            });

            // 托盘：显示 / 隐藏桌宠 + 退出
            let show_i = MenuItem::with_id(app, "show", "显示桌宠", true, None::<&str>)?;
            let hide_i = MenuItem::with_id(app, "hide", "隐藏桌宠", true, None::<&str>)?;
            let quit_i = MenuItem::with_id(app, "quit", "退出", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show_i, &hide_i, &quit_i])?;
            let mut tray = TrayIconBuilder::new()
                .tooltip("KnowledgeX 桌宠 · 投喂")
                .menu(&menu)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "show" => {
                        if let Some(p) = app.get_webview_window("pet") {
                            let _ = p.show();
                            let _ = p.set_focus();
                        }
                    }
                    "hide" => {
                        if let Some(p) = app.get_webview_window("pet") {
                            let _ = p.hide();
                        }
                    }
                    "quit" => app.exit(0),
                    _ => {}
                });
            // 图标缺失时不 panic（bundle.icon 正常配置时一定有）
            if let Some(icon) = app.default_window_icon() {
                tray = tray.icon(icon.clone());
            }
            let _tray = tray.build(app)?;

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("运行个人知识助手桌面端时出错");
}
