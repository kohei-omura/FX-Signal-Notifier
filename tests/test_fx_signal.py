#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fx_signal.py のリグレッションテスト（ネットワーク不要）。

    python3 -m unittest discover -s tests -v
    python3 tests/test_fx_signal.py

主に「壊れたら気づけないもの」を守る:
  - 指標の系列版が、バーごとに計算し直した値と一致すること（高速化の前提）
  - 価格が取れない回に画面の表示内容を消さないこと
  - API障害でクラッシュしたり通知が二重に飛んだりしないこと
"""
import json, os, random, shutil, sys, tempfile, types, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mock_api                                   # noqa: E402
sys.modules["requests"] = mock_api                # fx_signal の import より前に差し込む
os.environ.pop("MODE", None)
import fx_signal as F                             # noqa: E402
F.requests = mock_api


def sample_ohlc(n=600, seed=1234):
    rnd = random.Random(seed)
    out, px = [], 159.0
    for _ in range(n):
        px += rnd.gauss(0, 0.03)
        out.append((px + abs(rnd.gauss(0, 0.02)), px - abs(rnd.gauss(0, 0.02)), px))
    return out


class IndicatorSeriesTest(unittest.TestCase):
    """系列版 == バーごとの計算し直し。ここが崩れると統計が静かに狂う。"""

    def setUp(self):
        F.MODE = "day"; F.P = F.PARAMS["day"]
        self.oh = sample_ohlc()
        self.closes = [r[2] for r in self.oh]

    def _same(self, a, b, where):
        if a is None or b is None:
            self.assertEqual(a is None, b is None, where)
        else:
            self.assertAlmostEqual(a, b, places=10, msg=where)

    def test_series_match_per_bar_recompute(self):
        P = F.P
        rs = F.rsi_series(self.closes, P["rsi"])
        ats = F.atr_series(self.oh, P["atr"])
        axs = F.adx_series(self.oh, P["adx"])
        mds = F.macd_hist_series(self.closes, *P["macd"])
        bbs = F.bb_series(self.closes, P["bb"][0], P["bb"][1])
        for k in range(20, len(self.oh)):
            self._same(F.rsi(self.closes[:k+1], P["rsi"]), rs[k], f"RSI@{k}")
            self._same(F.atr(self.oh[:k+1], P["atr"]), ats[k], f"ATR@{k}")

            ax = F.adx(self.oh[:k+1], P["adx"])
            self.assertEqual(ax is None, axs[k] is None, f"ADX有無@{k}")
            if ax:
                for i in range(3):
                    self._same(ax[i], axs[k][i], f"ADX[{i}]@{k}")

            md = F.macd(self.closes[:k+1], *P["macd"])
            self.assertEqual(md is None, mds[k] is None, f"MACD有無@{k}")
            if md:
                self._same(md[2], mds[k][0], f"MACDヒスト@{k}")
                self._same(md[3], mds[k][1], f"MACDヒスト(前)@{k}")

            bb = F.bollinger(self.closes[:k+1], P["bb"][0], P["bb"][1])
            self.assertEqual(bb is None, bbs[k] is None, f"BB有無@{k}")
            if bb:
                self._same(bb[0], bbs[k][0], f"BB中心@{k}")
                self._same(bb[3], bbs[k][1], f"BB標準偏差@{k}")


class RunTestCase(unittest.TestCase):
    """main() を一時ディレクトリで走らせる共通土台。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        p = lambda n: os.path.join(self.dir, n)
        F.STATUS_FILE = p("status.json"); F.POSITIONS_FILE = p("positions.json")
        F.ENTRY_LOG_FILE = p("entry_log.json"); F.NEWS_FILE = p("news_blackout.json")
        F.MODE_FILE = p("mode.json")
        self.write(F.MODE_FILE, {"mode": "day"})
        self.write(F.POSITIONS_FILE, {"positions": [
            {"id": "t1", "symbol": "USD_JPY", "side": "long",
             "entry": 158.50, "lot": 10000, "auto": True, "status": "open"}]})
        # バックオフの実待ちでテストが遅くなるのを避ける（眠った回数だけ数える）
        self.slept = []
        self._real_time = F.time
        F.time = types.SimpleNamespace(sleep=self.slept.append)
        self.addCleanup(setattr, F, "time", self._real_time)
        self.sent = {"line": [], "mail": []}
        F.notify_line = lambda t: self.sent["line"].append(t)
        F.notify_mail = lambda s, b: self.sent["mail"].append((s, b))
        mock_api.fail_mode = None
        self.addCleanup(setattr, mock_api, "fail_mode", None)
        self.reset_caches()

    @staticmethod
    def write(path, obj):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)

    @staticmethod
    def read(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def reset_caches(self):
        for n in ("_OHLC_CACHE", "_KLINE_DAY_CACHE", "_MTF_CACHE", "_SCORE_CACHE", "_kline_locks"):
            getattr(F, n).clear()
        F._WARNINGS.clear(); F._NEWS_CACHE = None; F._fail_streak[0] = 0
        mock_api.reset()

    def status(self):
        return self.read(F.STATUS_FILE)


class PriceOutageTest(RunTestCase):
    def test_status_is_preserved_when_ticker_fails(self):
        """価格が1件も取れない回に status.json を潰さない。
        （潰すと画面から価格も保有ポジションも消え、TP/SL監視も黙って飛ぶ）"""
        F.main()
        before = self.status()
        self.assertTrue(all(p["bid"] for p in before["pairs"]))
        self.assertEqual(len(before["open_positions"]), 1)

        self.reset_caches()
        F.fetch_ticker = lambda *a, **k: (F.warn("ticker失敗", tag="ticker") or {})
        try:
            F.main()
        finally:
            del F.fetch_ticker
        after = self.status()
        self.assertEqual([p["bid"] for p in after["pairs"]], [p["bid"] for p in before["pairs"]])
        self.assertEqual(after["open_positions"], before["open_positions"])
        # generated_at は更新しない（画面の「更新停止」検知を正しく効かせるため）
        self.assertEqual(after["generated_at"], before["generated_at"])
        self.assertTrue(after.get("degraded"))
        self.assertTrue(after.get("warnings"))


class ApiFailureTest(RunTestCase):
    def _run_under(self, fail):
        mock_api.fail_mode = fail
        self.reset_caches()
        F.main()

    def test_total_outage_does_not_crash(self):
        def dead(url, params):
            raise ConnectionError("network down")
        self._run_under(dead)
        self.assertTrue(F._WARNINGS)

    def test_random_failures_are_retried(self):
        rnd = random.Random(9)
        def flaky(url, params):
            if url.endswith("/klines") and rnd.random() < 0.3:
                raise TimeoutError("read timeout")
            return None
        self._run_under(flaky)
        st = self.status()
        self.assertEqual(len(st["pairs"]), len(F.SYMBOLS))
        self.assertTrue(all(p["bid"] for p in st["pairs"]))

    def test_rate_limit_and_maintenance_do_not_crash(self):
        self._run_under(lambda url, params: mock_api.Resp({}, 429) if url.endswith("/klines") else None)
        self.reset_caches()
        self._run_under(lambda url, params: mock_api.Resp({"status": 5, "messages": []}))
        self.reset_caches()
        self._run_under(lambda url, params: mock_api.Resp(None))

    def test_circuit_breaker_stops_retrying(self):
        """API全断のとき延々リトライして1回の実行が数分に伸びないこと。"""
        def dead(url, params):
            raise ConnectionError("down")
        self._run_under(dead)
        # ブレーカー無しなら失敗1件につき2回眠るので、待ち時間の合計が数分になる
        self.assertLess(sum(self.slept), 30,
                        f"サーキットブレーカーが効いていない（合計{sum(self.slept)}秒待機）")


class NewsCalendarTest(RunTestCase):
    def _write_calendar(self, generated_at):
        self.write(F.NEWS_FILE, {"generated_at": generated_at, "events":
                                 [{"country": "USD", "time": "2030-01-01 00:00", "title": "x"}]})

    def test_stale_calendar_warns(self):
        import datetime
        old = (datetime.datetime.now(F.JST) - datetime.timedelta(days=10)).strftime("%Y-%m-%d %H:%M JST")
        self._write_calendar(old)
        F.main()
        self.assertIn("news-stale", [w["tag"] for w in F._WARNINGS])

    def test_fresh_calendar_is_quiet(self):
        import datetime
        now = datetime.datetime.now(F.JST).strftime("%Y-%m-%d %H:%M JST")
        self._write_calendar(now)
        F.main()
        self.assertNotIn("news-stale", [w["tag"] for w in F._WARNINGS])

    def test_missing_calendar_warns(self):
        F.main()
        self.assertIn("news-missing", [w["tag"] for w in F._WARNINGS])


class EntryLogTest(RunTestCase):
    def test_failed_scoring_is_not_recorded_as_zero(self):
        """判定材料が取れない回に score 0.0 / 強さ「弱」を捏造しない。
        （記録簿は成績検証に使うので、架空の弱エントリーが混ざると集計が狂う）"""
        real = F.score_pair
        F.score_pair = lambda *a, **k: None
        try:
            F.stamp_new_entries(self.read(F.POSITIONS_FILE))
        finally:
            F.score_pair = real
        rec = self.read(F.ENTRY_LOG_FILE)["entries"][0]
        self.assertIsNone(rec["score"])
        self.assertIsNone(rec["tech"])
        self.assertEqual(rec["strength"], "不明")


class NotificationTest(RunTestCase):
    def test_subject_summarises_content(self):
        F.MODE = "day"
        s = F.mail_subject([("買い", "USD_JPY"), ("売り", "AUD_JPY")], [("cut", "GBP_JPY")], 2)
        self.assertIn("🛑損切り GBP/JPY", s)
        self.assertIn("🟢買い USD/JPY", s)
        self.assertIn("🔴売り AUD/JPY", s)
        self.assertTrue(s.index("損切り") < s.index("買い"), "緊急度順になっていない")

    def test_subject_has_fallback(self):
        F.MODE = "day"
        self.assertEqual(F.mail_subject([], [], 0), "【FX/day】シグナル通知")

    def test_same_signal_is_not_notified_twice(self):
        F.main()
        first = len(self.sent["line"]) + len(self.sent["mail"])
        self.assertGreater(first, 0, "1回目で通知が出ていない（前提が崩れている）")
        self.reset_caches()
        self.sent["line"].clear(); self.sent["mail"].clear()
        F.main()
        for _, body in self.sent["mail"]:
            self.assertNotIn("エントリー目安", body, "同じシグナルが再通知されている")


class MtfTest(RunTestCase):
    def test_year_indexed_timeframe_falls_back_to_previous_year(self):
        """年明け直後、当年のバーが足りない時に前年も取りに行くこと。
        （取りに行かないと常にレンジ扱いになり、上位足フィルタが黙って無効化される）"""
        years = []
        def few_bars_this_year(url, params):
            if params.get("interval") != "4hour":
                return None
            y = params.get("date"); years.append(y)
            n = 5 if y == str(F.datetime.datetime.now(F.JST).year) else 400
            data = [{"openTime": str(i*14400000), "open": "159", "high": "159",
                     "low": "159", "close": f"{159.0 + i*0.01:.4f}"} for i in range(n)]
            return mock_api.Resp({"status": 0, "data": data})
        mock_api.fail_mode = few_bars_this_year
        self.assertNotEqual(F.htf_trend("USD_JPY", "4hour"), 0, "レンジ扱いのまま")
        self.assertEqual(len(years), 2, "前年を取りに行っていない")

    def test_sufficient_data_does_not_fetch_extra_year(self):
        years = []
        def enough(url, params):
            if params.get("interval") != "4hour":
                return None
            years.append(params.get("date"))
            return None
        mock_api.fail_mode = enough
        F.htf_trend("USD_JPY", "4hour")
        self.assertEqual(len(years), 1, "通常時に余計な取得が発生している")


class MfeTest(RunTestCase):
    def test_peak_includes_current_price_on_first_evaluation(self):
        """初回評価でも現在値を最高益に取り込む（トレール利確の押し戻し量がズレる）。"""
        F._OHLC_CACHE["USD_JPY"] = [(159.0, 159.0, 159.0)] * 5
        adv = F.position_advice(
            {"id": "x", "symbol": "USD_JPY", "side": "long", "entry": 158.0,
             "lot": 10000, "tp_pips": 100, "sl_pips": 100},
            {"USD_JPY": {"bid": 159.0, "ask": 159.005}},
            {"atr": 0.5, "score": 0.05, "rsi": 55, "adx": 25}, None)
        self.assertAlmostEqual(adv["mfe"], 159.0, places=3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
