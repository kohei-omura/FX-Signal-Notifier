#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""長期バックテスト（1日1回・5分ごとの通知とは別枠）。

status.json に載る統計は直近8日ぶんしかない。しかも4通貨とも円クロスで値動きが
強く相関するため、実質の独立サンプルは件数が示すよりずっと少ない。
「判定そのものに優位性があるのか」「しきい値を上げれば直るのか」を判断するには
足りないので、期間を延ばして同じ集計をやり直す。

判定ロジックは fx_signal.py のものをそのまま使う（二重実装を作らない）。
出力: backtest.json（ツール画面が読む）
"""
import datetime, json, os, sys

import fx_signal as F

# モード別の検証期間。1分足は本数が多いので短めにする。
WINDOWS = {"scalp": (14, 30000), "day": (60, 6000), "swing": (120, 3000)}
MODES = [m.strip() for m in os.environ.get("BACKTEST_MODES", "scalp,day,swing").split(",") if m.strip()]
OUT = "backtest.json"


def pool_summary(parts):
    """通貨ごとの集計を1つにまとめる。

       期待Rは件数で重み付けした平均でよいが、信頼区間は各通貨の区間を平均しても
       正しくない。全体の分散＝群内分散＋群間分散（全分散の法則）で出し直す。"""
    n = sum(p["n"] for p in parts)
    if not n:
        return {"n": 0}
    mean = sum(p["avg_r"] * p["n"] for p in parts) / n
    within = sum((p["n"] - 1) * (p.get("sd", 0.0) ** 2) for p in parts)
    between = sum(p["n"] * (p["avg_r"] - mean) ** 2 for p in parts)
    var = (within + between) / (n - 1) if n > 1 else 0.0
    se = (var / n) ** 0.5
    return {"n": n,
            "winrate": round(sum((p.get("winrate") or 0) * p["n"] for p in parts) / n),
            "avg_r": round(mean, 3),
            "avg_r_gross": round(sum(p.get("avg_r_gross", p["avg_r"]) * p["n"] for p in parts) / n, 3),
            "cost_r": round(sum(p.get("cost_r", 0.0) * p["n"] for p in parts) / n, 3),
            "sd": round(var ** 0.5, 4),
            "ci_lo": round(mean - 1.96 * se, 3),
            "ci_hi": round(mean + 1.96 * se, 3)}


def run_mode(mode):
    days, cap = WINDOWS.get(mode, (60, 6000))
    F.MODE = mode
    F.P = F.PARAMS[mode]
    # スタイル別のテク:ファンダ比。fx_signal.main() と同じ値にする。
    F.TECH_W, F.FUND_W = (0.45, 0.55) if mode == "swing" else (0.85, 0.15)
    F.STATS_DAYS = {m: days for m in F.PARAMS}
    F.STATS_MAX_BARS = {m: cap for m in F.PARAMS}
    for c in (F._OHLC_CACHE, F._KLINE_DAY_CACHE, F._MTF_CACHE, F._SCORE_CACHE):
        c.clear()

    symbols, total_pol, total_band, blocked, n_all = {}, {}, {}, 0, 0
    for sym in F.SYMBOLS:
        try:
            st = F.compute_signal_stats(sym)
        except Exception as e:
            print(f"[WARN] {mode}/{sym} 失敗: {e}", file=sys.stderr)
            continue
        if not st:
            print(f"[INFO] {mode}/{sym} データ不足", file=sys.stderr)
            continue
        symbols[sym] = st
        n_all += st["n"]
        blocked += st.get("mtf_blocked") or 0
        for k, v in (st.get("policies") or {}).items():
            a = total_pol.setdefault(k, {"parts": []})
            a["parts"].append(v)
        for bn, b in (st.get("bands") or {}).items():
            t = total_band.setdefault(bn, {"n": 0})
            t["n"] += b["n"]
            for k in F.EXIT_POLICIES:
                if b.get(k) is not None:
                    t[k] = t.get(k, 0.0) + b[k] * b["n"]

    if not symbols:
        return None
    pol = {k: pool_summary(a["parts"]) for k, a in total_pol.items() if a["parts"]}
    band = {}
    for bn, t in total_band.items():
        band[bn] = {"n": t["n"]}
        for k in F.EXIT_POLICIES:
            if k in t:
                band[bn][k] = round(t[k]/t["n"], 3)
    return {"days": days, "n": n_all, "mtf_blocked": blocked,
            "policies": pol, "bands": band,
            "symbols": {s: {"n": v["n"], "policies": v.get("policies"),
                            "bands": v.get("bands"),
                            "mtf_blocked": v.get("mtf_blocked")} for s, v in symbols.items()}}


def main():
    out = {"generated_at": datetime.datetime.now(F.JST).strftime("%Y-%m-%d %H:%M JST"),
           "note": "1日1回の長期検証。status.json の統計（直近8日）より母数が多い。",
           "modes": {}}
    for mode in MODES:
        if mode not in F.PARAMS:
            print(f"[WARN] 未知のモード: {mode}", file=sys.stderr); continue
        r = run_mode(mode)
        if not r:
            continue
        out["modes"][mode] = r
        print(f"[OK] {mode}: {r['days']}日 / 採用{r['n']}件 / 上位足で見送り{r['mtf_blocked']}件")
        for k in F.EXIT_POLICIES:
            v = r["policies"].get(k)
            if v:
                print(f"      {k:14} 勝率{v['winrate']:3}% 期待R{v['avg_r']:+.3f}")
        for bn in sorted(r["bands"], key=lambda x: {"弱": 0, "中": 1, "強": 2}.get(x[0], 9)):
            b = r["bands"][bn]
            print(f"      スコア{bn:16} n={b['n']:4} " +
                  "  ".join(f"{k}={b.get(k, 0):+.3f}" for k in F.EXIT_POLICIES))
    if not out["modes"]:
        print("[ERROR] どのモードも集計できませんでした（既存の backtest.json は残します）",
              file=sys.stderr)
        sys.exit(1)
    F.write_json(OUT, out)
    print(f"[OK] {OUT} に保存")


if __name__ == "__main__":
    main()
