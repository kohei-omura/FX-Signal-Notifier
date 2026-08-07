// Cloudflare Worker — GMO外国為替FXの中継 + LINE通知 + GitHub Action起動(Cron)
// 環境変数: LINE_TOKEN / NOTIFY_KEY(任意) / GH_PAT

// GitHub側の宛先
const GH_OWNER    = "kohei-omura";
const GH_REPO     = "FX-Signal-Notifier";
const GH_WORKFLOW = "fx-signal.yml";
const GH_REF      = "main";

// ★ライブ通知(index.html発)のLINE送信ルール。
//   LINEの月間上限を使い切らないよう、「総合判定が一定割合以上」のライブ通知だけを通す。
//   文面中の「（5/10）」「3/13」のような判定比を読み取って判定する。
const LIVE_PREFIXES   = ["⚡ライブ", "⚡ ライブ", "ライブ "];
const LIVE_MIN_RATIO  = 0.5;   // 50%以上でLINEへ。1.01にすれば実質すべて遮断
// index.html側でも「総合判定50%以上＋上位足一致」で絞っているため、ここは古い版が残っていた場合の保険。
// 画面側の条件だけで運用したいときは false にする。
const LIVE_FILTER_ON  = true;

function isLive(text) {
  const t = String(text || "").trimStart();
  return LIVE_PREFIXES.some(p => t.startsWith(p));
}

// 「（5/10）」「(3/13)」「5/10」などから判定比を取り出す。見つからなければ null。
function judgeRatio(text) {
  const m = String(text || "").match(/[（(]?\s*(\d{1,3})\s*\/\s*(\d{1,3})\s*[）)]?/g);
  if (!m) return null;
  let best = null;
  for (const seg of m) {
    const p = seg.match(/(\d{1,3})\s*\/\s*(\d{1,3})/);
    if (!p) continue;
    const a = parseInt(p[1], 10), b = parseInt(p[2], 10);
    // 価格やpipsを誤検出しないよう、分母が2〜30の「判定比らしい」ものだけ採用
    if (b >= 2 && b <= 30 && a <= b) best = a / b;
  }
  return best;
}

// LINEへ送ってよいか。ライブ通知だけがフィルタ対象で、サーバー発の通知は常に通す。
function allowToLine(text) {
  if (!LIVE_FILTER_ON) return { ok: true };
  if (!isLive(text)) return { ok: true };
  const r = judgeRatio(text);
  if (r === null) return { ok: false, reason: "live-no-judge-ratio" };
  if (r < LIVE_MIN_RATIO) return { ok: false, reason: `live-low-score(${(r*100).toFixed(0)}%)` };
  return { ok: true };
}

export default {
  async fetch(request, env) {
    const u = new URL(request.url);
    if (request.method === "OPTIONS") return new Response(null, { headers: cors() });

    // 手動テスト用 GET /?action=cron-test
    if (request.method === "GET" && u.searchParams.get("action") === "cron-test") {
      if (env.NOTIFY_KEY && u.searchParams.get("key") !== env.NOTIFY_KEY) return json({ error: "unauthorized" }, 401);
      if (!env.GH_PAT) return json({ error: "GH_PAT未設定" }, 500);
      const res = await dispatchWorkflow(env);
      return json(res, res.ok ? 200 : 502);
    }

    // LINE通知
    if (request.method === "POST" && u.searchParams.get("action") === "notify") {
      try {
        if (env.NOTIFY_KEY && u.searchParams.get("key") !== env.NOTIFY_KEY) return json({ error: "unauthorized" }, 401);
        if (!env.LINE_TOKEN) return json({ error: "LINE_TOKEN未設定" }, 500);
        const { text } = await request.json();
        if (!text) return json({ error: "no text" }, 400);

        // ★ライブ通知は総合判定が LIVE_MIN_RATIO 未満なら送らない（LINE枠の節約）
        const gate = allowToLine(text);
        if (!gate.ok) {
          return json({ ok: true, skipped: gate.reason });
        }

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
  },

  // Cron Trigger(5分)がここを呼ぶ → GitHub Actionを起動
  async scheduled(event, env, ctx) {
    ctx.waitUntil(dispatchWorkflow(env));
  }
};

async function dispatchWorkflow(env) {
  const url = `https://api.github.com/repos/${GH_OWNER}/${GH_REPO}/actions/workflows/${GH_WORKFLOW}/dispatches`;
  const r = await fetch(url, {
    method: "POST",
    headers: {
      "Authorization": "Bearer " + env.GH_PAT,
      "Accept": "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "fx-navi-cron",
      "Content-Type": "application/json"
    },
    body: JSON.stringify({ ref: GH_REF })
  });
  return { ok: r.ok, status: r.status, detail: r.ok ? "dispatched" : await r.text() };
}

function cors() {
  return { "access-control-allow-origin": "*", "access-control-allow-methods": "GET, POST, OPTIONS", "access-control-allow-headers": "content-type" };
}
function json(o, status = 200) {
  return new Response(JSON.stringify(o), { status, headers: { ...cors(), "content-type": "application/json" } });
}
