"""GitHub Pages 用の静的ダッシュボード（docs/index.html）を生成する。

- 全銘柄を分析し、買い/売りTOPをサーバー側で描画。
- 全銘柄の結果をページに埋め込み:
    * 検索バー … コード/名前から即引き（最大5件）
    * マイ銘柄  … ブラウザに保存した自分の銘柄の判定を一覧表示（端末内保存）
  （静的ページなのでサーバー計算不要・CORS問題なし）
"""
from __future__ import annotations
import html
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import ranking as R
from config import market_map, load_holdings

JST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent
DOCS = ROOT / "docs"


def _seg(market: str) -> str:
    m = {"prime": ("P", "p"), "standard": ("S", "s"), "growth": ("G", "g")}
    if market not in m:
        return ""
    label, cls = m[market]
    return f'<span class="seg {cls}">{label}</span>'


def _esc(s) -> str:
    return html.escape(str(s))


def _score_bar(score: float) -> str:
    pct = max(-100, min(100, score)) / 100
    if pct >= 0:
        return ('<span class="bar"><span class="bar-pos" '
                f'style="width:{pct*50:.0f}%"></span></span>')
    return ('<span class="bar"><span class="bar-neg" '
            f'style="width:{abs(pct)*50:.0f}%;margin-left:{50-abs(pct)*50:.0f}%">'
            '</span></span>')


def _badge(signal: str) -> str:
    m = {"BUY": ("買", "buy"), "SELL": ("売", "sell"), "HOLD": ("待", "hold")}
    label, cls = m.get(signal, ("待", "hold"))
    return f'<span class="badge {cls}">{label}</span>'


def _card(rank: int, a, show_levels: bool, market: str = "") -> str:
    levels = ""
    if show_levels and a.entry and a.target and a.stop:
        rr = f' ・ RR {a.rr}' if a.rr else ''
        levels = ('<div class="levels">'
                  f'<span class="lv tgt">利確 ¥{a.target:,.0f}</span>'
                  f'<span class="lv stp">損切 ¥{a.stop:,.0f}</span>'
                  f'<span class="lv rr">{_esc(rr.strip(" ・"))}</span></div>')
    reasons = ""
    if a.reasons:
        chips = "".join(f'<span class="chip">{_esc(r)}</span>'
                        for r in a.reasons[:3])
        reasons = f'<div class="reasons">{chips}</div>'
    fund = ""
    if a.fund:
        f = a.fund
        fc = []
        if f.get("per") is not None: fc.append(f'<span class="fchip">PER {f["per"]:.1f}</span>')
        if f.get("pbr") is not None: fc.append(f'<span class="fchip">PBR {f["pbr"]:.2f}</span>')
        if f.get("roe") is not None: fc.append(f'<span class="fchip">ROE {f["roe"]:.1f}%</span>')
        if f.get("divy") is not None: fc.append(f'<span class="fchip">利回り {f["divy"]:.1f}%</span>')
        if f.get("growth") is not None: fc.append(f'<span class="fchip">増益 {f["growth"]:+.0f}%</span>')
        if fc:
            fund = f'<div class="funds">{"".join(fc)}</div>'
    val = ""
    if a.fund and a.fund.get("theo"):
        theo = a.fund["theo"]
        gap = (a.price / theo - 1) * 100 if theo else 0
        if gap <= -5:
            lab, vcls = f"割安 {abs(gap):.0f}%", "und"
        elif gap >= 5:
            lab, vcls = f"割高 {gap:.0f}%", "over"
        else:
            lab, vcls = "ほぼ適正", "fair"
        val = (f'<div class="val"><span class="tchip {vcls}">'
               f'理論株価 ¥{theo:,} ・ {lab}</span></div>')
    bt_html = ""
    if a.bt:
        b = a.bt
        items = [f'<span class="btchip win">利確勝率 {b["win_rate"]}%</span>']
        if b.get("days_tp"):
            items.append(f'<span class="btchip">利確まで ~{b["days_tp"]}日</span>')
        if b.get("days_sl"):
            items.append(f'<span class="btchip">損切まで ~{b["days_sl"]}日</span>')
        bt_html = f'<div class="bt">{"".join(items)}</div>'
    has_c = a.combined is not None
    disp = a.combined if has_c else a.score
    sc_cls = "pos" if disp >= 0 else "neg"
    sc_txt = f'複合 {a.combined:.0f}' if has_c else f'{a.score:+.0f}'
    return (f'<div class="card" style="animation-delay:{rank*0.05:.2f}s">'
            f'<div class="row1"><span class="rank">{rank}</span>'
            f'<div class="title"><span class="code">{_esc(a.code)}</span>'
            f'<span class="name">{_esc(a.name)}</span>{_seg(market)}</div>{_badge(a.signal)}</div>'
            f'<div class="row2"><span class="price" data-px="{_esc(a.code)}">¥{a.price:,.0f}</span>'
            f'<span class="score {sc_cls}">{sc_txt}</span>{_score_bar(disp)}</div>'
            f'{levels}{fund}{val}{bt_html}{reasons}</div>')


def _section(title: str, sub: str, cards_html: str, accent: str) -> str:
    return (f'<section><h2 class="{accent}"><span>{_esc(title)}</span>'
            f'<em>{_esc(sub)}</em></h2>'
            f'<div class="cards">{cards_html}</div></section>')


def _holding_levels(h, a, cfg):
    import signals as S
    cur = a.price
    tgt, stp = h.get("target"), h.get("stop")
    if tgt is None or stp is None:
        at, as_ = S.holding_levels(h.get("buy") or cur, a.atr, cfg)
        tgt = at if tgt is None else tgt
        stp = as_ if stp is None else stp
    return tgt, stp


def _holding_card(h, a, cfg) -> str:
    code = h["code"]
    buy = h.get("buy")
    if a is None:
        return ('<div class="card"><div class="row1"><div class="title">'
                f'<span class="code">{_esc(code)}</span>'
                '<span class="name">データ取得待ち</span></div></div></div>')
    cur, name = a.price, a.name
    tgt, stp = _holding_levels(h, a, cfg)
    pl = ((cur - buy) / buy * 100) if buy else 0.0
    pl_cls = "pos" if pl >= 0 else "neg"
    if cur >= tgt:
        st_label, st_cls = "利確圏", "buy"
    elif cur <= stp:
        st_label, st_cls = "損切圏", "sell"
    else:
        st_label, st_cls = "保有中", "hold"
    buy_s = f"¥{buy:,.0f}" if buy else "—"
    fund = ""
    if a.fund:
        f = a.fund
        fc = []
        if a.combined is not None:
            fc.append(f'<span class="fchip cmb">複合 {a.combined:.0f}</span>')
        if f.get("per") is not None: fc.append(f'<span class="fchip">PER {f["per"]:.1f}</span>')
        if f.get("pbr") is not None: fc.append(f'<span class="fchip">PBR {f["pbr"]:.2f}</span>')
        if f.get("roe") is not None: fc.append(f'<span class="fchip">ROE {f["roe"]:.1f}%</span>')
        if f.get("divy") is not None: fc.append(f'<span class="fchip">利回り {f["divy"]:.1f}%</span>')
        if f.get("growth") is not None: fc.append(f'<span class="fchip">増益 {f["growth"]:+.0f}%</span>')
        if fc:
            fund = f'<div class="funds">{"".join(fc)}</div>'
    val = ""
    if a.fund and a.fund.get("theo"):
        theo = a.fund["theo"]
        gap = (cur / theo - 1) * 100 if theo else 0
        if gap <= -5:
            vlab, vcls = f"割安 {abs(gap):.0f}%", "und"
        elif gap >= 5:
            vlab, vcls = f"割高 {gap:.0f}%", "over"
        else:
            vlab, vcls = "ほぼ適正", "fair"
        val = (f'<div class="val"><span class="tchip {vcls}">'
               f'理論株価 ¥{theo:,} ・ {vlab}</span></div>')
    sig_badge = _badge(a.signal)
    bt_html = ""
    if a.bt:
        b = a.bt
        items = [f'<span class="btchip win">利確勝率 {b["win_rate"]}%</span>']
        if b.get("days_tp"):
            items.append(f'<span class="btchip">利確まで ~{b["days_tp"]}日</span>')
        if b.get("days_sl"):
            items.append(f'<span class="btchip">損切まで ~{b["days_sl"]}日</span>')
        bt_html = f'<div class="bt">{"".join(items)}</div>'
    sell_warn = ""
    if a.signal == "SELL":
        sell_warn = '<div class="hsell">⚠ テクニカルは売りシグナル</div>'
    reasons = ""
    if a.reasons:
        chips = "".join(f'<span class="chip">{_esc(r)}</span>' for r in a.reasons[:3])
        reasons = f'<div class="reasons">{chips}</div>'
    return (f'<div class="card"><div class="row1">'
            f'<div class="title"><span class="code">{_esc(code)}</span>'
            f'<span class="name">{_esc(name)}</span></div>'
            f'<span class="hstat {st_cls}">{st_label}</span>{sig_badge}</div>'
            f'<div class="row2"><span class="price" data-px="{_esc(code)}">¥{cur:,.0f}</span>'
            f'<span class="score {pl_cls}">{pl:+.1f}%</span></div>'
            f'<div class="levels"><span class="lv">買値 {buy_s}</span>'
            f'<span class="lv tgt">利確 ¥{tgt:,.0f}</span>'
            f'<span class="lv stp">損切 ¥{stp:,.0f}</span></div>'
            f'{bt_html}{fund}{val}{sell_warn}{reasons}</div>')


CSS = """
:root{
  --bg:#0c0e13; --bg2:#12151d; --card:#161a24; --line:#242a37;
  --ink:#e8e6df; --mut:#8b93a7; --gold:#d4af56; --gold-d:#9a7c30;
  --buy:#46c46a; --sell:#ef5f7a; --hold:#6b7286;
}
*{box-sizing:border-box;margin:0;padding:0}
body{
  font-family:'Zen Kaku Gothic New',sans-serif; color:var(--ink);
  background:
    radial-gradient(1200px 600px at 80% -10%, rgba(212,175,86,.10), transparent 60%),
    radial-gradient(900px 500px at -10% 20%, rgba(70,90,140,.10), transparent 55%),
    var(--bg);
  background-attachment:fixed; line-height:1.6;
  padding:env(safe-area-inset-top) 0 64px; -webkit-font-smoothing:antialiased;
}
.wrap{max-width:680px;margin:0 auto;padding:0 18px}
header{padding:34px 0 10px;border-bottom:1px solid var(--line);margin-bottom:8px}
.brand{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap}
.brand h1{font-family:'Shippori Mincho',serif;font-weight:800;font-size:30px;
  letter-spacing:.04em;color:var(--gold)}
.brand .en{font-family:'IBM Plex Mono',monospace;font-size:11px;letter-spacing:.32em;
  color:var(--mut);text-transform:uppercase}
.meta{font-family:'IBM Plex Mono',monospace;font-size:12px;color:var(--mut);
  margin-top:8px;display:flex;gap:14px;flex-wrap:wrap}
.meta b{color:var(--gold-d);font-weight:600}
section{margin-top:30px}
h2{font-family:'Shippori Mincho',serif;font-size:19px;font-weight:600;
  display:flex;align-items:baseline;gap:10px;margin-bottom:14px;
  padding-left:12px;border-left:3px solid var(--gold)}
h2.sell{border-color:var(--sell)} h2.watch{border-color:var(--mut)}
h2.find{border-color:var(--gold)}
h2 em{font-style:normal;font-family:'IBM Plex Mono',monospace;font-size:10px;
  letter-spacing:.2em;color:var(--mut);text-transform:uppercase}
.cards{display:flex;flex-direction:column;gap:10px}
.card{background:linear-gradient(180deg,var(--card),var(--bg2));
  border:1px solid var(--line);border-radius:14px;padding:14px 15px;
  opacity:0;transform:translateY(8px);animation:rise .5s ease forwards;
  box-shadow:0 1px 0 rgba(255,255,255,.02) inset}
@keyframes rise{to{opacity:1;transform:none}}
.row1{display:flex;align-items:center;gap:11px}
.rank{font-family:'IBM Plex Mono',monospace;font-weight:600;font-size:13px;
  color:var(--gold);min-width:22px;height:22px;display:grid;place-items:center;
  border:1px solid var(--gold-d);border-radius:50%;flex:none;padding:0 4px}
.title{flex:1;min-width:0;display:flex;align-items:baseline;gap:9px;flex-wrap:wrap}
.code{font-family:'IBM Plex Mono',monospace;font-size:15px;font-weight:600}
.name{font-size:13px;color:var(--mut)}
.seg{flex:none;font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:600;
  padding:1px 6px;border-radius:6px;border:1px solid var(--line);color:var(--mut)}
.seg.p{color:#6fa8ff;border-color:rgba(111,168,255,.4)}
.seg.g{color:var(--gold);border-color:var(--gold-d)}
.hstat{flex:none;font-size:12px;font-weight:700;padding:3px 11px;border-radius:8px;
  border:1px solid var(--line);color:var(--mut)}
.hstat.buy{color:var(--buy);border-color:rgba(70,196,106,.35)}
.hstat.sell{color:var(--sell);border-color:rgba(239,95,122,.35)}
.funds{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
.fchip{font-size:11px;font-weight:700;color:var(--gold);background:rgba(212,175,99,.08);
  border:1px solid var(--gold-d);border-radius:7px;padding:2px 8px}
.fchip.cmb{color:#0c1118;background:var(--gold);border-color:var(--gold)}
.hsell{margin-top:8px;font-size:13px;font-weight:700;color:var(--sell);
  background:rgba(239,95,122,.10);border:1px solid rgba(239,95,122,.35);
  border-radius:9px;padding:7px 11px}
.bt{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
.btchip{font-size:11px;font-weight:700;color:var(--mut);background:rgba(139,147,167,.10);
  border:1px solid var(--line);border-radius:7px;padding:2px 8px}
.btchip.win{color:var(--buy);border-color:rgba(70,196,106,.35)}
.val{margin-top:8px}
.tchip{display:inline-block;font-size:12px;font-weight:800;border-radius:8px;
  padding:3px 11px;border:1px solid var(--line);color:var(--mut)}
.tchip.und{color:var(--buy);background:rgba(70,196,106,.10);border-color:rgba(70,196,106,.35)}
.tchip.over{color:var(--sell);background:rgba(239,95,122,.10);border-color:rgba(239,95,122,.35)}
.badge{flex:none;width:26px;height:26px;display:grid;place-items:center;
  border-radius:8px;font-size:13px;font-weight:700}
.badge.buy{color:var(--buy);background:rgba(70,196,106,.12);border:1px solid rgba(70,196,106,.3)}
.badge.sell{color:var(--sell);background:rgba(239,95,122,.12);border:1px solid rgba(239,95,122,.3)}
.badge.hold{color:var(--mut);background:rgba(139,147,167,.1);border:1px solid var(--line)}
.rm{flex:none;width:26px;height:26px;border-radius:8px;border:1px solid var(--line);
  background:var(--bg);color:var(--mut);font-size:16px;line-height:1;cursor:pointer}
.rm:active{background:var(--card)}
.row2{display:flex;align-items:center;gap:12px;margin-top:11px}
.price{font-family:'IBM Plex Mono',monospace;font-size:15px;font-weight:600}
.score{font-family:'IBM Plex Mono',monospace;font-size:13px;font-weight:600;
  min-width:42px;text-align:right}
.score.pos{color:var(--buy)} .score.neg{color:var(--sell)}
.bar{flex:1;height:6px;background:var(--bg);border-radius:99px;position:relative;
  overflow:hidden;border:1px solid var(--line)}
.bar::before{content:"";position:absolute;left:50%;top:0;bottom:0;width:1px;background:var(--line)}
.bar-pos{position:absolute;left:50%;top:0;bottom:0;background:linear-gradient(90deg,var(--gold-d),var(--buy));border-radius:99px}
.bar-neg{position:absolute;top:0;bottom:0;background:linear-gradient(90deg,var(--sell),var(--gold-d));border-radius:99px}
.levels{display:flex;gap:8px;flex-wrap:wrap;margin-top:11px}
.lv{font-family:'IBM Plex Mono',monospace;font-size:11px;padding:3px 9px;
  border-radius:7px;border:1px solid var(--line)}
.lv.tgt{color:var(--buy)} .lv.stp{color:var(--sell)} .lv.rr{color:var(--mut)}
.reasons{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px}
.chip{font-size:11px;color:var(--mut);background:var(--bg);border:1px solid var(--line);
  padding:3px 9px;border-radius:99px}
.rankline{font-family:'IBM Plex Mono',monospace;font-size:11px;color:var(--gold-d);margin-top:10px}
.empty{color:var(--mut);font-size:13px;padding:8px 2px}
#q,#myq{width:100%;font-family:'IBM Plex Mono',monospace;font-size:16px;color:var(--ink);
  background:var(--bg2);border:1px solid var(--line);border-radius:12px;padding:13px 15px;outline:none}
#q:focus,#myq:focus{border-color:var(--gold-d)}
#q::placeholder,#myq::placeholder{color:var(--mut)}
#results,#mylist{margin-top:12px}
.myadd{display:flex;gap:8px}
.myadd #myq{flex:1}
#myadd{flex:none;padding:0 18px;border-radius:12px;border:1px solid var(--gold-d);
  background:rgba(212,175,86,.12);color:var(--gold);font-weight:700;font-size:15px;cursor:pointer}
#myadd:active{background:rgba(212,175,86,.22)}
.mytools{display:flex;align-items:center;gap:10px;margin-top:12px;flex-wrap:wrap}
#mycopy{padding:8px 14px;border-radius:10px;border:1px solid var(--line);
  background:var(--bg2);color:var(--ink);font-size:13px;cursor:pointer}
#mycopy:active{background:var(--card)}
.mut{font-size:12px;color:var(--mut)}
footer{margin-top:38px;padding-top:18px;border-top:1px solid var(--line);
  font-size:11px;color:var(--mut);line-height:1.8}
footer b{color:var(--gold-d)}
"""

JS = r"""
const TOTAL = STOCKS.length;
function badge(g){
  const m = {BUY:['買','buy'], SELL:['売','sell'], HOLD:['待','hold']};
  const x = m[g] || m.HOLD;
  return '<span class="badge ' + x[1] + '">' + x[0] + '</span>';
}
function segTag(mk){
  const x = {prime:['P','p'], standard:['S','s'], growth:['G','g']}[mk];
  return x ? '<span class="seg ' + x[1] + '">' + x[0] + '</span>' : '';
}
function bar(sc){
  const p = Math.max(-100, Math.min(100, sc)) / 100;
  if (p >= 0) return '<span class="bar"><span class="bar-pos" style="width:' + (p*50) + '%"></span></span>';
  return '<span class="bar"><span class="bar-neg" style="width:' + (Math.abs(p)*50) + '%;margin-left:' + (50-Math.abs(p)*50) + '%"></span></span>';
}
function card(s, removable){
  const scls = s.sc >= 0 ? 'pos' : 'neg';
  let levels = '';
  if (s.t && s.st) {
    levels = '<div class="levels"><span class="lv tgt">利確 ¥' + s.t.toLocaleString() +
      '</span><span class="lv stp">損切 ¥' + s.st.toLocaleString() + '</span>' +
      (s.rr ? '<span class="lv rr">RR ' + s.rr + '</span>' : '') + '</div>';
  }
  let reasons = '';
  if (s.r && s.r.length) {
    reasons = '<div class="reasons">' + s.r.map(function(r){return '<span class="chip">' + r + '</span>';}).join('') + '</div>';
  }
  const rm = removable ? '<button class="rm" data-c="' + s.c + '">×</button>' : '';
  return '<div class="card">' +
    '<div class="row1"><span class="rank">' + s.rk + '</span>' +
    '<div class="title"><span class="code">' + s.c + '</span><span class="name">' + s.n + '</span>' + segTag(s.m) + '</div>' +
    badge(s.g) + rm + '</div>' +
    '<div class="row2"><span class="price" data-px="' + s.c + '">¥' + s.p.toLocaleString() + '</span>' +
    '<span class="score ' + scls + '">' + (s.sc >= 0 ? '+' : '') + s.sc + '</span>' + bar(s.sc) + '</div>' +
    levels + reasons +
    '<div class="rankline">スコア順 総合 ' + s.rk + ' 位 / ' + TOTAL + ' 銘柄中</div>' +
    '</div>';
}
function byCode(code){
  const v = code.toLowerCase();
  for (let i=0;i<STOCKS.length;i++){ if (STOCKS[i].c.toLowerCase() === v) return STOCKS[i]; }
  return null;
}

/* ---- 検索バー ---- */
const q = document.getElementById('q');
const results = document.getElementById('results');
const hint = document.getElementById('hint');
function run(){
  const v = q.value.trim().toLowerCase();
  if (!v) { results.innerHTML = ''; hint.style.display = ''; return; }
  hint.style.display = 'none';
  const m = STOCKS.filter(function(s){
    return s.c.toLowerCase().indexOf(v) === 0 || s.n.toLowerCase().indexOf(v) >= 0;
  }).sort(function(a,b){return b.sc - a.sc;}).slice(0, 5);
  results.innerHTML = m.length ? m.map(function(s){return card(s,false);}).join('')
    : '<p class="empty">該当する銘柄が見つかりません。コードや銘柄名を確認してください。</p>';
}
q.addEventListener('input', run);

/* ---- 株価の自動更新（市場時間中 約20分遅延） ---- */
function applyPrices(map){
  document.querySelectorAll('[data-px]').forEach(function(el){
    const c = el.getAttribute('data-px');
    if (map[c] != null){ el.textContent = '¥' + Number(map[c]).toLocaleString(); }
  });
  for (let i=0;i<STOCKS.length;i++){ if (map[STOCKS[i].c] != null) STOCKS[i].p = Math.round(map[STOCKS[i].c]); }
}
function refreshPrices(){
  fetch('prices.json?t=' + Date.now()).then(function(r){ return r.ok ? r.json() : null; })
    .then(function(d){
      if (!d || !d.px) return;
      applyPrices(d.px);
      const lab = document.getElementById('pxasof');
      if (lab && d.asof) lab.textContent = '株価 ' + d.asof + ' 時点（約20分遅延）';
      if (q.value.trim()) run();   // 検索結果も最新価格に
    }).catch(function(){});
}
refreshPrices();
setInterval(refreshPrices, 5 * 60 * 1000);
"""


def build_html(cfg: dict) -> tuple[str, dict]:
    now = datetime.now(JST)
    date_str = now.strftime("%Y.%m.%d %H:%M")
    top = int(cfg.get("dashboard_top", 10))

    analyses = R.analyze_universe(cfg)
    total = len(analyses)
    holds = load_holdings()
    analyses = R.apply_fundamentals(analyses, cfg,
                                    extra_codes=[h["code"] for h in holds])
    buys = analyses[:top]
    R.attach_barrier_stats(buys, cfg)

    mk = market_map(cfg)
    buy_sub = "TECH × FUNDAMENTAL" if any(b.combined is not None for b in buys) else "BUY SIGNALS"
    buy_cards = "".join(_card(i, a, True, mk.get(a.code, "")) for i, a in enumerate(buys, 1))

    amap = {a.code: a for a in analyses}
    hold_section = ""
    if holds:
        # 保有株の「利確勝率・想定保有日数」を日足から試算
        try:
            hframes = R.D.fetch_many([h["code"] for h in holds])
        except Exception:
            hframes = {}
        for h in holds:
            a = amap.get(h["code"])
            if a is None:
                continue
            tgt, stp = _holding_levels(h, a, cfg)
            a.bt = R.S.barrier_stats(hframes.get(h["code"]), a.price, tgt, stp)
        hc = "".join(_holding_card(h, amap.get(h["code"]), cfg) for h in holds)
        hold_section = _section("保有銘柄", "MY HOLDINGS", hc, "watch")

    index = []
    for rank, a in enumerate(analyses, 1):
        index.append({
            "c": _esc(a.code), "n": _esc(a.name), "g": a.signal,
            "sc": round(a.score), "p": round(a.price),
            "t": round(a.target) if a.target else None,
            "st": round(a.stop) if a.stop else None,
            "rr": a.rr, "r": [_esc(x) for x in (a.reasons or [])[:3]],
            "rk": rank, "m": mk.get(a.code, ""),
        })
    index_json = json.dumps(index, ensure_ascii=False, default=str).replace("</", "<\\/")

    data = {
        "generated": date_str, "total": total,
        "buys": [vars(a) for a in buys],
    }

    head = (
        '<!DOCTYPE html><html lang="ja"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
        '<title>株オラクル — Kabu Oracle</title>'
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        '<link href="https://fonts.googleapis.com/css2?family=Shippori+Mincho:wght@600;800'
        '&family=Zen+Kaku+Gothic+New:wght@400;500;700&family=IBM+Plex+Mono:wght@500;600'
        '&display=swap" rel="stylesheet">'
        '<link rel="manifest" href="manifest.json">'
        '<meta name="theme-color" content="#0c0e13">'
        '<meta name="mobile-web-app-capable" content="yes">'
        '<meta name="apple-mobile-web-app-capable" content="yes">'
        '<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">'
        '<meta name="apple-mobile-web-app-title" content="株オラクル">'
        '<link rel="apple-touch-icon" href="apple-touch-icon.png">'
        '<style>' + CSS + '</style></head>'
    )

    body = f'''<body><div class="wrap">
  <header>
    <div class="brand"><h1>株オラクル</h1><span class="en">Kabu Oracle</span></div>
    <div class="meta"><span>更新 <b>{date_str}</b> JST</span><span>分析 <b>{total}</b> 銘柄</span><span id="pxasof"></span></div>
  </header>

  <section id="search-sec">
    <h2 class="find"><span>銘柄サーチ</span><em>CODE SEARCH</em></h2>
    <input id="q" type="search" inputmode="text" autocomplete="off"
           placeholder="証券コード や 銘柄名（例: 7203 / トヨタ）">
    <div id="results" class="cards"></div>
    <p id="hint" class="empty">コードや銘柄名を入れると買い/売り判定・利確/損切・総合順位を表示します（最大5件）。</p>
  </section>

  {hold_section}

  {_section("買い候補 TOP10", buy_sub, buy_cards, "buy")}

  <footer>
    <b>免責</b>：本ページは自分用の分析補助であり投資助言ではありません。
    株価は約15〜20分遅延（yfinance）。シグナルは確率的で利益を保証しません。
    投資判断はご自身の責任で行ってください。<br>Generated by Kabu Oracle on GitHub Actions.
  </footer>
</div>'''

    script = "<script>\nconst STOCKS = " + index_json + ";\n" + JS + "\n</script>"
    page = head + body + script + "</body></html>"
    return page, data


def write_dashboard(cfg: dict) -> Path:
    DOCS.mkdir(exist_ok=True)
    page, data = build_html(cfg)
    (DOCS / "index.html").write_text(page, encoding="utf-8")
    (DOCS / "data.json").write_text(
        json.dumps(data, ensure_ascii=False, default=str, indent=2), encoding="utf-8")
    (DOCS / ".nojekyll").write_text("", encoding="utf-8")
    manifest = {
        "name": "株オラクル", "short_name": "株オラクル",
        "start_url": "./", "scope": "./", "display": "standalone",
        "background_color": "#0c0e13", "theme_color": "#0c0e13",
        "icons": [
            {"src": "icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "icon-512.png", "sizes": "512x512", "type": "image/png"},
        ],
    }
    (DOCS / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"ダッシュボード生成: {DOCS/'index.html'}")
    return DOCS / "index.html"


def write_prices(cfg: dict) -> dict:
    """表示中の買い/売りTOPと監視銘柄の最新株価だけを docs/prices.json に書く。"""
    import data as D
    DOCS.mkdir(exist_ok=True)
    codes: set[str] = {str(c).strip() for c in (cfg.get("watchlist") or [])}
    dj = DOCS / "data.json"
    if dj.exists():
        try:
            d = json.loads(dj.read_text(encoding="utf-8"))
            for key in ("buys", "sells"):
                for a in d.get(key, []):
                    if a.get("code"):
                        codes.add(str(a["code"]))
        except Exception:
            pass
    prices = D.fetch_last_prices(sorted(codes))
    now = datetime.now(JST).strftime("%H:%M")
    out = {"asof": now, "px": {k: round(v) for k, v in prices.items()}}
    (DOCS / "prices.json").write_text(
        json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"価格更新: {len(prices)} 件 @ {now} JST")
    return out


def check_holdings(cfg: dict) -> None:
    """holdings.txt の各銘柄を監視し、利確/損切ラインに到達したらLINE/メール通知。

    同じ到達は1日1回だけ通知（holdings_state.json で管理）。約15〜20分遅延。
    """
    import data as D
    import signals as S
    import notify as N
    from config import load_holdings, load_universe

    holds = load_holdings()
    if not holds:
        print("保有銘柄なし（holdings.txt）")
        return

    codes = [h["code"] for h in holds]
    px = D.fetch_last_prices(codes)            # 現在値（日中足・約20分遅延）
    frames = D.fetch_many(codes)               # ATR・シグナル用の日足
    bench_df = D.fetch_one(cfg.get("benchmark", "^N225"))
    bench = bench_df["Close"] if bench_df is not None else None
    namemap = {c: n for c, n in load_universe(
        {"universe_file": cfg.get("universe_file", "universe_all.csv"), "markets": "all"})}

    state_path = ROOT / "holdings_state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        state = {}
    today = datetime.now(JST).strftime("%Y-%m-%d")

    tp, sl, sg = [], [], []
    for h in holds:
        c = h["code"]
        buy = h.get("buy")
        cur = px.get(c)
        if cur is None:
            continue
        name = namemap.get(c, "")
        df = frames.get(c)
        atr = float(S.ind.atr(df, 14).iloc[-1]) if (df is not None and len(df) > 20) else 0.0
        tgt, stp = h.get("target"), h.get("stop")
        if tgt is None or stp is None:
            at, as_ = S.holding_levels(buy or cur, atr, cfg)
            tgt = at if tgt is None else tgt
            stp = as_ if stp is None else stp
        pl = ((cur - buy) / buy * 100) if buy else 0.0
        if tgt and cur >= tgt and state.get(f"{c}:tp") != today:
            tp.append(f"  {c} {name}  現在¥{cur:,.0f} / 買値¥{(buy or 0):,.0f} ({pl:+.1f}%)  利確¥{tgt:,.0f}")
            state[f"{c}:tp"] = today
        if stp and cur <= stp and state.get(f"{c}:sl") != today:
            sl.append(f"  {c} {name}  現在¥{cur:,.0f} / 買値¥{(buy or 0):,.0f} ({pl:+.1f}%)  損切¥{stp:,.0f}")
            state[f"{c}:sl"] = today
        # テクニカルの売りシグナル転換（価格ラインとは別の早期サイン）
        if df is not None and len(df) > 80:
            an = S.analyze(df, c, name, bench=bench, cfg=cfg)
            if an.error is None and an.signal == "SELL" and state.get(f"{c}:sg") != today:
                reason = an.reasons[0] if an.reasons else "下降サイン"
                sg.append(f"  {c} {name}  現在¥{cur:,.0f} ({pl:+.1f}%)  {reason}")
                state[f"{c}:sg"] = today

    if tp or sl or sg:
        now = datetime.now(JST).strftime("%H:%M")
        parts = [f"🔔 株オラクル｜保有アラート（{now} JST・約20分遅延）"]
        if tp:
            parts.append("✅ 利確の目安に到達")
            parts += tp
        if sl:
            parts.append("🛑 損切の目安に到達")
            parts += sl
        if sg:
            parts.append("⚠ 売りシグナルに転換（テクニカル）")
            parts += sg
        parts.append("※自分用の目安です。最終判断はご自身で。")
        msg = "\n".join(parts)
        print(msg)
        N.notify_all(cfg, "【株オラクル】保有アラート", msg)
    else:
        print("到達アラートなし")

    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
