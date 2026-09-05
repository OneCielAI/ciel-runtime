# Managed tool launch policy

Ciel-owned tool definitions can declare `injection_mode`: `always`, `native`,
or `non_native`. The default is `always`, retaining channel/runtime integration.
`WorkspaceMcpLaunchService.prepare(..., native=...)` filters only its
`injected_servers` argument. User-owned `workspace_mcp.servers` is not filtered.
The metadata is removed before generating CLI arguments or MCP JSON.

Claude's generated DuckDuckGo and fetch bundle is non-native-only, including
when the Ciel web-search override is enabled. Anthropic direct and routed
connections both retain their own web tools. Codex and Codex app-server pass
their existing native-provider classification to the same projection policy;
their current launch paths do not auto-generate a DuckDuckGo bundle.

Selection happens for each launch, without deleting shared files or changing
another running instance. This does not dynamically reconfigure an already
running CLI after a provider switch. It does not change the CLI's own search
settings, install a fallback after an error, or remove user-installed tools.
