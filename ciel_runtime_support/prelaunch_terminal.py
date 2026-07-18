"""Prelaunch screen renderer and terminal input adapters."""

from __future__ import annotations

from dataclasses import dataclass
import getpass
import os
import shutil
import sys
import time
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class PrelaunchRenderBrand:
    animated_ansi_text: Callable[..., Any]
    credits: str
    version: str


@dataclass(frozen=True, slots=True)
class PrelaunchRenderText:
    ansi: Callable[..., Any]
    cell_width: Callable[..., Any]
    fit_cells: Callable[..., Any]
    pad_cells: Callable[..., Any]
    ui_text: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class PrelaunchRenderData:
    api_key_status_line: Callable[..., Any]
    get_current_provider: Callable[..., Any]
    llm_option_description_for_value: Callable[..., Any]
    llm_option_panel_rows: Callable[..., Any]
    load_config: Callable[..., Any]
    main_menu_rows: Callable[..., Any]
    provider_mode_label: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class PrelaunchRenderServices:
    brand: PrelaunchRenderBrand
    data: PrelaunchRenderData
    text: PrelaunchRenderText


@dataclass(frozen=True, slots=True)
class PrelaunchInputStyle:
    ansi: Callable[..., Any]
    log: Callable[..., Any]


def visible_rows(rows: list[str], selected: int, limit: int) -> list[tuple[int | None, str]]:
    if len(rows) <= limit:
        return [(i, row) for i, row in enumerate(rows)]
    limit = max(4, limit)
    start = max(0, min(selected - limit // 2, len(rows) - limit))
    end = min(len(rows), start + limit)
    visible: list[tuple[int | None, str]] = []
    if start > 0:
        visible.append((None, f"... {start} above"))
    visible.extend((i, rows[i]) for i in range(start, end))
    if end < len(rows):
        visible.append((None, f"... {len(rows) - end} below"))
    return visible


def render_prelaunch_screen(
    main_idx: int,
    panel: str | None,
    panel_idx: int,
    panel_rows: list[str],
    checks: list[str],
    messages: list[str],
    first_render: bool,
    *,
    services: PrelaunchRenderServices,
) -> bool:
    brand = services.brand
    data = services.data
    text = services.text
    CREDITS = brand.credits
    VERSION = brand.version
    animated_ansi_text = brand.animated_ansi_text
    ansi = text.ansi
    cell_width = text.cell_width
    fit_cells = text.fit_cells
    pad_cells = text.pad_cells
    ui_text = text.ui_text
    api_key_status_line = data.api_key_status_line
    get_current_provider = data.get_current_provider
    llm_option_description_for_value = data.llm_option_description_for_value
    llm_option_panel_rows = data.llm_option_panel_rows
    load_config = data.load_config
    main_menu_rows = data.main_menu_rows
    provider_mode_label = data.provider_mode_label
    cfg = load_config()
    provider, pcfg = get_current_provider(cfg)
    lang = cfg.get("language", "en")
    columns, height = shutil.get_terminal_size((110, 32))
    render_width = max(40, columns - 1)
    screen: list[str] = []
    def add(text: str = "", code: str | None = None) -> None:
        # Redraws start at cursor home. Each row must overwrite the full
        # previous row; otherwise Windows cmd leaves stale text on the right.
        fitted = pad_cells(text, render_width)
        screen.append(ansi(fitted, code) if code else fitted)

    def add_rendered(visible_text: str, rendered_text: str) -> None:
        visible = fit_cells(visible_text, render_width)
        padding = " " * max(0, render_width - cell_width(visible))
        screen.append(rendered_text + padding)

    mode_line = f"mode: {provider_mode_label(provider, pcfg)}"
    title_text = f"Ciel Runtime v{VERSION}"
    add_rendered(title_text, animated_ansi_text(title_text))
    add(CREDITS, "2")
    add("")
    add(f"provider: {provider}    language: {lang}    {mode_line}", "32")
    add(f"base_url: {pcfg.get('base_url')}", "2")
    add(f"model: {pcfg.get('current_model')}", "32")
    add(api_key_status_line(provider, pcfg), "2")
    add("")
    rows = main_menu_rows(cfg, provider, pcfg, lang)
    for i, row in enumerate(rows):
        line = ("> " if i == main_idx and panel is None else "  ") + row
        if i == main_idx and panel is None:
            add(line, "7;1")
        elif "disabled:" in row:
            add(line, "2")
        elif "Launch" in row or "실행" in row or "起動" in row or "启动" in row:
            add(line, "32;1")
        elif row == ui_text("quit", lang):
            add(line, "31")
        else:
            add(line)
    if panel:
        titles = {
            "language": "Language",
            "provider": "Provider",
            "api-key": "API key",
            "base-url": "Base URL",
            "model": "Model",
            "advisor-model": "Advisor Model",
            "test": "Compatibility test",
            "options": ui_text("options", lang),
            "channel-delivery": ui_text("channel_delivery", lang),
            "log-level": ui_text("log_level", lang),
            "channels": "Channels",
            "context": ui_text("context_setup", lang),
            "preset": ui_text("presets", lang),
            "timeout": ui_text("timeout_preset", lang),
        }
        add("")
        add("-" * render_width, "38;5;208")
        panel_title = titles.get(panel, panel)
        title_suffix = "" if panel_title.lower().endswith(("options", "presets", "setup", "설정", "옵션", "프리셋", "設定", "オプション", "プリセット", "设置", "选项", "预设")) else " options"
        add(f"{panel_title}{title_suffix}", "1;38;5;208")
        # Reserve an extra line for the per-row description when shown.
        description_reserve = 2 if panel == "options" else 0
        fixed = len(screen) + len(checks) + len(messages) + 5 + description_reserve
        limit = max(5, height - fixed)
        for actual, row in visible_rows(panel_rows, panel_idx, limit):
            if actual is None:
                add("    " + row, "2")
            elif actual == panel_idx:
                add("  > " + row, "7;1")
            else:
                add("    " + row)
        if panel == "options" and panel_rows:
            # Map panel_idx back to its option key, then show its localized
            # description below the panel so the user always sees the meaning of
            # the currently-highlighted row.
            try:
                _, panel_values = llm_option_panel_rows(provider, pcfg, lang)
            except Exception:
                panel_values = []
            current_key = panel_values[panel_idx] if 0 <= panel_idx < len(panel_values) else ""
            description = llm_option_description_for_value(provider, pcfg, current_key, lang) if current_key else ""
            add("")
            if description:
                add("  " + description, "2")
            else:
                add("")
    if messages:
        add("")
        for line in messages[-8:]:
            add("  " + line, "36;1")
    if checks:
        add("")
        add("-" * render_width, "38;5;208")
        for line in checks[:2]:
            add("  " + line, "1;38;5;208")
    add("")
    help_text = "Up/Down moves. Enter selects. Esc/Left closes submenu. q quits. Actions expand in place."
    add(help_text, "2")
    rendered = "\n".join(screen) + "\n"
    if sys.stdout.isatty():
        prefix = "\033[2J\033[H" if first_render else "\033[H"
        sys.stdout.write(prefix + rendered + "\033[J")
        sys.stdout.flush()
    else:
        print(rendered, end="")
    return False


def _prompt_menu_value_raw(
    label: str,
    default: str = "",
    secret: bool = False,
    *,
    style: PrelaunchInputStyle,
) -> str | None:
    ansi = style.ansi
    if os.name == "nt" or not sys.stdin.isatty():
        return None
    try:
        import codecs
        import select
        import termios
    except Exception:
        return None
    fd = sys.stdin.fileno()
    try:
        old_settings = termios.tcgetattr(fd)
        new_settings = termios.tcgetattr(fd)
        new_settings[3] = new_settings[3] & ~(termios.ECHO | termios.ICANON)
        new_settings[6][termios.VMIN] = 1
        new_settings[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, new_settings)
    except Exception:
        return None

    chars: list[str] = []
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    try:
        sys.stdout.write("\n" + ansi(label, "1;38;5;208"))
        sys.stdout.flush()
        while True:
            first = os.read(fd, 1)
            if not first:
                continue
            data = bytearray(first)
            try:
                while select.select([fd], [], [], 0)[0]:
                    more = os.read(fd, 4096)
                    if not more:
                        break
                    data.extend(more)
            except Exception as exc:
                style.log("WARN", f"prelaunch_terminal_io_failed error={type(exc).__name__}: {exc}")

            display: list[str] = []
            done = False
            for byte in data:
                b = bytes((byte,))
                if b in (b"\r", b"\n"):
                    display.append("\n")
                    done = True
                    break
                if b in (b"\x03",):
                    raise KeyboardInterrupt
                if b in (b"\x04", b"\x1b"):
                    display.append("\n")
                    sys.stdout.write("".join(display))
                    sys.stdout.flush()
                    return default
                if b in (b"\x7f", b"\x08"):
                    if chars:
                        chars.pop()
                        if not secret:
                            display.append("\b \b")
                    continue
                if b == b"\x15":
                    if chars and not secret:
                        display.append("\b \b" * len(chars))
                    chars.clear()
                    continue
                if byte < 0x20:
                    continue
                text = decoder.decode(b)
                if not text:
                    continue
                for ch in text:
                    if ch in ("\ufffd", "\r", "\n"):
                        continue
                    chars.append(ch)
                    if not secret:
                        display.append(ch)
            if display:
                sys.stdout.write("".join(display))
                sys.stdout.flush()
            if done:
                break
        return "".join(chars).strip() or default
    finally:
        try:
            termios.tcsetattr(fd, termios.TCSANOW, old_settings)
        except Exception as exc:
            style.log("WARN", f"prelaunch_terminal_io_failed error={type(exc).__name__}: {exc}")


def prompt_menu_value(
    prompt: str,
    default: str = "",
    secret: bool = False,
    restore_tty: Callable[[], None] | None = None,
    raw_tty: Callable[[], None] | None = None,
    *,
    style: PrelaunchInputStyle,
) -> str:
    ansi = style.ansi
    label = f"{prompt}"
    if default:
        label += f" [{default}]"
    label += ": "
    if restore_tty:
        restore_tty()
    if sys.stdout.isatty():
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()
    try:
        raw_value = _prompt_menu_value_raw(label, default, secret, style=style)
        if raw_value is not None:
            value = raw_value
        else:
            sys.stdout.write("\n" + ansi(label, "1;38;5;208"))
            sys.stdout.flush()
            if secret:
                value = getpass.getpass("")
            else:
                value = input()
    finally:
        if sys.stdout.isatty():
            sys.stdout.write("\033[?25l")
            sys.stdout.flush()
        if raw_tty:
            raw_tty()
    value = value.strip()
    return value or default


def _prompt_menu_multiline_value_raw(
    label: str, secret: bool = False, *, style: PrelaunchInputStyle
) -> str | None:
    ansi = style.ansi
    """Read a pasted or typed multi-line value from a TTY.

    A blank line, Ctrl-D, or Esc finishes input. Do not auto-finish on a newline:
    web terminals and SSH relays can deliver a paste one line at a time, so a
    debounce-based finish can incorrectly store only the first line.
    """
    if not sys.stdin.isatty():
        return None
    chars: list[str] = []
    if os.name == "nt":
        try:
            import msvcrt
        except Exception:
            return None
        sys.stdout.write("\n" + ansi(label, "1;38;5;208"))
        sys.stdout.flush()
        while True:
            ch = msvcrt.getwch()
            batch = [ch]
            time.sleep(0.01)
            while msvcrt.kbhit():
                batch.append(msvcrt.getwch())
            for ch in batch:
                if ch == "\x03":
                    raise KeyboardInterrupt
                if ch in ("\x04", "\x1b"):
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                    return "".join(chars).strip()
                if ch in ("\r", "\n"):
                    chars.append("\n")
                    sys.stdout.write("\n")
                    continue
                if ch in ("\x08", "\x7f"):
                    if chars:
                        chars.pop()
                    continue
                if ch == "\x15":
                    chars.clear()
                    continue
                if ch < " ":
                    continue
                chars.append(ch)
                if not secret:
                    sys.stdout.write(ch)
            sys.stdout.flush()
            text = "".join(chars)
            if text.endswith("\n\n"):
                return text.strip()
    try:
        import codecs
        import select
        import termios
    except Exception:
        return None
    fd = sys.stdin.fileno()
    try:
        old_settings = termios.tcgetattr(fd)
        new_settings = termios.tcgetattr(fd)
        new_settings[3] = new_settings[3] & ~(termios.ECHO | termios.ICANON)
        new_settings[6][termios.VMIN] = 1
        new_settings[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, new_settings)
    except Exception:
        return None
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    try:
        sys.stdout.write("\n" + ansi(label, "1;38;5;208"))
        sys.stdout.flush()
        while True:
            first = os.read(fd, 1)
            if not first:
                continue
            data = bytearray(first)
            try:
                while select.select([fd], [], [], 0)[0]:
                    more = os.read(fd, 4096)
                    if not more:
                        break
                    data.extend(more)
            except Exception as exc:
                style.log("WARN", f"prelaunch_terminal_io_failed error={type(exc).__name__}: {exc}")
            display: list[str] = []
            for byte in data:
                b = bytes((byte,))
                if b == b"\x03":
                    raise KeyboardInterrupt
                if b in (b"\x04", b"\x1b"):
                    display.append("\n")
                    sys.stdout.write("".join(display))
                    sys.stdout.flush()
                    return "".join(chars).strip()
                if b in (b"\x7f", b"\x08"):
                    if chars:
                        chars.pop()
                    continue
                if b == b"\x15":
                    chars.clear()
                    continue
                text = decoder.decode(b)
                if not text:
                    continue
                for ch in text:
                    if ch == "\ufffd":
                        continue
                    if ch in ("\r", "\n"):
                        chars.append("\n")
                        display.append("\n")
                    elif ch >= " ":
                        chars.append(ch)
                        if not secret:
                            display.append(ch)
            if display:
                sys.stdout.write("".join(display))
                sys.stdout.flush()
            text = "".join(chars)
            if text.endswith("\n\n"):
                return text.strip()
    finally:
        try:
            termios.tcsetattr(fd, termios.TCSANOW, old_settings)
        except Exception as exc:
            style.log("WARN", f"prelaunch_terminal_io_failed error={type(exc).__name__}: {exc}")


def prompt_menu_multiline_value(
    prompt: str,
    restore_tty: Callable[[], None] | None = None,
    raw_tty: Callable[[], None] | None = None,
    secret: bool = True,
    *,
    style: PrelaunchInputStyle,
) -> str:
    ansi = style.ansi
    label = f"{prompt} (finish with a blank line): "
    if restore_tty:
        restore_tty()
    if sys.stdout.isatty():
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()
    try:
        raw_value = _prompt_menu_multiline_value_raw(label, secret=secret, style=style)
        if raw_value is not None:
            value = raw_value
        else:
            sys.stdout.write("\n" + ansi(label, "1;38;5;208"))
            sys.stdout.flush()
            lines: list[str] = []
            while True:
                line = input()
                if not line.strip():
                    break
                lines.append(line)
            value = "\n".join(lines)
    finally:
        if sys.stdout.isatty():
            sys.stdout.write("\033[?25l")
            sys.stdout.flush()
        if raw_tty:
            raw_tty()
    return value.strip()

