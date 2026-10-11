#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""実際に建てた取引（記録簿 entry_log.json）の答え合わせ。

記録簿には「建てた時点の判断材料」しか無く、勝ったか負けたかは端末のGMO CSVにしか無い。
使う人はエントリー直後に TP/SL の OCO を入れているので、記録された建値・TP幅・SL幅で
実際の1分足をなぞれば、OCOがどちらで約定したかを再現できる。
これで月ごと・モードごと・条件ごとの成績を、サーバー側だけで出せるようにする。

前提と限界（画面にもそのまま出す）:
  - OCOの値は記録簿のTP/SL幅から計算（手で動かした場合は再現できない）。
  - 1分足はBID。売りの決済はASKなので、公表スプレッドぶんずらして判定する。
  - 同じ1分足でTPとSLの両方に触れたらSLを先とみなす（保守的）。
  - 途中で手動決済した取引は区別できない。
"""
import datetime, sys

import fx_signal as F

REVIEW_FROM = "2026-09-01"      # これより前の記録は対象外（月ごとの比較に使うのは9月以降）
MAX_DAYS = 10                    # 何日先まで追うか（スイングでも十分な長さ）
INTERVAL = "1min"


def _ts_jst(s):
    s = str(s or "").strip()
    for fmt, n in (("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%d %H:%M", 16)):
        try:
            return datetime.datetime.strptime(s[:n], fmt).replace(tzinfo=F.JST)
        except ValueError:
            continue
    return None


def _bars(symbol, start, days):
    """start(JST)の前日から days 日ぶんの1分足を、時刻順の [(openTime, high, low, close)] で返す。"""
    rows = {}
    for k in range(-1, days + 1):
        d = (start + datetime.timedelta(days=k)).strftime("%Y%m%d")
        rows.update(F.klines_day(symbol, d, interval=INTERVAL) or {})
    return [(t, h, l, c) for t, (h, l, c) in sorted(rows.items())]


def replay(e, bars_fn=_bars, now=None):
    """1件の取引をOCOで再現する。戻り値は dict（result: tp/sl/open/nodata）。"""
    sym = e.get("symbol"); side = e.get("side")
    try:
        entry = float(e["entry"]); tpp = float(e["tp_pips"]); slp = float(e["sl_pips"])
    except (KeyError, TypeError, ValueError):
        return {"result": "nodata"}
    t0 = _ts_jst(e.get("logged_at"))
    if not sym or not t0 or tpp <= 0 or slp <= 0:
        return {"result": "nodata"}
    buy = side in ("買", "買い", "long")
    d = 1 if buy else -1
    tp = entry + d * tpp * F.PIP_SIZE
    sl = entry - d * slp * F.PIP_SIZE
    spr = 0.0 if buy else F.SPREAD_PIPS.get(sym, F.DEFAULT_SPREAD_PIPS) * F.PIP_SIZE
    # 建てた分の「次の1分」から見る（建てる前の値動きを使わない）
    start_ms = (int(t0.timestamp()) // 60 + 1) * 60000
    end_ms = start_ms + MAX_DAYS * 86400000
    bars = [b for b in bars_fn(sym, t0, MAX_DAYS) if start_ms <= b[0] < end_ms]
    if not bars:
        return {"result": "nodata"}
    mfe = 0.0; mae = 0.0
    for t, h, l, c in bars:
        hi, lo = h + spr, l + spr
        hit_sl = (lo <= sl) if buy else (hi >= sl)
        hit_tp = (hi >= tp) if buy else (lo <= tp)
        fav = ((hi - entry) if buy else (entry - lo)) / (slp * F.PIP_SIZE)
        adv = ((entry - lo) if buy else (hi - entry)) / (slp * F.PIP_SIZE)
        if hit_sl:
            return {"result": "sl", "r": -1.0, "mfe": round(max(mfe, 0.0), 2),
                    "mins": round((t - start_ms) / 60000) + 1}
        mfe = max(mfe, fav); mae = max(mae, adv)
        if hit_tp:
            return {"result": "tp", "r": round(tpp / slp, 3), "mfe": round(mfe, 2),
                    "mae": round(mae, 2), "mins": round((t - start_ms) / 60000) + 1}
    last = bars[-1][3] + spr
    return {"result": "open", "r_now": round((last - entry) * d / (slp * F.PIP_SIZE), 3),
            "mfe": round(mfe, 2), "mae": round(mae, 2)}


def _adx_band(v):
    if v is None:
        return "不明"
    v = float(v)
    return "〜20" if v < 20 else "20〜30" if v < 30 else "30〜40" if v < 40 else "40〜"


def _hour_band(t):
    h = t.hour
    if 6 <= h < 9:
        return "早朝(6-9)"
    if 9 <= h < 15:
        return "東京(9-15)"
    if 15 <= h < 21:
        return "欧州(15-21)"
    return "NY(21-6)"


def _summ(rows):
    done = [x for x in rows if x["result"] in ("tp", "sl")]
    n = len(done)
    if not n:
        return {"n": 0, "open": len(rows)}
    rs = [x["r"] for x in done]
    m = sum(rs) / n
    sd = (sum((v - m) ** 2 for v in rs) / (n - 1)) ** 0.5 if n > 1 else 0.0
    se = sd / n ** 0.5 if n > 1 else 0.0
    return {"n": n, "open": len(rows) - n, "wins": sum(1 for v in rs if v > 0),
            "winrate": round(sum(1 for v in rs if v > 0) / n * 100),
            "sum_r": round(sum(rs), 2), "avg_r": round(m, 3),
            "ci_lo": round(m - 1.96 * se, 3), "ci_hi": round(m + 1.96 * se, 3),
            # 損切りになった取引のうち、一度は+0.5R以上伸びていた割合（伸びを守れていない度合い）
            "sl_after_run": sum(1 for x in done if x["result"] == "sl" and (x.get("mfe") or 0) >= 0.5)}


GROUPS = {
    "month": lambda x: x["month"],
    "mode": lambda x: x["mode"],
    "symbol": lambda x: x["symbol"],
    "side": lambda x: x["side"],
    "conf_mark": lambda x: (x.get("conf_mark") or "不明").split(" ")[0],
    "adx": lambda x: _adx_band(x.get("adx")),
    "mtf_vs_entry": lambda x: x.get("mtf_vs_entry") or "不明",
    "strength": lambda x: x.get("strength") or "不明",
    "hour": lambda x: x["hour"],
    "news": lambda x: "指標前後" if x.get("news_near") else "なし",
}


def run(entries=None, bars_fn=_bars, since=REVIEW_FROM):
    if entries is None:
        entries = (F.read_json(F.ENTRY_LOG_FILE) or {}).get("entries", [])
    rows = []
    for e in entries:
        if str(e.get("logged_at", "")) < since:
            continue
        t0 = _ts_jst(e.get("logged_at"))
        r = replay(e, bars_fn=bars_fn)
        if r["result"] == "nodata":
            continue
        side = "買い" if e.get("side") in ("買", "買い", "long") else "売り"
        rows.append(dict(r, id=e.get("pos_id"), at=str(e.get("logged_at", ""))[:16],
                         month=str(e.get("logged_at", ""))[:7], symbol=e.get("symbol"),
                         side=side, mode=e.get("mode") or "不明", conf_mark=e.get("conf_mark"),
                         adx=e.get("adx"), mtf_vs_entry=e.get("mtf_vs_entry"),
                         strength=e.get("strength"), news_near=e.get("news_near"),
                         hour=_hour_band(t0), rsi=e.get("rsi"),
                         tp_pips=e.get("tp_pips"), sl_pips=e.get("sl_pips")))
    out = {"generated_at": datetime.datetime.now(F.JST).strftime("%Y-%m-%d %H:%M JST"),
           "assumption": "記録簿の建値・TP幅・SL幅でOCOを置いたとして1分足で再現（手動決済は区別できない）",
           "all": _summ(rows), "trades": rows, "by": {}}
    for g, key in GROUPS.items():
        buckets = {}
        for x in rows:
            buckets.setdefault(key(x), []).append(x)
        out["by"][g] = {k: _summ(v) for k, v in sorted(buckets.items())}
    # 月×モード（9月と10月の違いがどのモードから来ているか）
    mm = {}
    for x in rows:
        mm.setdefault(f"{x['month']} {x['mode']}", []).append(x)
    out["by"]["month_mode"] = {k: _summ(v) for k, v in sorted(mm.items())}
    return out


def report(rv):
    print(f"[答え合わせ] {rv['assumption']}")
    a = rv["all"]
    print(f"  全体 n={a.get('n')} 勝率{a.get('winrate')}% 合計{a.get('sum_r')}R 平均{a.get('avg_r')}R 未決着{a.get('open')}")
    for g in ("month", "month_mode", "mode", "conf_mark", "adx", "mtf_vs_entry", "hour", "side", "symbol", "strength", "news"):
        for k, v in rv["by"].get(g, {}).items():
            if v.get("n"):
                print(f"  {g:13} {k:16} n={v['n']:3} 勝{v['wins']:3} 勝率{v['winrate']:3}% "
                      f"合計{v['sum_r']:+7.2f}R 平均{v['avg_r']:+.3f} 伸びてから損切り{v['sl_after_run']}")


if __name__ == "__main__":
    rv = run()
    report(rv)
    if "--write" in sys.argv:
        F.write_json(F.data_path("trade_review.json"), rv)
