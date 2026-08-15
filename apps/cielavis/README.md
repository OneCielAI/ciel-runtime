# Cielavis

Cielavis is the desktop-first visual agent client for Ciel Runtime. The first phase targets Windows while keeping the React interaction layer reusable for macOS, Linux, Android, iOS, and a browser deployment.

## Phase 1

- Probe the configured Runtime through its public `/ca/channel/*` API.
- When no Runtime is reachable, open an embedded supervised PowerShell/ConPTY session and launch Ciel Runtime with the requested web port.
- Render multiple native PTY sessions as xterm.js tabs. Runtime and speech/Colab setup use separate sessions.
- Poll the channel wait endpoint for correlated replies and submit typed web-chat messages without importing Runtime internals.
- Probe ASR/TTS health after the channel connects. Missing workers automatically open a read-only status session; login and deployment require an explicit button because they can authenticate accounts or consume Colab resources.

Windows Terminal and WezTerm are not embedded as child windows. Cielavis uses the Windows Pseudoconsole API through `portable-pty`, with xterm.js as the renderer. This is the same host/renderer boundary intended by ConPTY and remains portable to Unix PTYs later.

## Development

```powershell
cd apps/cielavis
npm install
npm test
npm run build
npm run desktop:build
./install.ps1
```

The unpackaged Windows executable is written to `src-tauri/target/release/cielavis-desktop.exe`.
The local installer copies it to `%LOCALAPPDATA%\Cielavis\cielavis.exe` and writes the
`%USERPROFILE%\.local\bin\cielavis.ps1` launcher.
