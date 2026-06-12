// 个人知识助手 桌面端：极简 Tauri 壳。
// 窗口在 dev 下加载 devUrl（本地 FastAPI 后端），前端不调用任何 Tauri JS API，
// 因此无需自定义 command / 额外权限。
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    tauri::Builder::default()
        .run(tauri::generate_context!())
        .expect("运行个人知识助手桌面端时出错");
}
