# Cielarvis

Cielarvis is the desktop-first visual agent client for Ciel Runtime. The first phase targets Windows while keeping the React interaction layer reusable for macOS, Linux, Android, iOS, and a browser deployment.

## Agentic desktop SDK v1

Cielarvis now treats every visible tool as an application package rather than a page-specific panel. The first SDK boundary consists of:

- A versioned `ai.oneciel.cielarvis.app/v1` manifest describing identity, icon, capabilities, host type, and one or more window surfaces.
- A desktop kernel that registers packages and owns window lifecycle, focus, z-order, minimize, maximize, move, resize, and singleton behavior.
- A taskbar generated from the registered manifests. Installing another package can therefore add an icon without editing the desktop shell.
- Built-in packages for Ciel Runtime and Ciel Chat. The chat composer at the bottom is a normal managed SDK window, not a privileged hard-coded desktop element.
- A portable `cielarvis-js-app-v1` host for marketplace JavaScript applications. UI bundles run in a script-only sandbox and receive only manifest-granted capabilities through a versioned message bridge.
- A declared `cielarvis-native-app-v1` ABI boundary for optional Windows DLL applications that need native performance or device integration.

JavaScript is the primary cross-platform package format for Windows, macOS, Linux, mobile, and web. Native libraries are deliberately not loaded yet. A marketplace build must verify publisher signatures and capabilities, then load native code in a separate broker process rather than inside the WebView or desktop kernel. Built-in renderers use the same manifest and lifecycle contract that future packages will use.

## Ciel Browser control boundary

The Browser is an independent add-in and does not use the Runtime web-chat channel. Its visible toolbar and embedded MCP transport share the platform-neutral `ai.oneciel.cielarvis.browser-control/v1` controller. Remote sites render in a dedicated child WebView whose label is excluded from the main-window Tauri capability, so page script cannot invoke CIELARVIS commands.

The v1 controller covers tab lifecycle, navigation, bounded DOM snapshots, JavaScript evaluation, viewport screenshots, pointer movement/click/drag/wheel primitives, and Unicode text or raw key events. Screenshot results carry a document `frame_id`, viewport metrics, and device-pixel ratio; pointer commands reject stale frames and translate screenshot pixels to WebView CSS coordinates. Windows uses WebView2 DevTools Protocol behind the adapter. Other platforms implement the same controller contract with their native engine and do not change the MCP schemas.

CIELARVIS starts an MCP Streamable HTTP server on a random loopback-only port. The Browser app's **MCP ONLINE** panel shows its endpoint and per-process `Authorization: Bearer ...` value. The token is regenerated at startup, is not persisted, and is never exposed to a remote child WebView. Screenshots are returned as MCP image content; pointer and keyboard tools require a current `frame_id`.

## Windows phase

- Probe the configured Runtime through its public `/ca/channel/*` API.
- When no Runtime is reachable, open an embedded supervised PowerShell/ConPTY session and launch Ciel Runtime with the requested web port.
- Serve Ciel Runtime through the first standardized application: a managed, movable and resizable terminal-multiplexer window. Multiple native PTY sessions remain xterm.js tabs inside that app; Runtime and speech/Colab setup use separate sessions.
- Poll the channel wait endpoint for correlated replies and submit typed web-chat messages without importing Runtime internals.
- Probe ASR/TTS health after the channel connects. Missing workers automatically open a read-only status session; login and deployment require an explicit button because they can authenticate accounts or consume Colab resources.

Windows Terminal and WezTerm are not embedded as child windows. Cielarvis uses the Windows Pseudoconsole API through `portable-pty`, with xterm.js as the renderer. This is the same host/renderer boundary intended by ConPTY and remains portable to Unix PTYs later.

## Development

```powershell
cd apps/cielarvis
npm install
npm test
npm run build
npm run desktop:build
./install.ps1
```

The unpackaged Windows executable is written to `src-tauri/target/release/cielarvis-desktop.exe`.
The local installer copies it to `%LOCALAPPDATA%\Cielarvis\cielarvis.exe` and writes the
`%USERPROFILE%\.local\bin\cielarvis.ps1` launcher.
