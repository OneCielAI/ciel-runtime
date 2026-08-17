mod browser;
mod browser_mcp;
mod microphone;
mod runtime;
mod terminal;

use tauri::Manager;

pub fn run() {
    let result = tauri::Builder::default()
        .manage(browser::BrowserState::default())
        .manage(browser_mcp::BrowserMcpState::default())
        .manage(terminal::TerminalState::default())
        .setup(|app| {
            microphone::install_permission_handler(app.handle())?;
            let app_handle = app.handle().clone();
            let status = app.state::<browser_mcp::BrowserMcpState>().inner().clone();
            tauri::async_runtime::spawn(browser_mcp::serve(app_handle, status));
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            browser::browser_create_tab,
            browser::browser_list_tabs,
            browser::browser_close_tab,
            browser::browser_activate_tab,
            browser::browser_set_bounds,
            browser::browser_set_visible,
            browser::browser_navigate,
            browser::browser_back,
            browser::browser_forward,
            browser::browser_reload,
            browser::browser_snapshot,
            browser::browser_evaluate,
            browser::browser_screenshot,
            browser::browser_pointer,
            browser::browser_keyboard,
            browser_mcp::browser_mcp_status,
            browser_mcp::browser_mcp_configure_runtime,
            terminal::terminal_spawn,
            terminal::terminal_write,
            terminal::terminal_resize,
            terminal::terminal_kill,
            runtime::runtime_discover,
            runtime::runtime_wait_messages,
            runtime::runtime_send_message,
            runtime::runtime_transcribe_audio,
            runtime::bootstrap_plan,
        ])
        .run(tauri::generate_context!());
    let _ = std::fs::remove_file(runtime::browser_mcp_config_path());
    result.expect("failed to run Cielarvis desktop shell");
}
