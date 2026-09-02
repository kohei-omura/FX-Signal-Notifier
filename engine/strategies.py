#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""判定ロジックの候補を、同じ検証手順で横並びに比べる。

現行ロジック（EMA/MACD/RSI/ボリンジャーの加重スコア）はコスト控除前で
ランダムと区別が付かなかった。パラメータを振っても後半で再現しないことも確認済み。
そこで指標の調整ではなく、構造の違う仮説を試す。

各候補は同じ条件で評価する:
  - 往復スプレッド控除後
  - 上位足フィルタは本番と同じ
  - 95%信頼区間つき
  - 前半で選び後半で検証（過学習チェック）

random（乱数エントリー）は対照群。ここが「-コスト」付近に出れば検証装置が正しい。
"""
import datetime, json, os, random, sys

import fx_signal as F
from backtest import pool_summary

OUT = F.data_path("strategies.json")
MODES = [m.strip() for m in os.environ.get("STRAT_MODES", "day,swing").split(",") if m.strip()]
WINDOWS = {"scalp": (14, 30000), "day": (365, 40000), "swing": (365, 9000),
           "mtf": (365, 40000)}


# ---------------- 候補ロジック ----------------
# いずれも rule(ctx, i) -> "買い" / "売り" / None

def rule_current(ctx, i):
    """現行: 加重スコアがしきい値超え。比較の基準。"""
    ef, es, rv = ctx["ef"][i], ctx["es"][i], ctx["rsi"][i]
    md, bb, ax, a = ctx["macd"][i], ctx["bb"][i], ctx["adx"][i], ctx["atr"][i]
    if None in (ef, es, rv) or None in (md, bb, ax):
        return None
    t = F._compose_score(ctx["symbol"], ctx["closes"][i], ef, es, rv,
                         md[0], md[1], bb[0], bb[1], a, ax[0])["total"]
    th = ctx["th"]
    return "買い" if t >= th else ("売り" if t <= -th else None)


def rule_inverse(ctx, i):
    """現行の逆。現行が『ランダムより悪い』なら、裏返すと優位になるはず。
       scalpは実際に有意に悪かったので、それが本物かを確かめる意味がある。"""
    s = rule_current(ctx, i)
    return None if s is None else ("売り" if s == "買い" else "買い")


def rule_donchian(ctx, i, n=20):
    """N本高値/安値のブレイク（ドンチャン）。
       オシレーター合成とは系統が違う、古典的な順張り。"""
    if i < n:
        return None
    hi = max(ctx["oh"][j][0] for j in range(i-n, i))
    lo = min(ctx["oh"][j][1] for j in range(i-n, i))
    c = ctx["closes"][i]
    if c > hi:
        return "買い"
    if c < lo:
        return "売り"
    return None


def rule_meanrev(ctx, i):
    """逆張り: ボリンジャー外＋RSI過熱で、戻りを取りに行く。
       現行は順張り側なので、正反対の系統。短期FXは平均回帰しやすいという前提。"""
    bb, rv = ctx["bb"][i], ctx["rsi"][i]
    if bb is None or rv is None or not bb[1]:
        return None
    c = ctx["closes"][i]
    upper, lower = bb[0] + 2*bb[1], bb[0] - 2*bb[1]
    if c >= upper and rv >= 70:
        return "売り"
    if c <= lower and rv <= 30:
        return "買い"
    return None


def rule_session_break(ctx, i):
    """東京時間(9-15時JST)のレンジを、ロンドン序盤(16-18時JST)にブレイクした方向へ。
       指標ではなく市場構造に根拠を置いた仮説。"""
    t = ctx["times"][i]
    dt = datetime.datetime.fromtimestamp(t/1000, F.JST)
    if not (16 <= dt.hour < 18):
        return None
    day0 = dt.replace(hour=9, minute=0, second=0, microsecond=0)
    lo_ms, hi_ms = day0.timestamp()*1000, day0.replace(hour=15).timestamp()*1000
    hs, ls = [], []
    for j in range(max(0, i-200), i):
        if lo_ms <= ctx["times"][j] < hi_ms:
            hs.append(ctx["oh"][j][0]); ls.append(ctx["oh"][j][1])
    if len(hs) < 5:
        return None
    c = ctx["closes"][i]
    if c > max(hs):
        return "買い"
    if c < min(ls):
        return "売り"
    return None


def rule_trend_pullback(ctx, i):
    """上位足の方向に、短期の押し目/戻りで入る。
       上位足フィルタは既に『逆行を止める』形でしか使っていないので、
       方向の根拠そのものに使う形を試す。"""
    al = ctx["aligned"][i]
    rv = ctx["rsi"][i]
    if not al or rv is None:
        return None
    if al == 1 and rv <= 40:
        return "買い"
    if al == -1 and rv >= 60:
        return "売り"
    return None


def make_random_rule(rate, seed=0):
    """対照群。同程度の頻度で無作為に入る。
       検証装置が正しければ、ここは『-スプレッド』付近に出るはず。"""
    rnd = random.Random(seed)
    def rule(ctx, i):
        if rnd.random() >= rate:
            return None
        return "買い" if rnd.random() < 0.5 else "売り"
    return rule


RULES = {
    "current":        (rule_current,        "現行（加重スコア）"),
    "inverse":        (rule_inverse,        "現行の逆"),
    "donchian20":     (rule_donchian,       "20本ブレイク（順張り）"),
    "meanrev":        (rule_meanrev,        "BB外＋RSI過熱の逆張り"),
    "session_break":  (rule_session_break,  "東京レンジ→ロンドンブレイク"),
    "trend_pullback": (rule_trend_pullback, "上位足方向へ押し目/戻り"),
}


def evaluate(rule, mode, entry_range=None):
    parts = []
    for sym in F.SYMBOLS:
        try:
            st = F.compute_signal_stats(sym, rule=rule, entry_range=entry_range)
        except Exception as e:
            print(f"[WARN] {mode}/{sym}: {e}", file=sys.stderr)
            continue
        if st and st.get("policies", {}).get("advice"):
            parts.append(st["policies"]["advice"])
    return pool_summary(parts) if parts else None


def noise_floor(mode, rate, seeds=24):
    """無作為エントリーを何通りも回して「ただのブレの範囲」を測る。

       1回の乱数だけでは、たまたま良く出た結果を優位性と勘違いする。
       候補ロジックは、この分布の上端を超えて初めて『情報を持っている』と言える。
       0を超えたかどうかではなく、ノイズの上端を超えたかどうかで判断する。"""
    vals = []
    for sd in range(seeds):
        r = evaluate(make_random_rule(rate, 1000 + sd), mode)
        if r:
            vals.append(r["avg_r"])
    if not vals:
        return None
    vals.sort()
    def pct(q):
        k = min(len(vals)-1, max(0, int(round(q * (len(vals)-1)))))
        return vals[k]
    return {"seeds": len(vals), "min": round(vals[0], 3), "p05": round(pct(0.05), 3),
            "median": round(pct(0.5), 3), "p95": round(pct(0.95), 3),
            "max": round(vals[-1], 3)}


def stop_width_sweep(mode, rule, mults=(1.0, 1.5, 2.0, 3.0)):
    """SL幅を広げるとスプレッドが1Rに占める割合は下がる。

       有望候補は素の優位性(+0.052R)とスプレッド(0.043R)がほぼ同じ大きさで、
       手取りが0付近から動かなかった。SLを広げてコスト比率を下げたときに
       素の優位性が残るなら手取りは改善するし、消えるならそれは幅に依存した
       見かけの優位でしかなかったということ。"""
    base = F.PARAMS[mode]
    out = []
    for m in mults:
        F.P = dict(base); F.P["slm"] = round(base["slm"] * m, 3)
        r = evaluate(rule, mode)
        if r:
            out.append({"slm_mult": m, "slm": F.P["slm"], **r})
    F.P = base
    return out


def run_mode(mode):
    days, cap = WINDOWS.get(mode, (90, 9000))
    F.MODE = mode; F.P = F.PARAMS[mode]
    F.TECH_W, F.FUND_W = (0.45, 0.55) if mode == "swing" else (0.85, 0.15)
    F.STATS_DAYS = {m: days for m in F.PARAMS}
    F.STATS_MAX_BARS = {m: cap for m in F.PARAMS}
    # klinesの日次キャッシュはキーに足が入っているのでモードをまたいで使い回す
    for c in (F._OHLC_CACHE, F._MTF_CACHE, F._SCORE_CACHE, F._SERIES_CACHE):
        c.clear()

    rules = dict(RULES)
    base = evaluate(rules["current"][0], mode)
    floor = None
    # 対照群は現行と同じくらいの頻度に合わせる
    if base:
        bars = F.STATS_MAX_BARS[mode]
        rate = min(1.0, base["n"] / max(1, bars * len(F.SYMBOLS)) * 4)
        rules["random"] = (make_random_rule(rate, 7), "★対照群：無作為エントリー")
        floor = noise_floor(mode, rate)

    out = {}
    for key, (rule, label) in rules.items():
        whole = base if key == "current" else evaluate(rule, mode)
        if not whole:
            print(f"[INFO] {mode}/{key}: 母数不足", file=sys.stderr); continue
        first = evaluate(rule, mode, (0.0, 0.5))
        second = evaluate(rule, mode, (0.5, 1.0))
        out[key] = {"label": label, "all": whole,
                    "first_half": first, "second_half": second}
    # 素の実力が最も高い候補について、SL幅を変えた場合も見る
    best_key = max(out, key=lambda k: out[k]["all"].get("avg_r_gross", -9)) if out else None
    widths = None
    if best_key and best_key != "random":
        widths = {"rule": best_key, "label": out[best_key]["label"],
                  "rows": stop_width_sweep(mode, rules[best_key][0])}
    return {"days": days, "rules": out, "noise_floor": floor, "stop_width": widths}


def main():
    report = {"generated_at": datetime.datetime.now(F.JST).strftime("%Y-%m-%d %H:%M JST"),
              "note": "判定ロジック候補の比較。randomは対照群（-スプレッド付近に出るのが正常）。",
              "modes": {}}
    for mode in MODES:
        if mode not in F.PARAMS:
            continue
        r = run_mode(mode)
        report["modes"][mode] = r
        print(f"\n■ {mode}モード（{r['days']}日）")
        nf = r.get("noise_floor")
        if nf:
            print(f"  ノイズの範囲（無作為エントリー{nf['seeds']}通り）: "
                  f"{nf['min']:+.3f} 〜 {nf['max']:+.3f}（中央値{nf['median']:+.3f} / 上位5%は{nf['p95']:+.3f}）")
            print(f"  → 候補は {nf['p95']:+.3f} を超えて初めて『ただのブレではない』と言える")
        print(f"  {'候補':22} {'件数':>6} {'期待R':>8} {'95%区間':>20}  {'前半→後半':>18}")
        for key, v in sorted(r["rules"].items(), key=lambda kv: -(kv[1]["all"]["avg_r"])):
            a = v["all"]
            j = "判定不能" if a["ci_lo"] < 0 < a["ci_hi"] else ("★プラス" if a["ci_lo"] > 0 else "✗マイナス")
            f_, s_ = v.get("first_half"), v.get("second_half")
            oos = (f"{f_['avg_r']:+.3f}→{s_['avg_r']:+.3f}" if f_ and s_ else "—")
            beat = "  ← ノイズ超え" if (nf and a["avg_r"] > nf["p95"]) else ""
            print(f"  {v['label']:22} {a['n']:6} {a['avg_r']:+8.3f} "
                  f"[{a['ci_lo']:+.3f}〜{a['ci_hi']:+.3f}] {j:8} {oos:>18}{beat}")
        w = r.get("stop_width")
        if w and w.get("rows"):
            print(f"\n  SL幅を変えた場合（{w['label']}）")
            print(f"    {'SL倍率':>8} {'件数':>6} {'控除前':>8} {'コスト':>7} {'手取り':>8} {'95%区間':>19}")
            for row in w["rows"]:
                print(f"    ×{row['slm_mult']:<7.1f} {row['n']:6} {row.get('avg_r_gross',0):+8.3f} "
                      f"{-row['cost_r']:+7.3f} {row['avg_r']:+8.3f} "
                      f"[{row['ci_lo']:+.3f}〜{row['ci_hi']:+.3f}]")
    F.write_json(OUT, report)
    print(f"\n[OK] {OUT} に保存")


if __name__ == "__main__":
    main()
