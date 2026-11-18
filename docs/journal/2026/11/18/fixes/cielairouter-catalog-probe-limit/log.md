# Log

## 2026-11-18

- User report: on the KOBE sandbox (latest beta), Claude with cielairouter is blocked as "Base URL unreachable (ValueError)".
- Measured the gateway catalog: 1,292,256 bytes, 2,319 models. Found the 1 MiB probe cap in `provider_status._generic_status` (from 2ccfbaf).
- Reproduced with the installed beta 66da4e2 and the real gateway: the status line and the launch gate show the exact error. The patched tree allows the launch.
- Fix: a 16 MiB catalog probe cap, and an explicit message for an oversized catalog. Tests fail before and pass after. See [record.md](record.md).
