// Cloudflare Worker — GMOコイン外国為替FXの現在値(bid/ask)をCORS付きで中継する。
// 無料(Workers Free: 10万req/日)。デプロイ後のURLを index.html の LIVE_PRICE_URL に貼る。
export default {
  async fetch(request) {
    // プリフライト対応
    if (request.method === "OPTIONS") {
      return new Response(null, { headers: cors() });
    }
    try {
      const r = await fetch("https://forex-api.coin.z.com/public/v1/ticker", {
        headers: { "User-Agent": "fx-navi-proxy" },
        cf: { cacheTtl: 0 }
      });
      const body = await r.text();
      return new Response(body, {
        headers: { ...cors(), "content-type": "application/json; charset=utf-8", "cache-control": "no-store" }
      });
    } catch (e) {
      return new Response(JSON.stringify({ error: String(e) }), {
        status: 502, headers: { ...cors(), "content-type": "application/json" }
      });
    }
  }
};
function cors() {
  return {
    "access-control-allow-origin": "*",
    "access-control-allow-methods": "GET, OPTIONS",
    "access-control-allow-headers": "*"
  };
}
