/* ============================================================================
   株×FX 連動（クロスマーケット）— 4アプリ共通モジュール  v1
   ----------------------------------------------------------------------------
   株オラクル(日本株) / GLOBE ORACLE(米国株) / FX Navi(主要円ペア) / Carry Navi(金利差) は
   同じ GitHub Pages（kohei-omura.github.io）にあるので、お互いの公開JSONを読むだけで連携する。

     株高＋円安 → リスクオン      株安＋円高 → リスクオフ
     株安＋円安 / 株高＋円高 → 例外（金利が主役の相場）

   ※このファイルは4つのリポジトリに「同じ内容」を置く（変える時は4つとも差し替える）。
     判定の式は xmarket.py と同じ（tests/test_xmarket.py が一致を確認する）。
   ※読み取り専用。端末の保存領域・トークン・各アプリの判定や通知には一切触らない。
     読めないアプリがあっても、読めた分だけで表示する。
   使い方: <div id="xmarket" data-app="kabu|globe|fx|carry"></div> と <script src="xmarket.js" defer>
   ============================================================================ */
(function (root) {
  'use strict';

  /* ───────────── 判定（xmarket.py と同じ式・同じ文言） ───────────── */
  var STALE_MIN = { kabu: 5 * 1440, globe: 4 * 1440, fx: 90, carry: 180 };
  var TH = 0.25, US_FULL = 1.0, FX_FULL = 0.8, W_H4 = 0.6, W_H1 = 0.4, SOX_TH = 1.5, RATE_BP = 0.05;
  var US_W = [['sp', 0.4], ['nq', 0.4], ['dow', 0.2]];
  var JP_LABEL = { '上昇基調': 1, 'もみ合い': 0, '下落基調': -1 };
  var REGIMES = {
    on: [2, 'リスクオン', '☀', '株高＋円安：基本どおりの追い風。株も高金利通貨も買われやすい'],
    off: [-2, 'リスクオフ', '☔', '株安＋円高：安全資産の円が買われる局面。株もクロス円も下げやすい'],
    on_soft: [1, 'ややリスクオン', '🌤', '株は強いが、円は方向感なし'],
    off_soft: [-1, 'ややリスクオフ', '☁', '株は弱いが、円は方向感なし'],
    yen_weak: [1, '円安・株は様子見', '⛅', '円安が進行。株は方向感なし'],
    yen_strong: [-1, '円高・株は様子見', '⛅', '円高が進行。株は方向感なし'],
    flat: [0, '中立', '⛅', '株・為替とも方向感なし。連動より個別の材料で動きやすい'],
    rate_dn: [-1, '例外：株安＋円安', '⚠', '通常と逆の動き（金利・政策が主役の可能性）。株と為替の連動は当てにしない'],
    rate_up: [0, '例外：株高＋円高', '⚠', '通常と逆の動き。輸出株は円高が重荷、内需株が相対的に優位']
  };
  var RATE_DESC = {
    rate_dn: '金利上昇が主役（金利差で円安、株は金利高を嫌気）。株と為替の連動は当てにしない',
    rate_up: '米金利の低下が主役（金利差の縮小で円高、株は金利安を好感）。輸出株は上値が重くなりやすい'
  };
  var ADVICE = {
    kabu: {
      on: '輸出株（自動車・機械・電機）に追い風。買い候補は通常どおり',
      off: '全面安に警戒。新規買いは急がず、保有の損切ラインを確認',
      on_soft: '地合いは良好。為替の追い風は無いので銘柄の中身で選ぶ',
      off_soft: '地合いが弱い。新規買いは数量を控えめに',
      yen_weak: '円安は輸出株の支え。内需株より輸出株が相対的に優位',
      yen_strong: '円高は輸出株の重荷。内需株（小売・食品・通信）が相対的に優位',
      flat: '為替・地合いの影響は小さい。決算など個別の材料を重視',
      rate_dn: '円安でも輸出株が買われにくい局面。金利・政策の発表を確認',
      rate_up: '輸出株は上値が重く、内需・グロース株が優位になりやすい'
    },
    globe: {
      on: '米国株に追い風。円安で円建ての評価額は増えるが、新規のドル買いは割高',
      off: '株安＋円高で、円建ての評価額は二重に目減りしやすい。損切ラインを確認',
      on_soft: '株は堅調。為替の影響は小さい',
      off_soft: '株は軟調。新規買いは数量を控えめに',
      yen_weak: '円安で円建ての評価額は増える。新規のドル買いは割高',
      yen_strong: '円高で円建ての評価額は目減り。買い増し・ドル転には有利',
      flat: '為替の影響は小さい。銘柄の中身で判断',
      rate_dn: '金利上昇は株の重荷。高PERのグロース株は特に注意',
      rate_up: '株高でも、円建ての上昇は円高で削られやすい'
    },
    fx: {
      on: 'クロス円の買いシグナルは地合いと同じ向き。売りシグナルは逆風',
      off: 'クロス円の売りシグナルは地合いと同じ向き。買いシグナルは逆風',
      on_soft: '株は強く円売り方向がやや有利。ただし為替はまだ追随していない',
      off_soft: '株が弱く円買いが進みやすい。買いシグナルは慎重に',
      yen_weak: '円安トレンド。株の後押しは弱いので伸びを欲張らない',
      yen_strong: '円高トレンド。株の後押しは弱いので伸びを欲張らない',
      flat: '株・為替とも方向感なし。レンジ前提で無理に追わない',
      rate_dn: '金利が主役。株との連動は当てにせず、指標・要人発言の時間を確認',
      rate_up: '金利が主役。株との連動は当てにせず、指標・要人発言の時間を確認'
    },
    carry: {
      on: 'キャリー（金利差の買い持ち）に追い風',
      off: 'キャリーの巻き戻し（急落）に警戒。新規は見送りか数量を控えめに',
      on_soft: '株高はキャリーの支え。円の動きを確認',
      off_soft: '株安はキャリーの逆風になりやすい。円高への転換に注意',
      yen_weak: '円安はキャリーの追い風',
      yen_strong: '円高はキャリーの逆風。新規は数量を控えめに',
      flat: '大きな追い風・逆風なし。金利差とC/Rで判断',
      rate_dn: '株安＋円安は長続きしにくい。円高への急転（巻き戻し）に注意',
      rate_up: '円高方向。キャリーには逆風'
    }
  };
  var APPS = ['kabu', 'globe', 'fx', 'carry'];

  function isObj(v) { return v !== null && typeof v === 'object' && !Array.isArray(v); }
  function num(v) { return (typeof v === 'number' && isFinite(v)) ? v : null; }
  function clamp(x) { return Math.max(-1, Math.min(1, x)); }
  function r2(x) { return x == null ? null : Math.floor(x * 100 + 0.5) / 100; }
  function fx_(v, d, sign) {            /* 表示用の固定小数（Python側と1文字も違わないよう、丸めてから整形） */
    var k = Math.pow(10, d), neg = v < 0;
    v = Math.floor(Math.abs(v) * k + 0.5) / k;
    return (neg ? '-' : sign ? '+' : '') + v.toFixed(d);
  }
  function dir(v) { return v == null ? 0 : v >= TH ? 1 : v <= -TH ? -1 : 0; }

  /* '2026-11-15 10:55 JST' / '2026.11.15 10:43'（すべてJST）→ UNIXミリ秒 */
  function parseMs(s) {
    if (typeof s !== 'string') return null;
    var m = /^(\d{4})[-.](\d{2})[-.](\d{2})[ T](\d{2}):(\d{2})/.exec(s);
    if (!m) return null;
    var y = +m[1], mo = +m[2], d = +m[3], h = +m[4], mi = +m[5];
    if (y < 1 || mo < 1 || mo > 12 || d < 1 || h > 23 || mi > 59) return null;
    var t = new Date(Date.UTC(y, mo - 1, d, h, mi));
    if (t.getUTCMonth() !== mo - 1 || t.getUTCDate() !== d) return null;     /* 2/30 など実在しない日付 */
    return t.getTime() - 9 * 3600000;
  }
  function fresh(ts, key, nowMs) {
    var ms = parseMs(ts);
    return ms != null && (nowMs - ms) <= STALE_MIN[key] * 60000;
  }

  function normalize(kd, gp, fxs, cs, nowMs) {
    kd = isObj(kd) ? kd : null; gp = isObj(gp) ? gp : null; fxs = isObj(fxs) ? fxs : null; cs = isObj(cs) ? cs : null;
    var inp = { jp: null, us: null, fx: null, yen: null };

    var rg = kd && kd.regime;
    if (isObj(rg) && typeof rg.label === 'string' && Object.prototype.hasOwnProperty.call(JP_LABEL, rg.label) &&
        fresh(kd.generated, 'kabu', nowMs)) {
      inp.jp = { label: rg.label, chg1: num(rg.chg1), chg20: num(rg.chg20), price: num(rg.price) };
    }

    var idx = gp && gp.idx;
    if (isObj(idx) && fresh(gp.asof, 'globe', nowMs)) {
      var g = function (k, f) { var d = idx[k]; return isObj(d) ? num(d[f]) : null; };
      inp.us = { sp: g('SP500', 'chg'), nq: g('NASDAQ', 'chg'), dow: g('DOW', 'chg'), sox: g('SOX', 'chg'),
                 us10y: g('US10Y', 'price'), us10y_chg: g('US10Y', 'chg'),
                 usdjpy: g('USDJPY', 'price'), usdjpy_chg: g('USDJPY', 'chg') };
    }

    if (fxs && Array.isArray(fxs.pairs) && fresh(fxs.generated_at, 'fx', nowMs)) {
      var usd = null, cross = [], bid = null;
      fxs.pairs.forEach(function (p) {
        if (!isObj(p)) return;
        var sym = String(p.symbol || '');
        if (!/_JPY$/.test(sym)) return;
        var m = isObj(p.mtf) ? p.mtf : {};
        var h1 = num(m['1hour']), h4 = num(m['4hour']);
        if (h1 == null || h4 == null) return;
        var v = clamp(W_H4 * h4 + W_H1 * h1);
        if (sym === 'USD_JPY') { usd = v; bid = num(p.bid); } else cross.push(v);
      });
      if (usd != null || cross.length) {
        var cs_ = 0; cross.forEach(function (v) { cs_ += v; });
        inp.fx = { usd: usd, cross: cross.length ? cs_ / cross.length : null, bid: bid };
      }
    }

    var y = cs && isObj(cs.link) ? cs.link.yen : null;
    if (isObj(y) && num(y.score) != null && fresh(cs.generated_at, 'carry', nowMs)) {
      inp.yen = { score: num(y.score), level: typeof y.level === 'string' ? y.level : null };
    }
    return inp;
  }

  function judge(inp) {
    inp = inp || {};
    var jp = inp.jp, us = inp.us, fx = inp.fx, yen = inp.yen;

    /* 株：日経の地合い（中期）と米国株の前日比（直近）の平均 */
    var jpS = jp ? JP_LABEL[jp.label] : null, usM = null;
    if (us) {
      var s0 = 0, t0 = 0;
      US_W.forEach(function (kw) { if (us[kw[0]] != null) { s0 += us[kw[0]] * kw[1]; t0 += kw[1]; } });
      if (t0) usM = s0 / t0;
    }
    var usS = usM != null ? clamp(usM / US_FULL) : null;
    var parts = [jpS, usS].filter(function (v) { return v != null; });
    var S = parts.length ? (parts.length === 2 ? (parts[0] + parts[1]) / 2 : parts[0]) : null;

    /* 円（+＝円安）：ドル円とクロス円の上位足 → 無ければ Carry の円の強さ → 無ければドル円の前日比 */
    var Y = null, ysrc = null;
    if (fx && (fx.usd != null || fx.cross != null)) {
      Y = (fx.usd != null && fx.cross != null) ? 0.5 * fx.usd + 0.5 * fx.cross : (fx.usd != null ? fx.usd : fx.cross);
      ysrc = 'fx';
    } else if (yen && yen.score != null) { Y = -yen.score; ysrc = 'carry'; }
    else if (us && us.usdjpy_chg != null) { Y = us.usdjpy_chg / FX_FULL; ysrc = 'globe'; }
    if (Y != null) Y = clamp(Y);
    if (S == null && Y == null) return null;

    var s = dir(S), y = dir(Y), rate = 0;
    if (us && us.us10y != null && us.us10y_chg != null && us.us10y_chg > -100) {
      var bp = us.us10y - us.us10y / (1 + us.us10y_chg / 100);
      rate = bp >= RATE_BP ? 1 : bp <= -RATE_BP ? -1 : 0;
    }
    var code = (s > 0 && y > 0) ? 'on' : (s < 0 && y < 0) ? 'off' : (s < 0 && y > 0) ? 'rate_dn' : (s > 0 && y < 0) ? 'rate_up'
      : s > 0 ? 'on_soft' : s < 0 ? 'off_soft' : y > 0 ? 'yen_weak' : y < 0 ? 'yen_strong' : 'flat';
    var R = REGIMES[code], desc = R[3];
    if ((code === 'rate_dn' && rate > 0) || (code === 'rate_up' && rate < 0)) desc = RATE_DESC[code];

    var notes = [];
    if (fx && fx.usd != null && fx.cross != null) {
      var du = dir(fx.usd), dc = dir(fx.cross);
      if (du === 0 && dc < 0) notes.push('ドル円は横ばいでもクロス円は下落：ドルと円が同時に買われる形（リスク回避でよく出る）');
      else if (du > 0 && dc < 0) notes.push('ドル円だけ上昇：円安ではなくドル高が主役');
      else if (du < 0 && dc > 0) notes.push('ドル円だけ下落：円高ではなくドル安が主役');
    }
    if (yen && yen.level === 'warn') notes.push('Carry Navi の円の強さが警戒水準（' + fx_(yen.score, 2, true) + '）：円クロス全体で円が買われている');
    if (rate > 0) notes.push('米10年金利が上昇（' + fx_(us.us10y, 2) + '%）：日米金利差の拡大は円安方向、株には重荷');
    else if (rate < 0) notes.push('米10年金利が低下（' + fx_(us.us10y, 2) + '%）：金利差の縮小は円高方向、株には支え');
    if (us && us.sox != null) {
      if (us.sox >= SOX_TH) notes.push('半導体指数SOXが' + fx_(us.sox, 1, true) + '%：日米の半導体関連に追い風');
      else if (us.sox <= -SOX_TH) notes.push('半導体指数SOXが' + fx_(us.sox, 1, true) + '%：日米の半導体関連に逆風');
    }
    if (jp && usS != null && jpS * dir(usS) < 0) notes.push('日経の地合い（' + jp.label + '）と米国株の前日の動きが逆：日本固有の材料にも注意');

    var adv = {};
    APPS.forEach(function (a) { adv[a] = ADVICE[a][code]; });
    return {
      code: code, lv: R[0], label: R[1], icon: R[2], desc: desc,
      s: s, y: y, S: r2(S), Y: r2(Y), ysrc: ysrc, rate: rate,
      jp_s: r2(jpS), us_s: r2(usS), us_m: r2(usM),
      stock_txt: S == null ? '不明' : s > 0 ? '強い' : s < 0 ? '弱い' : '方向感なし',
      yen_txt: Y == null ? '不明' : y > 0 ? '円安' : y < 0 ? '円高' : '横ばい',
      notes: notes, advice: adv
    };
  }

  function line(app, r) {
    if (!r || !ADVICE[app]) return '';
    return '🌐 株×FX地合い: ' + r.icon + ' ' + r.label + '（株:' + r.stock_txt + '／円:' + r.yen_txt + '）\n  → ' + r.advice[app];
  }

  /* ───────────── 4アプリ合計：円高の影響・損益の税区分 ─────────────
     d = { kh, kp, gh, gp, fxs, cs, cp }（各アプリの公開JSON。無いものは null） */
  function rateOf(d) {
    var g = d.gp && isObj(d.gp.idx) && isObj(d.gp.idx.USDJPY) ? num(d.gp.idx.USDJPY.price) : null;
    if (g != null && g > 0) return g;
    var out = null;
    ((d.fxs && Array.isArray(d.fxs.pairs)) ? d.fxs.pairs : []).forEach(function (p) {
      if (isObj(p) && p.symbol === 'USD_JPY' && num(p.bid) > 0) out = num(p.bid);
    });
    return out;
  }
  function carryPx(cs, sym) {
    var out = null;
    ((cs && Array.isArray(cs.pairs)) ? cs.pairs : []).forEach(function (p) {
      if (isObj(p) && p.symbol === sym) out = { px: num(p.bid) != null ? num(p.bid) : num(p.price), qjpy: num(p.qjpy) != null ? num(p.qjpy) : 1 };
    });
    return out;
  }
  function nz(v) { v = Number(v); return isFinite(v) ? v : null; }   /* 保有ファイルは手入力由来なので文字列の数値も受ける */

  function portfolio(d) {
    d = d || {};
    var rate = rateOf(d);
    var o = { rate: rate,
      us: { n: 0, value: 0, pl: 0, plN: 0 },              /* 米国株（円換算） */
      jp: { n: 0, avg: null, pl: 0, plN: 0 },             /* 日本株（株数があれば実額、無ければ平均%） */
      fx: { n: 0, expo: 0, pl: 0, plN: 0 },               /* FX Navi（expo＝外貨の買い越し・円換算。売りはマイナス） */
      carry: { n: 0, expo: 0, pl: 0, plN: 0 } };          /* Carry Navi */
    var gpx = (d.gp && isObj(d.gp.px)) ? d.gp.px : {};
    ((d.gh && Array.isArray(d.gh.items)) ? d.gh.items : []).forEach(function (h) {
      if (!isObj(h)) return;
      var px = nz(gpx[h.c]); if (px == null) px = nz(h.p);
      var qty = nz(h.qty) || 0, buy = nz(h.buy);
      o.us.n++;
      if (rate && px != null && qty) {
        o.us.value += px * qty * rate;
        if (buy) { o.us.pl += (px - buy) * qty * rate; o.us.plN++; }
      }
    });
    var kpx = (d.kp && isObj(d.kp.px)) ? d.kp.px : {}, pcs = [];
    ((d.kh && Array.isArray(d.kh.items)) ? d.kh.items : []).forEach(function (h) {
      if (!isObj(h)) return;
      var px = nz(kpx[h.c]); if (px == null) px = nz(h.p);
      var qty = nz(h.qty) || 0, buy = nz(h.buy);
      o.jp.n++;
      if (px != null && buy) {
        pcs.push((px / buy - 1) * 100);
        if (qty) { o.jp.pl += (px - buy) * qty; o.jp.plN++; }
      }
    });
    if (pcs.length) { var t = 0; pcs.forEach(function (v) { t += v; }); o.jp.avg = t / pcs.length; }
    ((d.fxs && Array.isArray(d.fxs.open_positions)) ? d.fxs.open_positions : []).forEach(function (p) {
      if (!isObj(p) || !/_JPY$/.test(String(p.symbol || ''))) return;
      var px = nz(p.current); if (px == null) px = nz(p.entry);
      var lot = nz(p.lot) || 0, sg = p.side === 'short' ? -1 : 1;
      o.fx.n++;
      if (px != null && lot) o.fx.expo += sg * lot * px;
      if (nz(p.yen) != null) { o.fx.pl += nz(p.yen); o.fx.plN++; }
    });
    ((d.cp && Array.isArray(d.cp.positions)) ? d.cp.positions : []).forEach(function (q) {
      if (!isObj(q)) return;
      var sym = String(q.sym || q.symbol || ''), c = carryPx(d.cs, sym);
      var entry = nz(q.entry), lot = nz(q.lot) || 0, px = c && c.px != null ? c.px : entry;
      o.carry.n++;
      if (px == null || !lot) return;
      if (/_JPY$/.test(sym)) o.carry.expo += lot * 10000 * px;          /* AUD/NZD などは円の影響を直接は受けない */
      if (c && c.px != null && entry) { o.carry.pl += (c.px - entry) * lot * 10000 * c.qjpy; o.carry.plN++; }
    });
    /* 円が1%高くなった時の概算（外貨建ての持ち高 × 1%）。日本株は直接の為替損益が無いので含めない */
    o.stress = { us: -o.us.value * 0.01, fx: -o.fx.expo * 0.01, carry: -o.carry.expo * 0.01 };
    o.stress.total = o.stress.us + o.stress.fx + o.stress.carry;
    var legs = [o.stress.us, o.stress.fx, o.stress.carry].filter(function (v) { return Math.abs(v) >= 1; });
    o.stress.legs = legs.length;
    o.stress.stacked = legs.length >= 2 && legs.every(function (v) { return v < 0; });   /* 同じ向き（円安頼み）に重なっている */
    return o;
  }

  /* いま開いている市場（東証は平日の立会時間で判定。祝日は考慮しない） */
  function markets(d, nowMs) {
    d = d || {};
    var j = new Date(nowMs + 9 * 3600000), wd = j.getUTCDay(), hm = j.getUTCHours() * 60 + j.getUTCMinutes();
    var tokyo = wd >= 1 && wd <= 5 && ((hm >= 540 && hm < 690) || (hm >= 750 && hm < 930));
    var mk = d.gp && isObj(d.gp.mkt) ? d.gp.mkt : null, us = null;
    if (mk && num(mk.open_ms) != null && num(mk.close_ms) != null) us = nowMs >= mk.open_ms && nowMs < mk.close_ms;
    var fx = (d.fxs && typeof d.fxs.market_open === 'boolean' && fresh(d.fxs.generated_at, 'fx', nowMs)) ? d.fxs.market_open : null;
    if (fx == null) fx = !(wd === 0 || (wd === 6 && hm >= 420) || (wd === 1 && hm < 420));   /* 土曜7時〜月曜7時は休場 */
    var hint = tokyo ? '東証が開いています。株は場中（〜15:30）、FXは24時間いつでも'
      : fx ? '東証は時間外。いま値動きを追えるのはFX（平日24時間）' + (us ? '・米国株（取引中）' : '')
      : '株もFXも休場。来週に向けて地合いと保有の確認を';
    return { tokyo: tokyo, us: us, fx: fx, hint: hint };
  }

  var XM = { VERSION: '1', normalize: normalize, judge: judge, line: line, parseMs: parseMs,
             portfolio: portfolio, markets: markets };
  if (typeof module !== 'undefined' && module.exports) module.exports = XM;
  if (root) root.XMarket = XM;
  if (typeof document === 'undefined' || typeof window === 'undefined') return;

  /* ───────────── 画面（読み取り専用の1枚のカード） ───────────── */
  var HOST = /^(localhost|127\.0\.0\.1)$/.test(location.hostname) ? location.origin : 'https://kohei-omura.github.io';
  var URL_ = { kabu: HOST + '/kabu-oracle/', globe: HOST + '/GLOBE-ORACLE/', fx: HOST + '/FX-Signal-Notifier/', carry: HOST + '/Carry-Navi/' };
  var NAME = { kabu: '🇯🇵 株オラクル', globe: '🌎 GLOBE ORACLE', fx: '📊 FX Navi', carry: '💱 Carry Navi' };
  var SHORT = { kabu: ['🇯🇵', '株オラクル'], globe: ['🌎', 'GLOBE'], fx: ['📊', 'FX Navi'], carry: ['💱', 'Carry'] };
  var SUB = { kabu: '日本株', globe: '米国株', fx: '主要円ペア', carry: '金利差' };

  var CSS = '' +
    '.xm{--xf:var(--fg,var(--ink,#eef1f6));--xmu:var(--mut,#8b96a8);--xu:var(--buy,var(--up,#3ee6a0));--xd:var(--sell,var(--down,#ff6b81));' +
    '--xg:var(--gold,#d8b574);--xl:rgba(255,255,255,.09);--xl2:rgba(255,255,255,.16);--xc:rgba(255,255,255,.04);' +
    'display:flex;flex-direction:column;gap:10px;color:var(--xf);font-size:13px;line-height:1.6;text-align:left;min-width:0}' +
    '.xm *{box-sizing:border-box}' +
    '.xm-up{color:var(--xu)!important}.xm-dn{color:var(--xd)!important}.xm-mu{color:var(--xmu)}' +
    '.xm-h{display:flex;align-items:baseline;justify-content:space-between;gap:8px;margin:0}' +
    '.xm-h b{font-size:14px;font-weight:800;letter-spacing:.02em}.xm-h small{font-size:10.5px;color:var(--xmu);white-space:nowrap}' +
    '.xm-v{border:1px solid var(--xl2);border-radius:16px;padding:12px 14px;background:var(--xc)}' +
    '.xm-v.p{border-color:rgba(76,208,125,.45);background:linear-gradient(135deg,rgba(76,208,125,.13),var(--xc) 70%)}' +
    '.xm-v.n{border-color:rgba(255,107,134,.45);background:linear-gradient(135deg,rgba(255,107,134,.13),var(--xc) 70%)}' +
    '.xm-v.w{border-color:rgba(240,190,90,.5);background:linear-gradient(135deg,rgba(240,190,90,.12),var(--xc) 70%)}' +
    '.xm-vh{display:flex;align-items:center;gap:10px}.xm-ic{font-size:28px;line-height:1}' +
    '.xm-vt{flex:1;min-width:0;display:flex;flex-direction:column}.xm-vt small{font-size:10.5px;color:var(--xmu)}' +
    '.xm-vt b{font-size:19px;font-weight:800;letter-spacing:.02em}' +
    '.xm-ga{display:flex;gap:3px}.xm-ga span{width:9px;height:18px;border-radius:3px;background:rgba(255,255,255,.08)}' +
    '.xm-ga span.on.neg{background:var(--xd)}.xm-ga span.on.pos{background:var(--xu)}.xm-ga span.on.mid{background:var(--xg)}' +
    '.xm-d{margin:8px 0 0;font-size:13px}' +
    '.xm-ax{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:10px}' +
    '.xm-a{min-width:0}.xm-a small{display:flex;justify-content:space-between;font-size:10.5px;color:var(--xmu)}' +
    '.xm-a small b{color:var(--xf);font-weight:700}' +
    '.xm-tr{position:relative;height:6px;border-radius:3px;margin-top:4px;background:linear-gradient(90deg,rgba(255,107,134,.45),rgba(255,255,255,.1) 45%,rgba(255,255,255,.1) 55%,rgba(76,208,125,.45))}' +
    '.xm-tr i{position:absolute;top:-3px;width:4px;height:12px;margin-left:-2px;border-radius:2px;background:var(--xf);box-shadow:0 0 0 2px rgba(0,0,0,.35)}' +
    '.xm-adv{margin:10px 0 0;padding:8px 10px;border-radius:10px;background:rgba(255,255,255,.05);font-size:12.5px;font-weight:700}' +
    '.xm-adv small{display:block;font-size:10.5px;font-weight:400;color:var(--xmu)}' +
    '.xm-no{margin:8px 0 0;padding-left:18px;font-size:12px}' +
    '.xm-g{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:6px}' +
    '.xm-i{background:var(--xc);border:1px solid var(--xl);border-radius:12px;padding:8px 10px;min-width:0}' +
    '.xm-i div{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}' +
    '.xm-n{font-size:10px;color:var(--xmu)}.xm-p{font-size:14px;font-weight:800;font-variant-numeric:tabular-nums}' +
    '.xm-c{font-size:11.5px;font-weight:700;color:var(--xmu);font-variant-numeric:tabular-nums}' +
    '.xm-mk{display:flex;flex-wrap:wrap;gap:6px;align-items:center}' +
    '.xm-ch{display:inline-flex;align-items:center;gap:5px;padding:3px 9px;border-radius:999px;border:1px solid var(--xl);font-size:11px;color:var(--xmu);white-space:nowrap}' +
    '.xm-ch i{width:7px;height:7px;border-radius:50%;background:rgba(255,255,255,.22)}' +
    '.xm-ch.on{color:var(--xf);border-color:rgba(76,208,125,.45)}.xm-ch.on i{background:var(--xu);box-shadow:0 0 6px var(--xu)}' +
    '.xm-hint{margin:0;font-size:12px;color:var(--xmu)}' +
    '.xm-b{border:1px solid var(--xl);border-radius:12px;padding:10px 12px;background:var(--xc)}' +
    '.xm-b h4{margin:0 0 4px;font-size:12.5px;font-weight:800}.xm-b p{margin:0;font-size:12px;color:var(--xmu)}' +
    '.xm-b p b{color:var(--xf);font-variant-numeric:tabular-nums}' +
    '.xm-row{display:flex;justify-content:space-between;gap:8px;font-size:12px;padding:2px 0}' +
    '.xm-row span:first-child{color:var(--xmu);min-width:0}.xm-row b{font-variant-numeric:tabular-nums;white-space:nowrap}' +
    '.xm-tot{border-top:1px solid var(--xl);margin-top:4px;padding-top:5px;font-weight:700}' +
    '.xm-w{margin-top:6px!important;color:var(--xg)!important;font-weight:700}' +
    '.xm-gd{border:1px solid var(--xl);border-radius:12px;background:var(--xc)}' +
    '.xm-gd summary{cursor:pointer;padding:9px 12px;font-size:12.5px;font-weight:700;list-style:none;display:flex;justify-content:space-between}' +
    '.xm-gd summary::-webkit-details-marker{display:none}.xm-gd summary:after{content:"＋";color:var(--xmu)}.xm-gd[open] summary:after{content:"−"}' +
    '.xm-gb{padding:0 12px 10px;font-size:12px}.xm-gb h5{margin:8px 0 2px;font-size:12px;font-weight:800;color:var(--xg)}' +
    '.xm-gb ul{margin:0;padding-left:18px}.xm-gb table{width:100%;border-collapse:collapse;font-size:11.5px}' +
    '.xm-gb td,.xm-gb th{border-bottom:1px solid var(--xl);padding:4px 6px 4px 0;text-align:left;vertical-align:top}' +
    '.xm-gb th{color:var(--xmu);font-weight:400;white-space:nowrap}' +
    '.xm-apps{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:6px}' +
    '.xm-app{display:flex;flex-direction:column;align-items:center;gap:1px;padding:7px 2px;border-radius:12px;border:1px solid var(--xl);' +
    'background:var(--xc);color:var(--xf);text-decoration:none;font-size:11px;font-weight:700;text-align:center;min-width:0;-webkit-tap-highlight-color:transparent}' +
    '.xm-app i{font-style:normal;font-size:16px;line-height:1.2}.xm-app small{font-size:9.5px;font-weight:400;color:var(--xmu)}.xm-app span{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%}' +
    '.xm-app.me{border-color:var(--xg);color:var(--xg)}.xm-app:active{transform:scale(.97)}' +
    '.xm-src{margin:0;font-size:10.5px;color:var(--xmu)}' +
    '@media (max-width:360px){.xm-g{grid-template-columns:repeat(2,minmax(0,1fr))}.xm-apps{grid-template-columns:repeat(2,minmax(0,1fr))}}';

  function esc(x) {
    return String(x == null ? '' : x).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function sg(v, d) { return (v > 0 ? '+' : v < 0 ? '−' : '±') + Math.abs(v).toFixed(d == null ? 1 : d); }
  function pc(v, d) { return v == null ? '—' : sg(v, d) + '%'; }
  function ud(v) { return v == null ? '' : v > 0 ? ' xm-up' : v < 0 ? ' xm-dn' : ''; }
  function yen(v) { return (v > 0 ? '+' : v < 0 ? '−' : '') + '¥' + Math.round(Math.abs(v)).toLocaleString(); }
  function tile(n, p, c, cls) {
    return '<div class="xm-i"><div class="xm-n">' + n + '</div><div class="xm-p">' + p + '</div><div class="xm-c' + (cls || '') + '">' + c + '</div></div>';
  }
  function track(v) {           /* −1〜+1 を 0〜100% の位置に */
    var pos = v == null ? 50 : (clamp(v) + 1) * 50;
    return '<div class="xm-tr"><i style="left:' + pos.toFixed(1) + '%"></i></div>';
  }
  function trendTxt(v) { return v == null ? '—' : v >= TH ? '↑ 上昇' : v <= -TH ? '↓ 下落' : '→ 横ばい'; }

  var GUIDE = '<details class="xm-gd"><summary>株とFXの使い分け・守るルール</summary><div class="xm-gb">' +
    '<h5>基本の連動</h5><ul><li>リスクオン：株高＋円安（高金利通貨が買われる）</li><li>リスクオフ：株安＋円高（安全資産の円が買われる）</li>' +
    '<li>円安 → 輸出企業の業績期待で日本株は上がりやすい／円高 → 下がりやすい</li>' +
    '<li>例外：FRB・日銀の急な利上げ／利下げ局面は金利差が優先され、「株安＋円安」など逆の動きになる</li></ul>' +
    '<h5>どちらを使うか</h5><table><tr><th></th><th>株</th><th>FX</th></tr>' +
    '<tr><th>ねらい</th><td>中長期の成長・配当・優待</td><td>短期の値幅・毎日のスワップ</td></tr>' +
    '<tr><th>時間</th><td>平日 9:00〜15:30</td><td>平日ほぼ24時間（夜・早朝も）</td></tr>' +
    '<tr><th>レバレッジ</th><td>現物1倍〜信用 約3.3倍</td><td>最大25倍（少額から・その分リスク大）</td></tr>' +
    '<tr><th>下落局面</th><td>基本は様子見</td><td>売りから入れる</td></tr>' +
    '<tr><th>制度</th><td>NISA（非課税）が使える</td><td>NISA対象外</td></tr></table>' +
    '<h5>併用のルール</h5><ul><li>株を買う前にドル円のトレンド、FXの前に日経平均・米国株の地合いを確認（上の判定がその役目）</li>' +
    '<li>株の損失とFXの利益（その逆も）は損益通算できない。株とFXは別々に勝ち負けを管理する</li>' +
    '<li>米国株とクロス円の買いは、どちらも「円高で損」。同じ向きに重ねすぎない</li></ul></div></details>';

  function render(box, self, d, nowMs) {
    var inp = normalize(d.kd, d.gp, d.fxs, d.cs, nowMs), j = judge(inp);
    var pf = portfolio(d), mk = markets(d, nowMs), h = '';

    if (j) {
      var cls = j.code.indexOf('rate_') === 0 ? 'w' : j.lv > 0 ? 'p' : j.lv < 0 ? 'n' : '';
      var ga = '';
      for (var i = -2; i <= 2; i++) ga += '<span class="' + (i === j.lv ? 'on ' : '') + (i < 0 ? 'neg' : i > 0 ? 'pos' : 'mid') + '"></span>';
      h += '<div class="xm-v ' + cls + '"><div class="xm-vh"><span class="xm-ic">' + j.icon + '</span>' +
        '<span class="xm-vt"><small>株の地合い × 円の方向</small><b>' + esc(j.label) + '</b></span>' +
        '<div class="xm-ga" aria-hidden="true">' + ga + '</div></div>' +
        '<p class="xm-d">' + esc(j.desc) + '</p>' +
        '<div class="xm-ax">' +
        '<div class="xm-a"><small><span>株安</span><b>株 ' + esc(j.stock_txt) + '</b><span>株高</span></small>' + track(j.S) + '</div>' +
        '<div class="xm-a"><small><span>円高</span><b>' + esc(j.yen_txt) + '</b><span>円安</span></small>' + track(j.Y) + '</div></div>' +
        '<p class="xm-adv"><small>' + esc(NAME[self] || '') + ' では</small>' + esc(j.advice[self] || '') + '</p>' +
        (j.notes.length ? '<ul class="xm-no">' + j.notes.map(function (n) { return '<li>' + esc(n) + '</li>'; }).join('') + '</ul>' : '') +
        '</div>';
    } else {
      h += '<p class="xm-hint">株・為替のデータをまだ読み込めていません（各アプリの更新待ち）。</p>';
    }

    /* 判定の材料 */
    var jp = inp.jp, us = inp.us, fx = inp.fx, yn = inp.yen, g = '';
    g += jp ? tile('日経平均', esc(jp.label), '20日 ' + pc(jp.chg20), ud(jp.chg20)) : tile('日経平均', '—', '未取得', '');
    g += us ? tile('S&amp;P500 前日比', pc(us.sp, 2), 'NQ ' + pc(us.nq, 2), ud(us.nq)) : tile('S&amp;P500', '—', '未取得', '');
    var dpx = fx && fx.bid != null ? fx.bid : us && us.usdjpy != null ? us.usdjpy : null;
    g += tile('ドル円', dpx == null ? '—' : '¥' + dpx.toFixed(2),
              fx && fx.usd != null ? trendTxt(fx.usd) : us && us.usdjpy_chg != null ? '前日 ' + pc(us.usdjpy_chg, 2) : '未取得',
              fx && fx.usd != null ? ud(dir(fx.usd)) : '');
    g += tile('クロス円', fx && fx.cross != null ? trendTxt(fx.cross) : '—',
              fx && fx.cross != null ? (dir(fx.cross) < 0 ? '円高方向' : dir(fx.cross) > 0 ? '円安方向' : '方向感なし') : '未取得',
              fx && fx.cross != null ? ud(dir(fx.cross)) : '');
    g += tile('円の強さ', yn ? sg(yn.score, 2) : '—',
              yn ? (yn.score >= TH ? '円高' : yn.score <= -TH ? '円安' : '中立') : '未取得',
              yn ? ud(-dir(yn.score)) : '');
    g += tile('米10年金利', us && us.us10y != null ? us.us10y.toFixed(2) + '%' : '—',
              j && j.rate > 0 ? '上昇' : j && j.rate < 0 ? '低下' : us && us.us10y != null ? '横ばい' : '未取得', '');
    h += '<div class="xm-g">' + g + '</div>' +
      '<p class="xm-src">ドル円・クロス円（ユーロ／ポンド／豪ドル）は FX Navi の1時間足・4時間足の向き。円の強さは Carry Navi の指数（+＝円高）。</p>';

    /* いま開いている市場 */
    function chip(n, on) { return '<span class="xm-ch' + (on ? ' on' : '') + '"><i></i>' + n + ' ' + (on ? '取引中' : '時間外') + '</span>'; }
    h += '<div class="xm-mk">' + chip('東証', mk.tokyo) + (mk.us == null ? '' : chip('米国株', mk.us)) + chip('FX', mk.fx) + '</div>' +
      '<p class="xm-hint">' + esc(mk.hint) + '</p>';

    /* 円高の影響（4アプリ合計） */
    var st = pf.stress, rows = '';
    if (Math.abs(st.us) >= 1) rows += '<div class="xm-row"><span>🌎 米国株（円換算 ¥' + Math.round(pf.us.value).toLocaleString() + '）</span><b class="' + ud(st.us).trim() + '">' + yen(st.us) + '</b></div>';
    if (Math.abs(st.fx) >= 1) rows += '<div class="xm-row"><span>📊 FX Navi の保有 ' + pf.fx.n + '件</span><b class="' + ud(st.fx).trim() + '">' + yen(st.fx) + '</b></div>';
    if (Math.abs(st.carry) >= 1) rows += '<div class="xm-row"><span>💱 Carry Navi の保有 ' + pf.carry.n + '件</span><b class="' + ud(st.carry).trim() + '">' + yen(st.carry) + '</b></div>';
    h += '<div class="xm-b"><h4>円が1%高くなったら' + (pf.rate ? '（ドル円で約' + (pf.rate * 0.01).toFixed(1) + '円）' : '') + '</h4>' +
      (rows ? rows + '<div class="xm-row xm-tot"><span>4アプリ合計の概算</span><b class="' + ud(st.total).trim() + '">' + yen(st.total) + '</b></div>' +
              (st.stacked ? '<p class="xm-w">⚠ 外貨建ての保有が同じ向き（円安頼み）に重なっています。円高の局面では同時に減ります。</p>' : '') +
              (pf.jp.n ? '<p>日本株 ' + pf.jp.n + '銘柄は直接の為替損益なし（輸出株は円高で業績面の逆風）。</p>' : '')
            : '<p>外貨建ての保有（米国株・FX・Carry）はありません。' + (pf.jp.n ? '日本株 ' + pf.jp.n + '銘柄は直接の為替損益なし。' : '') + '</p>') +
      '</div>';

    /* 損益は税の区分ごと（通算できない） */
    var sRows = '', fRows = '';
    if (pf.jp.n) sRows += '<div class="xm-row"><span>🇯🇵 日本株 ' + pf.jp.n + '銘柄</span><b class="' + ud(pf.jp.plN ? pf.jp.pl : pf.jp.avg).trim() + '">' +
      (pf.jp.plN ? yen(pf.jp.pl) : pf.jp.avg == null ? '—' : '平均 ' + pc(pf.jp.avg)) + '</b></div>';
    if (pf.us.n) sRows += '<div class="xm-row"><span>🌎 米国株 ' + pf.us.n + '銘柄</span><b class="' + ud(pf.us.plN ? pf.us.pl : null).trim() + '">' +
      (pf.us.plN ? yen(pf.us.pl) : '—') + '</b></div>';
    if (pf.fx.n) fRows += '<div class="xm-row"><span>📊 FX Navi ' + pf.fx.n + '件</span><b class="' + ud(pf.fx.plN ? pf.fx.pl : null).trim() + '">' + (pf.fx.plN ? yen(pf.fx.pl) : '—') + '</b></div>';
    if (pf.carry.n) fRows += '<div class="xm-row"><span>💱 Carry Navi ' + pf.carry.n + '件</span><b class="' + ud(pf.carry.plN ? pf.carry.pl : null).trim() + '">' + (pf.carry.plN ? yen(pf.carry.pl) : '—') + '</b></div>';
    h += '<div class="xm-b"><h4>評価損益（税の区分ごと）</h4>' +
      '<p>① 株（上場株式等）</p>' + (sRows || '<div class="xm-row"><span>保有なし</span><b>—</b></div>') +
      '<p style="margin-top:6px">② FX（先物取引に係る雑所得等）</p>' + (fRows || '<div class="xm-row"><span>保有なし</span><b>—</b></div>') +
      '<p style="margin-top:6px">①と②は損益通算できません（株の損でFXの利益は減らせない）。それぞれで勝ち負けを管理します。</p></div>';

    h += GUIDE;
    h += '<div class="xm-apps">' + APPS.map(function (a) {
      return '<a class="xm-app' + (a === self ? ' me' : '') + '" href="' + URL_[a] + '"' + (a === self ? ' aria-current="page"' : '') + ' aria-label="' + NAME[a] + '"><i>' + SHORT[a][0] + '</i><span>' + SHORT[a][1] + '</span><small>' + SUB[a] + '</small></a>';
    }).join('') + '</div>';

    var src = [];
    if (d.kd && d.kd.generated) src.push('日本株 ' + esc(String(d.kd.generated).slice(5, 16)));
    if (d.gp && d.gp.asof) src.push('米国株 ' + esc(String(d.gp.asof).slice(5, 16)));
    if (d.fxs && d.fxs.generated_at) src.push('FX ' + esc(String(d.fxs.generated_at).slice(5, 16)));
    if (d.cs && d.cs.generated_at) src.push('Carry ' + esc(String(d.cs.generated_at).slice(5, 16)));
    h += '<p class="xm-src">' + (src.length ? src.join('・') + ' 時点。' : '') + '判定は目安で、各アプリのシグナルや利確・損切は変えません。投資判断は自己責任で。</p>';

    box.innerHTML = '<div class="xm">' + h + '</div>';
    try {
      box.dispatchEvent(new CustomEvent('xmarket', { bubbles: true, detail: { judge: j, portfolio: pf, markets: mk } }));
    } catch (e) {}
    var cnt = document.getElementById('xmcnt');           /* 見出しの横に結果を出す場所（あるアプリだけ） */
    if (cnt && j) cnt.textContent = j.icon + ' ' + j.label;
  }

  function get(u) {
    return fetch(u + '?t=' + Date.now(), { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; });
  }
  function start() {
    var box = document.getElementById('xmarket');
    if (!box || !window.fetch) return;
    var self = box.getAttribute('data-app') || '';
    if (!document.getElementById('xm-style')) {
      var st = document.createElement('style'); st.id = 'xm-style'; st.textContent = CSS; document.head.appendChild(st);
    }
    var busy = false;
    function load() {
      if (busy) return; busy = true;
      Promise.all([get(URL_.kabu + 'data.json'), get(URL_.kabu + 'holdings.json'), get(URL_.kabu + 'prices.json'),
                   get(URL_.globe + 'prices.json'), get(URL_.globe + 'holdings.json'),
                   get(URL_.fx + 'data/status.json'), get(URL_.carry + 'carry_status.json'), get(URL_.carry + 'carry_positions.json')])
        .then(function (a) {
          render(box, self, { kd: a[0], kh: a[1], kp: a[2], gp: a[3], gh: a[4], fxs: a[5], cs: a[6], cp: a[7] }, Date.now());
        })
        .catch(function (e) { try { console.warn('[xmarket] 表示に失敗', e); } catch (_) {} })
        .then(function () { busy = false; });
    }
    load();
    setInterval(function () { if (!document.hidden) load(); }, 5 * 60 * 1000);
    document.addEventListener('visibilitychange', function () { if (!document.hidden) load(); });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this));
