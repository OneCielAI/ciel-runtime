# ADR 0001: Runtime bounded contexts and file-size budget

## Status

Accepted. Migration is in progress on `architecture/runtime-evolution-2026-07-19`.

## Decision

`ciel_runtime.py` becomes a compatibility facade and composition root. It may
select adapters and assemble typed services, but it must not own provider wire
logic, persistence, UI rendering, channel transport, retry state machines, or
runtime process implementations.

Production code is partitioned into these inward-facing contexts:

1. `architecture`, registries, policies, and immutable domain values
2. provider and protocol adapters
3. channel application services and infrastructure adapters
4. router application services and HTTP adapters
5. runtime launch/process application services
6. configuration repositories and migrations
7. presentation policies and terminal/web adapters
8. the top-level composition root and compatibility facade

Dependencies point from adapters and composition toward domain/application
contracts. Support modules never import `ciel_runtime`. Cross-context behavior
uses typed ports or immutable values, not provider-name conditionals or a shared
service-locator dictionary.

Every production Python file must remain below 5,000 physical lines. The final
facade budget is 4,999 lines. During migration, `MAIN_FILE_LINE_BUDGET` is a
monotonically decreasing ratchet and may never be raised.

## Rejected alternatives

- Moving the monolith unchanged into a differently named file
- Loading source fragments with `exec` into a shared global namespace
- Circular wildcard imports between numbered runtime fragments
- A global dictionary used as an untyped service locator
- Provider-specific branches inside runtime, protocol, or presentation layers

These alternatives reduce a filename's line count without reducing coupling.
