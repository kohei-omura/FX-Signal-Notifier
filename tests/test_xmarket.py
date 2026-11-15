# -*- coding: utf-8 -*-
"""株×FX 連動（xmarket.py / xmarket.js）のテスト。ネットワーク不要・通知は送らない。

  python -m unittest discover -s tests -v

xmarket.py と xmarket.js は4つのリポジトリに同じ内容を置く共通ファイル。
ここでは (1) 判定の型が正しいこと (2) 壊れた入力でも落ちないこと (3) Python と JS が同じ結果を出すこと を確認する。
(3) は node がある時だけ実行する。このテストファイルも各リポジトリで同じ内容。
"""
import importlib.util
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _find(name):
    """リポジトリごとに置き場所が違う（直下 / engine/ / kabu/）ので探す。"""
    for sub in ("", "engine", "kabu"):
        p = os.path.join(ROOT, sub, name)
        if os.path.isfile(p):
            return p
    return None


PY, JS = _find("xmarket.py"), _find("xmarket.js")
spec = importlib.util.spec_from_file_location("xmarket_under_test", PY)
X = importlib.util.module_from_spec(spec)
spec.loader.exec_module(X)

NOW = X.parse_ms("2026-11-16 10:00 JST")


def docs(label="上昇基調", us=0.8, h=(1, 1), cross=(1, 1), yen=-0.4, level="tail", us10y=(4.5, 0.0),
         sox=0.5, usdjpy_chg=0.1, t_k="2026.11.16 09:40", t_g="2026-11-16 06:10",
         t_f="2026-11-16 09:55 JST", t_c="2026-11-16 09:05 JST"):
    kd = {"generated": t_k, "regime": {"label": label, "icon": "☀", "chg20": 3.2, "price": 61000, "chg1": 0.4}}
    gp = {"asof": t_g, "idx": {"SP500": {"price": 7500, "chg": us}, "NASDAQ": {"price": 25000, "chg": us},
                               "DOW": {"price": 52000, "chg": us}, "SOX": {"price": 10000, "chg": sox},
                               "US10Y": {"price": us10y[0], "chg": us10y[1]},
                               "USDJPY": {"price": 159.88, "chg": usdjpy_chg}}}
    fxs = {"generated_at": t_f, "market_open": True, "pairs": [
        {"symbol": "USD_JPY", "bid": 159.7, "mtf": {"1hour": h[0], "4hour": h[1]}},
        {"symbol": "EUR_JPY", "bid": 176.0, "mtf": {"1hour": cross[0], "4hour": cross[1]}},
        {"symbol": "AUD_JPY", "bid": 108.2, "mtf": {"1hour": cross[0], "4hour": cross[1]}}]}
    cs = {"generated_at": t_c, "link": {"yen": {"score": yen, "level": level}}}
    return kd, gp, fxs, cs


def run(*a, **k):
    return X.judge(X.normalize(*docs(*a, **k), NOW))


class RegimeTest(unittest.TestCase):
    def test_risk_on(self):
        r = run()
        self.assertEqual((r["code"], r["lv"], r["s"], r["y"]), ("on", 2, 1, 1))
        self.assertIn("追い風", r["advice"]["carry"])

    def test_risk_off(self):
        r = run(label="下落基調", us=-0.9, h=(-1, -1), cross=(-1, -1), yen=0.6, level="warn")
        self.assertEqual((r["code"], r["lv"]), ("off", -2))
        self.assertIn("買いシグナルは逆風", r["advice"]["fx"])
        self.assertTrue(any("警戒水準" in n for n in r["notes"]))

    def test_exception_stock_down_yen_weak_by_rates(self):
        """株安＋円安＋米金利上昇 → 金利主導の説明になる"""
        r = run(label="下落基調", us=-0.9, h=(1, 1), cross=(1, 1), us10y=(5.0, 3.0))
        self.assertEqual(r["code"], "rate_dn")
        self.assertEqual(r["rate"], 1)
        self.assertIn("金利上昇が主役", r["desc"])

    def test_exception_stock_up_yen_strong(self):
        r = run(label="上昇基調", us=0.9, h=(-1, -1), cross=(-1, -1), us10y=(4.0, -3.0))
        self.assertEqual(r["code"], "rate_up")
        self.assertIn("米金利の低下が主役", r["desc"])
        r2 = run(label="上昇基調", us=0.9, h=(-1, -1), cross=(-1, -1), us10y=(4.0, 0.0))
        self.assertEqual(r2["code"], "rate_up")
        self.assertNotIn("米金利の低下が主役", r2["desc"])

    def test_soft_and_flat(self):
        self.assertEqual(run(h=(0, 0), cross=(0, 0))["code"], "on_soft")
        self.assertEqual(run(label="下落基調", us=-0.5, h=(0, 0), cross=(0, 0))["code"], "off_soft")
        self.assertEqual(run(label="もみ合い", us=0.0, h=(1, 1), cross=(1, 1))["code"], "yen_weak")
        self.assertEqual(run(label="もみ合い", us=0.0, h=(-1, -1), cross=(-1, -1))["code"], "yen_strong")
        self.assertEqual(run(label="もみ合い", us=0.0, h=(0, 0), cross=(0, 0))["code"], "flat")

    def test_dollar_flat_but_cross_down_counts_as_yen_strength(self):
        """ドル円が横ばいでも、クロス円が下げていれば円高として扱う（リスク回避でよく出る形）"""
        r = run(label="下落基調", us=-0.5, h=(0, 0), cross=(-1, -1))
        self.assertEqual(r["code"], "off")
        self.assertTrue(any("ドルと円が同時に買われる" in n for n in r["notes"]))

    def test_every_regime_has_advice_for_every_app(self):
        for app in ("kabu", "globe", "fx", "carry"):
            self.assertEqual(set(X.ADVICE[app]), set(X.REGIMES))
            for code in X.REGIMES:
                self.assertTrue(X.ADVICE[app][code])


class FallbackTest(unittest.TestCase):
    def test_stale_fx_falls_back_to_carry_yen(self):
        kd, gp, fxs, cs = docs(h=(1, 1), cross=(1, 1), yen=0.6, level="warn", t_f="2026-11-16 07:00 JST")
        inp = X.normalize(kd, gp, fxs, cs, NOW)
        self.assertIsNone(inp["fx"])                    # 3時間前のFXは使わない
        r = X.judge(inp)
        self.assertEqual((r["ysrc"], r["y"]), ("carry", -1))

    def test_no_fx_apps_falls_back_to_usdjpy_change(self):
        kd, gp, _, _ = docs(usdjpy_chg=0.9)
        r = X.judge(X.normalize(kd, gp, None, None, NOW))
        self.assertEqual((r["ysrc"], r["y"]), ("globe", 1))

    def test_only_fx_available(self):
        _, _, fxs, _ = docs(h=(-1, -1), cross=(-1, -1))
        r = X.judge(X.normalize(None, None, fxs, None, NOW))
        self.assertEqual((r["code"], r["stock_txt"]), ("yen_strong", "不明"))

    def test_nothing_available(self):
        self.assertIsNone(X.judge(X.normalize(None, None, None, None, NOW)))
        self.assertEqual(X.line("fx", None), "")

    def test_old_stock_data_is_ignored(self):
        kd, gp, fxs, cs = docs(t_k="2026.11.01 15:56", t_g="2026-11-01 06:10")
        inp = X.normalize(kd, gp, fxs, cs, NOW)
        self.assertIsNone(inp["jp"])
        self.assertIsNone(inp["us"])

    def test_broken_inputs_do_not_raise(self):
        bad = [None, [], "x", 3, {"regime": []}, {"regime": {"label": ["a"]}, "generated": 5},
               {"idx": {"SP500": 1}, "asof": "2026-11-16 06:10"}, {"pairs": [1, None, {"symbol": 5, "mtf": "x"}],
               "generated_at": "2026-11-16 09:55 JST"}, {"link": {"yen": {"score": "0.5"}}, "generated_at": "2026-11-16 09:05 JST"},
               {"link": [], "generated_at": True}]
        for a in bad:
            for b in bad:
                X.judge(X.normalize(a, b, a, b, NOW))
                X.judge(X.normalize(b, a, b, a, NOW))

    def test_line_format(self):
        t = X.line("kabu", run())
        self.assertTrue(t.startswith("🌐 株×FX地合い: ☀ リスクオン（株:強い／円:円安）\n  → "))
        self.assertEqual(X.line("unknown", run()), "")


class SnapshotTest(unittest.TestCase):
    """取得まわり：失敗しても例外を出さず、空文字になる（通知の本文を壊さない）。"""

    def setUp(self):
        self._get = X._get_json

    def tearDown(self):
        X._get_json = self._get

    def test_notify_line_uses_own_docs_without_fetching(self):
        called = []
        X._get_json = lambda u: called.append(u) or None
        kd, gp, fxs, cs = docs()
        t = X.notify_line("carry", own={"kabu": kd, "globe": gp, "fx": fxs, "carry": cs}, now_ms=NOW)
        self.assertEqual(called, [])
        self.assertTrue(t.startswith("\n\n🌐 株×FX地合い"))

    def test_one_sided_view_is_not_notified(self):
        """株か為替の片方しか読めない時は足さない（各アプリが元々出している内容と同じになるため）"""
        X._get_json = lambda u: None
        kd, gp, fxs, cs = docs()
        self.assertEqual(X.notify_line("fx", own={"fx": fxs}, now_ms=NOW), "")
        self.assertEqual(X.notify_line("kabu", own={"kabu": kd}, now_ms=NOW), "")
        self.assertNotEqual(X.notify_line("kabu", own={"kabu": kd, "fx": fxs}, now_ms=NOW), "")

    def test_fetch_failure_gives_empty(self):
        def boom(u):
            raise RuntimeError("network down")
        X._get_json = boom
        self.assertEqual(X.notify_line("fx"), "")
        X._get_json = lambda u: None
        self.assertEqual(X.notify_line("fx"), "")


@unittest.skipUnless(shutil.which("node") and JS, "node が無い環境では JS との一致テストを省略")
class ParityTest(unittest.TestCase):
    """Python(xmarket.py) と JS(xmarket.js) が、同じ入力に同じ判定・同じ文言を返す。"""

    def _cases(self):
        rnd = random.Random(20261115)
        labels = ["上昇基調", "もみ合い", "下落基調", "不明"]
        ts = ["2026-11-16 09:55 JST", "2026-11-16 07:00 JST", "2026.11.16 09:40", "2026-11-01 06:10", "2026-02-30 10:00", "x", None]
        cases = []
        for _ in range(400):
            f = lambda lo, hi: round(rnd.uniform(lo, hi), rnd.choice([1, 2, 3]))
            h = lambda: rnd.choice([-1, 0, 1, None, 0.5])
            kd = rnd.choice([None, {"generated": rnd.choice(ts), "regime": {
                "label": rnd.choice(labels), "chg1": f(-3, 3), "chg20": f(-9, 9), "price": rnd.choice([61142, None])}}])
            idx = {k: {"price": f(1, 9), "chg": rnd.choice([f(-4, 4), None, 0, -100])}
                   for k in ("SP500", "NASDAQ", "DOW", "SOX", "US10Y", "USDJPY") if rnd.random() < 0.85}
            gp = rnd.choice([None, {"asof": rnd.choice(ts), "idx": idx}])
            pairs = [{"symbol": s, "bid": f(100, 200), "mtf": {"1hour": h(), "4hour": h()}}
                     for s in ("USD_JPY", "EUR_JPY", "GBP_JPY", "AUD_JPY", "AUD_NZD") if rnd.random() < 0.8]
            fxs = rnd.choice([None, {"generated_at": rnd.choice(ts), "pairs": pairs}])
            cs = rnd.choice([None, {"generated_at": rnd.choice(ts), "link": {"yen": {
                "score": rnd.choice([f(-1, 1), None]), "level": rnd.choice(["warn", "soft", "tail", "neutral", None])}}}])
            cases.append([kd, gp, fxs, cs])
        return cases

    def test_python_and_js_agree(self):
        cases = self._cases()
        with tempfile.TemporaryDirectory() as td:
            with open(os.path.join(td, "cases.json"), "w", encoding="utf-8") as f:
                json.dump({"now": NOW, "cases": cases}, f, ensure_ascii=False)
            script = ("const X=require(%s);const fs=require('fs');"
                      "const d=JSON.parse(fs.readFileSync(%s,'utf8'));"
                      "const out=d.cases.map(c=>{const r=X.judge(X.normalize(c[0],c[1],c[2],c[3],d.now));"
                      "return {r:r,l:['kabu','globe','fx','carry'].map(a=>X.line(a,r))};});"
                      "process.stdout.write(JSON.stringify(out));"
                      % (json.dumps(JS), json.dumps(os.path.join(td, "cases.json"))))
            res = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(res.returncode, 0, res.stderr)
        got = json.loads(res.stdout)
        self.assertEqual(len(got), len(cases))
        kinds = set()
        for c, g in zip(cases, got):
            r = X.judge(X.normalize(c[0], c[1], c[2], c[3], NOW))
            self.assertEqual(g["r"], r, "入力: %s" % json.dumps(c, ensure_ascii=False))
            self.assertEqual(g["l"], [X.line(a, r) for a in ("kabu", "globe", "fx", "carry")])
            kinds.add(r["code"] if r else None)
        self.assertGreaterEqual(len(kinds), 8, "テストケースが相場の型を十分に網羅していない: %s" % kinds)

    def test_parse_ms_agrees(self):
        samples = ["2026-11-15 10:55 JST", "2026.11.15 10:43", "2026-11-15T10:48", "2026-02-30 10:00",
                   "2026-13-01 10:00", "2026-11-15 24:00", "2026-11-15", "", "abc"]
        script = ("const X=require(%s);process.stdout.write(JSON.stringify(%s.map(X.parseMs)));"
                  % (json.dumps(JS), json.dumps(samples)))
        res = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(json.loads(res.stdout), [X.parse_ms(s) for s in samples])

    def test_portfolio_and_markets(self):
        """4アプリ合計（画面だけの計算）：円高1%の影響と、税区分ごとの損益。"""
        d = {"gp": {"idx": {"USDJPY": {"price": 160.0}}, "px": {"GOOGL": 300.0},
                    "mkt": {"open_ms": NOW - 1000, "close_ms": NOW + 1000}},
             "gh": {"items": [{"c": "GOOGL", "buy": 290.0, "qty": 5}]},
             "kh": {"items": [{"c": "7803", "buy": 400.0, "p": 480}]}, "kp": {"px": {"7803": 440}},
             "fxs": {"generated_at": "2026-11-16 09:55 JST", "market_open": True,
                     "open_positions": [{"symbol": "USD_JPY", "side": "long", "lot": 10000, "current": 160.0, "yen": -1200},
                                        {"symbol": "EUR_JPY", "side": "short", "lot": 1000, "current": 176.0, "yen": 300}]},
             "cs": {"pairs": [{"symbol": "MXN_JPY", "bid": 8.5, "qjpy": 1.0}, {"symbol": "AUD_NZD", "bid": 1.1, "qjpy": 97.0}]},
             "cp": {"positions": [{"sym": "MXN_JPY", "entry": 8.0, "lot": 2}, {"sym": "AUD_NZD", "entry": 1.0, "lot": 1}]}}
        script = ("const X=require(%s);const d=%s;process.stdout.write(JSON.stringify("
                  "{p:X.portfolio(d),m:X.markets(d,%d),e:X.portfolio(null),w:X.markets({},%d)}));"
                  % (json.dumps(JS), json.dumps(d), NOW, X.parse_ms("2026-11-15 12:00 JST")))
        res = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(res.returncode, 0, res.stderr)
        o = json.loads(res.stdout)
        p = o["p"]
        self.assertAlmostEqual(p["us"]["value"], 300 * 5 * 160)                 # 24万円
        self.assertAlmostEqual(p["us"]["pl"], 10 * 5 * 160)
        self.assertAlmostEqual(p["jp"]["avg"], 10.0)                            # 価格は prices.json を優先
        self.assertAlmostEqual(p["fx"]["expo"], 10000 * 160 - 1000 * 176)
        self.assertAlmostEqual(p["fx"]["pl"], -900)
        self.assertAlmostEqual(p["carry"]["expo"], 2 * 10000 * 8.5)             # AUD/NZD は円の影響に入れない
        self.assertAlmostEqual(p["carry"]["pl"], 0.5 * 20000 + 0.1 * 10000 * 97.0)
        self.assertAlmostEqual(p["stress"]["total"], -(240000 + 1424000 + 170000) * 0.01)
        self.assertTrue(p["stress"]["stacked"])
        self.assertEqual(o["e"]["stress"]["total"], 0)
        self.assertEqual((o["m"]["tokyo"], o["m"]["us"], o["m"]["fx"]), (True, True, True))   # 月曜10時
        self.assertEqual((o["w"]["tokyo"], o["w"]["us"], o["w"]["fx"]), (False, None, False))  # 日曜


if __name__ == "__main__":
    unittest.main()
