"""Time the authenticated readiness GET; prints status/timing/count only, never the key or body."""
import sys, time, json, hashlib, urllib.request, urllib.error
snap = r"C:\Users\djlov\.local\share\ciel-runtime-ea59a6105838f611d1c4168ab150f01c6f4e6fd2"
sys.path.insert(0, snap)
import ciel_runtime as cr
pcfg = (cr.load_config().get("providers") or {}).get("cielairouter") or {}
key = cr.provider_primary_api_key("cielairouter", pcfg)
print("key_present", bool(key), "key_fp", hashlib.sha256(key.encode()).hexdigest()[:12] if key else "")
base = str(pcfg.get("base_url")).rstrip("/")
print("ciel_gateway_route", bool(pcfg.get("ciel_gateway_route")), "ip_family", pcfg.get("ip_family"))
paths = ["/v1/models?prefix=alias&configuredOnly=true&availableOnly=true", "/v1/models"]
for path in paths:
    for attempt in range(2):
        headers = {"x-api-key": key, "authorization": f"Bearer {key}", "user-agent": "claude-cli"}
        t = time.time()
        try:
            with urllib.request.urlopen(urllib.request.Request(base + path, headers=headers), timeout=60) as r:
                ttfb = time.time() - t
                body = r.read()
                total = time.time() - t
                try:
                    n = len(json.loads(body).get("data") or [])
                except Exception:
                    n = "unparsed"
                print("ok", path, attempt, r.status, "ttfb", round(ttfb, 3), "total", round(total, 3), "bytes", len(body), "models", n)
        except urllib.error.HTTPError as e:
            print("http_error", path, attempt, e.code, round(time.time() - t, 3))
        except Exception as e:
            print("fail", path, attempt, type(e).__name__, round(time.time() - t, 3))
