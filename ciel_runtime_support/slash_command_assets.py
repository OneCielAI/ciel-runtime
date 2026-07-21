"""Static Claude slash-command documents installed by Ciel Runtime."""

VERSION_SLASH_COMMAND = """---
description: Show ciel-runtime version
argument-hint: [ignored]
---

CIEL_RUNTIME_VERSION_STATUS

Show the running ciel-runtime version for this session. This command is handled locally by the ciel-runtime router and must not be forwarded upstream.
"""

ADVISOR_SLASH_COMMAND = """---
description: Run the selected ciel-runtime Advisor Model
argument-hint: [question or focus]
---

CIEL_RUNTIME_ADVISOR_CALL

Focus: $ARGUMENTS

Use the Advisor Model selected in the ciel-runtime launch menu. If the Advisor Model is off, explain how to enable it. Otherwise review the current conversation, tool history, and task state. Return concise guidance with the blocker, next concrete action, and validation step.
"""


ADVISOR_NATIVE_DISABLED_SLASH_COMMAND = """---
description: Ciel Runtime Advisor unavailable in Claude Native mode
argument-hint: [ignored]
---

Ciel Runtime Advisor is unavailable in direct Claude Native mode.

Do not run tools, shell commands, file searches, config scans, or environment checks for this command. Reply immediately with this exact status and the short enablement hint below.

This session bypasses the ciel-runtime router, so /advisor cannot call the configured Advisor Model here. To use /advisor, launch a non-native provider or enable Anthropic routed mode in ciel-runtime.
"""


ROUTER_DEBUG_SLASH_COMMAND = """---
description: Toggle ciel-runtime router external debug access
argument-hint: [on|off|status]
---

CIEL_RUNTIME_ROUTER_DEBUG_ACCESS

Value: $ARGUMENTS

Toggle ciel-runtime router debug external access. With no argument, this toggles the current state. Use `on`, `off`, or `status` for explicit control.
"""


ROUTER_DEBUG_NATIVE_DISABLED_SLASH_COMMAND = """---
description: ciel-runtime router debug unavailable in Claude Native mode
argument-hint: [ignored]
---

ciel-runtime router debug controls are unavailable in direct Claude Native mode.

Do not run tools, shell commands, file searches, config scans, or environment checks for this command. Reply immediately with this exact status and the short enablement hint below.

This session bypasses the ciel-runtime router. Launch a non-native provider or enable Anthropic routed mode to use /router-debug.
"""


LLM_SLIDER_SLASH_COMMAND = """---
description: Move/select ciel-runtime live LLM preset slider
argument-hint: [left|right|status|list|restore|preset-id]
---

CIEL_RUNTIME_LIVE_LLM_OPTIONS

Value: $0
Arguments: $ARGUMENTS

Use one compact ciel-runtime live LLM preset control. With no argument, show the current slider. Use `left` or `right` to move one preset, `restore` to return to captured options, or a preset id/alias such as `coding`, `300k`, `512k`, or `1m`.
"""


LLM_OPTIONS_SLASH_COMMAND = """---
description: Show or change ciel-runtime live LLM options
argument-hint: [left|right|status|list|restore|preset-id]
---

CIEL_RUNTIME_LIVE_LLM_OPTIONS

Value: $0
Arguments: $ARGUMENTS

Show or change the live ciel-runtime LLM preset for this routed session. With no argument, show status and the compact preset slider. Use `left` or `right` to move one preset, `restore` to return to captured options, or a preset id/alias such as `coding`, `300k`, `512k`, or `1m`.
"""


LLM_RESTORE_SLASH_COMMAND = """---
description: Restore ciel-runtime live LLM options
argument-hint: [ignored]
---

CIEL_RUNTIME_LIVE_LLM_OPTIONS

Value: restore

Restore the LLM options captured before the first live preset change in this routed session.
"""


CHANNEL_CLEAR_SLASH_COMMAND = """---
description: Discard pending ciel-runtime external channel backlog
argument-hint: [all|status]
---

CIEL_RUNTIME_CHANNEL_CLEAR_BACKLOG

Value: $ARGUMENTS

Discard pending ciel-runtime external channel backlog without sending it to the model. Use `status` to show pending counts without clearing.
"""


API_KEYS_SLASH_COMMAND = """---
description: Set/show ciel-runtime live API key(s)
argument-hint: [status|clear|KEY|KEY1,KEY2]
---

Set API key(s) for the current ciel-runtime provider without restarting Claude Code. With no argument, show masked key status. Use `clear` or `unset` to remove keys for only the current provider. Multiple keys may be comma-, semicolon-, or newline-separated and are used round-robin. Never print raw keys.

CIEL_RUNTIME_LIVE_API_KEYS

Value: $0
Arguments:
$ARGUMENTS
"""


IMPORT_SESSION_SLASH_COMMAND = """---
description: Import a Claude/Codex session transcript into this session
argument-hint: Codex|Claude [transcript-path]
---

CIEL_RUNTIME_IMPORT_SESSION

Target: $1
Path: $2
Arguments: $ARGUMENTS

Import a session transcript into the current Ciel Runtime-routed session. `Target` names the source transcript format and must be `Codex` or `Claude`.
"""


LEGACY_MARKER_PREFIX = "CLAUDE" + "_ANY"
LEGACY_ADVISOR_CALL_MARKER = LEGACY_MARKER_PREFIX + "_ADVISOR_CALL"
LEGACY_ROUTER_DEBUG_ACCESS_MARKER = LEGACY_MARKER_PREFIX + "_ROUTER_DEBUG_ACCESS"
LEGACY_LIVE_LLM_OPTIONS_MARKER = LEGACY_MARKER_PREFIX + "_LIVE_LLM_OPTIONS"
LEGACY_CHANNEL_CLEAR_BACKLOG_MARKER = LEGACY_MARKER_PREFIX + "_CHANNEL_CLEAR_BACKLOG"
LEGACY_LIVE_API_KEYS_MARKER = LEGACY_MARKER_PREFIX + "_LIVE_API_KEYS"

ADVISOR_REQUEST_MARKERS = ("CIEL_RUNTIME_ADVISOR_CALL", LEGACY_ADVISOR_CALL_MARKER)
ROUTER_DEBUG_REQUEST_MARKERS = (
    "CIEL_RUNTIME_ROUTER_DEBUG_ACCESS",
    LEGACY_ROUTER_DEBUG_ACCESS_MARKER,
)
VERSION_REQUEST_MARKERS = ("CIEL_RUNTIME_VERSION_STATUS",)
LIVE_LLM_OPTIONS_REQUEST_MARKERS = (
    "CIEL_RUNTIME_LIVE_LLM_OPTIONS",
    LEGACY_LIVE_LLM_OPTIONS_MARKER,
)
CHANNEL_CLEAR_REQUEST_MARKERS = (
    "CIEL_RUNTIME_CHANNEL_CLEAR_BACKLOG",
    LEGACY_CHANNEL_CLEAR_BACKLOG_MARKER,
)
LIVE_API_KEYS_REQUEST_MARKERS = (
    "CIEL_RUNTIME_LIVE_API_KEYS",
    LEGACY_LIVE_API_KEYS_MARKER,
)
IMPORT_SESSION_REQUEST_MARKERS = ("CIEL_RUNTIME_IMPORT_SESSION",)

CIEL_RUNTIME_ADVISOR_COMMAND_MARKERS = (
    "CIEL_RUNTIME_ADVISOR_CALL",
    "Run the selected ciel-runtime Advisor Model",
    "Ciel Runtime Advisor is unavailable in direct Claude Native mode",
    LEGACY_ADVISOR_CALL_MARKER,
)
CIEL_RUNTIME_ROUTER_DEBUG_COMMAND_MARKERS = (
    "CIEL_RUNTIME_ROUTER_DEBUG_ACCESS",
    "Toggle ciel-runtime router external debug access",
    "ciel-runtime router debug controls are unavailable in direct Claude Native mode",
    LEGACY_ROUTER_DEBUG_ACCESS_MARKER,
)
CIEL_RUNTIME_VERSION_COMMAND_MARKERS = (
    "CIEL_RUNTIME_VERSION_STATUS",
    "Show ciel-runtime version",
)
CIEL_RUNTIME_LLM_OPTIONS_COMMAND_MARKERS = (
    "CIEL_RUNTIME_LIVE_LLM_OPTIONS",
    "Show or change ciel-runtime live LLM options",
    "Restore ciel-runtime live LLM options",
    LEGACY_LIVE_LLM_OPTIONS_MARKER,
)
CIEL_RUNTIME_CHANNEL_CLEAR_COMMAND_MARKERS = (
    "CIEL_RUNTIME_CHANNEL_CLEAR_BACKLOG",
    "Discard pending ciel-runtime external channel backlog",
    LEGACY_CHANNEL_CLEAR_BACKLOG_MARKER,
)
CIEL_RUNTIME_API_KEYS_COMMAND_MARKERS = (
    "CIEL_RUNTIME_LIVE_API_KEYS",
    "Set/show ciel-runtime live API key(s)",
    LEGACY_LIVE_API_KEYS_MARKER,
)
CIEL_RUNTIME_IMPORT_SESSION_COMMAND_MARKERS = (
    "CIEL_RUNTIME_IMPORT_SESSION",
    "Import a Claude/Codex session transcript",
)
