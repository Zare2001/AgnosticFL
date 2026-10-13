#!/usr/bin/env python3
"""agentbus - tiny message bus for the AgnosticFL build agents.

Stdlib only. One server (on the central VM, port 7700) and a CLI client.

Server:
    python3 agentbus.py serve [--port 7700] [--db ~/.flhub/agentbus.sqlite]

Client (reads AGENTBUS_URL, AGENTBUS_TOKEN, FLHUB_ROLE from the environment):
    agentbus.py send   --to nvflare --type push --subject "a1b2c3 core: add FedAvg" [--body "..."]
    agentbus.py inbox  [--all]            # new messages since your cursor (advances cursor)
    agentbus.py wait   [--timeout 600]    # block until a new message arrives (long-poll)
    agentbus.py log    [--since 0] [--limit 200]
    agentbus.py status-set --phase P1 --summary "island run passes" [--blockers "..."]
    agentbus.py status                    # status board of all agents
    agentbus.py put    FILE --to central [--secret] [--note "..."]   # upload a file
    agentbus.py get    FILE_ID [-o OUT]                              # download (auto-decrypts)
    agentbus.py export [-o bus_transcript.md]                        # full transcript for the report
    agentbus.py health

Agents: central, nvflare, vantage6, flower. Use --to all to broadcast.
Secret files are encrypted client-side with openssl (AES-256-CBC, PBKDF2) using
AGENTBUS_TOKEN as the passphrase, so the server only ever stores ciphertext.
"""
import argparse
import base64
import hmac
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

AGENTS = {"central", "nvflare", "vantage6", "flower", "human"}
TYPES = {
    "push", "request", "reply", "deploy-request", "deploy-result", "blocker",
    "milestone", "info", "final-call", "report-ready", "all-done", "secret",
}
MAX_BODY = 200 * 1024 * 1024  # 200 MB request cap (files are base64)
STATE_DIR = Path(os.environ.get("FLHUB_STATE_DIR", Path.home() / ".flhub"))


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------- server
class Store:
    def __init__(self, path):
        self.lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS msg(id INTEGER PRIMARY KEY AUTOINCREMENT,
              ts TEXT, sender TEXT, recipient TEXT, type TEXT, subject TEXT, body TEXT);
            CREATE TABLE IF NOT EXISTS status(agent TEXT PRIMARY KEY, ts TEXT,
              phase TEXT, summary TEXT, blockers TEXT);
            CREATE TABLE IF NOT EXISTS file(id INTEGER PRIMARY KEY AUTOINCREMENT,
              ts TEXT, sender TEXT, recipient TEXT, name TEXT, secret INTEGER,
              note TEXT, data BLOB);
            """
        )
        self.db.commit()

    def q(self, sql, args=()):
        with self.lock:
            cur = self.db.execute(sql, args)
            self.db.commit()
            return cur


class Handler(BaseHTTPRequestHandler):
    server_version = "agentbus/1.0"

    # quieter logs: one line per request
    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (now(), fmt % args))

    def _auth(self):
        token = self.server.token
        got = self.headers.get("Authorization", "")
        ok = hmac.compare_digest(got, f"Bearer {token}")
        if not ok:
            self._send(401, {"error": "unauthorized"})
        return ok

    def _send(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self):
        n = int(self.headers.get("Content-Length", "0"))
        if n > MAX_BODY:
            raise ValueError("body too large")
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        qs = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
        if url.path == "/health":
            return self._send(200, {"ok": True, "ts": now()})
        if not self._auth():
            return
        st = self.server.store
        if url.path == "/inbox":
            agent = qs.get("agent", "")
            since = int(qs.get("since", "0"))
            wait = min(int(qs.get("wait", "0")), 600)
            deadline = time.time() + wait
            while True:
                rows = st.q(
                    "SELECT id,ts,sender,recipient,type,subject,body FROM msg "
                    "WHERE id>? AND (recipient=? OR recipient='all') AND sender!=? "
                    "ORDER BY id", (since, agent, agent)).fetchall()
                if rows or time.time() >= deadline:
                    break
                time.sleep(1.0)
            return self._send(200, {"messages": [_row(r) for r in rows]})
        if url.path == "/log":
            since = int(qs.get("since", "0"))
            limit = min(int(qs.get("limit", "500")), 5000)
            rows = st.q(
                "SELECT id,ts,sender,recipient,type,subject,body FROM msg "
                "WHERE id>? ORDER BY id LIMIT ?", (since, limit)).fetchall()
            return self._send(200, {"messages": [_row(r) for r in rows]})
        if url.path == "/status":
            rows = st.q("SELECT agent,ts,phase,summary,blockers FROM status ORDER BY agent").fetchall()
            return self._send(200, {"status": [dict(zip(
                ["agent", "ts", "phase", "summary", "blockers"], r)) for r in rows]})
        if url.path == "/file":
            row = st.q("SELECT id,ts,sender,recipient,name,secret,note,data FROM file WHERE id=?",
                       (int(qs.get("id", "0")),)).fetchone()
            if not row:
                return self._send(404, {"error": "no such file"})
            return self._send(200, {
                "id": row[0], "ts": row[1], "from": row[2], "to": row[3], "name": row[4],
                "secret": bool(row[5]), "note": row[6],
                "data_b64": base64.b64encode(row[7]).decode()})
        if url.path == "/files":
            rows = st.q("SELECT id,ts,sender,recipient,name,secret,note,length(data) FROM file "
                        "ORDER BY id").fetchall()
            return self._send(200, {"files": [dict(zip(
                ["id", "ts", "from", "to", "name", "secret", "note", "bytes"], r)) for r in rows]})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._auth():
            return
        try:
            body = self._json()
        except Exception as e:  # noqa: BLE001
            return self._send(400, {"error": str(e)})
        st = self.server.store
        path = urllib.parse.urlparse(self.path).path
        if path == "/msg":
            sender, to, typ = body.get("from"), body.get("to"), body.get("type", "info")
            if sender not in AGENTS or (to not in AGENTS and to != "all"):
                return self._send(400, {"error": f"from/to must be one of {sorted(AGENTS)} or all"})
            if typ not in TYPES:
                return self._send(400, {"error": f"type must be one of {sorted(TYPES)}"})
            cur = st.q("INSERT INTO msg(ts,sender,recipient,type,subject,body) VALUES(?,?,?,?,?,?)",
                       (now(), sender, to, typ, body.get("subject", "")[:300], body.get("body", "")))
            return self._send(200, {"id": cur.lastrowid})
        if path == "/status":
            agent = body.get("agent")
            if agent not in AGENTS:
                return self._send(400, {"error": "bad agent"})
            st.q("INSERT INTO status(agent,ts,phase,summary,blockers) VALUES(?,?,?,?,?) "
                 "ON CONFLICT(agent) DO UPDATE SET ts=excluded.ts, phase=excluded.phase, "
                 "summary=excluded.summary, blockers=excluded.blockers",
                 (agent, now(), body.get("phase", ""), body.get("summary", ""), body.get("blockers", "")))
            return self._send(200, {"ok": True})
        if path == "/file":
            data = base64.b64decode(body.get("data_b64", ""))
            cur = st.q("INSERT INTO file(ts,sender,recipient,name,secret,note,data) VALUES(?,?,?,?,?,?,?)",
                       (now(), body.get("from"), body.get("to"), body.get("name"),
                        int(bool(body.get("secret"))), body.get("note", ""), data))
            return self._send(200, {"file_id": cur.lastrowid})
        return self._send(404, {"error": "not found"})


def _row(r):
    return dict(zip(["id", "ts", "from", "to", "type", "subject", "body"], r))


def serve(args):
    token = os.environ.get("AGENTBUS_TOKEN")
    if not token or len(token) < 16:
        sys.exit("AGENTBUS_TOKEN must be set (>=16 chars)")
    db = Path(args.db).expanduser()
    db.parent.mkdir(parents=True, exist_ok=True)
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    srv.daemon_threads = True
    srv.token = token
    srv.store = Store(str(db))
    print(f"agentbus listening on {args.host}:{args.port} db={db}", flush=True)
    srv.serve_forever()


# --------------------------------------------------------------------- client
def _cfg():
    url = os.environ.get("AGENTBUS_URL", "http://145.38.205.58:7700").rstrip("/")
    token = os.environ.get("AGENTBUS_TOKEN")
    role = os.environ.get("FLHUB_ROLE")
    if not token:
        sys.exit("AGENTBUS_TOKEN not set (source ~/.flhub/secrets.env)")
    return url, token, role


def _req(method, path, payload=None, timeout=660):
    url, token, _ = _cfg()
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url + path, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            sys.exit(f"agentbus HTTP {e.code}: {e.read().decode(errors='replace')}")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if attempt == 4:
                sys.exit(f"agentbus unreachable at {url}: {e}")
            time.sleep(3 * (attempt + 1))


def _role(args):
    role = getattr(args, "as_", None) or _cfg()[2]
    if role not in AGENTS:
        sys.exit("Set FLHUB_ROLE (central|nvflare|vantage6|flower) or pass --as")
    return role


def _cursor_path(role):
    return STATE_DIR / f"bus_cursor_{role}"


def _print_msgs(msgs):
    for m in msgs:
        print(f"#{m['id']} {m['ts']} [{m['type']}] {m['from']} -> {m['to']}: {m['subject']}")
        if m.get("body"):
            for line in m["body"].splitlines():
                print(f"    {line}")


def cmd_send(args):
    body = args.body
    if args.body_file:
        body = Path(args.body_file).read_text()
    r = _req("POST", "/msg", {"from": _role(args), "to": args.to, "type": args.type,
                              "subject": args.subject, "body": body or ""})
    print(f"sent #{r['id']}")


def _fetch_inbox(args, wait):
    role = _role(args)
    cp = _cursor_path(role)
    since = 0 if getattr(args, "all", False) else int(cp.read_text()) if cp.exists() else 0
    r = _req("GET", f"/inbox?agent={role}&since={since}&wait={wait}")
    msgs = r["messages"]
    if msgs and not getattr(args, "all", False):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        cp.write_text(str(msgs[-1]["id"]))
    return msgs


def cmd_inbox(args):
    msgs = _fetch_inbox(args, 0)
    _print_msgs(msgs) if msgs else print("(no new messages)")


def cmd_wait(args):
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        msgs = _fetch_inbox(args, min(600, int(deadline - time.time()) or 1))
        if msgs:
            _print_msgs(msgs)
            return
    print("(timeout, no new messages)")


def cmd_log(args):
    _print_msgs(_req("GET", f"/log?since={args.since}&limit={args.limit}")["messages"])


def cmd_status_set(args):
    _req("POST", "/status", {"agent": _role(args), "phase": args.phase,
                             "summary": args.summary, "blockers": args.blockers or ""})
    print("status updated")


def cmd_status(args):
    for s in _req("GET", "/status")["status"]:
        print(f"{s['agent']:9} {s['ts']} {s['phase']:6} {s['summary']}"
              + (f"  BLOCKERS: {s['blockers']}" if s["blockers"] else ""))


def _openssl(mode, data):
    _, token, _ = _cfg()
    flag = [] if mode == "enc" else ["-d"]
    p = subprocess.run(
        ["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-salt", *flag,
         "-pass", "env:AGENTBUS_TOKEN"],
        input=data, capture_output=True, env={**os.environ, "AGENTBUS_TOKEN": token})
    if p.returncode != 0:
        sys.exit(f"openssl {mode} failed: {p.stderr.decode()}")
    return p.stdout


def cmd_put(args):
    raw = Path(args.file).read_bytes()
    data = _openssl("enc", raw) if args.secret else raw
    r = _req("POST", "/file", {"from": _role(args), "to": args.to, "name": Path(args.file).name,
                               "secret": args.secret, "note": args.note or "",
                               "data_b64": base64.b64encode(data).decode()})
    fid = r["file_id"]
    _req("POST", "/msg", {"from": _role(args), "to": args.to,
                          "type": "secret" if args.secret else "info",
                          "subject": f"file #{fid}: {Path(args.file).name}",
                          "body": f"{args.note or ''}\nfetch with: tools/bus get {fid} -o <path>"})
    print(f"uploaded file #{fid}")


def cmd_get(args):
    r = _req("GET", f"/file?id={args.file_id}")
    data = base64.b64decode(r["data_b64"])
    if r["secret"]:
        data = _openssl("dec", data)
    out = Path(args.output or r["name"])
    out.write_bytes(data)
    if r["secret"]:
        os.chmod(out, 0o600)
    print(f"wrote {out} ({len(data)} bytes{', decrypted' if r['secret'] else ''})")


def cmd_export(args):
    msgs, since = [], 0
    while True:
        chunk = _req("GET", f"/log?since={since}&limit=5000")["messages"]
        if not chunk:
            break
        msgs += chunk
        since = chunk[-1]["id"]
    lines = ["# Agent bus transcript", "", f"Exported {now()} - {len(msgs)} messages", ""]
    for m in msgs:
        lines.append(f"- **#{m['id']}** `{m['ts']}` **{m['from']} → {m['to']}** [{m['type']}] {m['subject']}")
        if m.get("body"):
            lines += [f"  > {ln}" for ln in m["body"].splitlines()]
    Path(args.output).write_text("\n".join(lines) + "\n")
    print(f"wrote {args.output}")


def cmd_health(_args):
    url, _, _ = _cfg()
    with urllib.request.urlopen(url + "/health", timeout=10) as r:
        print(r.read().decode())


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=7700)
    s.add_argument("--db", default=str(STATE_DIR / "agentbus.sqlite"))
    s.set_defaults(fn=serve)

    def with_as(sp):
        sp.add_argument("--as", dest="as_", help="override FLHUB_ROLE")
        return sp

    s = with_as(sub.add_parser("send"))
    s.add_argument("--to", required=True)
    s.add_argument("--type", default="info")
    s.add_argument("--subject", required=True)
    s.add_argument("--body")
    s.add_argument("--body-file")
    s.set_defaults(fn=cmd_send)

    s = with_as(sub.add_parser("inbox"))
    s.add_argument("--all", action="store_true", help="show everything, do not move cursor")
    s.set_defaults(fn=cmd_inbox)

    s = with_as(sub.add_parser("wait"))
    s.add_argument("--timeout", type=int, default=600)
    s.set_defaults(fn=cmd_wait)

    s = sub.add_parser("log")
    s.add_argument("--since", type=int, default=0)
    s.add_argument("--limit", type=int, default=200)
    s.set_defaults(fn=cmd_log)

    s = with_as(sub.add_parser("status-set"))
    s.add_argument("--phase", required=True)
    s.add_argument("--summary", required=True)
    s.add_argument("--blockers")
    s.set_defaults(fn=cmd_status_set)

    sub.add_parser("status").set_defaults(fn=cmd_status)

    s = with_as(sub.add_parser("put"))
    s.add_argument("file")
    s.add_argument("--to", required=True)
    s.add_argument("--secret", action="store_true")
    s.add_argument("--note")
    s.set_defaults(fn=cmd_put)

    s = sub.add_parser("get")
    s.add_argument("file_id", type=int)
    s.add_argument("-o", "--output")
    s.set_defaults(fn=cmd_get)

    s = sub.add_parser("export")
    s.add_argument("-o", "--output", default="bus_transcript.md")
    s.set_defaults(fn=cmd_export)

    sub.add_parser("health").set_defaults(fn=cmd_health)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
