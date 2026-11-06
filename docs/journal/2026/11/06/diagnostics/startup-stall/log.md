# Change log

## 2026-11-06

- Fixed and verified startup latency:
  - Owned ConPTY runs took `--continue` to the first menu frame in 26.5 s on beta a5794b4 and 3.0–4.5 s on the patched tree.
  - No recursive launcher children ran.
  - The real port scan dropped from 7.551 s to 0.012 s.
- Verified the following on the patched tree:
  - launch phase trace and lease release
  - side MCP parity on two native servers, with a pane screenshot
  - the renamed reply-route menu row
- Owned reproduction: the blank pre-menu window was 29.5 s, made up of the parent scan plus two recursive `--version` children that each ran their own scan. After the menu renders, the launcher waits in `read_menu_key`.
- Recorded measured startup delays, the long-stall boundary and the side MCP inheritance contract.
