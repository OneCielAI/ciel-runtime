"""Read-only: copy turn, request and error lines from a router log window.

Usage: python router_excerpt.py <out.txt> <start> <end> <router.log>...
Times are the router's local timestamps (YYYY-MM-DDTHH:MM:SS).
"""
import sys

out, start, end, logs = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4:]
keys = ("agent_turn_ended", "upstream_direct_request", "access \"POST /v1/messages", "anthropic_route_error",
        "anthropic_sse_client_disconnected", "router_client_disconnected", "auto-synthesized", "downgraded",
        "dropped", "channel_windows_console_deferred", "channel_stdin_proxy_injected", "native_compact",
        "channel_llm_inject_deferred", "router_spawned", "model_list_unavailable", "cielairouter_gateway_central")
with open(out, "w", encoding="utf-8") as output:
    for name in logs:
        output.write(f"== {name}\n")
        with open(name, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if start <= line[:19] <= end and any(key in line for key in keys):
                    output.write(line)
