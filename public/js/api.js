/* Tiny helper for talking to backend.py. The login lives in a cookie, so the browser sends it automatically. */
export async function api(path, method = "GET", body) {
  let res;
  try {
    res = await fetch("/api" + path, {
      method, credentials: "same-origin",
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined
    });
  } catch (e) {
    throw Object.assign(new Error("Can't reach the Waymate server. Is backend.py running?"), { code: "server_down" });
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw Object.assign(new Error(data.error || "Something went wrong"), { status: res.status, code: data.code });
  return data;
}
