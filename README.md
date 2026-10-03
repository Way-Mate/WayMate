# Waymate

Date on your daily metro ride: share your route and time, see people who travel the **same way** on the **same stretch** of
the Delhi Metro Blue Line at the **same time**, send a buddy request, chat. 18+ only, with report and block.

**Stack:** Python (FastAPI) backend in ONE file · plain HTML/CSS/JS frontend · Supabase (Auth + Postgres).

```
waymate/
├── backend.py            ← the whole server: website + sign-in + API + matching (read this first)
├── requirements.txt      ← fastapi, uvicorn, requests, python-dotenv (+ httpx for tests)
├── .env.example          ← copy to .env and paste your Supabase keys
├── start.sh / start.bat  ← one-command start (macOS-Linux / Windows)
├── data/stations.json    ← the 50 Blue Line stations (used by the backend AND the page)
├── supabase/schema.sql   ← the database tables + permissions (run once in Supabase)
├── pyproject.toml        ← tells Vercel where the app is (backend:app) · .python-version · .vercelignore
├── public/               ← what the browser loads (Vercel serves this folder directly)
│   ├── index.html
│   ├── css/              ← styles.css (phone), desktop.css (laptop), landing.css
│   ├── js/api.js         ← 15-line helper that calls the backend
│   ├── js/app.js         ← all screens + buttons (numbered sections, every button = one entry in ACTIONS)
│   └── assets/           ← logo, favicon
└── tests/                ← python -m unittest discover -s tests -v
```

## 1. Set up Supabase (new account, once)
1. Sign up at **supabase.com** and confirm your email.
2. **New project**: name it `waymate`, generate and SAVE the database password, pick a nearby region (e.g. South Asia – Mumbai),
   keep **Enable Data API** ticked, create it, wait ~2 minutes until it says the project is ready.
3. Left sidebar → **SQL Editor → New query** → paste ALL of `supabase/schema.sql` → **Run**. Expect "Success. No rows returned".
   Check **Table Editor**: six tables starting with `wm_`. (The file also grants the server key permission — new projects
   don't do that automatically.)
4. **Project Settings → API Keys**: copy the **Publishable key** (`sb_publishable_…`) and the **Secret key** (`sb_secret_…`)
   (click *Create new API keys* if you don't see them). The **Project URL** (`https://xxxx.supabase.co`) is on the project
   home page / Connect dialog / Project Settings → Data API.
5. **Authentication → Sign In / Providers → Email**: keep it enabled; while testing switch **Confirm email OFF** and Save
   (the free email sender allows only 2 emails/hour). **Authentication → URL Configuration**: Site URL `http://localhost:3000`,
   and add `http://localhost:3000/**` under Redirect URLs.
6. Optional — phone login: **Providers → Phone** → enable and connect an SMS provider (e.g. Twilio; India needs DLT registration).

## 2. Run it on your computer (Python 3.9+)
1. Unzip, open a terminal in the `waymate` folder.
2. `cp .env.example .env`  (Windows: `copy .env.example .env`) → open `.env` and paste the URL, publishable key and secret key
   (no quotes, no spaces) → save.
3. `./start.sh`  (Windows: `start.bat`)  — or by hand: `python3 -m venv venv && source venv/bin/activate &&
   pip install -r requirements.txt && python backend.py`. The terminal should say **Waymate is running → http://localhost:3000**.
4. Open http://localhost:3000 → *Get started* → create an account → profile → route.
5. Test with two people: open a second browser / Incognito window, sign up with another email, post routes with the same
   From/To and times within 45 minutes → each should see the other → request → accept → chat.
> After editing `.env` always **restart** `python backend.py`. If something is wrong, the page tells you what
> (keys, paused project, tables/permissions missing).
> The **secret key** is a secret: only in `.env` (git-ignored), used only by `backend.py`, never sent to the browser.

> **Tip:** while running locally, open **http://localhost:3000/docs** — FastAPI's automatic page listing every endpoint
> (hidden on Vercel). Handy for seeing what the backend offers.

## 3. How a request flows
`browser (app.js)` → `fetch /api/...` → **`backend.py`** → Supabase Auth (who is this?) → Supabase database (data) → JSON back.
Every permission rule lives in `backend.py` (it is the only thing that can reach the tables).

## 4. Matching rules (`matches()` in backend.py)
A is shown to B only if all are true: same direction · leave within **45 min** · share at least **half of the shorter trip**
(min 2 stations) · both gender preferences allow it · nobody blocked/suspended · not already connected.
Change `MATCH_WINDOW_MIN` / `MIN_OVERLAP_RATIO` at the top of `backend.py` (`1.0` = shorter trip must lie fully on the other's path).

## 5. Safety
Report/Block links on matches, buddies and chats. Blocking hides both people from each other, cancels the connection and stops
messages. 3 different people reporting an account within 30 days suspends it (hidden, can't send requests/messages).
Review reports in Supabase **Table Editor → wm_reports**; reinstate someone by setting `wm_profiles.suspended = false`.

## 6. Tests
`python -m unittest discover -s tests -v` — 18 tests run the real backend against a fake Supabase (`tests/mock_supabase.py`);
no internet or account needed.

## 7. Deploy on Vercel + GoDaddy domain
**A. GitHub** — create a repo and push this folder (`.env` is git-ignored — check `git status` doesn't list it).
**B. Vercel** — vercel.com → *Add New → Project* → import the repo (it detects FastAPI/Python). Before clicking Deploy add
*Environment Variables*: `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, `SUPABASE_SECRET_KEY`, `SITE_URL` (= `https://yourdomain.com`).
Deploy, open the `*.vercel.app` address, and check `/api/status` shows `"ok":true`. (Changing a variable later needs a redeploy.)
**C. Domain** — Vercel project → *Settings → Domains* → add `yourdomain.com` AND `www.yourdomain.com`; make ONE the primary and let
the other redirect to it (so a login cookie isn't split across two hosts). Vercel shows the exact DNS records. At GoDaddy
(*My Products → Domains → DNS*): delete the parked/forwarding `A @` record and the `www` CNAME that point elsewhere, then add
`A  @  76.76.21.21` and `CNAME  www  <value shown by Vercel, e.g. cname.vercel-dns.com>`. Don't change GoDaddy's nameservers.
Wait for Vercel to show *Valid Configuration* (minutes to a few hours); the https certificate is automatic.
**D. Supabase** — *Authentication → URL Configuration*: Site URL `https://yourdomain.com`; Redirect URLs add
`https://yourdomain.com/**` and `https://www.yourdomain.com/**`. Before real users: turn **Confirm email ON** and set up your own
SMTP (*Authentication → SMTP Settings*), otherwise the 2-emails/hour limit blocks sign-ups.
**E. Update later** — `git push`; Vercel redeploys automatically.

## Not included yet
Rate limiting (left out on purpose for now), photos, ticket/trip booking, people search, an admin page for reports.
