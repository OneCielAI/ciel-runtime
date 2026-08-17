use std::{
    collections::HashMap,
    sync::{Arc, Mutex},
};

use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use tauri::{
    Emitter, LogicalPosition, LogicalSize, Manager, State, Webview, WebviewUrl,
    WebviewWindowBuilder, WindowEvent,
    webview::{NewWindowResponse, PageLoadEvent, WebviewBuilder},
};
use tokio::{
    sync::oneshot,
    time::{Duration, timeout},
};
use url::Url;
use uuid::Uuid;

const DEFAULT_URL: &str = "https://www.google.com/";
const CDP_TIMEOUT: Duration = Duration::from_secs(15);

#[derive(Clone, Default)]
pub struct BrowserState {
    inner: Arc<Mutex<BrowserStore>>,
}

#[derive(Default)]
struct BrowserStore {
    tabs: HashMap<String, BrowserTab>,
    active_tab: Option<String>,
}

#[derive(Clone, Debug, Serialize)]
pub struct BrowserTab {
    id: String,
    label: String,
    url: String,
    title: String,
    loading: bool,
    visible: bool,
    frame_id: u64,
    popup: bool,
}

#[derive(Clone, Debug, Serialize)]
struct BrowserEvent {
    kind: &'static str,
    tab: BrowserTab,
}

#[derive(Clone, Copy, Debug, Deserialize)]
pub struct BrowserBounds {
    x: f64,
    y: f64,
    width: f64,
    height: f64,
}

#[derive(Clone, Debug, Deserialize, schemars::JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum PointerAction {
    Move,
    Down,
    Up,
    Click,
    DoubleClick,
    Wheel,
}

#[derive(Clone, Debug, Deserialize, schemars::JsonSchema)]
pub struct PointerInput {
    action: PointerAction,
    x: f64,
    y: f64,
    #[serde(default = "default_button")]
    button: String,
    #[serde(default)]
    delta_x: f64,
    #[serde(default)]
    delta_y: f64,
    #[serde(default)]
    modifiers: Vec<String>,
    #[serde(default = "default_coordinate_space")]
    coordinate_space: String,
    frame_id: u64,
}

#[derive(Clone, Debug, Deserialize, schemars::JsonSchema)]
#[serde(tag = "action", rename_all = "snake_case")]
pub enum KeyboardInput {
    Type {
        text: String,
        frame_id: u64,
    },
    Press {
        key: String,
        #[serde(default)]
        code: String,
        #[serde(default)]
        modifiers: Vec<String>,
        frame_id: u64,
    },
    Down {
        key: String,
        #[serde(default)]
        code: String,
        #[serde(default)]
        modifiers: Vec<String>,
        frame_id: u64,
    },
    Up {
        key: String,
        #[serde(default)]
        code: String,
        #[serde(default)]
        modifiers: Vec<String>,
        frame_id: u64,
    },
}

fn default_button() -> String {
    "left".into()
}
fn default_coordinate_space() -> String {
    "screenshot".into()
}

fn normalize_url(raw: Option<&str>) -> Result<Url, String> {
    let value = raw.unwrap_or(DEFAULT_URL).trim();
    let candidate = if value.contains("://") || value.starts_with("about:") {
        value.to_string()
    } else {
        format!("https://{value}")
    };
    let parsed = Url::parse(&candidate).map_err(|error| format!("Invalid browser URL: {error}"))?;
    match parsed.scheme() {
        "http" | "https" | "about" => Ok(parsed),
        scheme => Err(format!(
            "Browser navigation does not allow the {scheme}: scheme"
        )),
    }
}

fn tab_for(state: &BrowserState, tab_id: &str) -> Result<BrowserTab, String> {
    state
        .inner
        .lock()
        .map_err(|_| "Browser state lock was poisoned".to_string())?
        .tabs
        .get(tab_id)
        .cloned()
        .ok_or_else(|| format!("Unknown browser tab: {tab_id}"))
}

fn webview_for(app: &tauri::AppHandle, tab: &BrowserTab) -> Result<Webview, String> {
    app.get_webview(&tab.label)
        .ok_or_else(|| format!("Browser renderer is unavailable: {}", tab.id))
}

fn bump_frame(state: &BrowserState, tab_id: &str) -> Result<BrowserTab, String> {
    let mut store = state
        .inner
        .lock()
        .map_err(|_| "Browser state lock was poisoned".to_string())?;
    let tab = store
        .tabs
        .get_mut(tab_id)
        .ok_or_else(|| format!("Unknown browser tab: {tab_id}"))?;
    tab.frame_id = tab.frame_id.saturating_add(1);
    Ok(tab.clone())
}

fn require_frame(tab: &BrowserTab, frame_id: u64) -> Result<(), String> {
    if tab.frame_id != frame_id {
        return Err(format!(
            "stale_frame: requested {frame_id}, current {}",
            tab.frame_id
        ));
    }
    Ok(())
}

#[tauri::command]
pub async fn browser_create_tab(
    app: tauri::AppHandle,
    window: tauri::Window,
    state: State<'_, BrowserState>,
    url: Option<String>,
) -> Result<BrowserTab, String> {
    let target = normalize_url(url.as_deref())?;
    let id = Uuid::new_v4().simple().to_string();
    let label = format!("ciel-browser-{id}");
    let initial = BrowserTab {
        id: id.clone(),
        label: label.clone(),
        url: target.to_string(),
        title: "New tab".into(),
        loading: true,
        visible: false,
        frame_id: 1,
        popup: false,
    };
    {
        let mut store = state
            .inner
            .lock()
            .map_err(|_| "Browser state lock was poisoned".to_string())?;
        store.tabs.insert(id.clone(), initial.clone());
        store.active_tab = Some(id.clone());
    }

    let browser_state = state.inner().clone();
    let event_app = app.clone();
    let event_id = id.clone();
    let popup_app = app.clone();
    let popup_state = state.inner().clone();
    let builder = WebviewBuilder::new(label, WebviewUrl::External(target))
        .devtools(cfg!(debug_assertions))
        .on_navigation(|candidate| matches!(candidate.scheme(), "http" | "https" | "about"))
        .on_document_title_changed({
            let title_state = state.inner().clone();
            let title_app = app.clone();
            let title_id = id.clone();
            move |_view, title| {
                if let Ok(mut store) = title_state.inner.lock()
                    && let Some(tab) = store.tabs.get_mut(&title_id)
                {
                    tab.title = title;
                    let _ = title_app.emit(
                        "cielarvis://browser-event",
                        BrowserEvent {
                            kind: "title_changed",
                            tab: tab.clone(),
                        },
                    );
                }
            }
        })
        .on_page_load(move |_view, payload| {
            if let Ok(mut store) = browser_state.inner.lock()
                && let Some(tab) = store.tabs.get_mut(&event_id)
            {
                tab.url = payload.url().to_string();
                tab.loading = matches!(payload.event(), PageLoadEvent::Started);
                tab.frame_id = tab.frame_id.saturating_add(1);
                let _ = event_app.emit(
                    "cielarvis://browser-event",
                    BrowserEvent {
                        kind: if tab.loading {
                            "navigation_started"
                        } else {
                            "navigation_finished"
                        },
                        tab: tab.clone(),
                    },
                );
            }
        })
        .on_new_window(move |target, features| {
            if !matches!(target.scheme(), "http" | "https" | "about") {
                return NewWindowResponse::Deny;
            }
            let popup_id = Uuid::new_v4().simple().to_string();
            let popup_label = format!("ciel-browser-popup-{popup_id}");
            let popup_tab = BrowserTab {
                id: popup_id.clone(),
                label: popup_label.clone(),
                url: target.to_string(),
                title: "Login".into(),
                loading: true,
                visible: true,
                frame_id: 1,
                popup: true,
            };
            if let Ok(mut store) = popup_state.inner.lock() {
                store.tabs.insert(popup_id.clone(), popup_tab.clone());
            } else {
                return NewWindowResponse::Deny;
            }

            let title_state = popup_state.clone();
            let title_app = popup_app.clone();
            let title_id = popup_id.clone();
            let load_state = popup_state.clone();
            let load_app = popup_app.clone();
            let load_id = popup_id.clone();
            let nested_app = popup_app.clone();
            let close_state = popup_state.clone();
            let close_app = popup_app.clone();
            let close_id = popup_id.clone();
            let popup_window = WebviewWindowBuilder::new(
                &popup_app,
                popup_label,
                WebviewUrl::External("about:blank".parse().expect("valid popup bootstrap URL")),
            )
            .window_features(features)
            .title("CIEL Browser Login")
            .devtools(cfg!(debug_assertions))
            .on_navigation(|candidate| matches!(candidate.scheme(), "http" | "https" | "about"))
            .on_document_title_changed(move |_window, title| {
                if let Ok(mut store) = title_state.inner.lock()
                    && let Some(tab) = store.tabs.get_mut(&title_id)
                {
                    tab.title = title;
                    let _ = title_app.emit(
                        "cielarvis://browser-event",
                        BrowserEvent {
                            kind: "title_changed",
                            tab: tab.clone(),
                        },
                    );
                }
            })
            .on_page_load(move |_window, payload| {
                if let Ok(mut store) = load_state.inner.lock()
                    && let Some(tab) = store.tabs.get_mut(&load_id)
                {
                    tab.url = payload.url().to_string();
                    tab.loading = matches!(payload.event(), PageLoadEvent::Started);
                    tab.frame_id = tab.frame_id.saturating_add(1);
                    let _ = load_app.emit(
                        "cielarvis://browser-event",
                        BrowserEvent {
                            kind: if tab.loading {
                                "navigation_started"
                            } else {
                                "navigation_finished"
                            },
                            tab: tab.clone(),
                        },
                    );
                }
            })
            .on_new_window(move |_url, _features| {
                let _ = nested_app.emit("cielarvis://browser-popup-nested", ());
                NewWindowResponse::Allow
            })
            .build();

            match popup_window {
                Ok(window) => {
                    window.on_window_event(move |event| {
                        if !matches!(event, WindowEvent::Destroyed) {
                            return;
                        }
                        if let Ok(mut store) = close_state.inner.lock()
                            && let Some(tab) = store.tabs.remove(&close_id)
                        {
                            let _ = close_app.emit(
                                "cielarvis://browser-event",
                                BrowserEvent {
                                    kind: "popup_closed",
                                    tab,
                                },
                            );
                        }
                    });
                    let _ = popup_app.emit(
                        "cielarvis://browser-event",
                        BrowserEvent {
                            kind: "popup_opened",
                            tab: popup_tab,
                        },
                    );
                    NewWindowResponse::Create { window }
                }
                Err(_) => {
                    if let Ok(mut store) = popup_state.inner.lock() {
                        store.tabs.remove(&popup_id);
                    }
                    NewWindowResponse::Deny
                }
            }
        });

    if let Err(error) = window.add_child(
        builder,
        LogicalPosition::new(-10_000.0, -10_000.0),
        LogicalSize::new(32.0, 32.0),
    ) {
        if let Ok(mut store) = state.inner.lock() {
            store.tabs.remove(&id);
        }
        return Err(format!(
            "Could not create isolated browser renderer: {error}"
        ));
    }
    if let Ok(view) = webview_for(&app, &initial) {
        let _ = view.hide();
    }
    Ok(initial)
}

#[tauri::command]
pub fn browser_list_tabs(state: State<'_, BrowserState>) -> Result<Vec<BrowserTab>, String> {
    let store = state
        .inner
        .lock()
        .map_err(|_| "Browser state lock was poisoned".to_string())?;
    let mut tabs: Vec<_> = store.tabs.values().cloned().collect();
    tabs.sort_by(|left, right| left.id.cmp(&right.id));
    Ok(tabs)
}

#[tauri::command]
pub fn browser_close_tab(
    app: tauri::AppHandle,
    state: State<'_, BrowserState>,
    tab_id: String,
) -> Result<(), String> {
    let tab = tab_for(&state, &tab_id)?;
    if tab.popup {
        app.get_webview_window(&tab.label)
            .ok_or_else(|| format!("Browser popup is unavailable: {}", tab.id))?
            .close()
            .map_err(|error| error.to_string())?;
    } else {
        webview_for(&app, &tab)?
            .close()
            .map_err(|error| error.to_string())?;
    }
    let mut store = state
        .inner
        .lock()
        .map_err(|_| "Browser state lock was poisoned".to_string())?;
    store.tabs.remove(&tab_id);
    if store.active_tab.as_deref() == Some(&tab_id) {
        store.active_tab = store.tabs.keys().next().cloned();
    }
    Ok(())
}

#[tauri::command]
pub fn browser_activate_tab(
    app: tauri::AppHandle,
    state: State<'_, BrowserState>,
    tab_id: String,
) -> Result<BrowserTab, String> {
    let tab = tab_for(&state, &tab_id)?;
    if tab.popup {
        let window = app
            .get_webview_window(&tab.label)
            .ok_or_else(|| format!("Browser popup is unavailable: {}", tab.id))?;
        window.show().map_err(|error| error.to_string())?;
        window.set_focus().map_err(|error| error.to_string())?;
        return Ok(tab);
    }
    let mut store = state
        .inner
        .lock()
        .map_err(|_| "Browser state lock was poisoned".to_string())?;
    store.active_tab = Some(tab_id.clone());
    for candidate in store.tabs.values_mut() {
        let active = candidate.id == tab_id;
        candidate.visible = active && candidate.visible;
        if candidate.popup {
            continue;
        }
        if let Some(view) = app.get_webview(&candidate.label) {
            if candidate.visible {
                let _ = view.show();
            } else {
                let _ = view.hide();
            }
        }
    }
    Ok(tab)
}

#[tauri::command]
pub fn browser_set_bounds(
    app: tauri::AppHandle,
    state: State<'_, BrowserState>,
    tab_id: String,
    bounds: BrowserBounds,
) -> Result<(), String> {
    let tab = tab_for(&state, &tab_id)?;
    if tab.popup {
        let window = app
            .get_webview_window(&tab.label)
            .ok_or_else(|| format!("Browser popup is unavailable: {}", tab.id))?;
        window
            .set_position(LogicalPosition::new(bounds.x, bounds.y))
            .map_err(|error| error.to_string())?;
        return window
            .set_size(LogicalSize::new(
                bounds.width.max(1.0),
                bounds.height.max(1.0),
            ))
            .map_err(|error| error.to_string());
    }
    let view = webview_for(&app, &tab)?;
    view.set_position(LogicalPosition::new(bounds.x, bounds.y))
        .map_err(|error| error.to_string())?;
    view.set_size(LogicalSize::new(
        bounds.width.max(1.0),
        bounds.height.max(1.0),
    ))
    .map_err(|error| error.to_string())
}

#[tauri::command]
pub fn browser_set_visible(
    app: tauri::AppHandle,
    state: State<'_, BrowserState>,
    tab_id: String,
    visible: bool,
) -> Result<(), String> {
    let tab = tab_for(&state, &tab_id)?;
    if tab.popup {
        let window = app
            .get_webview_window(&tab.label)
            .ok_or_else(|| format!("Browser popup is unavailable: {}", tab.id))?;
        if visible {
            window.show()
        } else {
            window.hide()
        }
        .map_err(|error| error.to_string())?;
    } else {
        let view = webview_for(&app, &tab)?;
        if visible { view.show() } else { view.hide() }.map_err(|error| error.to_string())?;
    }
    let mut store = state
        .inner
        .lock()
        .map_err(|_| "Browser state lock was poisoned".to_string())?;
    if let Some(tab) = store.tabs.get_mut(&tab_id) {
        tab.visible = visible;
    }
    Ok(())
}

#[tauri::command]
pub fn browser_navigate(
    app: tauri::AppHandle,
    state: State<'_, BrowserState>,
    tab_id: String,
    url: String,
) -> Result<BrowserTab, String> {
    let target = normalize_url(Some(&url))?;
    let tab = tab_for(&state, &tab_id)?;
    webview_for(&app, &tab)?
        .navigate(target.clone())
        .map_err(|error| error.to_string())?;
    let mut store = state
        .inner
        .lock()
        .map_err(|_| "Browser state lock was poisoned".to_string())?;
    let current = store
        .tabs
        .get_mut(&tab_id)
        .ok_or_else(|| format!("Unknown browser tab: {tab_id}"))?;
    current.url = target.to_string();
    current.loading = true;
    current.frame_id = current.frame_id.saturating_add(1);
    Ok(current.clone())
}

async fn eval(
    app: &tauri::AppHandle,
    state: &BrowserState,
    tab_id: &str,
    script: String,
) -> Result<Value, String> {
    let tab = tab_for(state, tab_id)?;
    let view = webview_for(app, &tab)?;
    let (sender, receiver) = oneshot::channel();
    let sender = Arc::new(Mutex::new(Some(sender)));
    view.eval_with_callback(script, move |result| {
        if let Ok(mut slot) = sender.lock()
            && let Some(sender) = slot.take()
        {
            let _ = sender.send(result);
        }
    })
    .map_err(|error| error.to_string())?;
    let raw = timeout(CDP_TIMEOUT, receiver)
        .await
        .map_err(|_| "Browser JavaScript timed out".to_string())?
        .map_err(|_| "Browser JavaScript callback was cancelled".to_string())?;
    serde_json::from_str(&raw)
        .or(Ok(Value::String(raw)))
        .map_err(|error: serde_json::Error| error.to_string())
}

#[tauri::command]
pub async fn browser_back(
    app: tauri::AppHandle,
    state: State<'_, BrowserState>,
    tab_id: String,
) -> Result<Value, String> {
    eval(&app, &state, &tab_id, "history.back(); true".into()).await
}

#[tauri::command]
pub async fn browser_forward(
    app: tauri::AppHandle,
    state: State<'_, BrowserState>,
    tab_id: String,
) -> Result<Value, String> {
    eval(&app, &state, &tab_id, "history.forward(); true".into()).await
}

#[tauri::command]
pub fn browser_reload(
    app: tauri::AppHandle,
    state: State<'_, BrowserState>,
    tab_id: String,
) -> Result<(), String> {
    let tab = tab_for(&state, &tab_id)?;
    webview_for(&app, &tab)?
        .reload()
        .map_err(|error| error.to_string())
}

#[tauri::command]
pub async fn browser_snapshot(
    app: tauri::AppHandle,
    state: State<'_, BrowserState>,
    tab_id: String,
) -> Result<Value, String> {
    let script = r#"(() => ({
      url: location.href, title: document.title,
      viewport: { width: innerWidth, height: innerHeight, scrollX, scrollY, devicePixelRatio },
      text: (document.body?.innerText || '').slice(0, 500000),
      links: Array.from(document.querySelectorAll('a[href]')).slice(0, 2000).map((el, index) => ({ index, text: (el.innerText || el.getAttribute('aria-label') || '').trim().slice(0, 500), href: el.href })),
      controls: Array.from(document.querySelectorAll('button,input,textarea,select,[role=button],[contenteditable=true]')).slice(0, 2000).map((el, index) => { const r = el.getBoundingClientRect(); return { index, tag: el.tagName.toLowerCase(), type: el.getAttribute('type') || '', text: (el.innerText || el.getAttribute('aria-label') || el.getAttribute('placeholder') || '').trim().slice(0, 500), x: r.x, y: r.y, width: r.width, height: r.height, disabled: Boolean(el.disabled) }; })
    }))()"#;
    let mut value = eval(&app, &state, &tab_id, script.into()).await?;
    let tab = bump_frame(&state, &tab_id)?;
    if let Value::Object(ref mut object) = value {
        object.insert("frame_id".into(), json!(tab.frame_id));
    }
    Ok(value)
}

#[tauri::command]
pub async fn browser_evaluate(
    app: tauri::AppHandle,
    state: State<'_, BrowserState>,
    tab_id: String,
    script: String,
) -> Result<Value, String> {
    if script.len() > 1_000_000 {
        return Err("Browser JavaScript exceeds the 1 MB command limit".into());
    }
    let tab = tab_for(&state, &tab_id)?;
    let view = webview_for(&app, &tab)?;
    call_cdp(
        view,
        "Runtime.evaluate",
        json!({
            "expression": format!("(async () => {{ {script}\n }})()"),
            "awaitPromise": true,
            "returnByValue": true,
            "userGesture": true,
        }),
    )
    .await
}

#[cfg(windows)]
async fn call_cdp(view: Webview, method: &'static str, parameters: Value) -> Result<Value, String> {
    use webview2_com::CallDevToolsProtocolMethodCompletedHandler;
    use windows::core::HSTRING;

    let (sender, receiver) = oneshot::channel();
    let sender = Arc::new(Mutex::new(Some(sender)));
    let params = parameters.to_string();
    view.with_webview(move |platform| {
        let callback_sender = sender.clone();
        let result = unsafe {
            platform.controller().CoreWebView2().and_then(|core| {
                let handler = CallDevToolsProtocolMethodCompletedHandler::create(Box::new(
                    move |status, result| {
                        let output = status.map(|_| result).map_err(|error| error.to_string());
                        if let Ok(mut slot) = callback_sender.lock()
                            && let Some(sender) = slot.take()
                        {
                            let _ = sender.send(output);
                        }
                        Ok(())
                    },
                ));
                core.CallDevToolsProtocolMethod(
                    &HSTRING::from(method),
                    &HSTRING::from(params),
                    &handler,
                )
            })
        };
        if let Err(error) = result
            && let Ok(mut slot) = sender.lock()
            && let Some(sender) = slot.take()
        {
            let _ = sender.send(Err(error.to_string()));
        }
    })
    .map_err(|error| error.to_string())?;
    let raw = timeout(CDP_TIMEOUT, receiver)
        .await
        .map_err(|_| format!("Browser command {method} timed out"))?
        .map_err(|_| format!("Browser command {method} was cancelled"))??;
    serde_json::from_str(&raw)
        .map_err(|error| format!("Invalid WebView2 response for {method}: {error}"))
}

#[cfg(not(windows))]
async fn call_cdp(
    _view: Webview,
    _method: &'static str,
    _parameters: Value,
) -> Result<Value, String> {
    Err("Native screenshot and input control are not implemented for this platform adapter".into())
}

#[tauri::command]
pub async fn browser_screenshot(
    app: tauri::AppHandle,
    state: State<'_, BrowserState>,
    tab_id: String,
) -> Result<Value, String> {
    let tab = tab_for(&state, &tab_id)?;
    let view = webview_for(&app, &tab)?;
    let metrics = call_cdp(view.clone(), "Runtime.evaluate", json!({
        "expression": "({width: innerWidth, height: innerHeight, scrollX, scrollY, devicePixelRatio})",
        "returnByValue": true,
    })).await?;
    let viewport = metrics
        .pointer("/result/value")
        .cloned()
        .unwrap_or_else(|| json!({}));
    let mut result = call_cdp(view, "Page.captureScreenshot", json!({ "format": "jpeg", "quality": 82, "fromSurface": true, "captureBeyondViewport": false })).await?;
    let frame = bump_frame(&state, &tab_id)?;
    if let Value::Object(ref mut object) = result {
        object.insert("tab_id".into(), json!(tab_id));
        object.insert("frame_id".into(), json!(frame.frame_id));
        object.insert("mime_type".into(), json!("image/jpeg"));
        object.insert("coordinate_space".into(), json!("screenshot_pixels"));
        object.insert("viewport".into(), viewport);
    }
    Ok(result)
}

fn modifier_bits(modifiers: &[String]) -> u8 {
    modifiers.iter().fold(0, |bits, modifier| {
        bits | match modifier.to_ascii_lowercase().as_str() {
            "alt" => 1,
            "ctrl" | "control" => 2,
            "meta" | "command" | "win" => 4,
            "shift" => 8,
            _ => 0,
        }
    })
}

#[tauri::command]
pub async fn browser_pointer(
    app: tauri::AppHandle,
    state: State<'_, BrowserState>,
    tab_id: String,
    input: PointerInput,
) -> Result<Value, String> {
    let tab = tab_for(&state, &tab_id)?;
    require_frame(&tab, input.frame_id)?;
    let view = webview_for(&app, &tab)?;
    let (x, y) = if input.coordinate_space == "screenshot" {
        let metrics = call_cdp(
            view.clone(),
            "Runtime.evaluate",
            json!({ "expression": "devicePixelRatio", "returnByValue": true }),
        )
        .await?;
        let scale = metrics
            .pointer("/result/value")
            .and_then(Value::as_f64)
            .unwrap_or(1.0)
            .max(0.01);
        (input.x / scale, input.y / scale)
    } else if input.coordinate_space == "css" {
        (input.x, input.y)
    } else {
        return Err(format!(
            "Unsupported pointer coordinate space: {}",
            input.coordinate_space
        ));
    };
    let base = json!({ "x": x, "y": y, "button": input.button, "modifiers": modifier_bits(&input.modifiers) });
    let invoke = |kind: &str, count: u8| {
        let mut params = base.clone();
        params["type"] = json!(kind);
        params["clickCount"] = json!(count);
        params
    };
    match input.action {
        PointerAction::Move => call_cdp(view, "Input.dispatchMouseEvent", invoke("mouseMoved", 0)).await,
        PointerAction::Down => call_cdp(view, "Input.dispatchMouseEvent", invoke("mousePressed", 1)).await,
        PointerAction::Up => call_cdp(view, "Input.dispatchMouseEvent", invoke("mouseReleased", 1)).await,
        PointerAction::Click => {
            call_cdp(view.clone(), "Input.dispatchMouseEvent", invoke("mousePressed", 1)).await?;
            call_cdp(view, "Input.dispatchMouseEvent", invoke("mouseReleased", 1)).await
        }
        PointerAction::DoubleClick => {
            call_cdp(view.clone(), "Input.dispatchMouseEvent", invoke("mousePressed", 2)).await?;
            call_cdp(view, "Input.dispatchMouseEvent", invoke("mouseReleased", 2)).await
        }
        PointerAction::Wheel => call_cdp(view, "Input.dispatchMouseEvent", json!({ "type": "mouseWheel", "x": x, "y": y, "deltaX": input.delta_x, "deltaY": input.delta_y, "modifiers": modifier_bits(&input.modifiers) })).await,
    }
}

#[tauri::command]
pub async fn browser_keyboard(
    app: tauri::AppHandle,
    state: State<'_, BrowserState>,
    tab_id: String,
    input: KeyboardInput,
) -> Result<Value, String> {
    let (frame_id, method, parameters, key_up) = match input {
        KeyboardInput::Type { text, frame_id } => {
            (frame_id, "Input.insertText", json!({ "text": text }), None)
        }
        KeyboardInput::Press {
            key,
            code,
            modifiers,
            frame_id,
        } => (
            frame_id,
            "Input.dispatchKeyEvent",
            json!({ "type": "rawKeyDown", "key": key, "code": code, "modifiers": modifier_bits(&modifiers) }),
            Some((key, code, modifiers)),
        ),
        KeyboardInput::Down {
            key,
            code,
            modifiers,
            frame_id,
        } => (
            frame_id,
            "Input.dispatchKeyEvent",
            json!({ "type": "rawKeyDown", "key": key, "code": code, "modifiers": modifier_bits(&modifiers) }),
            None,
        ),
        KeyboardInput::Up {
            key,
            code,
            modifiers,
            frame_id,
        } => (
            frame_id,
            "Input.dispatchKeyEvent",
            json!({ "type": "keyUp", "key": key, "code": code, "modifiers": modifier_bits(&modifiers) }),
            None,
        ),
    };
    let tab = tab_for(&state, &tab_id)?;
    require_frame(&tab, frame_id)?;
    let view = webview_for(&app, &tab)?;
    let result = call_cdp(view.clone(), method, parameters).await?;
    if let Some((key, code, modifiers)) = key_up {
        call_cdp(view, "Input.dispatchKeyEvent", json!({ "type": "keyUp", "key": key, "code": code, "modifiers": modifier_bits(&modifiers) })).await
    } else {
        Ok(result)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalizes_only_browser_safe_urls() {
        assert_eq!(
            normalize_url(Some("example.com")).unwrap().as_str(),
            "https://example.com/"
        );
        assert!(normalize_url(Some("file:///etc/passwd")).is_err());
        assert!(normalize_url(Some("javascript:alert(1)")).is_err());
    }

    #[test]
    fn maps_input_modifiers_to_cdp_bits() {
        assert_eq!(modifier_bits(&["CTRL".into(), "shift".into()]), 10);
        assert_eq!(modifier_bits(&["unknown".into()]), 0);
    }
}
