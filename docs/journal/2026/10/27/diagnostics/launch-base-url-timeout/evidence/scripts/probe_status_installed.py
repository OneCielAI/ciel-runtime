"""Run an installed snapshot's launch readiness gate against the local config.

Prints only the current provider, base URL, timings and the gate's own lines
(never API keys or response bodies). argv[1] = installed snapshot directory.
"""
import sys, time
snap = sys.argv[1]
rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 3
sys.path.insert(0, snap)
import ciel_runtime as cr
cfg = cr.load_config()
provider, pcfg = cr.get_current_provider(cfg)
print("snapshot", snap.rsplit("-", 1)[-1][:7], "provider", provider, "base_url", pcfg.get("base_url"),
      "gateway_route", bool(pcfg.get("ciel_gateway_route")))
for i in range(rounds):
    t = time.time()
    status = cr.base_url_status_line(provider, pcfg)
    s1 = time.time() - t
    t = time.time()
    errors = [e for e in cr.launch_readiness_errors(cfg) if "Launch blocked" in e or "reachable" in e.lower()]
    s2 = time.time() - t
    print(time.strftime("%H:%M:%S"), "round", i, "status", round(s1, 3), status, "| gate", round(s2, 3), errors or "no blockers")
