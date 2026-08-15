use portable_pty::{Child, CommandBuilder, MasterPty, PtySize, native_pty_system};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::io::{Read, Write};
use std::sync::Mutex;
use std::thread;
use tauri::{AppHandle, Emitter, State};
use uuid::Uuid;

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct TerminalSpawnRequest {
    pub title: String,
    pub kind: String,
    pub program: String,
    #[serde(default)]
    pub args: Vec<String>,
    pub cwd: Option<String>,
    pub cols: u16,
    pub rows: u16,
}

#[derive(Clone, Debug, Serialize)]
pub struct TerminalSessionInfo {
    id: String,
    title: String,
    kind: String,
}

#[derive(Clone, Debug, Serialize)]
struct TerminalOutput {
    id: String,
    data: String,
}

struct TerminalSession {
    master: Box<dyn MasterPty + Send>,
    writer: Box<dyn Write + Send>,
    child: Box<dyn Child + Send + Sync>,
}

impl Drop for TerminalSession {
    fn drop(&mut self) {
        // A terminal tab owns the process it started.  Closing Cielavis must
        // not leave a bootstrap Runtime or a speech setup shell orphaned.
        let _ = self.child.kill();
    }
}

#[derive(Default)]
pub struct TerminalState(Mutex<HashMap<String, TerminalSession>>);

fn validated_dimension(value: u16, fallback: u16) -> u16 {
    if value == 0 { fallback } else { value.min(500) }
}

#[tauri::command]
pub fn terminal_spawn(
    app: AppHandle,
    state: State<'_, TerminalState>,
    request: TerminalSpawnRequest,
) -> Result<TerminalSessionInfo, String> {
    if request.program.trim().is_empty() {
        return Err("Terminal program is required".into());
    }
    let cols = validated_dimension(request.cols, 120);
    let rows = validated_dimension(request.rows, 24);
    let pair = native_pty_system()
        .openpty(PtySize {
            rows,
            cols,
            pixel_width: 0,
            pixel_height: 0,
        })
        .map_err(|error| error.to_string())?;
    let mut command = CommandBuilder::new(request.program.clone());
    command.args(request.args.clone());
    if let Some(cwd) = request
        .cwd
        .as_deref()
        .filter(|value| !value.trim().is_empty())
    {
        command.cwd(cwd);
    }
    let child = pair
        .slave
        .spawn_command(command)
        .map_err(|error| error.to_string())?;
    drop(pair.slave);
    let mut reader = pair
        .master
        .try_clone_reader()
        .map_err(|error| error.to_string())?;
    let writer = pair
        .master
        .take_writer()
        .map_err(|error| error.to_string())?;
    let id = Uuid::new_v4().to_string();
    let output_id = id.clone();
    thread::Builder::new()
        .name(format!("cielavis-pty-{output_id}"))
        .spawn(move || {
            let mut buffer = [0_u8; 8192];
            loop {
                match reader.read(&mut buffer) {
                    Ok(0) => break,
                    Ok(count) => {
                        let _ = app.emit(
                            "cielavis://terminal-output",
                            TerminalOutput {
                                id: output_id.clone(),
                                data: String::from_utf8_lossy(&buffer[..count]).into_owned(),
                            },
                        );
                    }
                    Err(_) => break,
                }
            }
        })
        .map_err(|error| error.to_string())?;
    state
        .0
        .lock()
        .map_err(|_| "Terminal state lock was poisoned".to_string())?
        .insert(
            id.clone(),
            TerminalSession {
                master: pair.master,
                writer,
                child,
            },
        );
    Ok(TerminalSessionInfo {
        id,
        title: request.title,
        kind: request.kind,
    })
}

#[tauri::command]
pub fn terminal_write(
    state: State<'_, TerminalState>,
    id: String,
    data: String,
) -> Result<(), String> {
    let mut sessions = state
        .0
        .lock()
        .map_err(|_| "Terminal state lock was poisoned")?;
    let session = sessions
        .get_mut(&id)
        .ok_or("Terminal session was not found")?;
    session
        .writer
        .write_all(data.as_bytes())
        .map_err(|error| error.to_string())?;
    session.writer.flush().map_err(|error| error.to_string())
}

#[tauri::command]
pub fn terminal_resize(
    state: State<'_, TerminalState>,
    id: String,
    cols: u16,
    rows: u16,
) -> Result<(), String> {
    let sessions = state
        .0
        .lock()
        .map_err(|_| "Terminal state lock was poisoned")?;
    let session = sessions.get(&id).ok_or("Terminal session was not found")?;
    session
        .master
        .resize(PtySize {
            rows: validated_dimension(rows, 24),
            cols: validated_dimension(cols, 120),
            pixel_width: 0,
            pixel_height: 0,
        })
        .map_err(|error| error.to_string())
}

#[tauri::command]
pub fn terminal_kill(state: State<'_, TerminalState>, id: String) -> Result<(), String> {
    let mut sessions = state
        .0
        .lock()
        .map_err(|_| "Terminal state lock was poisoned")?;
    let mut session = sessions
        .remove(&id)
        .ok_or("Terminal session was not found")?;
    session.child.kill().map_err(|error| error.to_string())
}
