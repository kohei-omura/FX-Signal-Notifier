// Cloudflare Worker — GMO外国為替FXの中継 + ライブLINE通知
//  GET  /                         … ticker（為替レート）を返す
//  GET  /?path=/public/v1/klines… … 指定エンドポイントを中継（?last=Nで末尾だけ）
//  POST /?action=notify           … {text} を受け取りLINEへ送信（トークンはWorker側に保存）
// 必要な環境変数（Cloudflareの Variables/Secrets に設定）:
//  LINE_TOKEN  … LINEチャネルアクセストークン（必須・ライブ通知を使う場合）
//  NOTIFY_KEY  … 簡易合言葉（任意）。設定時は ?key= が一致しないと通知拒否
export default {
  async fetch(request, env) {
    const u = new URL(request.url);
    if (request.method === "OPTIONS") return new Response(null, { headers: cors() });

    // ライブLINE通知
    if (request.method === "POST" && u.searchParams.get("action") === "notify") {
      try {
        if (env.NOTIFY_KEY && u.searchParams.get("key") !== env.NOTIFY_KEY) return json({ error: "unauthorized" }, 401);
        if (!env.LINE_TOKEN) return json({ error: "LINE_TOKEN未設定" }, 500);
        const { text } = await request.json();
        if (!text) return json({ error: "no text" }, 400);
        const r = await fetch("https://api.line.me/v2/bot/message/broadcast", {
          method: "POST",
          headers: { "Authorization": "Bearer " + env.LINE_TOKEN, "Content-Type": "application/json" },
          body: JSON.stringify({ messages: [{ type: "text", text: String(text).slice(0, 4900) }] })
        });
        return json({ ok: r.ok, status: r.status });
      } catch (e) { return json({ error: String(e) }, 500); }
    }

    // 価格・klines中継
    let path = u.searchParams.get("path") || "/public/v1/ticker";
    if (!path.startsWith("/public/v1/")) path = "/public/v1/ticker";
    const last = parseInt(u.searchParams.get("last") || "0", 10);
    try {
      const r = await fetch("https://forex-api.coin.z.com" + path, { headers: { "User-Agent": "fx-navi-proxy" }, cf: { cacheTtl: 0 } });
      let body = await r.text();
      if (last > 0) {
        try { const j = JSON.parse(body); if (Array.isArray(j.data) && j.data.length > last) { j.data = j.data.slice(-last); body = JSON.stringify(j); } } catch (e) {}
      }
      return new Response(body, { headers: { ...cors(), "content-type": "application/json; charset=utf-8", "cache-control": "no-store" } });
    } catch (e) { return json({ error: String(e) }, 502); }
  }
};
function cors() {
  return { "access-control-allow-origin": "*", "access-control-allow-methods": "GET, POST, OPTIONS", "access-control-allow-headers": "content-type" };
}
function json(o, status = 200) {
  return new Response(JSON.stringify(o), { status, headers: { ...cors(), "content-type": "application/json" } });
}
