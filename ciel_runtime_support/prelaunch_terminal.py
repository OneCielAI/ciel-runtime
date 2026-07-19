"""Prelaunch screen renderer and terminal input adapters."""

from __future__ import annotations

from dataclasses import dataclass
import getpass
import os
import shutil
import sys
import time
import unicodedata
from pathlib import Path
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


@dataclass(frozen=True, slots=True)
class TerminalSelectionServices:
    enable_ansi: Callable[[], None]
    ansi: Callable[[str, str], str]
    intro_panel_lines: Callable[[int], list[str]]
    status_lines: Callable[[], list[str]]
    read_key: Callable[[int | None], str]


ANIMATED_TEXT_PALETTE = (203, 209, 215, 221, 229, 187, 151, 116, 111, 147, 183, 219)


def enable_ansi() -> None:
    if os.name == "nt":
        os.system("")


def ansi(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if sys.stdout.isatty() else text


def animated_ansi_text(text: str, *, phase: int | None = None, bold: bool = True) -> str:
    if not sys.stdout.isatty():
        return text
    current_phase = int(time.monotonic() * 8) if phase is None else phase
    parts: list[str] = []
    for index, character in enumerate(text):
        if character.isspace():
            parts.append(character)
            continue
        code = f"38;5;{ANIMATED_TEXT_PALETTE[(current_phase + index) % len(ANIMATED_TEXT_PALETTE)]}"
        parts.append(ansi(character, f"1;{code}" if bold else code))
    return "".join(parts)


def cell_width(text: str) -> int:
    return sum(
        0
        if unicodedata.combining(character)
        else 2
        if unicodedata.east_asian_width(character) in ("F", "W")
        else 1
        for character in text
    )


def fit_cells(value: Any, width: int) -> str:
    text = str(value if value is not None else "")
    width = max(1, width)
    if cell_width(text) <= width:
        return text
    suffix = "..." if width >= 4 else ""
    limit = max(1, width - cell_width(suffix))
    output: list[str] = []
    used = 0
    for character in text:
        character_width = (
            0
            if unicodedata.combining(character)
            else 2
            if unicodedata.east_asian_width(character) in ("F", "W")
            else 1
        )
        if used + character_width > limit:
            break
        output.append(character)
        used += character_width
    return "".join(output) + suffix


def pad_cells(value: Any, width: int) -> str:
    text = fit_cells(value, width)
    return text + (" " * max(0, width - cell_width(text)))


def intro_panel_lines(width: int, app_name: str, credits: str) -> list[str]:
    width = max(48, min(width, 120))
    border = "-" * (width - 2)
    lines = [f"+{border}+"]
    title = f" {app_name} "
    lines.append(f"|{title}{' ' * max(0, width - len(title) - 2)}|")
    if width >= 92:
        left_width = 39
        right_width = width - left_width - 4
        rows = [
            ("Welcome back!", "Tips for getting started"),
            ("", "Choose provider, model, base URL, and API key before launch."),
            ("   CLAUDE", "Routes Claude Code to Anthropic, Ollama, vLLM, Nvidia, or NIM."),
            ("      ANY", "Adds DuckDuckGo web search tooling for non-native providers."),
            (credits, "Use --ca-* flags for headless runs; Claude flags pass through."),
        ]
        for left, right in rows:
            lines.append(
                f"| {left[:left_width].ljust(left_width)} | "
                f"{right[:right_width].ljust(right_width)}|"
            )
    else:
        rows = [
            f"{app_name} routes Claude Code through selectable providers.",
            "Anthropic, Ollama, vLLM, Nvidia Hosted, and self-hosted NIM.",
            "DuckDuckGo web search is attached for non-native providers.",
            "Headless setup uses --ca-* flags; Claude flags pass through.",
            credits,
        ]
        lines.extend(f"| {row[: width - 4].ljust(width - 4)} |" for row in rows)
    lines.append(f"+{border}+")
    return lines


def append_menu_key_debug_log(path: Path, line: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(line)
    except OSError:
        pass


def read_menu_key(
    fd: int | None = None,
    *,
    debug_log: Callable[[str], None] = lambda _line: None,
) -> str:
    if os.name == "nt":
        import msvcrt

        character = msvcrt.getwch()
        if character in ("\x00", "\xe0"):
            code = msvcrt.getwch()
            return {"H": "up", "P": "down", "K": "left", "M": "right"}.get(code, "")
        if character in ("\r", "\n"):
            return "enter"
        if character == "\x1b":
            return "esc"
        return character.lower()
    descriptor = sys.stdin.fileno() if fd is None or fd < 0 else fd
    character = os.read(descriptor, 1)
    log = f"{time.time():.3f} first={character!r}"
    if character == b"\x1b":
        sequence = character.decode("latin-1")
        following = os.read(descriptor, 1)
        log += f" next={following!r}"
        if not following:
            debug_log(log + " result='esc'\n")
            return "esc"
        sequence += following.decode("latin-1")
        if following == b"[":
            while True:
                following = os.read(descriptor, 1)
                log += f" next={following!r}"
                if not following:
                    break
                sequence += following.decode("latin-1")
                if 0x40 <= following[0] <= 0x7E:
                    break
        elif following == b"O":
            following = os.read(descriptor, 1)
            log += f" next={following!r}"
            if following:
                sequence += following.decode("latin-1")
        result = {
            "\x1b[A": "up",
            "\x1b[B": "down",
            "\x1b[D": "left",
            "\x1b[C": "right",
            "\x1b[5~": "pageup",
            "\x1b[6~": "pagedown",
            "\x1b[H": "home",
            "\x1b[F": "end",
        }.get(sequence, "esc")
        debug_log(log + f" seq={sequence!r} result={result!r}\n")
        return result
    result = "enter" if character in (b"\r", b"\n") else character.decode("latin-1").lower()
    debug_log(log + f" result={result!r}\n")
    return result


def portable_select(
    title: str,
    rows: list[str],
    current: int = 0,
    footer: str = "",
    info_lines: list[str] | None = None,
    show_intro: bool = False,
    *,
    services: TerminalSelectionServices,
) -> int | None:
    services.enable_ansi()
    index = max(0, min(current, len(rows) - 1))
    status_cache = services.status_lines()[:5]
    output_is_tty = os.isatty(sys.stdout.fileno()) if os.name != "nt" else True
    if output_is_tty:
        sys.stdout.write("\033[?25l")
        sys.stdout.flush()
    fd = sys.stdin.fileno()
    old_settings = None
    if os.name != "nt" and os.isatty(fd):
        try:
            import termios

            old_settings = termios.tcgetattr(fd)
            new_settings = termios.tcgetattr(fd)
            new_settings[3] &= ~(termios.ECHO | termios.ICANON)
            new_settings[6][termios.VMIN] = 1
            new_settings[6][termios.VTIME] = 0
            termios.tcsetattr(fd, termios.TCSANOW, new_settings)
        except Exception:
            fd = -1
    try:
        while True:
            columns = shutil.get_terminal_size((100, 24)).columns
            screen = services.intro_panel_lines(columns) if show_intro else []
            screen.append(services.ansi(title, "1"))
            for line in status_cache:
                color = "32" if line.startswith(("provider:", "model:")) else "2"
                screen.append("  " + services.ansi(line, color))
            screen.append("")
            for row_index, row in enumerate(rows):
                text = ("> " if row_index == index else "  ") + row
                if row_index == index:
                    screen.append(services.ansi(text, "7;1"))
                elif row.startswith(("Quit", "종료", "終了", "退出")):
                    screen.append(services.ansi(text, "31"))
                elif any(word in row for word in ("Launch", "실행", "起動", "启动")):
                    screen.append(services.ansi(text, "32;1"))
                else:
                    screen.append(text)
            if info_lines:
                screen.extend(("", services.ansi("-" * min(120, max(72, columns - 4)), "38;5;208")))
                screen.extend(services.ansi(line, "1;38;5;208") for line in info_lines)
            screen.extend(("", services.ansi(footer or "Up/Down moves. Enter selects. Esc/q cancels.", "2")))
            sys.stdout.write("\033[2J\033[H" + "\n".join(screen) + "\n")
            sys.stdout.flush()
            key = services.read_key(fd if fd >= 0 else None)
            if key in ("up", "k"):
                index = (index - 1) % len(rows)
            elif key in ("down", "j"):
                index = (index + 1) % len(rows)
            elif key == "enter":
                return index
            elif key in ("esc", "q"):
                return None
    finally:
        if old_settings is not None:
            try:
                import termios

                termios.tcsetattr(fd, termios.TCSANOW, old_settings)
            except Exception:
                pass
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()


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
