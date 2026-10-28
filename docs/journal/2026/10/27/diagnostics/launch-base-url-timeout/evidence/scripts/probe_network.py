import socket, ssl, time, urllib.request, urllib.error, sys
host = "ciel-router-01.ezonebot.com"
t = time.time()
try:
    infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    print("dns_ok", round(time.time()-t, 3), sorted({i[4][0] for i in infos}))
except Exception as e:
    print("dns_fail", type(e).__name__, e); sys.exit()
for addr in sorted({i[4][0] for i in infos}):
    t = time.time()
    try:
        s = socket.create_connection((addr, 443), timeout=10)
        print("tcp_ok", addr, round(time.time()-t, 3))
        t = time.time()
        ctx = ssl.create_default_context()
        ss = ctx.wrap_socket(s, server_hostname=host)
        print("tls_ok", addr, round(time.time()-t, 3), ss.version())
        ss.close()
    except Exception as e:
        print("tcp_or_tls_fail", addr, type(e).__name__, e, round(time.time()-t, 3))
for path in ("/v1/models?prefix=alias&configuredOnly=true&availableOnly=true", "/health"):
    t = time.time()
    try:
        with urllib.request.urlopen(urllib.request.Request("https://"+host+path, headers={"User-Agent": "probe"}), timeout=10) as r:
            print("http", path, r.status, round(time.time()-t, 3))
    except urllib.error.HTTPError as e:
        print("http", path, e.code, round(time.time()-t, 3))
    except Exception as e:
        print("http_fail", path, type(e).__name__, e, round(time.time()-t, 3))
import os
print("proxy_env", {k: bool(v) for k, v in os.environ.items() if "proxy" in k.lower()})
print("urllib_proxies", list(urllib.request.getproxies().keys()))
