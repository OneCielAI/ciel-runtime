"""Work mode settings and the launch arguments that switch it on.

Work mode is on by default for Ciel-launched Claude and Codex sessions that
attach the router's MCP server: the session-scoped plan lives behind the router's
``work_*`` tools.  Native launches (Claude Native, native Codex) inject
nothing, so Work mode does not apply to them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ciel_runtime_support.codex_config import codex_config_override_keys, toml_string

WORK_MODE_KEY = "work_mode"
WORK_PLAN_APPROVAL_ASK_KEY = "work_plan_approval_ask"
WORK_AUTO_CONTINUE_KEY = "work_auto_continue"
CLAUDE_MOD_NAME = "ciel-work"
CLAUDE_MOD_DIR = Path(__file__).resolve().parent / "claude_mods" / CLAUDE_MOD_NAME
# The router's Work tools; work_mcp_tools serves them, launch pre-approves them.
WORK_TOOL_NAMES = (
    "work_status",
    "work_plan_set",
    "work_node_add",
    "work_node_update",
    "work_node_remove",
    "work_select",
    "work_execute",
    "work_replan",
    "work_reorder",
    "work_urgent",
)

WORK_MODE_BASE_INSTRUCTIONS = (
    "Ciel Work mode is hybrid per-item planning and execution, not native Plan Mode. Keep a hierarchical plan "
    "current while executing approved work and planning queued requests concurrently.\n"
    "Work plans are private to this conversation. Codex supplies the conversation identity in MCP metadata; "
    "do not supply or invent a session_id argument. A new conversation has a separate plan; resuming retains it.\n"
    "1. Planning phase: before changing files, decompose the user's task into a tree with work_plan_set: "
    "goal -> phases -> tasks -> steps. Keep splitting until every leaf is one concrete, verifiable action. "
    "Read-only exploration is fine while planning; file edits and state-changing commands wait for execution. "
    "Shell permissions and sandboxing belong to the native host; Work does not inspect shell side effects or "
    "override native permissions. Do not use a shell to evade required planning or approval.\n"
    "Triage every new user request first: an independent request becomes its own goal (root-level item), its title "
    "starting with its item number where possible (\"1. ...\", \"2. ...\"); a request that changes or extends an "
    "existing item goes under that item as a child (work_node_add with parent_id). A new goal queues behind the "
    "unfinished active goal and becomes active when that goal is done; keep the active goal going meanwhile.\n"
    "2. When the plan is complete, call work_execute. If it reports that user confirmation is required, show "
    "the plan, ask the user, and call work_execute again with user_confirmed=true only after they agree.\n"
    "3. Executing phase: work through the leaves in order. Mark a leaf in_progress when you start it and done "
    "with short evidence (command output, test result, file) when it is verified. Add or split nodes with "
    "work_node_add with within_scope=true only for refinements wholly inside already approved scope; omit it for new scope requiring review/approval. Planning queued requests never pauses active execution. Use work_replan when the approach changes.\n"
    "4. Call work_status(root_id=...) for exact goal details only on demand. Raw user messages remain in native history; Work does not isolate that history. Never report a step as done without evidence.\n"
    "Set ref for a linked id (shown as [ id ] description); set waiting_on for an external dependency, "
    "and clear it with empty text when ready. Reorder root goals with work_reorder (1-based position); "
    "by default reorder only queued goals. Changing the active slot requires the user's explicit request "
    "and user_requested=true, except that when every active step waits or is blocked, work_select may move to the "
    "queued goal holding the next runnable step. On an explicit urgent/ad-hoc request use work_urgent with user_requested=true "
    "to hold other goals, decompose that request and execute; held goals resume afterwards.\n"
    "If the user asks how Work mode works or why an edit waited, explain this briefly; it is switched on or off "
    "in the ciel-runtime menu under LLM options -> Work mode."
)

# Codex gets these rules as instructions only; nothing in Ciel resumes a Codex turn.
WORK_AUTO_CONTINUE_INSTRUCTIONS = (
    "Work auto continue is on. In the executing phase, before you end a turn:\n"
    "- Keep handling approved pending Work. While actionable unfinished items remain inside the approved plan, "
    "continue with the next one instead of stopping to ask whether to go on.\n"
    "- Triage the backlog first: register any user request that is not yet in the tree, within the scope the user "
    "already approved and without duplicates (independent requests as numbered goals, dependent ones as children).\n"
    "- Take the highest-priority actionable item first; equal priorities keep their plan order.\n"
    "- When an item waits on something outside this conversation (a person, a review, a build, another agent), "
    "set its waiting_on label to what it waits for instead of editing its title, and meanwhile work on other "
    "independent actionable items, running independent actions in parallel where your tools allow.\n"
    "- End the turn only when every unfinished item is truly blocked, waiting or needs user approval, and say what "
    "each one waits for. Do not invent work, expand the approved scope or cross an approval boundary to keep going.\n"
    "Work auto continue is switched on or off in the ciel-runtime menu under LLM options -> Work auto continue."
)

# The default Codex contract: Work mode with auto continue on.
WORK_MODE_INSTRUCTIONS = WORK_MODE_BASE_INSTRUCTIONS + "\n" + WORK_AUTO_CONTINUE_INSTRUCTIONS

WORK_MODE_LAUNCH_NOTICE = (
    "Ciel Work mode is on: the model plans first (work_plan_set) and edits after work_execute. "
    "Turn off: ciel-runtime menu -> LLM options -> Work mode."
)


def work_mode_launch_notice(config: dict[str, Any], *, native: bool, router_mcp_attached: bool) -> str:
    """The console line printed before a Codex launch that carries Work mode; Codex has no plugin UI."""

    return WORK_MODE_LAUNCH_NOTICE if work_mode_applies(
        config, native=native, router_mcp_attached=router_mcp_attached) else ""


def work_mode_enabled(config: dict[str, Any]) -> bool:
    """On unless the user turned it off explicitly."""

    return config.get(WORK_MODE_KEY) is not False


def work_plan_approval_ask(config: dict[str, Any]) -> bool:
    """Automatic approval unless the user asked to confirm each plan."""

    return config.get(WORK_PLAN_APPROVAL_ASK_KEY) is True


def work_auto_continue_enabled(config: dict[str, Any]) -> bool:
    """The stored Work auto continue setting: on unless the user turned it off explicitly.

    This reads the setting only; whether Work mode itself applies to a launch is
    ``work_mode_applies``.
    """

    return config.get(WORK_AUTO_CONTINUE_KEY) is not False


def codex_work_mode_instructions(config: dict[str, Any]) -> str:
    """The Codex ``developer_instructions`` text for this configuration."""

    return WORK_MODE_INSTRUCTIONS if work_auto_continue_enabled(config) else WORK_MODE_BASE_INSTRUCTIONS


def work_mode_applies(config: dict[str, Any], *, native: bool, router_mcp_attached: bool) -> bool:
    return work_mode_enabled(config) and not native and router_mcp_attached


def claude_work_mode_args(config: dict[str, Any], *, native: bool, router_mcp_attached: bool,
                          mod_dir: Path = CLAUDE_MOD_DIR) -> list[str]:
    """``--plugin-dir`` for the bundled Work mod; the flag is repeatable, so user flags stay."""

    if not work_mode_applies(config, native=native, router_mcp_attached=router_mcp_attached):
        return []
    if not (mod_dir / ".claude-plugin" / "plugin.json").is_file():
        return []
    return ["--plugin-dir", str(mod_dir)]


def codex_work_mode_args(config: dict[str, Any], *, native: bool, router_mcp_attached: bool,
                         passthrough: list[str] | tuple[str, ...] = ()) -> list[str]:
    """Codex ``developer_instructions`` carrying the Work mode contract.

    A user-supplied ``developer_instructions`` override wins: Ciel does not
    replace instructions the user chose for this launch.
    """

    if not work_mode_applies(config, native=native, router_mcp_attached=router_mcp_attached):
        return []
    overrides = codex_config_override_keys(list(passthrough))
    args: list[str] = []
    if "developer_instructions" not in overrides:
        args.extend(["-c", f"developer_instructions={toml_string(codex_work_mode_instructions(config))}"])
    # Codex blocks MCP calls that need approval when approval_policy is never.
    # Only Ciel's Work tools are pre-approved; other router tools keep defaults.
    for tool in WORK_TOOL_NAMES:
        key = f"mcp_servers.ciel-runtime-router.tools.{tool}.approval_mode"
        if key not in overrides:
            args.extend(["-c", f'{key}="approve"'])
    return args


def work_mode_menu_label(config: dict[str, Any]) -> str:
    return "on (default)" if work_mode_enabled(config) else "off"


def work_plan_approval_menu_label(config: dict[str, Any]) -> str:
    return "ask user" if work_plan_approval_ask(config) else "auto (default)"


def work_auto_continue_menu_label(config: dict[str, Any]) -> str:
    return "on (default)" if work_auto_continue_enabled(config) else "off"
