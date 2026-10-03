"""A tiny FAKE Supabase (Auth + database API) so the tests run without internet or accounts."""
import json
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlparse

PUBLISHABLE, SECRET = "sb_publishable_test", "sb_secret_test"
TABLES = ("wm_profiles", "wm_posts", "wm_connections", "wm_messages", "wm_blocks", "wm_reports")
NO_ID = ("wm_profiles", "wm_posts", "wm_blocks")                       # tables without an auto id column
KEYS = {"wm_profiles": ["id"], "wm_posts": ["user_id"], "wm_blocks": ["blocker", "blocked"]}


def _val(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return datetime.fromisoformat(str(x).replace("Z", "+00:00"))


def _cmp(row_val, op, val):
    if op == "eq": return str(row_val) == val
    if op == "neq": return str(row_val) != val
    if op in ("gt", "gte", "lt", "lte"):
        a, b = _val(row_val), _val(val)
        return {"gt": a > b, "gte": a >= b, "lt": a < b, "lte": a <= b}[op]
    if op == "in": return str(row_val) in val.strip("()").split(",")
    if op == "ov": return bool({int(x) for x in val.strip("{}").split(",")} & set(row_val or []))
    raise ValueError("unsupported operator " + op)


def _split(s):
    parts, depth, cur = [], 0, ""
    for ch in s:
        depth += (ch == "(") - (ch == ")")
        if ch == "," and depth == 0: parts.append(cur); cur = ""
        else: cur += ch
    return parts + ([cur] if cur else [])


def _expr(row, e):
    for name in ("and", "or"):
        if e.startswith(name + "(") and e.endswith(")"):
            results = [_expr(row, p) for p in _split(e[len(name) + 1:-1])]
            return all(results) if name == "and" else any(results)
    col, op, val = e.split(".", 2)
    return _cmp(row.get(col), op, val)


class FakeSupabase:
    def __init__(self):
        self.reset()

    def reset(self):
        self.tables = {t: [] for t in TABLES}
        self.seq, self.users, self.tokens, self.refresh = {}, {}, {}, {}
        self.expired, self.emails, self.confirm_email, self.no_grants = set(), [], False, False

    # ---- fake sign-in server (GoTrue) ----
    def session(self, uid):
        at, rt = "at-" + uuid.uuid4().hex, "rt-" + uuid.uuid4().hex
        self.tokens[at], self.refresh[rt] = uid, uid
        return {"access_token": at, "refresh_token": rt, "expires_in": 3600, "user": {"id": uid}}

    def auth(self, path, query, data, headers):
        if headers.get("apikey") != PUBLISHABLE: return 401, {"msg": "Invalid API key"}
        if path != "user" and headers.get("Authorization"): return 401, {"msg": "Invalid JWT"}   # no Bearer for anonymous auth calls
        if path == "user":
            token = headers.get("Authorization", "")[7:]
            if token in self.tokens and token not in self.expired: return 200, {"id": self.tokens[token]}
            return 401, {"msg": "invalid JWT"}
        if path == "signup":
            self.last_signup_query = dict(query)
            email = data["email"]
            self.users.setdefault(email, {"id": str(uuid.uuid4()), "password": data["password"], "confirmed": not self.confirm_email})
            if self.confirm_email: self.emails.append(email); return 200, {"id": self.users[email]["id"], "email": email}
            return 200, self.session(self.users[email]["id"])
        if path == "token" and query.get("grant_type") == "password":
            u = self.users.get(data["email"])
            if not u or u["password"] != data["password"]: return 400, {"msg": "Invalid login credentials", "error_code": "invalid_credentials"}
            if not u["confirmed"]: return 400, {"msg": "Email not confirmed", "error_code": "email_not_confirmed"}
            return 200, self.session(u["id"])
        if path == "token" and query.get("grant_type") == "refresh_token":
            uid = self.refresh.get(data["refresh_token"])
            return (200, self.session(uid)) if uid else (400, {"msg": "Invalid Refresh Token"})
        if path == "resend": self.emails.append(data["email"]); return 200, {}
        if path == "otp": self.users.setdefault(data["phone"], {"id": str(uuid.uuid4()), "password": "", "confirmed": True}); return 200, {}
        if path == "verify":
            u = self.users.get(data["phone"])
            return (200, self.session(u["id"])) if u and data["token"] == "123456" else (400, {"msg": "Token has expired or is invalid"})
        return 404, {"msg": "unknown auth route"}

    # ---- fake database server (PostgREST) ----
    def rest(self, method, table, params, data, prefer, headers):
        if headers.get("apikey") != SECRET or headers.get("Authorization"): return 401, {"message": "Invalid JWT"}   # new keys: apikey header ONLY
        if self.no_grants: return 403, {"code": "42501", "message": "permission denied for table wm_profiles"}
        if table not in self.tables: return 404, {"code": "PGRST205", "message": f"Could not find the table 'public.{table}'"}
        rows = self.tables[table]
        match = lambda r: all(_expr(r, "or" + v) if k == "or" else _cmp(r.get(k), *v.split(".", 1)) for k, v in params
                              if k not in ("select", "order", "limit", "on_conflict"))
        pm = dict(params)
        if method == "GET":
            out = [r for r in rows if match(r)]
            if "order" in pm:
                col, direction = pm["order"].split(".")
                out.sort(key=lambda r: r[col], reverse=direction == "desc")
            return 200, out[: int(pm.get("limit", 10**9))]
        if method == "POST":
            keys = pm["on_conflict"].split(",") if "on_conflict" in pm else KEYS.get(table)
            twin = next((r for r in rows if keys and all(r.get(k) == data.get(k) for k in keys)), None)
            if table == "wm_connections" and any(r["requester"] == data["requester"] and r["recipient"] == data["recipient"] for r in rows):
                return 409, {"message": "duplicate key value violates unique constraint"}
            if twin and "merge-duplicates" in prefer: twin.update(data); return 201, [twin]
            if twin and "ignore-duplicates" in prefer: return 201, []
            if twin: return 409, {"message": "duplicate key"}
            self.seq[table] = self.seq.get(table, 0) + 1
            row = {"created_at": datetime.now(timezone.utc).isoformat()}
            if table not in NO_ID: row["id"] = self.seq[table]
            row.update({"wm_profiles": {"suspended": False, "bio": ""}, "wm_posts": {"pref": "any", "note": ""}}.get(table, {}))
            row.update(data); rows.append(row)
            return 201, [row]
        if method == "PATCH":
            hit = [r for r in rows if match(r)]
            for r in hit: r.update(data)
            return 200, hit
        if method == "DELETE":
            self.tables[table] = [r for r in rows if not match(r)]
            return 204, None

    def start(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def handle_any(self):
                u = urlparse(self.path)
                params = parse_qsl(u.query, keep_blank_values=True)
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                data = json.loads(raw) if raw else None
                prefer = self.headers.get("Prefer", "")
                if u.path.startswith("/auth/v1/"):
                    status, out = fake.auth(u.path[9:], dict(params), data, self.headers)
                else:
                    status, out = fake.rest(self.command, u.path.replace("/rest/v1/", ""), params, data, prefer, self.headers)
                    if status == 201 and "return=representation" not in prefer: out = None
                    if status == 200 and self.command == "PATCH" and "return=representation" not in prefer: out = None
                payload = b"" if out is None else json.dumps(out).encode()
                self.send_response(status); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload))); self.end_headers(); self.wfile.write(payload)
            do_GET = do_POST = do_PATCH = do_DELETE = handle_any

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self
