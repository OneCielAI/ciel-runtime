mod runtime;
mod terminal;

pub fn run() {
    tauri::Builder::default()
        .manage(terminal::TerminalState::default())
        .invoke_handler(tauri::generate_handler![
            terminal::terminal_spawn,
            terminal::terminal_write,
            terminal::terminal_resize,
            terminal::terminal_kill,
            runtime::runtime_discover,
            runtime::runtime_wait_messages,
            runtime::runtime_send_message,
            runtime::bootstrap_plan,
        ])
        .run(tauri::generate_context!())
        .expect("failed to run Cielarvis desktop shell");
}
