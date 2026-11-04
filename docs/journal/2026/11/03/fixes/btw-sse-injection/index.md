# /btw SSE injection journal

* [Implementation](implementation.md) - How external SSE/runtime input is admitted as a native Claude /btw side question, and why /btw cannot answer through MCP.
* [Verification](verification.md) - Test and isolated real-CLI evidence for the /btw injection change, including invalid probe runs that must not be cited.

# Evidence

* [evidence/](evidence/) - Probe harnesses (`btw_async_bridge_probe.py`, `btw_native_probe.py`) and raw run output. The `ccfg/` trees inside run directories are isolated Claude config fixtures captured by the probes, not journal concepts.
