/* Waymate — the website's logic. It talks to backend.py through api.js (no Supabase code in the browser).
   Sections: 1 setup · 2 helpers · 3 screens · 4 actions (every button = one entry) · 5 start-up */
import { api } from "./api.js";

/* ---------- 1. setup ---------- */
let STATIONS = [];                 // loaded from the backend at start-up
let me = null;                     // the signed-in person's profile
let chatWith = null;               // { id, name } while a chat is open
let authIntent = "signup";         // which button the email form shows: "signup" | "signin"
let screenName = "discover";       // which tab is open
let lastIncoming = 0;              // buddy requests seen so far (to announce new ones)
let chatTimer = null;              // refreshes the open chat
const passed = new Set();          // matches the user passed on this session

/* ---------- 2. helpers ---------- */
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const show = html => { $("#screen").innerHTML = html; };
const minsFromNow = iso => Math.round((new Date(iso) - Date.now()) / 60000);
function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.classList.remove("hidden");
  clearTimeout(toast.timer); toast.timer = setTimeout(() => t.classList.add("hidden"), Math.max(2800, msg.length * 55));
}
const stationOptions = selected => STATIONS.map((s, i) =>
  `<option value="${i}" ${i === selected ? "selected" : ""}>${esc(s.name)}</option>`).join("");
const friendly = msg => /email rate limit/i.test(msg || "")
  ? "Supabase's free test email sender allows only 2 emails per hour for the whole project. Wait up to an hour, turn off “Confirm email” in Supabase while testing, or set up your own SMTP."
  : msg;
/* Run an API call; on failure show the error as a toast and return null instead of throwing. */
async function ok(promise) {
  try { return await promise; }
  catch (e) {
    if (e.status === 401) { me = null; renderLanding(); toast("Please sign in again"); return null; }
    toast(friendly(e.message)); return null;
  }
}

/* Report / Block links shown next to any other person. */
const REASONS = [["fake_or_spam", "Fake profile or spam"], ["harassment", "Harassment"], ["inappropriate_content", "Inappropriate content"],
  ["unsafe_behaviour", "Unsafe behaviour"], ["other", "Something else"]];
const safety = (id, name) => `<div><button class="link-danger" data-do="report" data-id="${esc(id)}" data-name="${esc(name)}">Report</button>
  · <button class="link-danger" data-do="block" data-id="${esc(id)}" data-name="${esc(name)}">Block</button></div>`;

/* ---------- navigation: every clickable thing uses data-do="name" → ACTIONS[name] ---------- */
const SCREENS = { discover: renderDiscover, buddies: renderBuddies, profile: renderProfile };
function go(screen) {
  clearInterval(chatTimer); chatWith = null; screenName = screen;
  document.querySelectorAll("#tabbar button").forEach(b => b.classList.toggle("active", b.dataset.tab === screen));
  SCREENS[screen]();
}
document.addEventListener("click", e => {
  const el = e.target.closest("[data-do]");
  if (el && ACTIONS[el.dataset.do]) ACTIONS[el.dataset.do](el.dataset);
});

/* ---------- 3. screens ---------- */

function renderLanding() {
  $("#tabbar").classList.add("hidden");
  show(`<div class="landing">
    <div class="hero"><h1>Date on your daily metro ride 🚇</h1>
      <p>Waymate introduces you to people who travel the <b>same way</b>, on the <b>same stretch</b> of the Delhi Metro Blue Line, at the <b>same time</b> as you.</p>
      <div class="hero-cta"><button class="btn primary" data-do="auth" data-intent="signup">Get started</button>
        <button class="btn ghost" data-do="auth" data-intent="signin">I already have an account</button></div></div>
    <h2 class="page-title">How it works</h2>
    <div class="steps">
      <div class="card"><div class="step-n">1</div><b>Share your route</b><p class="muted">Pick where you get on, where you get off and when you leave.</p></div>
      <div class="card"><div class="step-n">2</div><b>See who rides with you</b><p class="muted">Only people going the same direction on a shared stretch, within 45 minutes of you.</p></div>
      <div class="card"><div class="step-n">3</div><b>Say hi, then chat</b><p class="muted">Send a buddy request. You can message once they accept.</p></div></div>
    <h2 class="page-title">Built with safety in mind</h2>
    <div class="steps">
      <div class="card"><b>18+ only</b><p class="muted">Waymate is for adults.</p></div>
      <div class="card"><b>You stay in control</b><p class="muted">Choose who you want to meet, and block or report anyone in one tap.</p></div>
      <div class="card"><b>No live location</b><p class="muted">We only use the route and time you choose to share.</p></div></div>
    <div class="hero-cta center"><button class="btn primary" data-do="auth" data-intent="signup">Create your account</button></div>
  </div>`);
}

function renderCheckEmail(email) {
  $("#tabbar").classList.add("hidden");
  show(`<div class="card"><h2 class="page-title">Confirm your email to continue ✉️</h2>
    <p>We sent a confirmation link to <b>${esc(email)}</b>. Open it to activate your account, then come back and sign in.</p>
    <p class="muted small">Can't see it? Check your spam or promotions folder.</p>
    <button class="btn primary block" data-do="resend" data-email="${esc(email)}">Resend email</button>
    <button class="btn ghost block" data-do="auth" data-intent="signin">I've confirmed — sign in</button>
    <button class="btn ghost block" data-do="auth" data-intent="signup">Use a different email</button></div>`);
}

function renderAuth(mode = "email", phone = "", intent = authIntent) {
  authIntent = intent;
  const signup = intent === "signup";
  $("#tabbar").classList.add("hidden");
  const phoneForm = phone
    ? `<p class="muted">We sent a code to +91 ${esc(phone)}</p>
       <label class="field"><span>6-digit code</span><input id="otp" class="input" inputmode="numeric" maxlength="6" autocomplete="one-time-code" /></label>
       <button class="btn primary block" data-do="verifyCode" data-phone="${esc(phone)}">Verify &amp; continue</button>
       <button class="btn ghost block" data-do="authMode" data-mode="phone">Change number</button>`
    : `<label class="field"><span>Mobile number (+91)</span><input id="phone" class="input" type="tel" inputmode="numeric" maxlength="10" placeholder="98XXXXXXXX" /></label>
       <button class="btn primary block" data-do="sendCode">Send code</button>
       <p class="muted small">We'll text you a code — no password needed.</p>`;
  const emailForm = `<label class="field"><span>Email</span><input id="email" class="input" type="email" autocomplete="email" /></label>
    <label class="field"><span>Password (min 8 characters)</span><input id="password" class="input" type="password" autocomplete="${signup ? "new-password" : "current-password"}" /></label>
    ${signup ? `<button class="btn primary block" data-do="signUp">Create account</button>` : `<button class="btn primary block" data-do="signIn">Sign in</button>`}`;
  show(`<div class="card"><button class="btn ghost small-btn" data-do="landing">← Back</button>
    <h2 class="page-title">${signup ? "Create your account" : "Welcome back"}</h2>
    ${mode === "phone" ? phoneForm : emailForm}
    <button class="btn ghost block" data-do="authMode" data-mode="${mode === "phone" ? "email" : "phone"}">${mode === "phone" ? "Use email instead" : "Use phone instead"}</button>
    <button class="btn ghost block" data-do="authIntent" data-mode="${mode}" data-intent="${signup ? "signin" : "signup"}">${signup ? "Already have an account? Sign in" : "New here? Create an account"}</button></div>`);
}

function renderProfileForm() {
  show(`<div class="card"><h2 class="page-title">Set up your profile</h2>
    <label class="field"><span>Name</span><input id="pName" class="input" /></label>
    <label class="field"><span>Gender</span><select id="pGender" class="input"><option value="F">Female</option><option value="M">Male</option><option value="O">Other</option></select></label>
    <label class="field"><span>Age (18+)</span><input id="pAge" class="input" type="number" min="18" max="120" /></label>
    <label class="field"><span>Short bio</span><input id="pBio" class="input" maxlength="240" /></label>
    <button class="btn primary block" data-do="saveProfile">Continue</button></div>`);
}

function matchCard(m) {
  return `<div class="card"><div class="row spread"><b>${esc(m.name)}, ${m.age}</b><span class="badge ok">${m.score}% fit</span></div>
    <div class="muted small">${esc(m.bio)}</div>
    <div>${esc(STATIONS[m.from_station].name)} → ${esc(STATIONS[m.to_station].name)}</div>
    <div class="muted small">${m.overlap} shared stations • leaves in ${Math.max(0, minsFromNow(m.departure_at))} min</div>
    <button class="btn primary small-btn" data-do="request" data-id="${esc(m.user_id)}">Say hi 👋</button>
    <button class="btn ghost small-btn" data-do="pass" data-id="${esc(m.user_id)}">Pass</button>
    ${safety(m.user_id, m.name)}</div>`;
}

async function renderRouteForm(post) {
  await ensureStations();
  const t = post ? new Date(post.departure_at) : new Date(Date.now() + 15 * 60000);
  const hhmm = `${String(t.getHours()).padStart(2, "0")}:${String(t.getMinutes()).padStart(2, "0")}`;
  show(`<div class="card"><h2 class="page-title">Where are you going?</h2>
    <label class="field"><span>From</span><select id="rFrom" class="input">${stationOptions(post?.from_station)}</select></label>
    <label class="field"><span>To</span><select id="rTo" class="input">${stationOptions(post?.to_station ?? STATIONS.length - 1)}</select></label>
    <label class="field"><span>Leaving at</span><input id="rTime" class="input" type="time" value="${hhmm}" /></label>
    <label class="field"><span>Meet</span><select id="rPref" class="input"><option value="any">Anyone</option><option value="male">Men</option><option value="female">Women</option></select></label>
    <label class="field"><span>Note (optional)</span><input id="rNote" class="input" maxlength="120" value="${esc(post?.note || "")}" /></label>
    <button class="btn primary block" data-do="saveRoute">Find my route buddies</button></div>`);
}


function renderSetupProblem(kind, host = "") {
  const text = {
    config: "<b>Supabase isn't configured yet.</b> Copy <code>.env.example</code> to <code>.env</code>, fill in <code>SUPABASE_URL</code>, <code>SUPABASE_PUBLISHABLE_KEY</code> and <code>SUPABASE_SECRET_KEY</code> (Supabase → Project Settings → API Keys), then restart <code>python backend.py</code>.",
    network: `<b>The server can't reach ${esc(host || "Supabase")}.</b> Check <code>SUPABASE_URL</code> in <code>.env</code>, that your project isn't <b>paused</b> (Supabase dashboard → Restore project), and your internet connection. Then restart the backend.`,
    key: "<b>Supabase rejected the keys.</b> In <code>.env</code> use the <b>Publishable key</b> (<code>sb_publishable_…</code>) for <code>SUPABASE_PUBLISHABLE_KEY</code> and the <b>Secret key</b> (<code>sb_secret_…</code>) for <code>SUPABASE_SECRET_KEY</code> — no extra spaces, no quotes — then restart the backend.",
    grants: "<b>The tables exist, but the server key has no permission on them.</b> Run the <code>grant …</code> lines at the bottom of <code>supabase/schema.sql</code> in the Supabase SQL Editor, then reload.",
    tables: "<b>Connected to Supabase, but the Waymate tables don't exist yet.</b> In the Supabase dashboard open <b>SQL Editor</b>, paste and run the whole of <code>supabase/schema.sql</code> (it also gives the server key permission), then reload this page.",
    server_down: "<b>We can't reach the Waymate server.</b> Please check your internet connection and try again in a moment. <span class=\"muted\">(Running it yourself? Start it with <code>python backend.py</code>.)</span>",
    other: "<b>Something went wrong on our side.</b> Please try again in a moment. <span class=\"muted\">(Owner: check the Vercel logs, or open <code>/api/status</code>.)</span>"
  }[kind] || "Something is wrong with the setup.";
  $("#tabbar").classList.add("hidden");
  const title = ["server_down", "other"].includes(kind) ? "We can't load Waymate right now" : "Almost there — connect Supabase";
  show(`<div class="card" data-nosnippet><h2 class="page-title">${title}</h2><p>${text}</p>
    <button class="btn primary block" data-do="reload">Try again</button></div>`);
}

/* After sign-in: show the profile form (first time) or the app. */
function startApp(profile) {
  me = profile;
  if (!me) { $("#tabbar").classList.add("hidden"); return renderProfileForm(); }
  $("#tabbar").classList.remove("hidden");
  setInterval(checkRequests, 30000);         // announce new buddy requests
  go("discover");
}
async function checkRequests() {
  if (!me || document.hidden) return;
  const data = await api("/buddies").catch(() => null);
  if (!data) return;
  if (data.incoming.length > lastIncoming) { toast("🫂 New buddy request"); if (screenName === "buddies" && !chatWith) renderBuddies(); }
  lastIncoming = data.incoming.length;
}

/* ----- discover: post your route → see matches ----- */
async function renderDiscover() {
  await ensureStations();
  const mine = await ok(api("/route"));
  if (!mine) return;
  if (!mine.route) return renderRouteForm();
  const route = mine.route;
  const found = await ok(api("/matches"));
  const visible = (found?.matches || []).filter(m => !passed.has(m.user_id));
  show(`<h2 class="page-title">Your route</h2>
    <div class="card"><b>${esc(STATIONS[route.from_station].name)} → ${esc(STATIONS[route.to_station].name)}</b>
      <div class="muted small">Leaving in ${Math.max(0, minsFromNow(route.departure_at))} min</div>
      <button class="btn ghost small-btn" data-do="editRoute">Change</button>
      <button class="btn danger small-btn" data-do="deleteRoute">Stop matching</button></div>
    <h2 class="page-title">Matches</h2>` +
    (visible.length ? visible.map(matchCard).join("") :
      `<div class="card empty">No matches yet. People appear when they travel the same way on a shared stretch and leave within 45 minutes of you.</div>`));
}

/* ----- buddies: requests in, requests out, accepted ----- */
async function renderBuddies() {
  const data = await ok(api("/buddies"));
  if (!data) return;
  lastIncoming = data.incoming.length;
  const row = (c, buttons) => `<div class="card"><b>${esc(c.user.name)}${c.user.age ? ", " + c.user.age : ""}</b>
    <div class="muted small">${esc(c.user.bio || "")}</div>${buttons}${c.user.id ? safety(c.user.id, c.user.name) : ""}</div>`;
  const reply = (c, status, label, cls) => `<button class="btn ${cls} small-btn" data-do="respond" data-id="${c.id}" data-status="${status}">${label}</button>`;
  show(`<h2 class="page-title">Buddies</h2>
    ${data.incoming.length ? "<h3>Requests for you</h3>" + data.incoming.map(c => row(c, reply(c, "accepted", "Accept", "primary") + reply(c, "declined", "Decline", "ghost"))).join("") : ""}
    ${data.outgoing.length ? "<h3>Waiting for a reply</h3>" + data.outgoing.map(c => row(c, reply(c, "cancelled", "Cancel", "ghost"))).join("") : ""}
    <h3>Your buddies</h3>
    ${data.buddies.length ? data.buddies.map(c => row(c,
      `<button class="btn primary small-btn" data-do="openChat" data-id="${esc(c.user.id)}" data-name="${esc(c.user.name)}">💬 Message</button>`)).join("")
      : `<div class="card empty">No buddies yet — say hi to someone on Discover.</div>`}`);
}

/* ----- chat: the open chat checks for new messages every 3 seconds ----- */
let lastMessageId = 0;
async function renderChat() {
  const data = await ok(api("/messages/" + chatWith.id));
  if (!data) return go("buddies");
  show(`<div class="card"><div class="row spread"><b>${esc(chatWith.name)}</b>${safety(chatWith.id, chatWith.name)}<button class="btn ghost small-btn" data-do="go" data-screen="buddies">← Back</button></div>
    <div id="chatLog" style="max-height:55vh;overflow:auto;margin:10px 0"></div>
    <input id="chatInput" class="input" placeholder="Message…" maxlength="2000" />
    <button class="btn primary block" data-do="send">Send</button></div>`);
  lastMessageId = 0;
  data.messages.forEach(addBubble);
  $("#chatInput").addEventListener("keydown", e => { if (e.key === "Enter") ACTIONS.send(); });
  clearInterval(chatTimer);
  chatTimer = setInterval(async () => {
    if (!chatWith || document.hidden) return;
    const fresh = await api(`/messages/${chatWith.id}?after=${lastMessageId}`).catch(() => null);
    if (fresh) fresh.messages.forEach(addBubble);
  }, 5000);
}
function addBubble(msg) {
  const log = $("#chatLog");
  if (!log || msg.id <= lastMessageId) return;
  lastMessageId = msg.id;
  const mine = msg.sender === me.id;
  log.insertAdjacentHTML("beforeend", `<div style="text-align:${mine ? "right" : "left"};margin:4px 0"><span class="badge ${mine ? "ok" : ""}">${esc(msg.body)}</span></div>`);
  log.scrollTop = log.scrollHeight;
}

/* ----- profile ----- */
async function renderProfile() {
  const blocked = (await ok(api("/blocked")))?.blocked || [];
  show(`<h2 class="page-title">Profile</h2><div class="card"><b>${esc(me.name)}, ${me.age}</b><div class="muted">${esc(me.bio)}</div></div>
    ${blocked.length ? "<h3>Blocked people</h3>" + blocked.map(x => `<div class="card row spread"><span>${esc(x.name)}</span>
      <button class="btn ghost small-btn" data-do="unblock" data-id="${esc(x.id)}">Unblock</button></div>`).join("") : ""}
    <button class="btn ghost block" data-do="logout">Log out</button>`);
}

/* ---------- 4. actions (what each button does) ---------- */
const ACTIONS = {
  go: d => go(d.screen),
  landing: renderLanding,
  auth: d => renderAuth("email", "", d.intent),                       // landing buttons → email form
  authIntent: d => renderAuth(d.mode, "", d.intent),                  // sign-in ⇄ create-account keeps the current method
  authMode: d => renderAuth(d.mode),
  reload: () => location.reload(),

  async signIn() {
    const email = $("#email").value.trim();
    try { await api("/auth/login", "POST", { email, password: $("#password").value }); location.reload(); }
    catch (e) { if (e.code === "email_not_confirmed") return renderCheckEmail(email); toast(friendly(e.message)); }
  },
  async signUp() {
    const email = $("#email").value.trim();
    try {
      const r = await api("/auth/signup", "POST", { email, password: $("#password").value });
      if (r.signedIn) location.reload(); else renderCheckEmail(email);      // "Confirm email" ON in Supabase → confirm page
    } catch (e) { toast(friendly(e.message)); }
  },
  async resend(d) {
    if (Date.now() < (ACTIONS.resend.next || 0)) return toast("Please wait a minute before resending");
    if (await ok(api("/auth/resend", "POST", { email: d.email }))) { ACTIONS.resend.next = Date.now() + 60000; toast("Confirmation email sent again"); }
  },
  async sendCode() {
    const phone = $("#phone").value.replace(/\D/g, "");
    if (Date.now() < (ACTIONS.sendCode.next || 0)) return toast("Please wait 30 seconds before asking for another code");
    if (await ok(api("/auth/phone/send", "POST", { phone }))) { ACTIONS.sendCode.next = Date.now() + 30000; renderAuth("phone", phone); }
  },
  async verifyCode(d) {
    if (await ok(api("/auth/phone/verify", "POST", { phone: d.phone, token: $("#otp").value.trim() }))) location.reload();
  },
  async logout() { await api("/auth/logout", "POST").catch(() => {}); location.reload(); },

  async saveProfile() {
    const profile = await ok(api("/profile", "PUT", { name: $("#pName").value, gender: $("#pGender").value, age: $("#pAge").value, bio: $("#pBio").value }));
    if (profile) startApp(profile.profile);
  },
  async saveRoute() {
    const [h, m] = $("#rTime").value.split(":").map(Number);
    const when = new Date(); when.setHours(h, m, 0, 0);
    if (when < Date.now() - 30 * 60000) when.setDate(when.getDate() + 1);   // earlier than now → tomorrow
    const saved = await ok(api("/route", "PUT", { from: $("#rFrom").value, to: $("#rTo").value, departureAt: when.toISOString(), pref: $("#rPref").value, note: $("#rNote").value }));
    if (saved) { passed.clear(); go("discover"); }
  },
  async editRoute() { renderRouteForm((await ok(api("/route")))?.route); },
  async deleteRoute() { if (confirm("Stop matching?") && await ok(api("/route", "DELETE"))) go("discover"); },
  pass: d => { passed.add(d.id); renderDiscover(); },
  async request(d) { if (await ok(api("/connections", "POST", { to: d.id }))) { toast("Request sent 👋"); renderDiscover(); } },
  async respond(d) { if (await ok(api(`/connections/${d.id}/respond`, "POST", { status: d.status }))) renderBuddies(); },
  openChat(d) { chatWith = { id: d.id, name: d.name }; renderChat(); },
  async send() {
    const input = $("#chatInput"), body = input.value.trim(); if (!body) return;
    const sent = await ok(api("/messages/" + chatWith.id, "POST", { body }));
    if (sent) { input.value = ""; addBubble(sent.message); }
  },

  async report(d) {
    const pick = Number(prompt(`Report ${d.name} — why?\n` + REASONS.map((r, i) => `${i + 1}. ${r[1]}`).join("\n")));
    if (!REASONS[pick - 1]) return;
    const details = prompt("Anything else we should know? (optional)") || "";
    if (!(await ok(api("/report", "POST", { user: d.id, reason: REASONS[pick - 1][0], details })))) return;
    toast("Thanks — we'll review this report.");
    if (confirm(`Also block ${d.name}?`)) ACTIONS.block(d, true);
  },
  async block(d, skipConfirm) {
    if (skipConfirm !== true && !confirm(`Block ${d.name}? You won't see or message each other.`)) return;
    if (await ok(api("/block", "POST", { user: d.id }))) { toast(`${d.name} blocked`); go("discover"); }
  },
  async unblock(d) { if (await ok(api("/block/" + d.id, "DELETE"))) renderProfile(); }
};

/* ---------- 5. start-up ---------- */
const isLocal = ["localhost", "127.0.0.1"].includes(location.hostname);
async function loadStations() {
  if (STATIONS.length) return;
  STATIONS = (await api("/stations")).stations;      // cached by the Vercel edge, so this is nearly free
}
async function ensureStations() {
  try { await loadStations(); } catch (e) { toast("Couldn't load the station list — please check your connection"); }
}

(async function boot() {
  // A "wm_hint" cookie means this browser has signed in before. Without it we show the landing page INSTANTLY, without
  // waiting for the server — so new visitors (and search engines) always see the real page straight away.
  const returning = document.cookie.includes("wm_hint=1");
  if (returning) show('<div class="card empty">Loading…</div>'); else renderLanding();
  try {
    const [who] = await Promise.allSettled([api("/me"), loadStations()]);      // both at once: one round trip, not three
    if (who.status === "rejected") throw who.reason;
    if (!who.value.signedIn) { document.cookie = "wm_hint=; Max-Age=0; path=/"; if (returning) renderLanding(); return; }
    startApp(who.value.profile);
  } catch (e) {
    if (!returning && !isLocal) return;             // visitors keep the landing page; any problem shows up when they act
    let status = null;
    try { status = await api("/status"); } catch (e2) { return renderSetupProblem("server_down"); }
    renderSetupProblem(status.ok ? "other" : status.problem, status.host);
  }
})();
