#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FX Signal & Position Navigator  (シグナル＋利確ナビ＋ATR推奨＋ダッシュボード)
-----------------------------------------------------------------------
GMOコイン 外国為替FX Public API を使い、GitHub Actionsだけで動く：
  (A) エントリー: 主要円ペアの買い/売りシグナル＋ATR推奨TP/SLを通知
  (B) エグジット: 保有ポジションを監視し利確/損切り到達で通知＋自動クローズ
  (C) 推奨自動設定: "auto":true のポジションはATRからTP/SLを自動算出
  (D) 画面表示: 毎回 status.json を書き出し、GitHub Pagesのダッシュボードで可視化

⚠️ ATR推奨は値動きに見合った目安で、未来の最適値の保証ではありません。自己責任で。
"""

import os, sys, json, smtplib, datetime
from email.mime.text import MIMEText
from email.utils import formatdate
from zoneinfo import ZoneInfo
import requests

# ===================== 設定 =====================
JST = ZoneInfo("Asia/Tokyo")
SYMBOLS    = ["USD_JPY", "EUR_JPY", "GBP_JPY", "AUD_JPY"]
INTERVAL   = "5min"
PRICE_TYPE = "BID"
SMA_SHORT, SMA_LONG = 5, 20
RSI_PERIOD = 14
RSI_LOW, RSI_HIGH = 30, 70
ATR_PERIOD  = 14
ATR_SL_MULT = 1.5
ATR_TP_MULT = 2.0
PIP_SIZE    = 0.01
DEFAULT_LOT = 10000
POSITIONS_FILE = "positions.json"
STATUS_FILE    = "status.json"
CHART_POINTS   = 60
BASE = "https://forex-api.coin.z.com/public/v1"
# ================================================

_OHLC_CACHE: dict[str, list[tuple]] = {}


# --------------- データ取得 ---------------
def get_ohlc(symbol):
    """直近営業日まで遡って (high,low,close) を集める。週末でも空にならない。"""
    if symbol in _OHLC_CACHE:
        return _OHLC_CACHE[symbol]
    need = max(SMA_LONG, RSI_PERIOD, ATR_PERIOD) + CHART_POINTS  # 十分な本数
    today = datetime.datetime.now(JST).date()
    rows = {}
    for back in range(0, 7):           # 最大7日遡る
        d = today - datetime.timedelta(days=back)
        try:
            j = requests.get(f"{BASE}/klines", timeout=15, params={
                "symbol": symbol, "priceType": PRICE_TYPE,
                "interval": INTERVAL, "date": d.strftime("%Y%m%d")}).json()
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


# --------------- 指標 ---------------
def sma(v, p):
    return sum(v[-p:]) / p if len(v) >= p else None


def sma_series(v, p):
    out = [None] * len(v)
    for i in range(p - 1, len(v)):
        out[i] = round(sum(v[i - p + 1:i + 1]) / p, 5)
    return out


def rsi(v, p):
    if len(v) < p + 1:
        return None
    d = [v[i] - v[i - 1] for i in range(1, len(v))]
    g = [max(x, 0.0) for x in d]; l = [max(-x, 0.0) for x in d]
    ag, al = sum(g[:p]) / p, sum(l[:p]) / p
    for i in range(p, len(d)):
        ag = (ag * (p - 1) + g[i]) / p; al = (al * (p - 1) + l[i]) / p
    return 100.0 if al == 0 else 100.0 - 100.0 / (1.0 + ag / al)


def atr(ohlc, p=ATR_PERIOD):
    if len(ohlc) < p + 1:
        return None
    trs = []
    for i in range(1, len(ohlc)):
        h, l, _ = ohlc[i]; pc = ohlc[i - 1][2]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    a = sum(trs[:p]) / p
    for i in range(p, len(trs)):
        a = (a * (p - 1) + trs[i]) / p
    return a


def suggest_tp_sl(a):
    return (round(a * ATR_TP_MULT / PIP_SIZE, 1), round(a * ATR_SL_MULT / PIP_SIZE, 1))


def detect_signal(closes):
    if len(closes) < max(SMA_LONG, RSI_PERIOD) + 2:
        return None
    prev, now = closes[:-1], closes
    sp, sn = sma(prev, SMA_SHORT), sma(now, SMA_SHORT)
    lp, ln = sma(prev, SMA_LONG),  sma(now, SMA_LONG)
    rp, rn = rsi(prev, RSI_PERIOD), rsi(now, RSI_PERIOD)
    if None in (sp, sn, lp, ln, rp, rn):
        return None
    buy, sell = [], []
    if sp <= lp and sn > ln: buy.append(f"ゴールデンクロス(SMA{SMA_SHORT}↑SMA{SMA_LONG})")
    if sp >= lp and sn < ln: sell.append(f"デッドクロス(SMA{SMA_SHORT}↓SMA{SMA_LONG})")
    if rp < RSI_LOW <= rn:   buy.append(f"RSI売られすぎ脱出({RSI_LOW}↑)")
    if rp > RSI_HIGH >= rn:  sell.append(f"RSI買われすぎ脱出({RSI_HIGH}↓)")
    if buy and not sell: return ("買い", buy, rn)
    if sell and not buy: return ("売り", sell, rn)
    return None


# --------------- ポジション ---------------
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
        tp = entry + float(p["tp_pips"]) * PIP_SIZE if side == "long" else entry - float(p["tp_pips"]) * PIP_SIZE
    if sl is None and p.get("sl_pips") is not None:
        sl = entry - float(p["sl_pips"]) * PIP_SIZE if side == "long" else entry + float(p["sl_pips"]) * PIP_SIZE
    return (float(tp) if tp is not None else None, float(sl) if sl is not None else None)


def auto_set_levels(data):
    msgs, changed = [], False
    for p in data.get("positions", []):
        if p.get("status", "open") != "open" or not p.get("auto") or p.get("auto_set"):
            continue
        a = atr(get_ohlc(p.get("symbol"))) if p.get("symbol") else None
        if not a:
            continue
        tp_pips, sl_pips = suggest_tp_sl(a)
        p["tp_pips"], p["sl_pips"], p["atr_used"], p["auto_set"] = tp_pips, sl_pips, round(a, 3), True
        changed = True
        side = p.get("side", "long"); entry = float(p["entry"]); tp_pr, sl_pr = _tp_sl_prices(p)
        msgs.append(f"🧭 推奨レベル設定 {p['symbol']} ({'買い' if side=='long' else '売り'})\n"
                    f"  建値:{entry} / ATR{ATR_PERIOD}:{a:.3f}\n"
                    f"  TP:+{tp_pips}pips({tp_pr:.3f}) / SL:-{sl_pips}pips({sl_pr:.3f})")
    return msgs, changed


def position_pl(p, ticker):
    """開いているポジションの現在損益等を計算して dict で返す。"""
    sym, side = p.get("symbol"), p.get("side", "long")
    entry, lot = float(p["entry"]), float(p.get("lot", DEFAULT_LOT))
    bid, ask = ticker[sym]["bid"], ticker[sym]["ask"]
    cur = bid if side == "long" else ask
    diff = (cur - entry) if side == "long" else (entry - cur)
    tp_pr, sl_pr = _tp_sl_prices(p)
    return {"id": p.get("id"), "symbol": sym, "side": side, "entry": entry, "lot": lot,
            "current": round(cur, 3), "pips": round(diff / PIP_SIZE, 1), "yen": round(diff * lot),
            "tp_price": round(tp_pr, 3) if tp_pr else None,
            "sl_price": round(sl_pr, 3) if sl_pr else None}


def check_positions(data, ticker):
    msgs, changed = [], False
    now_str = datetime.datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")
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
        if hit:
            kind, mark = hit
            msgs.append(f"{mark} {kind} {info['symbol']} ({'買い' if side=='long' else '売り'})\n"
                        f"  建値:{info['entry']} → 決済:{info['current']}\n"
                        f"  {info['pips']:+}pips / {info['yen']:+,}円")
            p.update(status="closed", close_price=info["current"], close_pips=info["pips"],
                     close_yen=info["yen"], close_reason=kind, closed_at=now_str)
            changed = True
    return msgs, changed


# --------------- 状態書き出し(ダッシュボード用) ---------------
def build_status(ticker, data, market_open):
    pairs = []
    notify_blocks = []
    for sym in SYMBOLS:
        ohlc = get_ohlc(sym)
        closes = [r[2] for r in ohlc]
        if len(closes) < max(SMA_LONG, RSI_PERIOD) + 2:
            continue
        rn = rsi(closes, RSI_PERIOD)
        ss, ll = sma(closes, SMA_SHORT), sma(closes, SMA_LONG)
        a = atr(ohlc)
        tp_pips, sl_pips = suggest_tp_sl(a) if a else (None, None)
        sig = detect_signal(closes)
        bias = "買い優勢" if (ss and ll and ss >= ll) else "売り優勢"
        info = {
            "symbol": sym,
            "bid": ticker.get(sym, {}).get("bid"),
            "ask": ticker.get(sym, {}).get("ask"),
            "rsi": round(rn, 1) if rn else None,
            "sma_short": round(ss, 3) if ss else None,
            "sma_long": round(ll, 3) if ll else None,
            "atr": round(a, 4) if a else None,
            "tp_pips": tp_pips, "sl_pips": sl_pips,
            "signal": sig[0] if sig else None,
            "reasons": sig[1] if sig else [],
            "bias": bias,
            "closes": [round(c, 3) for c in closes[-CHART_POINTS:]],
            "sma_s_series": sma_series(closes, SMA_SHORT)[-CHART_POINTS:],
            "sma_l_series": sma_series(closes, SMA_LONG)[-CHART_POINTS:],
        }
        pairs.append(info)
        if market_open and sig:
            price = ticker.get(sym, {}).get("bid", "-")
            rtxt = "\n".join(f"  ・{x}" for x in sig[1])
            blk = f"{'🟢' if sig[0]=='買い' else '🔴'} {sym} {sig[0]}シグナル\n{rtxt}\n  現在値:{price} / RSI:{rn:.1f}"
            if a:
                blk += f"\n  推奨 TP:+{tp_pips}pips / SL:-{sl_pips}pips (ATR{ATR_PERIOD}:{a:.3f})"
            notify_blocks.append(blk)

    open_pos, closed_pos = [], []
    for p in data.get("positions", []):
        if p.get("status", "open") == "open" and p.get("symbol") in ticker:
            open_pos.append(position_pl(p, ticker))
        elif p.get("status") == "closed":
            closed_pos.append({k: p.get(k) for k in
                ("id", "symbol", "side", "entry", "close_price", "close_pips",
                 "close_yen", "close_reason", "closed_at")})

    status = {
        "generated_at": datetime.datetime.now(JST).strftime("%Y-%m-%d %H:%M JST"),
        "market_open": market_open,
        "config": {"interval": INTERVAL, "sma_short": SMA_SHORT, "sma_long": SMA_LONG,
                   "rsi_period": RSI_PERIOD, "atr_period": ATR_PERIOD,
                   "atr_tp_mult": ATR_TP_MULT, "atr_sl_mult": ATR_SL_MULT},
        "pairs": pairs, "open_positions": open_pos, "closed_positions": closed_pos[-20:],
    }
    json.dump(status, open(STATUS_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return notify_blocks


# --------------- 通知 ---------------
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


# --------------- メイン ---------------
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

    notify_blocks = build_status(ticker, data, market_open)   # status.json を書き出し
    msgs = m1 + m2 + notify_blocks

    if not market_open:
        print(f"[INFO] {now_str} 市場クローズ(エントリー判定スキップ)")
    if not msgs:
        print(f"[INFO] {now_str} 通知なし。"); return
    body = (f"📊 FX通知\n時刻: {now_str}\n\n" + "\n\n".join(msgs)
            + "\n\n※ATR推奨は目安です。最適値の保証ではなく自己責任で。")
    print(body); notify_line(body); notify_mail("【FX】シグナル/利確ナビ通知", body)


if __name__ == "__main__":
    main()
