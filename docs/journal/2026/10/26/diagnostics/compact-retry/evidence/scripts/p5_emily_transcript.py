# Read-only: Emily transcript records between 2026-10-26T19:40Z and 21:20Z.
# Prints record type/subtype/timestamp and a short, non-secret preview.
import os, json, time, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
def T(x): return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(x))
t0 = time.time(); print("REMOTE_START", T(t0), "gmtoff", time.localtime(t0).tm_gmtoff)
p = r"C:\Users\aapw-a11u4\.claude\projects\C--Data-Source-SGSWMSCrossing-u4-home-emily-ai\a9a016f2-bd19-4066-8b44-6ee329194e16.jsonl"
st = os.stat(p); print("FILE size", st.st_size, "mtime", T(st.st_mtime))
def preview(r):
    m = r.get("message") if isinstance(r.get("message"), dict) else {}
    c = m.get("content") if m else r.get("content")
    if isinstance(c, str): return c[:240]
    if isinstance(c, list):
        out = []
        for b in c:
            if not isinstance(b, dict): continue
            t = b.get("type")
            if t == "text": out.append("T:" + b.get("text", "")[:200])
            elif t == "tool_use": out.append("TU:" + str(b.get("name")))
            elif t == "tool_result": out.append("TR")
            elif t == "thinking": out.append("TH")
            else: out.append(str(t))
        return " | ".join(out)[:260]
    return str(c)[:200]
with open(p, "rb") as fh:
    for i, raw in enumerate(fh):
        if b'"timestamp":"2026-10-26T2' not in raw and b'"timestamp": "2026-10-26T2' not in raw: continue
        try: r = json.loads(raw)
        except Exception: continue
        ts = r.get("timestamp") or ""
        if not ("2026-10-26T19:40" <= ts <= "2026-10-26T21:20"): continue
        typ = r.get("type")
        if typ == "assistant":
            m = r.get("message") or {}
            u = m.get("usage") or {}
            tot = sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
            pv = preview(r)
            if "TU:" in pv and len(pv) < 40: print(i, ts, "assistant", "tool_use", "tot", tot); continue
            print(i, ts, "assistant", m.get("model"), "tot", tot, "stop", m.get("stop_reason"), pv[:200].replace("\n", " "))
        elif typ in ("user", "system"):
            pv = preview(r)
            if pv == "TR": continue
            print(i, ts, typ, r.get("subtype"), "meta" if r.get("isMeta") else "", json.dumps(r.get("compactMetadata")) if r.get("compactMetadata") else "", pv.replace("\n", " "))
print("REMOTE_END", T(time.time()))
