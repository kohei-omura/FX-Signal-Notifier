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
# swing は120日で125件しか出ず判定不能だったため1年に延ばした。
# day も90日では有望候補(上位足押し目)が205件しか出ず判定できなかったため1年に延ばす。
WINDOWS = {"scalp": (14, 30000), "day": (365, 40000), "swing": (365, 9000),
           "mtf": (365, 40000)}
# しきい値を実際の運用ルールとして振ってみる。事後にスコア帯で切り分けるのとは違い、
# エントリー地点そのものが変わる。
SWEEP_TH = [0.40, 0.50, 0.55, 0.60, 0.65, 0.70]
MODES = [m.strip() for m in os.environ.get("BACKTEST_MODES", "scalp,day,swing,mtf").split(",") if m.strip()]
OUT = F.data_path("backtest.json")


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


def sweep_thresholds(mode):
    """しきい値を振って、それぞれを運用ルールとして回した場合の期待Rを出す。

       ★注意: 6通り試せば、優位性が無くても偶然どれかが良く見える（多重比較）。
       そのため前半だけで最良のしきい値を選び、後半（未使用データ）で検証する。
       後半でも保っていなければ、それは過去に合わせただけの数字。"""
    rows = []
    for th in SWEEP_TH:
        parts = []
        for sym in F.SYMBOLS:
            try:
                st = F.compute_signal_stats(sym, th_override=th)
            except Exception as e:
                print(f"[WARN] sweep {mode}/{sym}/th={th} 失敗: {e}", file=sys.stderr)
                continue
            if st and st.get("policies"):
                parts.append(st["policies"])
        if not parts:
            continue
        row = {"th": th}
        for k in F.EXIT_POLICIES:
            got = [p[k] for p in parts if k in p]
            if got:
                row[k] = pool_summary(got)
        if any(k in row for k in F.EXIT_POLICIES):
            rows.append(row)
    return rows


def holdout(mode, policy="advice"):
    """前半で最良のしきい値を選び、後半（選定に使っていないデータ）で試す。
       前半だけを見て決めたルールが後半でも通用するかを見る、過学習のチェック。"""
    def run(th, rng):
        parts = []
        for sym in F.SYMBOLS:
            try:
                st = F.compute_signal_stats(sym, th_override=th, entry_range=rng)
            except Exception:
                continue
            if st and st.get("policies", {}).get(policy):
                parts.append(st["policies"][policy])
        return pool_summary(parts) if parts else None

    first = {}
    for th in SWEEP_TH:
        r = run(th, (0.0, 0.5))
        if r and r["n"] >= 30:
            first[th] = r
    if not first:
        return None
    best = max(first, key=lambda t: first[t]["avg_r"])
    second = run(best, (0.5, 1.0))
    return {"policy": policy, "best_th": best,
            "first_half": first[best], "second_half": second,
            "candidates": {str(t): first[t]["avg_r"] for t in first}}


def run_mode(mode):
    days, cap = WINDOWS.get(mode, (60, 6000))
    F.MODE = mode
    F.P = F.PARAMS[mode]
    # スタイル別のテク:ファンダ比。fx_signal.main() と同じ値にする。
    F.TECH_W, F.FUND_W = (0.45, 0.55) if mode == "swing" else (0.85, 0.15)
    F.STATS_DAYS = {m: days for m in F.PARAMS}
    F.STATS_MAX_BARS = {m: cap for m in F.PARAMS}
    # klinesの日次キャッシュ(_KLINE_DAY_CACHE)はキーに足の種類が入っているので
    # モードをまたいで使い回せる。捨てると day と mtf のように同じ15分足を使う
    # モードで取り直しになり、1回の実行が30分のタイムアウトを超えていた。
    for c in (F._OHLC_CACHE, F._MTF_CACHE, F._SCORE_CACHE, F._SERIES_CACHE):
        c.clear()

    symbols, total_pol, total_band, blocked, n_all = {}, {}, {}, 0, 0
    total_atr = {}
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
        # レジーム別は信頼区間まで見たいので、通貨ごとの集計をそのまま貯めて後で合成する
        for bn, b in (st.get("atr_bands") or {}).items():
            slot = total_atr.setdefault(bn, {})
            for k in F.EXIT_POLICIES:
                if b.get(k):
                    slot.setdefault(k, []).append(b[k])

    if not symbols:
        return None
    pol = {k: pool_summary(a["parts"]) for k, a in total_pol.items() if a["parts"]}
    band = {}
    for bn, t in total_band.items():
        band[bn] = {"n": t["n"]}
        for k in F.EXIT_POLICIES:
            if k in t:
                band[bn][k] = round(t[k]/t["n"], 3)
    atr_band = {}
    for bn, per in total_atr.items():
        row = {}
        for k, parts in per.items():
            if parts:
                row[k] = pool_summary(parts)
        if row:
            row["n"] = max(v["n"] for v in row.values())
            atr_band[bn] = row
    # 実際に何日ぶんのデータが取れたか（取引所の保持期間で足りないことがある）
    covered = len({k[2] for k in F._KLINE_DAY_CACHE
                   if k[1] == F.P["interval"] and F._KLINE_DAY_CACHE[k]})
    return {"days": days, "days_covered": covered, "n": n_all, "mtf_blocked": blocked,
            "current_th": F.PARAMS[mode]["th"],
            "policies": pol, "bands": band, "atr_bands": atr_band,
            "sweep": sweep_thresholds(mode), "holdout": holdout(mode),
            "symbols": {s: {"n": v["n"], "policies": v.get("policies"),
                            "bands": v.get("bands"), "atr_bands": v.get("atr_bands"),
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
        print(f"[OK] {mode}: 指定{r['days']}日(実データ{r['days_covered']}日) / "
              f"採用{r['n']}件 / 上位足で見送り{r['mtf_blocked']}件")
        for k in F.EXIT_POLICIES:
            v = r["policies"].get(k)
            if v:
                print(f"      {k:14} 勝率{v['winrate']:3}% 期待R{v['avg_r']:+.3f}")
        for bn in sorted(r["bands"], key=lambda x: {"弱": 0, "中": 1, "強": 2}.get(x[0], 9)):
            b = r["bands"][bn]
            print(f"      スコア{bn:16} n={b['n']:4} " +
                  "  ".join(f"{k}={b.get(k, 0):+.3f}" for k in F.EXIT_POLICIES))
        _order = {lab: i for i, (_, lab) in enumerate(F.ATR_REGIME_BANDS)}
        for bn in sorted(r.get("atr_bands") or {}, key=lambda x: _order.get(x, 9)):
            b = r["atr_bands"][bn]
            v = b.get("advice") or {}
            if v:
                print(f"      値幅{bn:20} n={v['n']:4} 勝率{v['winrate']:3}% "
                      f"期待R{v['avg_r']:+.3f} [{v['ci_lo']:+.3f}〜{v['ci_hi']:+.3f}]")
        for row in r.get("sweep") or []:
            v = row.get("advice") or {}
            if v:
                print(f"      しきい値{row['th']:.2f} n={v['n']:4} "
                      f"期待R{v['avg_r']:+.3f} [{v['ci_lo']:+.3f}〜{v['ci_hi']:+.3f}]")
        h = r.get("holdout")
        if h and h.get("second_half"):
            f_, s_ = h["first_half"], h["second_half"]
            print(f"      前半で最良のしきい値 {h['best_th']:.2f}: "
                  f"前半{f_['avg_r']:+.3f}(n={f_['n']}) → 後半{s_['avg_r']:+.3f}(n={s_['n']})")
    if not out["modes"]:
        print("[ERROR] どのモードも集計できませんでした（既存の backtest.json は残します）",
              file=sys.stderr)
        sys.exit(1)
    F.write_json(OUT, out)
    print(f"[OK] {OUT} に保存")


if __name__ == "__main__":
    main()
