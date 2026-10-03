"""
Waymate backend (FastAPI).   Run:  python backend.py   then open  http://localhost:3000   (API docs: /docs)

This ONE file does everything on the server side:
  1. serves the website          (the files in public/)
  2. sign-up / sign-in           (Supabase Auth; the login lives in an httpOnly cookie)
  3. the JSON API under /api/... (profile, route, matches, buddies, chat, block, report)
  4. matching                    (find people on the same path, same direction, same time)
The database is Supabase Postgres (tables wm_*, see supabase/schema.sql). The browser never sees the service key.
"""
import json
import math
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.params import Body
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

# ───────────────────────── 1. settings (from the .env file) ─────────────────────────
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
for _suffix in ("/rest/v1", "/auth/v1"):                      # forgiving if the whole API URL was pasted
    if SUPABASE_URL.endswith(_suffix):
        SUPABASE_URL = SUPABASE_URL[: -len(_suffix)]
ANON_KEY = (os.getenv("SUPABASE_PUBLISHABLE_KEY") or os.getenv("SUPABASE_ANON_KEY") or "").strip()    # publishable key; used only for sign-in calls
SERVICE_KEY = (os.getenv("SUPABASE_SECRET_KEY") or os.getenv("SUPABASE_SERVICE_KEY") or "").strip()  # secret key; used for all database calls. KEEP SECRET.
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "3000"))
ON_VERCEL = bool(os.getenv("VERCEL"))                          # Vercel sets this automatically
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "1" if ON_VERCEL else "0") == "1"   # https-only cookies (automatic on Vercel)
SITE_URL = os.getenv("SITE_URL", "").strip().rstrip("/")       # your public address, e.g. https://waymate.in  (used in email links)

MIN_AGE = 18                  # adults only
MATCH_WINDOW_MIN = 45         # two people must leave within this many minutes of each other
MIN_OVERLAP_RATIO = 0.5       # they must share at least this share of the shorter trip (1.0 = whole shorter trip)
REASONS = ("fake_or_spam", "harassment", "inappropriate_content", "unsafe_behaviour", "other")

BASE = os.path.dirname(os.path.abspath(__file__))
app = FastAPI(title="Waymate", docs_url=None if ON_VERCEL else "/docs", redoc_url=None,
              openapi_url=None if ON_VERCEL else "/openapi.json")   # /docs = interactive list of every endpoint (local only)

with open(os.path.join(BASE, "data", "stations.json"), encoding="utf-8") as f:
    STATIONS = json.load(f)                                   # [{name, km, br}] br: T=trunk, V=Vaishali, N=Noida
YAMUNA_BANK = next(i for i, s in enumerate(STATIONS) if s["name"] == "Yamuna Bank")


# ───────────────────────── 2. small helpers ─────────────────────────
class ApiError(Exception):
    """Raise this anywhere to send a clean JSON error to the browser."""
    def __init__(self, status, message, code=None):
        super().__init__(message)
        self.status, self.message, self.code = status, message, code


@app.exception_handler(ApiError)
def _api_error(request, e):
    return JSONResponse({"error": e.message, "code": e.code}, status_code=e.status)


@app.exception_handler(requests.RequestException)
def _network_error(request, e):
    return JSONResponse({"error": "Can't reach Supabase. Check SUPABASE_URL in .env and that your project isn't paused."}, status_code=503)


@app.exception_handler(RequestValidationError)
def _bad_request(request, e):
    return JSONResponse({"error": "Invalid request"}, status_code=400)


def reply(**fields):
    """Every endpoint answers with a small JSON object, e.g. reply(ok=True)."""
    return fields


def json_body(payload: Optional[dict] = Body(default=None)):
    """The JSON the browser sent (or {} if it sent none)."""
    return payload or {}


def site_root(request):
    """Our public address, used for the link inside sign-up emails."""
    if SITE_URL:
        return SITE_URL + "/"
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
    return f"{proto}://{host}/"


def bad(message):
    raise ApiError(400, message)


def text(value, limit):
    return str(value or "").strip()[:limit]


def valid_id(value):
    """User ids are UUIDs. Checking the format also stops anyone sneaking filter syntax into a query."""
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        bad("Invalid user")


def parse_time(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        bad("Invalid time")


def first(rows):
    return rows[0] if rows else None


# ───────────────────────── 3. talking to Supabase ─────────────────────────
def _error_text(r):
    try:
        d = r.json()
        return d.get("msg") or d.get("error_description") or d.get("message") or d.get("error") or f"HTTP {r.status_code}"
    except ValueError:
        return r.text[:200] or f"HTTP {r.status_code}"


def key_headers(key, token=None):
    """New-style keys (sb_publishable_... / sb_secret_...) must go ONLY in the `apikey` header. Old JWT-style keys
    (anon / service_role) are also sent as a Bearer token. `token` is a signed-in user's access token."""
    headers = {"apikey": key}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    elif not key.startswith("sb_"):
        headers["Authorization"] = f"Bearer {key}"
    return headers


def rest(method, table, params=None, json_body=None, prefer=None):
    """Database call (PostgREST). `params` holds filters like {"user_id": "eq.123"}."""
    headers = key_headers(SERVICE_KEY)
    if prefer:
        headers["Prefer"] = prefer
    r = requests.request(method, f"{SUPABASE_URL}/rest/v1/{table}", params=params, json=json_body, headers=headers, timeout=15)
    if r.status_code >= 400:
        raise ApiError(409 if r.status_code == 409 else 502, _error_text(r))
    return r.json() if r.content else None


def auth_call(path, payload=None, params=None, token=None, method="POST"):
    """Sign-in call (Supabase Auth / GoTrue)."""
    headers = key_headers(ANON_KEY, token)
    r = requests.request(method, f"{SUPABASE_URL}/auth/v1/{path}", params=params, json=payload, headers=headers, timeout=15)
    data = r.json() if r.content else {}
    if r.status_code >= 400:
        raise ApiError(400 if r.status_code < 500 else 502, _error_text(r), data.get("error_code") if isinstance(data, dict) else None)
    return data


# ───────────────────────── 4. login session (cookies) ─────────────────────────
ACCESS, REFRESH = "wm_access", "wm_refresh"
_token_cache = {}   # access token -> (user id, valid until). Saves asking Supabase on every request.


def set_session_cookies(response, session):
    flags = dict(httponly=True, samesite="lax", secure=COOKIE_SECURE)
    response.set_cookie(ACCESS, session["access_token"], max_age=int(session.get("expires_in", 3600)), **flags)
    response.set_cookie(REFRESH, session["refresh_token"], max_age=60 * 24 * 3600, **flags)


def user_id_for(token):
    hit = _token_cache.get(token)
    if hit and hit[1] > time.time():
        return hit[0]
    try:
        uid = auth_call("user", token=token, method="GET")["id"]
    except ApiError:
        return None
    _token_cache[token] = (uid, time.time() + 30)
    return uid


def resolve_user(request):
    """The signed-in user's id, or None. Quietly renews an expired login using the refresh cookie."""
    token = request.cookies.get(ACCESS)
    uid = user_id_for(token) if token else None
    refresh = request.cookies.get(REFRESH)
    if not uid and refresh:
        try:
            session = auth_call("token", {"refresh_token": refresh}, params={"grant_type": "refresh_token"})
        except ApiError:
            return None
        request.state.new_session, uid = session, session["user"]["id"]
    return uid


@app.middleware("http")
async def _renew_cookies(request: Request, call_next):
    """If the login was just renewed, hand the browser its new cookies (even on error responses)."""
    response = await call_next(request)
    session = getattr(request.state, "new_session", None)
    if session:
        set_session_cookies(response, session)
    return response


def login_required(request: Request):
    """Add `uid: str = Depends(login_required)` to an endpoint to make it members-only; you get the user's id."""
    uid = resolve_user(request)
    if not uid:
        raise ApiError(401, "Please sign in", "signed_out")
    return uid


def session_response(session):
    response = JSONResponse({"ok": True, "signedIn": True})
    set_session_cookies(response, session)
    return response


# ───────────────────────── 5. routes & matching logic ─────────────────────────
def line_indexes(branch):
    return [i for i, s in enumerate(STATIONS) if s["br"] in ("T", branch)]


def between(line, a, b):
    x, y = line.index(a), line.index(b)
    return line[x:y + 1] if x <= y else line[y:x + 1][::-1]


def path_between(a, b):
    """Station numbers you pass from a to b, in travel order. Vaishali <-> Noida trips change at Yamuna Bank."""
    for branch in ("V", "N"):
        line = line_indexes(branch)
        if a in line and b in line:
            return between(line, a, b)
    return path_between(a, YAMUNA_BANK) + path_between(YAMUNA_BANK, b)[1:]


def shared_same_way(a, b):
    """How many stations the two paths share IF they travel in the same direction (else 0; needs >= 2)."""
    shared = [s for s in a if s in set(b)]
    if len(shared) < 2:
        return 0
    return len(shared) if b.index(shared[0]) < b.index(shared[-1]) else 0


def gender_word(gender):
    return {"M": "male", "F": "female"}.get(gender, "")


def prefs_allow(my_pref, my_gender, their_pref, their_gender):
    return (my_pref == "any" or my_pref == gender_word(their_gender)) and (their_pref == "any" or their_pref == gender_word(my_gender))


def require_profile(uid):
    profile = first(rest("GET", "wm_profiles", {"id": f"eq.{uid}", "select": "*"}))
    if not profile:
        bad("Set up your profile first")
    return profile


def pair_filter(a, b):
    return f"(and(requester.eq.{a},recipient.eq.{b}),and(requester.eq.{b},recipient.eq.{a}))"


def get_pair(a, b):
    return first(rest("GET", "wm_connections", {"or": pair_filter(a, b), "select": "*", "limit": "1"}))


def is_blocked(a, b):
    f = f"(and(blocker.eq.{a},blocked.eq.{b}),and(blocker.eq.{b},blocked.eq.{a}))"
    return bool(rest("GET", "wm_blocks", {"or": f, "select": "blocker", "limit": "1"}))


def hidden_user_ids(uid):
    """People who must not appear as matches: blocked either way, or already connected / asked."""
    rows = rest("GET", "wm_blocks", {"or": f"(blocker.eq.{uid},blocked.eq.{uid})", "select": "blocker,blocked"})
    rows += rest("GET", "wm_connections", {"or": f"(requester.eq.{uid},recipient.eq.{uid})", "select": "requester,recipient"})
    ids = {v for row in rows for v in row.values()}
    ids.discard(uid)
    return ids


# ───────────────────────── 6. website + status ─────────────────────────
@app.get("/api/status")
def status(request: Request):
    """Lets the page show a friendly 'connect Supabase' screen instead of a blank error."""
    if not (SUPABASE_URL and ANON_KEY and SERVICE_KEY) or "YOUR" in SUPABASE_URL + ANON_KEY + SERVICE_KEY:
        return reply(ok=False, problem="config")
    try:
        r = requests.get(f"{SUPABASE_URL}/rest/v1/wm_profiles", params={"select": "id", "limit": "1"}, timeout=10,
                         headers=key_headers(SERVICE_KEY))
    except requests.RequestException:
        return reply(ok=False, problem="network", host=SUPABASE_URL.split("//")[-1])
    if r.status_code == 403 and "42501" in r.text:         # table exists but the secret key has no permission on it
        return reply(ok=False, problem="grants")
    if r.status_code in (401, 403):
        return reply(ok=False, problem="key")
    if r.status_code == 404:
        return reply(ok=False, problem="tables")
    return reply(ok=r.status_code < 400, problem=None if r.status_code < 400 else "other")


@app.get("/api/stations")
def stations():
    return reply(stations=STATIONS)


# ───────────────────────── 7. sign up / sign in ─────────────────────────
@app.post("/api/auth/signup")
def signup(request: Request, payload: dict = Depends(json_body)):
    d = payload
    email, password = text(d.get("email"), 200).lower(), str(d.get("password") or "")
    if "@" not in email:
        bad("Enter a valid email")
    if len(password) < 8:
        bad("Password must be at least 8 characters")
    data = auth_call("signup", {"email": email, "password": password}, params={"redirect_to": site_root(request)})
    if data.get("access_token"):                      # Supabase "Confirm email" is OFF: signed in straight away
        return session_response(data)
    return reply(ok=True, signedIn=False, needsConfirmation=True)


@app.post("/api/auth/login")
def login(payload: dict = Depends(json_body)):
    d = payload
    data = auth_call("token", {"email": text(d.get("email"), 200).lower(), "password": str(d.get("password") or "")},
                     params={"grant_type": "password"})
    return session_response(data)


@app.post("/api/auth/resend")
def resend(payload: dict = Depends(json_body)):
    auth_call("resend", {"type": "signup", "email": text(payload.get("email"), 200).lower()})
    return reply(ok=True)


@app.post("/api/auth/phone/send")
def phone_send(payload: dict = Depends(json_body)):
    digits = "".join(c for c in str(payload.get("phone") or "") if c.isdigit())
    if len(digits) != 10 or digits[0] not in "6789":
        bad("Enter a valid 10-digit Indian mobile number")
    auth_call("otp", {"phone": "+91" + digits})
    return reply(ok=True)


@app.post("/api/auth/phone/verify")
def phone_verify(payload: dict = Depends(json_body)):
    d = payload
    digits = "".join(c for c in str(d.get("phone") or "") if c.isdigit())
    data = auth_call("verify", {"type": "sms", "phone": "+91" + digits, "token": text(d.get("token"), 10)})
    return session_response(data)


@app.post("/api/auth/logout")
def logout(request: Request):
    _token_cache.pop(request.cookies.get(ACCESS), None)
    response = JSONResponse({"ok": True})
    response.delete_cookie(ACCESS)
    response.delete_cookie(REFRESH)
    return response


# ───────────────────────── 8. profile ─────────────────────────
@app.get("/api/me")
def me(request: Request):
    uid = resolve_user(request)
    if not uid:
        return reply(signedIn=False)
    return reply(signedIn=True, profile=first(rest("GET", "wm_profiles", {"id": f"eq.{uid}", "select": "*"})))


@app.put("/api/profile")
def save_profile(payload: dict = Depends(json_body), uid: str = Depends(login_required)):
    d = payload
    name, bio = text(d.get("name"), 80), text(d.get("bio"), 240)
    gender = text(d.get("gender"), 1).upper()
    try:
        age = int(d.get("age"))
    except (TypeError, ValueError):
        age = 0
    if len(name) < 2:
        bad("Enter your name")
    if gender not in ("M", "F", "O"):
        bad("Select a gender")
    if not MIN_AGE <= age <= 120:
        bad(f"Waymate is for adults ({MIN_AGE}+)")
    rows = rest("POST", "wm_profiles", {"on_conflict": "id"},
                {"id": uid, "name": name, "gender": gender, "age": age, "bio": bio},
                "resolution=merge-duplicates,return=representation")
    return reply(profile=rows[0])


# ───────────────────────── 9. my route ─────────────────────────
@app.get("/api/route")
def get_route(uid: str = Depends(login_required)):
    return reply(route=first(rest("GET", "wm_posts", {"user_id": f"eq.{uid}", "select": "*"})))


@app.put("/api/route")
def save_route(payload: dict = Depends(json_body), uid: str = Depends(login_required)):
    require_profile(uid)
    d = payload
    try:
        start, end = int(d.get("from")), int(d.get("to"))
    except (TypeError, ValueError):
        bad("Select From and To stations")
    if not (0 <= start < len(STATIONS) and 0 <= end < len(STATIONS)):
        bad("Unknown station")
    if start == end:
        bad("Pick two different stations")
    departure = parse_time(d.get("departureAt"))
    now = datetime.now(timezone.utc)
    if not now - timedelta(hours=1) <= departure <= now + timedelta(hours=48):
        bad("Departure must be within the next 48 hours")
    pref = d.get("pref") or "any"
    if pref not in ("any", "male", "female"):
        bad("Invalid preference")
    row = {"user_id": uid, "from_station": start, "to_station": end, "path": path_between(start, end),
           "departure_at": departure.isoformat(), "pref": pref, "note": text(d.get("note"), 120)}
    rows = rest("POST", "wm_posts", {"on_conflict": "user_id"}, row, "resolution=merge-duplicates,return=representation")
    return reply(route=rows[0])


@app.delete("/api/route")
def delete_route(uid: str = Depends(login_required)):
    rest("DELETE", "wm_posts", {"user_id": f"eq.{uid}"})
    return reply(ok=True)


# ───────────────────────── 10. matches ─────────────────────────
@app.get("/api/matches")
def matches(uid: str = Depends(login_required)):
    """A sees B only if: same direction, leaving within 45 min, sharing at least half of the shorter trip,
    both gender preferences allow it, and neither has blocked / been suspended / already been asked."""
    mine_profile = require_profile(uid)
    mine = first(rest("GET", "wm_posts", {"user_id": f"eq.{uid}", "select": "*"}))
    if not mine:
        return reply(matches=[])
    my_time = parse_time(mine["departure_at"])
    lo, hi = my_time - timedelta(minutes=MATCH_WINDOW_MIN), my_time + timedelta(minutes=MATCH_WINDOW_MIN)
    # the database narrows it down (shares a station + leaves in the window); the rules below do the rest
    candidates = rest("GET", "wm_posts", [
        ("user_id", f"neq.{uid}"), ("path", "ov.{" + ",".join(map(str, mine["path"])) + "}"),
        ("departure_at", f"gte.{lo.isoformat()}"), ("departure_at", f"lte.{hi.isoformat()}"),
        ("select", "*"), ("limit", "200")])
    if not candidates:
        return reply(matches=[])
    people = {p["id"]: p for p in rest("GET", "wm_profiles", {
        "id": "in.(" + ",".join(c["user_id"] for c in candidates) + ")", "select": "id,name,age,bio,gender,suspended"})}
    hidden = hidden_user_ids(uid)
    found = []
    for c in candidates:
        other = people.get(c["user_id"])
        if not other or other["suspended"] or c["user_id"] in hidden:
            continue
        if not prefs_allow(mine["pref"], mine_profile["gender"], c["pref"], other["gender"]):
            continue
        shared = shared_same_way(c["path"], mine["path"])
        if shared < max(2, math.ceil(MIN_OVERLAP_RATIO * min(len(c["path"]), len(mine["path"])))):
            continue
        gap = abs((parse_time(c["departure_at"]) - my_time).total_seconds()) / 60
        same_trip = c["from_station"] == mine["from_station"] and c["to_station"] == mine["to_station"]
        score = min(98, 55 + min(3 * shared, 22) + round(18 * (1 - gap / MATCH_WINDOW_MIN)) + (10 if same_trip else 0))
        found.append({"user_id": c["user_id"], "name": other["name"], "age": other["age"], "bio": other["bio"],
                      "from_station": c["from_station"], "to_station": c["to_station"],
                      "departure_at": c["departure_at"], "overlap": shared, "score": score})
    found.sort(key=lambda m: -m["score"])
    return reply(matches=found[:20])


# ───────────────────────── 11. buddies ─────────────────────────
@app.get("/api/buddies")
def buddies(uid: str = Depends(login_required)):
    rows = rest("GET", "wm_connections", {"or": f"(requester.eq.{uid},recipient.eq.{uid})",
                                          "status": "in.(pending,accepted)", "select": "*", "order": "id.desc"})
    other = lambda c: c["recipient"] if c["requester"] == uid else c["requester"]
    ids = sorted({other(c) for c in rows})
    people = {p["id"]: p for p in (rest("GET", "wm_profiles", {"id": "in.(" + ",".join(ids) + ")", "select": "id,name,age,bio"}) if ids else [])}
    item = lambda c: {"id": c["id"], "user": people.get(other(c), {"id": other(c), "name": "Someone"})}
    return reply(
        incoming=[item(c) for c in rows if c["status"] == "pending" and c["recipient"] == uid],
        outgoing=[item(c) for c in rows if c["status"] == "pending" and c["requester"] == uid],
        buddies=[item(c) for c in rows if c["status"] == "accepted"])


@app.post("/api/connections")
def request_buddy(payload: dict = Depends(json_body), uid: str = Depends(login_required)):
    target = valid_id(payload.get("to"))
    suspended = require_profile(uid)["suspended"]
    if target == uid:
        bad("You cannot add yourself")
    if suspended:
        raise ApiError(403, "Your account is under review")
    if not first(rest("GET", "wm_profiles", {"id": f"eq.{target}", "select": "id"})):
        raise ApiError(404, "User not found")
    if is_blocked(uid, target):
        raise ApiError(403, "You can't send a request to this person")
    existing = get_pair(uid, target)
    if existing:
        if existing["status"] == "cancelled" and existing["requester"] == uid:       # re-send something I cancelled
            rest("PATCH", "wm_connections", {"id": f"eq.{existing['id']}"}, {"status": "pending"})
            return reply(ok=True)
        raise ApiError(409, "A request with this person already exists")
    rest("POST", "wm_connections", None, {"requester": uid, "recipient": target, "status": "pending"})
    return reply(ok=True)


@app.post("/api/connections/{cid}/respond")
def respond(cid: int, payload: dict = Depends(json_body), uid: str = Depends(login_required)):
    status_ = payload.get("status")
    if status_ not in ("accepted", "declined", "cancelled"):
        bad("Invalid status")
    c = first(rest("GET", "wm_connections", {"id": f"eq.{cid}", "select": "*"}))
    if not c or uid not in (c["requester"], c["recipient"]):
        raise ApiError(404, "Request not found")
    if status_ == "accepted" and (c["recipient"] != uid or c["status"] != "pending"):
        raise ApiError(403, "Only the recipient can accept a pending request")
    if status_ == "declined" and c["recipient"] != uid:
        raise ApiError(403, "Only the recipient can decline")
    rest("PATCH", "wm_connections", {"id": f"eq.{cid}"}, {"status": status_})
    return reply(ok=True)


# ───────────────────────── 12. chat ─────────────────────────
def require_chat_allowed(uid, other):
    c = get_pair(uid, other)
    if not c or c["status"] != "accepted":
        raise ApiError(403, "Accept this connection before messaging")
    if is_blocked(uid, other):
        raise ApiError(403, "You can't message this person")


@app.get("/api/messages/{other}")
def get_messages(other: str, after: Optional[int] = None, uid: str = Depends(login_required)):
    other = valid_id(other)
    require_chat_allowed(uid, other)
    params = {"or": f"(and(sender.eq.{uid},recipient.eq.{other}),and(sender.eq.{other},recipient.eq.{uid}))",
              "select": "*", "order": "id.desc", "limit": "200"}
    if after:
        params["id"] = f"gt.{after}"
    return reply(messages=rest("GET", "wm_messages", params)[::-1])      # oldest first


@app.post("/api/messages/{other}")
def send_message(other: str, payload: dict = Depends(json_body), uid: str = Depends(login_required)):
    other = valid_id(other)
    msg = text(payload.get("body"), 2000)
    if not msg:
        bad("Message cannot be empty")
    if require_profile(uid)["suspended"]:
        raise ApiError(403, "Your account is under review")
    require_chat_allowed(uid, other)
    rows = rest("POST", "wm_messages", None, {"sender": uid, "recipient": other, "body": msg}, "return=representation")
    return reply(message=rows[0])


# ───────────────────────── 13. block & report ─────────────────────────
@app.post("/api/block")
def block(payload: dict = Depends(json_body), uid: str = Depends(login_required)):
    target = valid_id(payload.get("user"))
    if target == uid:
        bad("You cannot block yourself")
    rest("POST", "wm_blocks", {"on_conflict": "blocker,blocked"}, {"blocker": uid, "blocked": target}, "resolution=ignore-duplicates")
    rest("PATCH", "wm_connections", {"or": pair_filter(uid, target), "status": "neq.cancelled"}, {"status": "cancelled"})
    return reply(ok=True)


@app.get("/api/blocked")
def blocked_list(uid: str = Depends(login_required)):
    rows = rest("GET", "wm_blocks", {"blocker": f"eq.{uid}", "select": "blocked"})
    ids = [r["blocked"] for r in rows]
    people = rest("GET", "wm_profiles", {"id": "in.(" + ",".join(ids) + ")", "select": "id,name"}) if ids else []
    return reply(blocked=people)


@app.delete("/api/block/{other}")
def unblock(other: str, uid: str = Depends(login_required)):
    rest("DELETE", "wm_blocks", {"blocker": f"eq.{uid}", "blocked": f"eq.{valid_id(other)}"})
    return reply(ok=True)


@app.post("/api/report")
def report(payload: dict = Depends(json_body), uid: str = Depends(login_required)):
    d = payload
    target = valid_id(d.get("user"))
    if target == uid:
        bad("You cannot report yourself")
    if d.get("reason") not in REASONS:
        bad("Choose a reason")
    rest("POST", "wm_reports", None, {"reporter": uid, "reported": target, "reason": d["reason"], "details": text(d.get("details"), 500)})
    since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    recent = rest("GET", "wm_reports", {"reported": f"eq.{target}", "created_at": f"gte.{since}", "select": "reporter"})
    if len({r["reporter"] for r in recent}) >= 3:                           # 3 different people -> hide the account for review
        rest("PATCH", "wm_profiles", {"id": f"eq.{target}"}, {"suspended": True})
    return reply(ok=True)


# ───────────────────────── 14. website files + start the server ─────────────────────────
class NoCacheStatic(StaticFiles):
    """Serves public/ locally; always fresh while you are developing."""
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


if not ON_VERCEL:   # on Vercel the CDN serves the public/ folder itself. Keep this LAST so /api/... routes win.
    app.mount("/", NoCacheStatic(directory=os.path.join(BASE, "public"), html=True), name="site")


if __name__ == "__main__":
    if not (SUPABASE_URL and ANON_KEY and SERVICE_KEY) or "YOUR" in SUPABASE_URL + ANON_KEY + SERVICE_KEY:
        print("\n  ⚠  Supabase is not configured yet. Copy .env.example to .env and fill in the Supabase URL + publishable + secret keys.\n")
    print(f"  Waymate is running →  http://localhost:{PORT}      (API docs: http://localhost:{PORT}/docs)\n")
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT)
