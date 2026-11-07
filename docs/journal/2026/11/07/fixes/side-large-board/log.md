# Change log

## 2026-11-07

- The side AI inherits every valid MCP tool from every MCP server connected to main, using the existing connections, credentials and native permission prompts; this is not Walkie-specific. It follows event hints to select the relevant source-system MCP, and the receipt records reported reply tool names as `side_mcp`. Work mutation remains host-controlled.
- The side pane uses a one-line-per-receipt list and a paged detail view, clipped by cell width.
- The foreign-pid guard test failed on CI python-minimum because the runner's own pid was 2468; the test now picks a pid that is not the runner's.
- Reproduced the walkie side stall. The 333-node board claim was 182,656 characters, which the native client replaced with oversize error text, so the claim was retried forever.
- Paged authenticated host Work results and verified them by digest in the mod.
- Rendered originals as interpreted CloudEvents with opaque fields hidden.
- Native 333-node E2E: before, no side model request; after, a reply was submitted and pending was 0.
