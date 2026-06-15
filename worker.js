// Cloudflare Worker — GMO外国為替FX Public APIをCORS付きで中継。
// 既定: /public/v1/ticker を返す。?path= でklines等も中継、?last=N で末尾N件に間引き（通信量削減）。
export default {
  async fetch(request) {
    if (request.method === "OPTIONS") return new Response(null, { headers: cors() });
    const u = new URL(request.url);
    let path = u.searchParams.get("path") || "/public/v1/ticker";
    if (!path.startsWith("/public/v1/")) path = "/public/v1/ticker";  // SSRF対策
    const last = parseInt(u.searchParams.get("last") || "0", 10);
    try {
      const r = await fetch("https://forex-api.coin.z.com" + path,
                            { headers: { "User-Agent": "fx-navi-proxy" }, cf: { cacheTtl: 0 } });
      let body = await r.text();
      if (last > 0) {
        try { const j = JSON.parse(body);
          if (Array.isArray(j.data) && j.data.length > last) { j.data = j.data.slice(-last); body = JSON.stringify(j); }
        } catch (e) {}
      }
      return new Response(body, { headers: { ...cors(),
        "content-type": "application/json; charset=utf-8", "cache-control": "no-store" } });
    } catch (e) {
      return new Response(JSON.stringify({ error: String(e) }),
        { status: 502, headers: { ...cors(), "content-type": "application/json" } });
    }
  }
};
function cors() {
  return { "access-control-allow-origin": "*",
           "access-control-allow-methods": "GET, OPTIONS", "access-control-allow-headers": "*" };
}
