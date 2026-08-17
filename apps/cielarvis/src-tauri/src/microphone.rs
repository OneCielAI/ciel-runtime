#[cfg(windows)]
use tauri::Manager;

#[cfg(windows)]
pub fn install_permission_handler(app: &tauri::AppHandle) -> Result<(), String> {
    use webview2_com::{
        Microsoft::Web::WebView2::Win32::{
            COREWEBVIEW2_PERMISSION_KIND, COREWEBVIEW2_PERMISSION_KIND_MICROPHONE,
            COREWEBVIEW2_PERMISSION_STATE_ALLOW, COREWEBVIEW2_PERMISSION_STATE_DENY,
        },
        PermissionRequestedEventHandler,
    };
    use windows::core::BOOL;

    let webview = app
        .get_webview("main")
        .ok_or_else(|| "The main CIELARVIS webview is unavailable".to_string())?;
    webview
        .with_webview(move |platform| {
            let result = unsafe {
                platform.controller().CoreWebView2().and_then(|core| {
                    let handler =
                        PermissionRequestedEventHandler::create(Box::new(move |_, args| {
                            let Some(args) = args else { return Ok(()) };
                            let mut kind = COREWEBVIEW2_PERMISSION_KIND::default();
                            let mut user_initiated = BOOL::default();
                            args.PermissionKind(&mut kind)?;
                            args.IsUserInitiated(&mut user_initiated)?;
                            if kind == COREWEBVIEW2_PERMISSION_KIND_MICROPHONE {
                                let state =
                                    if microphone_request_allowed(true, user_initiated.as_bool()) {
                                        COREWEBVIEW2_PERMISSION_STATE_ALLOW
                                    } else {
                                        COREWEBVIEW2_PERMISSION_STATE_DENY
                                    };
                                args.SetState(state)?;
                            }
                            Ok(())
                        }));
                    let mut token = 0;
                    core.add_PermissionRequested(&handler, &mut token)
                })
            };
            if let Err(error) = result {
                eprintln!("CIELARVIS microphone permission handler failed: {error}");
            }
        })
        .map_err(|error| error.to_string())
}

#[cfg(not(windows))]
pub fn install_permission_handler(_app: &tauri::AppHandle) -> Result<(), String> {
    Ok(())
}

fn microphone_request_allowed(is_microphone: bool, user_initiated: bool) -> bool {
    is_microphone && user_initiated
}

#[cfg(test)]
mod tests {
    use super::microphone_request_allowed;

    #[test]
    fn allows_only_user_initiated_microphone_requests() {
        assert!(microphone_request_allowed(true, true));
        assert!(!microphone_request_allowed(true, false));
        assert!(!microphone_request_allowed(false, true));
    }
}
