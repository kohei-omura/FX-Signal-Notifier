#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FX Signal & Position Navigator  — スキャル/デイトレ用 加重スコア版
-----------------------------------------------------------------
シグナル = テクニカル90% + ファンダ10% の加重スコアで判定。
  テクニカル: EMAクロス / MACD / RSI / ボリンジャー（ADXでトレンド強度を加味）
  ファンダ : 金利差キャリーの方向バイアス（円ペアは円が低金利→買い寄り・調整可）
モード: scalp(1分足・狭いTP/SL) / day(5分足・やや広め)
GMOコイン外国為替FX Public API + GitHub Actions。LINE/メール通知 + ダッシュボード(status.json)。

⚠️ スコアは判断補助で、未来の最適値・利益を保証しません。売買・損益は自己責任です。
"""

import os, sys, json, math, smtplib, datetime
from email.mime.text import MIMEText
from email.utils import formatdate
from zoneinfo import ZoneInfo
import requests

JST = ZoneInfo("Asia/Tokyo")
SYMBOLS = ["USD_JPY", "EUR_JPY", "GBP_JPY", "AUD_JPY"]
PRICE_TYPE = "BID"

# ===== モード（scalp / day）。GitHub Secrets/Variables の MODE で上書き可 =====
MODE = os.environ.get("MODE", "scalp").lower()
PARAMS = {
    "scalp": {"interval":"1min","ema_f":5,"ema_s":13,"rsi":7,"macd":(6,13,5),
              "bb":(20,2.0),"adx":14,"atr":14,"th":0.35},
    "day":   {"interval":"5min","ema_f":9,"ema_s":21,"rsi":14,"macd":(12,26,9),
              "bb":(20,2.0),"adx":14,"atr":14,"th":0.40},
}
P = PARAMS.get(MODE, PARAMS["scalp"])

# テクニカル内訳の重み（合計1.0）。EMA/MACDはADX強度で減衰
W_EMA, W_MACD, W_RSI, W_BB = 0.35, 0.25, 0.20, 0.20
TECH_W, FUND_W = 0.9, 0.1

# ファンダ(10%)：金利差キャリーの方向バイアス [-1,1]（円ペアは円が低金利→買い寄り）
# 現在の政策金利差に合わせて調整可。
FUND_BIAS = {"USD_JPY":0.5, "EUR_JPY":0.4, "GBP_JPY":0.5, "AUD_JPY":0.4}

# ===== TP/SL（Pattern1：ATR基準）=====
# SL = エントリー時ATR × SL_ATR_MULT（そのままpips）／ TP = SL × TP_SL_RATIO
SL_ATR_MULT = 1.0     # SL = ATR×1.0
TP_SL_RATIO = 1.5     # TP = SL×1.5（リスクリワード 1:1.5）

# 重要指標の前後はシグナル抑制（任意）。例: ["2026-06-10 21:30"]（JST）
NEWS_BLACKOUT = []
BLACKOUT_MIN = 15

PIP_SIZE = 0.01
DEFAULT_LOT = 10000
POSITIONS_FILE = "positions.json"
STATUS_FILE = "status.json"
CHART_POINTS = 60
BASE = "https://forex-api.coin.z.com/public/v1"
_OHLC_CACHE = {}


# ---------------- データ取得 ----------------
def get_ohlc(symbol):
    if symbol in _OHLC_CACHE:
        return _OHLC_CACHE[symbol]
    need = max(P["ema_s"], P["macd"][1], P["adx"]*2, P["atr"]) + CHART_POINTS + 30
    today = datetime.datetime.now(JST).date()
    rows = {}
    for back in range(0, 7):
        d = today - datetime.timedelta(days=back)
        try:
            j = requests.get(f"{BASE}/klines", timeout=15, params={
                "symbol": symbol, "priceType": PRICE_TYPE,
                "interval": P["interval"], "date": d.strftime("%Y%m%d")}).json()
            if j.get("status") == 0:
                for k in j.get("data", []):
                    rows[int(k["openTime"])] = (float(k["high"]), float(k["low"]), float(k["close"]))
        except Exception as e:
            print(f"[WARN] {symbol} klines失敗: {e}", file=sys.stderr)
        if len(rows) >= need:
            break
    out = [rows[t] for t in sorted(rows)]
    _OHLC_CACHE[symbol] = out
    return out


def fetch_ticker():
    out = {}
    try:
        for d in requests.get(f"{BASE}/ticker", timeout=10).json().get("data", []):
            out[d["symbol"]] = {"bid": float(d["bid"]), "ask": float(d["ask"])}
    except Exception as e:
        print(f"[WARN] ticker失敗: {e}", file=sys.stderr)
    return out


def market_is_open():
    try:
        return requests.get(f"{BASE}/status", timeout=10).json().get("data", {}).get("status") == "OPEN"
    except Exception:
        return True


# ---------------- 指標 ----------------
def clamp(x, lo=-1.0, hi=1.0):
    return max(lo, min(hi, x))


def ema_series(v, p):
    if len(v) < p:
        return [None]*len(v)
    k = 2/(p+1); out = [None]*len(v); e = sum(v[:p])/p; out[p-1] = e
    for i in range(p, len(v)):
        e = v[i]*k + e*(1-k); out[i] = e
    return out


def ema(v, p):
    s = ema_series(v, p)
    return s[-1] if s else None


def rsi(v, p):
    if len(v) < p+1:
        return None
    d = [v[i]-v[i-1] for i in range(1, len(v))]
    g = [max(x, 0.0) for x in d]; l = [max(-x, 0.0) for x in d]
    ag, al = sum(g[:p])/p, sum(l[:p])/p
    for i in range(p, len(d)):
        ag = (ag*(p-1)+g[i])/p; al = (al*(p-1)+l[i])/p
    return 100.0 if al == 0 else 100.0 - 100.0/(1.0+ag/al)


def macd(v, f, s, sig):
    ef, es = ema_series(v, f), ema_series(v, s)
    ml = [ (ef[i]-es[i]) if (ef[i] is not None and es[i] is not None) else None for i in range(len(v))]
    vals = [m for m in ml if m is not None]
    if len(vals) < sig+1:
        return None
    ss = ema_series(vals, sig)
    if ss[-1] is None or ss[-2] is None:
        return None
    hist = vals[-1]-ss[-1]; hist_prev = vals[-2]-ss[-2]
    return vals[-1], ss[-1], hist, hist_prev


def bollinger(v, p, k):
    if len(v) < p:
        return None
    w = v[-p:]; mid = sum(w)/p
    sd = (sum((x-mid)**2 for x in w)/p) ** 0.5
    return mid, mid+k*sd, mid-k*sd, sd


def atr(ohlc, p):
    if len(ohlc) < p+1:
        return None
    trs = []
    for i in range(1, len(ohlc)):
        h, l, _ = ohlc[i]; pc = ohlc[i-1][2]
        trs.append(max(h-l, abs(h-pc), abs(l-pc)))
    a = sum(trs[:p])/p
    for i in range(p, len(trs)):
        a = (a*(p-1)+trs[i])/p
    return a


def adx(ohlc, p):
    if len(ohlc) < 2*p+1:
        return None
    pdm, mdm, tr = [], [], []
    for i in range(1, len(ohlc)):
        h, l, c = ohlc[i]; ph, pl, pc = ohlc[i-1]
        up, dn = h-ph, pl-l
        pdm.append(up if (up > dn and up > 0) else 0.0)
        mdm.append(dn if (dn > up and dn > 0) else 0.0)
        tr.append(max(h-l, abs(h-pc), abs(l-pc)))
    def wilder(a):
        s = sum(a[:p]); out = [s]
        for i in range(p, len(a)):
            s = s - s/p + a[i]; out.append(s)
        return out
    atrs, pds, mds = wilder(tr), wilder(pdm), wilder(mdm)
    pdi = [100*pds[i]/atrs[i] if atrs[i] else 0 for i in range(len(atrs))]
    mdi = [100*mds[i]/atrs[i] if atrs[i] else 0 for i in range(len(atrs))]
    dx = [100*abs(pdi[i]-mdi[i])/((pdi[i]+mdi[i]) or 1) for i in range(len(pdi))]
    if len(dx) < p:
        av = sum(dx)/len(dx)
    else:
        av = sum(dx[:p])/p
        for i in range(p, len(dx)):
            av = (av*(p-1)+dx[i])/p
    return av, pdi[-1], mdi[-1]


def suggest_tp_sl(a):
    """Pattern1: SL=ATR×SL_ATR_MULT(pips), TP=SL×TP_SL_RATIO"""
    sl_pips = a * SL_ATR_MULT / PIP_SIZE
    tp_pips = sl_pips * TP_SL_RATIO
    return (round(tp_pips, 1), round(sl_pips, 1))


# ---------------- 加重スコア（テク90/ファンダ10） ----------------
def score_pair(symbol, ohlc):
    closes = [r[2] for r in ohlc]
    if len(closes) < max(P["ema_s"], P["macd"][1], P["adx"]*2) + 2:
        return None
    price = closes[-1]
    ef, es = ema(closes, P["ema_f"]), ema(closes, P["ema_s"])
    rv = rsi(closes, P["rsi"])
    md = macd(closes, *P["macd"])
    bb = bollinger(closes, P["bb"][0], P["bb"][1])
    a = atr(ohlc, P["atr"])
    ax = adx(ohlc, P["adx"])
    if None in (ef, es, rv, a) or md is None or bb is None or ax is None:
        return None
    adx_val, pdi, mdi = ax
    adxf = clamp(adx_val/40.0, 0.0, 1.0)        # トレンド強度 0..1
    adxf = max(adxf, 0.25)                       # 弱トレンドでも最低限

    ema_sig = clamp((ef-es)/(a if a else 1e-9))               # EMA方向（ATR正規化）
    macd_sig = clamp(md[2]/(0.6*a if a else 1e-9))            # MACDヒスト/ATR
    if md[2] > md[3]: macd_sig = clamp(macd_sig+0.1)          # ヒスト上昇=加点
    elif md[2] < md[3]: macd_sig = clamp(macd_sig-0.1)
    rsi_sig = clamp((rv-50)/50.0)                             # 50中心
    bb_sig = clamp((price-bb[0])/(P["bb"][1]*bb[3] if bb[3] else 1e-9))  # 中心線からの位置

    tech = (W_EMA*ema_sig*adxf + W_MACD*macd_sig*adxf + W_RSI*rsi_sig + W_BB*bb_sig)
    tech = clamp(tech)
    fund = clamp(FUND_BIAS.get(symbol, 0.0))
    total = clamp(TECH_W*tech + FUND_W*fund)

    reasons = []
    if abs(ema_sig) > 0.2: reasons.append(f"EMA{'上' if ema_sig>0 else '下'}({P['ema_f']}vs{P['ema_s']})")
    if abs(macd_sig) > 0.2: reasons.append(f"MACDヒスト{'+' if md[2]>0 else '-'}")
    reasons.append(f"RSI{rv:.0f}")
    reasons.append(f"ADX{adx_val:.0f}{'(強)' if adx_val>=25 else '(弱)'}")

    th = P["th"]
    side = "買い" if total >= th else ("売り" if total <= -th else None)
    tp_pips, sl_pips = suggest_tp_sl(a)
    return {"price":price, "ef":ef, "es":es, "rsi":round(rv,1), "adx":round(adx_val,1),
            "atr":round(a,4), "tp_pips":tp_pips, "sl_pips":sl_pips,
            "tech":round(tech,3), "fund":round(fund,3), "score":round(total,3),
            "side":side, "reasons":reasons,
            "ef_series":[round(x,3) if x is not None else None for x in ema_series(closes,P["ema_f"])[-CHART_POINTS:]],
            "es_series":[round(x,3) if x is not None else None for x in ema_series(closes,P["ema_s"])[-CHART_POINTS:]],
            "closes":[round(c,3) for c in closes[-CHART_POINTS:]]}


def in_blackout():
    now = datetime.datetime.now(JST).replace(tzinfo=None)
    for s in NEWS_BLACKOUT:
        try:
            t = datetime.datetime.strptime(s, "%Y-%m-%d %H:%M")
            if abs((now-t).total_seconds()) <= BLACKOUT_MIN*60:
                return True
        except Exception:
            pass
    return False


# ---------------- ポジション ----------------
def load_positions():
    if not os.path.exists(POSITIONS_FILE):
        return {"positions": []}
    try:
        data = json.load(open(POSITIONS_FILE, encoding="utf-8"))
        return data if "positions" in data else {"positions": []}
    except Exception as e:
        print(f"[WARN] positions.json読込失敗: {e}", file=sys.stderr)
        return {"positions": []}


def save_positions(data):
    json.dump(data, open(POSITIONS_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def _tp_sl_prices(p):
    side = p.get("side", "long"); entry = float(p["entry"])
    tp, sl = p.get("tp"), p.get("sl")
    if tp is None and p.get("tp_pips") is not None:
        tp = entry + float(p["tp_pips"])*PIP_SIZE if side == "long" else entry - float(p["tp_pips"])*PIP_SIZE
    if sl is None and p.get("sl_pips") is not None:
        sl = entry - float(p["sl_pips"])*PIP_SIZE if side == "long" else entry + float(p["sl_pips"])*PIP_SIZE
    return (float(tp) if tp is not None else None, float(sl) if sl is not None else None)


def auto_set_levels(data):
    msgs, changed = [], False
    for p in data.get("positions", []):
        if p.get("status", "open") != "open" or not p.get("auto") or p.get("auto_set"):
            continue
        a = atr(get_ohlc(p.get("symbol")), P["atr"]) if p.get("symbol") else None
        if not a:
            continue
        tp_pips, sl_pips = suggest_tp_sl(a)
        p["tp_pips"], p["sl_pips"], p["atr_used"], p["auto_set"] = tp_pips, sl_pips, round(a, 3), True
        changed = True
        side = p.get("side", "long"); entry = float(p["entry"]); tp_pr, sl_pr = _tp_sl_prices(p)
        msgs.append(f"🧭 推奨レベル設定 {p['symbol']} ({'買い' if side=='long' else '売り'})\n"
                    f"  建値:{entry} / ATR:{a:.3f}（{MODE}）\n"
                    f"  TP:+{tp_pips}pips({tp_pr:.3f}) / SL:-{sl_pips}pips({sl_pr:.3f})")
    return msgs, changed


def position_pl(p, ticker):
    sym, side = p.get("symbol"), p.get("side", "long")
    entry, lot = float(p["entry"]), float(p.get("lot", DEFAULT_LOT))
    bid, ask = ticker[sym]["bid"], ticker[sym]["ask"]
    cur = bid if side == "long" else ask
    diff = (cur-entry) if side == "long" else (entry-cur)
    tp_pr, sl_pr = _tp_sl_prices(p)
    return {"id":p.get("id"), "symbol":sym, "side":side, "entry":entry, "lot":lot,
            "current":round(cur,3), "pips":round(diff/PIP_SIZE,1), "yen":round(diff*lot),
            "tp_price":round(tp_pr,3) if tp_pr else None, "sl_price":round(sl_pr,3) if sl_pr else None}


def check_positions(data, ticker):
    msgs, changed = [], False
    for p in data.get("positions", []):
        if p.get("status", "open") != "open" or p.get("symbol") not in ticker:
            continue
        info = position_pl(p, ticker); side = info["side"]
        bid, ask = ticker[info["symbol"]]["bid"], ticker[info["symbol"]]["ask"]
        tp_pr, sl_pr = info["tp_price"], info["sl_price"]
        print(f"[INFO] {info['symbol']} {side} 建値{info['entry']} 現在{info['current']} {info['pips']:+}pips {info['yen']:+,}円")
        hit = None
        if side == "long":
            if tp_pr and bid >= tp_pr: hit = ("利確", "🎯")
            elif sl_pr and bid <= sl_pr: hit = ("損切り", "🛑")
        else:
            if tp_pr and ask <= tp_pr: hit = ("利確", "🎯")
            elif sl_pr and ask >= sl_pr: hit = ("損切り", "🛑")
        if hit and not p.get("hit_notified"):
            kind, mark = hit
            msgs.append(f"{mark} {kind}ライン到達 {info['symbol']} ({'買い' if side=='long' else '売り'})\n"
                        f"  建値:{info['entry']} → 現在:{info['current']}\n"
                        f"  {info['pips']:+}pips / {info['yen']:+,}円\n"
                        f"  ※GMOで決済後、アプリに実際の結果を登録してください")
            p["hit_notified"] = True; p["hit_reason"] = kind; changed = True
    return msgs, changed


# ---------------- status.json ----------------
def build_status(ticker, data, market_open):
    pairs, notify = [], []
    blackout = in_blackout()
    for sym in SYMBOLS:
        sc = score_pair(sym, get_ohlc(sym))
        if not sc:
            continue
        sig = None if blackout else sc["side"]
        bias = "買い優勢" if sc["score"] >= 0 else "売り優勢"
        pairs.append({
            "symbol":sym, "bid":ticker.get(sym,{}).get("bid"), "ask":ticker.get(sym,{}).get("ask"),
            "rsi":sc["rsi"], "adx":sc["adx"], "ema_f":round(sc["ef"],3), "ema_s":round(sc["es"],3),
            "atr":sc["atr"], "tp_pips":sc["tp_pips"], "sl_pips":sc["sl_pips"],
            "score":sc["score"], "tech":sc["tech"], "fund":sc["fund"],
            "signal":sig, "bias":bias, "reasons":sc["reasons"],
            "closes":sc["closes"], "ema_f_series":sc["ef_series"], "ema_s_series":sc["es_series"],
        })
        if market_open and sig:
            price = ticker.get(sym,{}).get("bid","-")
            rtxt = " / ".join(sc["reasons"])
            notify.append(f"{'🟢' if sig=='買い' else '🔴'} {sym} {sig}（{MODE}）\n"
                          f"  スコア{sc['score']:+.2f}（テク{sc['tech']:+.2f}/ファンダ{sc['fund']:+.2f}）\n"
                          f"  {rtxt}\n  現在値:{price}\n"
                          f"  推奨 TP:+{sc['tp_pips']}pips / SL:-{sc['sl_pips']}pips")

    open_pos, closed_pos = [], []
    for p in data.get("positions", []):
        if p.get("status","open") == "open" and p.get("symbol") in ticker:
            open_pos.append(position_pl(p, ticker))
        elif p.get("status") == "closed":
            closed_pos.append({k:p.get(k) for k in
                ("id","symbol","side","entry","close_price","close_pips","close_yen","close_reason","closed_at")})

    status = {
        "generated_at": datetime.datetime.now(JST).strftime("%Y-%m-%d %H:%M JST"),
        "market_open": market_open, "mode": MODE, "blackout": blackout,
        "weights": {"tech": TECH_W, "fund": FUND_W},
        "pairs": pairs, "open_positions": open_pos, "closed_positions": closed_pos[-20:],
    }
    json.dump(status, open(STATUS_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return notify


# ---------------- 通知 ----------------
def notify_line(text):
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
    if not token:
        print("[INFO] LINE未設定。スキップ"); return
    try:
        r = requests.post("https://api.line.me/v2/bot/message/broadcast",
                          headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                          json={"messages": [{"type": "text", "text": text}]}, timeout=15)
        print(f"[INFO] LINE送信 status={r.status_code} {r.text[:120]}")
    except Exception as e:
        print(f"[WARN] LINE送信失敗: {e}", file=sys.stderr)


def notify_mail(subject, body):
    addr = os.environ.get("GMAIL_ADDRESS"); pw = os.environ.get("GMAIL_APP_PASSWORD")
    to = os.environ.get("MAIL_TO") or addr
    if not (addr and pw):
        print("[INFO] Gmail未設定。スキップ"); return
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"], msg["From"], msg["To"] = subject, addr, to
        msg["Date"] = formatdate(localtime=True)
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as s:
            s.login(addr, pw); s.send_message(msg)
        print("[INFO] メール送信完了")
    except Exception as e:
        print(f"[WARN] メール送信失敗: {e}", file=sys.stderr)


# ---------------- メイン ----------------
def main():
    now_str = datetime.datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")
    if os.environ.get("TEST_NOTIFY", "").lower() == "true":
        m = f"✅ テスト通知\n時刻: {now_str}\nLINEとメールの疎通確認です。"
        print(m); notify_line(m); notify_mail("【FX】テスト通知", m); return

    market_open = market_is_open()
    ticker = fetch_ticker()
    data = load_positions()
    m1, c1 = auto_set_levels(data)
    m2, c2 = check_positions(data, ticker)
    if c1 or c2:
        save_positions(data)
    notify = build_status(ticker, data, market_open)
    msgs = m1 + m2 + notify

    if not market_open:
        print(f"[INFO] {now_str} 市場クローズ（エントリー判定スキップ）")
    if not msgs:
        print(f"[INFO] {now_str} 通知なし（mode={MODE}）。"); return
    body = (f"📊 FX通知 [{MODE}]\n時刻: {now_str}\n\n" + "\n\n".join(msgs)
            + "\n\n※スコアは目安です。最適値の保証ではなく自己責任で。")
    print(body); notify_line(body); notify_mail(f"【FX/{MODE}】シグナル通知", body)


if __name__ == "__main__":
    main()
