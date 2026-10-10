"""Backend tests. Run:  python -m unittest discover -s tests -v   (no internet / Supabase account needed)"""
from types import SimpleNamespace
import os
import sys
import time
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from mock_supabase import FakeSupabase  # noqa: E402

fake = FakeSupabase().start()
os.environ.update(SUPABASE_URL=fake.url, SUPABASE_PUBLISHABLE_KEY="sb_publishable_test", SUPABASE_SECRET_KEY="sb_secret_test")
import backend  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


class Browser:
    """One fake browser (keeps its own cookies). Responses expose .status_code and .json (a dict)."""
    def __init__(self):
        self.client = TestClient(backend.app)

    def _wrap(self, r):
        return SimpleNamespace(status_code=r.status_code, json=r.json() if r.content and "json" in r.headers.get("content-type", "") else None, text=r.text)

    def get(self, *a, **k): return self._wrap(self.client.get(*a, **k))
    def post(self, *a, **k): return self._wrap(self.client.post(*a, **k))
    def put(self, *a, **k): return self._wrap(self.client.put(*a, **k))
    def delete(self, *a, **k): return self._wrap(self.client.delete(*a, **k))
    def get_cookie(self, name): return SimpleNamespace(value=self.client.cookies.get(name))


def when(minutes):
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


class WaymateTests(unittest.TestCase):
    def setUp(self):
        fake.reset()
        backend._token_cache.clear()
        self.n, self.label = 0, {}

    def person(self, name, gender="M", age=25):
        """A signed-in test browser with a finished profile."""
        self.n += 1
        c = Browser()
        self.assertEqual(c.post("/api/auth/signup", json={"email": f"{name}@x.com", "password": "password123"}).status_code, 200)
        self.assertEqual(c.put("/api/profile", json={"name": f"{name} Tester", "gender": gender, "age": age}).status_code, 200)
        c.name, c.uid = name, c.get("/api/me").json["profile"]["id"]
        self.label[c.uid] = name
        return c

    def route(self, c, a, b, minutes=10, pref="any"):
        return c.put("/api/route", json={"from": a, "to": b, "departureAt": when(minutes), "pref": pref})

    def names(self, c):
        return {self.label[m["user_id"]] for m in c.get("/api/matches").json["matches"]}

    # ---------- setup / sign in ----------
    def test_status_reports_missing_tables(self):
        c = Browser()
        self.assertTrue(c.get("/api/status").json["ok"])
        fake.tables.pop("wm_profiles")
        self.assertEqual(c.get("/api/status").json["problem"], "tables")

    def test_new_style_keys_go_only_in_the_apikey_header(self):
        self.assertEqual(backend.key_headers("sb_secret_x"), {"apikey": "sb_secret_x"})
        self.assertEqual(backend.key_headers("eyJlegacy"), {"apikey": "eyJlegacy", "Authorization": "Bearer eyJlegacy"})   # old JWT keys unchanged
        self.assertEqual(backend.key_headers("sb_publishable_x", "user-jwt"), {"apikey": "sb_publishable_x", "Authorization": "Bearer user-jwt"})

    def test_status_reports_missing_permissions(self):
        fake.no_grants = True
        self.assertEqual(Browser().get("/api/status").json["problem"], "grants")

    def test_confirmation_email_link_uses_site_url(self):
        backend.SITE_URL = "https://waymate.example"
        try:
            Browser().post("/api/auth/signup", json={"email": "z@x.com", "password": "password123"})
            self.assertEqual(fake.last_signup_query["redirect_to"], "https://waymate.example/")
        finally:
            backend.SITE_URL = ""

    def test_email_confirmation_flow(self):
        fake.confirm_email = True
        c = Browser()
        r = c.post("/api/auth/signup", json={"email": "a@x.com", "password": "password123"})
        self.assertTrue(r.json["needsConfirmation"] and not r.json["signedIn"])
        self.assertFalse(c.get("/api/me").json["signedIn"])
        r = c.post("/api/auth/login", json={"email": "a@x.com", "password": "password123"})
        self.assertEqual((r.status_code, r.json["code"]), (400, "email_not_confirmed"))
        fake.users["a@x.com"]["confirmed"] = True
        self.assertEqual(c.post("/api/auth/login", json={"email": "a@x.com", "password": "password123"}).status_code, 200)
        self.assertTrue(c.get("/api/me").json["signedIn"])
        self.assertEqual(c.post("/api/auth/login", json={"email": "a@x.com", "password": "wrong"}).status_code, 400)

    def test_signup_validation_and_login_required(self):
        c = Browser()
        self.assertEqual(c.post("/api/auth/signup", json={"email": "nope", "password": "password123"}).status_code, 400)
        self.assertEqual(c.post("/api/auth/signup", json={"email": "a@x.com", "password": "short"}).status_code, 400)
        self.assertEqual(c.get("/api/route").status_code, 401)
        self.assertEqual(c.get("/api/matches").status_code, 401)

    def test_session_is_renewed_when_access_token_expires(self):
        c = self.person("ann")
        old = c.get_cookie("wm_access").value
        fake.expired.add(old); backend._token_cache.clear()
        self.assertTrue(c.get("/api/me").json["signedIn"])
        self.assertNotEqual(c.get_cookie("wm_access").value, old)

    def test_website_is_served_and_unknown_api_paths_are_404(self):
        c = Browser()
        home = c.get("/")
        self.assertEqual(home.status_code, 200); self.assertIn("Waymate", home.text)
        self.assertEqual(c.get("/js/app.js").status_code, 200); self.assertEqual(c.get("/css/styles.css").status_code, 200)
        self.assertEqual(c.get("/api/does-not-exist").status_code, 404)

    def test_malformed_json_is_a_clean_400(self):
        c = Browser()
        r = c.client.post("/api/auth/login", content="{not json", headers={"Content-Type": "application/json"})
        self.assertEqual((r.status_code, r.json()["error"]), (400, "Invalid request"))

    def test_login_sets_a_signed_in_hint_cookie_and_logout_clears_it(self):
        c = self.person("ann")
        self.assertEqual(c.client.cookies.get("wm_hint"), "1")        # lets the page paint instantly next time (holds nothing secret)
        c.post("/api/auth/logout")
        self.assertIsNone(c.client.cookies.get("wm_hint"))

    def test_station_list_can_be_cached_at_the_edge(self):
        r = Browser().client.get("/api/stations")
        self.assertIn("s-maxage", r.headers["cache-control"])

    def test_connections_to_supabase_are_reused(self):
        c = self.person("ann"); self.route(c, 2, 8)
        c.get("/api/matches")                                        # warm-up opens the connections
        fake.connections = 0
        c.get("/api/matches"); c.get("/api/buddies"); c.get("/api/me")
        self.assertEqual(fake.connections, 0)                        # a new https connection per call is what made it slow

    def test_independent_lookups_run_together(self):
        c = self.person("ann"); self.route(c, 2, 8)
        backend._token_cache.clear(); fake.latency = 0.1
        start = time.time(); r = c.get("/api/matches"); took = time.time() - start
        fake.latency = 0
        self.assertEqual(r.status_code, 200)
        self.assertLess(took, 0.6)       # 7 lookups one after another would need >= 0.7 s; with 4 of them together it is ~0.4 s

    def test_logout_clears_login(self):
        c = self.person("ann")
        c.post("/api/auth/logout")
        self.assertFalse(c.get("/api/me").json["signedIn"])

    def test_phone_otp(self):
        c = Browser()
        self.assertEqual(c.post("/api/auth/phone/send", json={"phone": "12345"}).status_code, 400)
        self.assertEqual(c.post("/api/auth/phone/send", json={"phone": "98765 43210"}).status_code, 200)
        self.assertEqual(c.post("/api/auth/phone/verify", json={"phone": "9876543210", "token": "000000"}).status_code, 400)
        self.assertEqual(c.post("/api/auth/phone/verify", json={"phone": "9876543210", "token": "123456"}).status_code, 200)
        self.assertTrue(c.get("/api/me").json["signedIn"])

    # ---------- profile & route ----------
    def test_profile_rules(self):
        c = Browser(); c.post("/api/auth/signup", json={"email": "a@x.com", "password": "password123"})
        self.assertEqual(c.put("/api/profile", json={"name": "Ann", "gender": "F", "age": 17}).status_code, 400)
        self.assertEqual(c.put("/api/profile", json={"name": "A", "gender": "F", "age": 30}).status_code, 400)
        self.assertEqual(c.put("/api/profile", json={"name": "Ann", "gender": "X", "age": 30}).status_code, 400)
        self.assertEqual(c.put("/api/profile", json={"name": "Ann", "gender": "F", "age": 18}).status_code, 200)

    def test_route_rules_and_paths(self):
        c = self.person("ann")
        self.assertEqual(self.route(c, 3, 3).status_code, 400)
        self.assertEqual(self.route(c, 3, 999).status_code, 400)
        self.assertEqual(self.route(c, 3, 8, minutes=60 * 72).status_code, 400)
        self.assertEqual(self.route(c, 2, 8).json["route"]["path"], [2, 3, 4, 5, 6, 7, 8])
        self.assertEqual(self.route(c, 8, 2).json["route"]["path"], [8, 7, 6, 5, 4, 3, 2])
        v = next(i for i, s in enumerate(backend.STATIONS) if s["br"] == "V")
        n = next(i for i, s in enumerate(backend.STATIONS) if s["br"] == "N")
        cross = self.route(c, v, n).json["route"]["path"]
        self.assertIn(backend.YAMUNA_BANK, cross); self.assertEqual(len(cross), len(set(cross)))
        self.assertEqual(len(fake.tables["wm_posts"]), 1)                       # one active route per person
        self.assertEqual(c.delete("/api/route").status_code, 200); self.assertIsNone(c.get("/api/route").json["route"])

    # ---------- matching ----------
    def test_same_path_same_direction_rule(self):
        a, b, cc, d, e, f, g = (self.person(x) for x in "ABCDEFG")
        for who, (s, t, m) in {a: (2, 8, 10), b: (2, 8, 12), cc: (4, 7, 10), d: (0, 3, 10), e: (7, 12, 10), f: (8, 2, 10), g: (2, 8, 90)}.items():
            self.assertEqual(self.route(who, s, t, m).status_code, 200)
        self.assertEqual(self.names(a), {"B", "C", "D"})        # same way + at least half of the shorter trip + within 45 min
        self.assertNotIn("A", self.names(f))                    # same stations, opposite direction -> never
        self.assertEqual(self.names(f), set())
        self.assertIn("A", self.names(b)); self.assertIn("A", self.names(cc)); self.assertIn("B", self.names(cc))   # "vice versa"
        top = a.get("/api/matches").json["matches"][0]
        self.assertEqual((self.label[top["user_id"]], top["score"]), ("B", 98))                # identical trip ranks first

    def test_gender_preferences(self):
        a, b, c = self.person("A", "F"), self.person("B", "M"), self.person("C", "F")
        self.route(a, 2, 8, pref="female"); self.route(b, 2, 8); self.route(c, 2, 8)
        self.assertEqual(self.names(a), {"C"})                  # A only wants women
        self.route(c, 2, 8, pref="male")
        self.assertEqual(self.names(a), set())                  # C only wants men, so A (a woman) is hidden from C and C from A

    # ---------- buddies & chat ----------
    def test_buddy_requests_and_chat(self):
        a, b, c = self.person("A"), self.person("B"), self.person("C")
        self.assertEqual(a.post("/api/connections", json={"to": a.uid}).status_code, 400)
        self.assertEqual(a.post("/api/connections", json={"to": "not-a-uuid"}).status_code, 400)
        self.assertEqual(a.post("/api/connections", json={"to": b.uid}).status_code, 200)
        self.assertEqual(a.post("/api/connections", json={"to": b.uid}).status_code, 409)
        self.assertEqual(b.post("/api/connections", json={"to": a.uid}).status_code, 409)
        cid = a.get("/api/buddies").json["outgoing"][0]["id"]
        self.assertEqual(b.get("/api/buddies").json["incoming"][0]["user"]["name"], "A Tester")
        self.assertEqual(a.post(f"/api/connections/{cid}/respond", json={"status": "accepted"}).status_code, 403)   # can't accept own request
        self.assertEqual(c.post(f"/api/connections/{cid}/respond", json={"status": "accepted"}).status_code, 404)   # outsider
        self.assertEqual(a.post(f"/api/messages/{b.uid}", json={"body": "hi"}).status_code, 403)                    # not accepted yet
        self.assertEqual(b.post(f"/api/connections/{cid}/respond", json={"status": "accepted"}).status_code, 200)
        self.assertEqual(b.post(f"/api/connections/{cid}/respond", json={"status": "accepted"}).status_code, 403)   # no longer pending
        self.assertEqual(len(a.get("/api/buddies").json["buddies"]), 1)
        self.assertEqual(a.post(f"/api/messages/{b.uid}", json={"body": "  "}).status_code, 400)
        first = a.post(f"/api/messages/{b.uid}", json={"body": "hi <b>"}).json["message"]
        b.post(f"/api/messages/{a.uid}", json={"body": "hello"})
        self.assertEqual([m["body"] for m in b.get(f"/api/messages/{a.uid}").json["messages"]], ["hi <b>", "hello"])
        self.assertEqual([m["body"] for m in a.get(f"/api/messages/{b.uid}?after={first['id']}").json["messages"]], ["hello"])
        self.assertEqual(c.get(f"/api/messages/{a.uid}").status_code, 403)

    # ---------- safety ----------
    def test_block_hides_and_stops_everything(self):
        a, b = self.person("A"), self.person("B")
        self.route(a, 2, 8); self.route(b, 2, 8)
        a.post("/api/connections", json={"to": b.uid}); cid = a.get("/api/buddies").json["outgoing"][0]["id"]
        b.post(f"/api/connections/{cid}/respond", json={"status": "accepted"})
        self.assertEqual(a.post("/api/block", json={"user": b.uid}).status_code, 200)
        self.assertEqual(a.get("/api/buddies").json["buddies"], [])                          # connection cancelled
        self.assertEqual(b.post(f"/api/messages/{a.uid}", json={"body": "hi"}).status_code, 403)
        self.assertEqual(b.post("/api/connections", json={"to": a.uid}).status_code, 403)
        self.assertEqual((self.names(a), self.names(b)), (set(), set()))                     # hidden both ways
        self.assertEqual(a.get("/api/blocked").json["blocked"][0]["name"], "B Tester")
        a.delete(f"/api/block/{b.uid}"); self.assertEqual(a.get("/api/blocked").json["blocked"], [])

    def test_three_reporters_suspend_an_account(self):
        bad_user = self.person("Bad"); self.route(bad_user, 2, 8)
        reporters = [self.person(x) for x in ("R1", "R2", "R3")]
        for r in reporters: self.route(r, 2, 8)
        self.assertEqual(reporters[0].post("/api/report", json={"user": bad_user.uid, "reason": "nonsense"}).status_code, 400)
        self.assertIn("Bad", self.names(reporters[0]))
        for r in reporters:
            self.assertEqual(r.post("/api/report", json={"user": bad_user.uid, "reason": "harassment", "details": "x"}).status_code, 200)
        self.assertNotIn("Bad", self.names(reporters[0]))                                       # hidden from matches
        self.assertEqual(bad_user.post("/api/connections", json={"to": reporters[0].uid}).status_code, 403)


if __name__ == "__main__":
    unittest.main()
