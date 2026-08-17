fn main() {
    const COMMANDS: &[&str] = &[
        "browser_create_tab",
        "browser_list_tabs",
        "browser_close_tab",
        "browser_activate_tab",
        "browser_set_bounds",
        "browser_set_visible",
        "browser_navigate",
        "browser_back",
        "browser_forward",
        "browser_reload",
        "browser_snapshot",
        "browser_evaluate",
        "browser_screenshot",
        "browser_pointer",
        "browser_keyboard",
        "browser_mcp_status",
        "browser_mcp_configure_runtime",
        "terminal_spawn",
        "terminal_list",
        "terminal_write",
        "terminal_resize",
        "terminal_kill",
        "runtime_discover",
        "runtime_wait_messages",
        "runtime_send_message",
        "runtime_transcribe_audio",
        "bootstrap_plan",
    ];
    tauri_build::try_build(
        tauri_build::Attributes::new()
            .windows_attributes(tauri_build::WindowsAttributes::new_without_app_manifest())
            .app_manifest(tauri_build::AppManifest::new().commands(COMMANDS)),
    )
    .expect("failed to build the CIELARVIS command capability manifest");
    embed_resource::compile_for_everything("test-manifest.rc", embed_resource::NONE)
        .manifest_required()
        .expect("failed to embed the Windows Common Controls v6 test manifest");
}
