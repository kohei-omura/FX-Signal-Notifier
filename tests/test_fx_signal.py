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
        # del してしまうとモジュール本体の関数ごと消え、後続のテストが壊れる。必ず元に戻す。
        original = F.fetch_ticker
        F.fetch_ticker = lambda *a, **k: (F.warn("ticker失敗", tag="ticker") or {})
        try:
            F.main()
        finally:
            F.fetch_ticker = original
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


class WarningSeverityTest(RunTestCase):
    """画面に出す警告は『実際に表示が劣化した時』だけにする。
       内部のフォールバックで埋め合わせが効く失敗まで出すと、正常なのにエラーに見える。"""

    def setUp(self):
        super().setUp()
        import datetime
        self._write_fresh_calendar()
        self.today = datetime.datetime.now(F.JST).date().strftime("%Y%m%d")

    def _write_fresh_calendar(self):
        import datetime
        self.write(F.NEWS_FILE, {
            "generated_at": datetime.datetime.now(F.JST).strftime("%Y-%m-%d %H:%M JST"),
            "events": [{"country": "USD", "time": "2030-01-01 00:00", "title": "x"}]})

    def test_missing_current_day_does_not_alarm(self):
        """JSTの日付が変わった直後は当日ぶんの足がまだ無く毎日必ず空振りする。
        前日以前で埋まるので、これを画面のエラーとして出してはいけない。"""
        def newday(url, params):
            if url.endswith("/klines") and params.get("date") == self.today:
                return mock_api.Resp({})        # status も messages も無い応答
            return None
        mock_api.fail_mode = newday
        F.main()
        st = self.status()
        self.assertTrue(all(p["bid"] for p in st["pairs"]), "価格が欠けた")
        self.assertTrue(all(p.get("closes") for p in st["pairs"]), "ローソク足が欠けた")
        tags = [w["tag"] for w in st.get("warnings", [])]
        self.assertNotIn("api:/klines", tags, "自動で埋まる失敗を画面に出している")

    def test_total_kline_outage_does_alarm(self):
        """逆に、本当に1本も取れない時はきちんと画面に出すこと。"""
        def dead(url, params):
            return mock_api.Resp({}) if url.endswith("/klines") else None
        mock_api.fail_mode = dead
        F.main()
        tags = [w["tag"] for w in F._WARNINGS]
        self.assertTrue(any(t.startswith("ohlc:") for t in tags), f"足の欠損が出ていない: {tags}")
        self.assertTrue(any(t.startswith("mtf:") for t in tags), f"上位足の欠損が出ていない: {tags}")

    def test_unexpected_body_is_logged_not_swallowed(self):
        """status も messages も無い応答は、中身を残さないと後から原因を追えない。"""
        seen = []
        real_warn = F.warn
        F.warn = lambda m, tag=None, surface=True: (seen.append(m), real_warn(m, tag, False))
        try:
            mock_api.fail_mode = lambda url, params: (
                mock_api.Resp({"foo": "bar"}) if url.endswith("/klines") else None)
            F.klines_day("USD_JPY", self.today)
        finally:
            F.warn = real_warn
        self.assertTrue(any("foo" in m for m in seen),
                        f"応答の中身がログに残っていない: {seen[:2]}")

    def test_surface_false_keeps_it_off_the_dashboard(self):
        F.warn("画面には出さない", tag="quiet-one", surface=False)
        F.warn("画面に出す", tag="loud-one")
        tags = [w["tag"] for w in F._WARNINGS]
        self.assertNotIn("quiet-one", tags)
        self.assertIn("loud-one", tags)


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


class ExitPolicyTest(RunTestCase):
    """決済ポリシー比較。同じシグナルに対して出口だけ変えたRを並べる。"""

    def setUp(self):
        super().setUp()
        F.MODE = "day"; F.P = F.PARAMS["day"]

    def test_policies_are_produced_for_each_symbol(self):
        st = F.compute_signal_stats("USD_JPY")
        self.assertIsNotNone(st)
        self.assertIn("policies", st)
        for name in F.EXIT_POLICIES:
            self.assertIn(name, st["policies"], f"{name} が出ていない")
            p = st["policies"][name]
            self.assertGreater(p["n"], 0)
            self.assertIsNotNone(p["avg_r"])

    def test_all_policies_share_the_same_entries(self):
        """出口だけの比較なので、母数（エントリー数）は全ポリシーで一致していること。"""
        st = F.compute_signal_stats("EUR_JPY")
        ns = {st["policies"][k]["n"] for k in F.EXIT_POLICIES if k in st["policies"]}
        self.assertEqual(len(ns), 1, f"母数が揃っていない: {ns}")

    def test_tp_sl_policy_only_yields_designed_r(self):
        """TP/SLだけで回したポリシーは +設計RR か -1R しか取らない。"""
        st = F.compute_signal_stats("GBP_JPY")
        p = st["policies"]["tp_sl"]
        self.assertAlmostEqual(p["payoff"], F.P["tsr"], places=2)

    def test_existing_stats_keys_are_unchanged(self):
        st = F.compute_signal_stats("AUD_JPY")
        for k in ("n", "tp_winrate", "hold_tp_min", "hold_sl_min", "stats_ts", "stats_mode"):
            self.assertIn(k, st)

    def test_policies_reach_status_json(self):
        """画面が読むのは status.json なので、pairs に policies が載ること。"""
        F.main()
        st = self.status()
        self.assertTrue(any("policies" in p for p in st["pairs"]),
                        "status.json の pairs に policies が無い（画面に何も出ない）")

    def test_policies_survive_the_stats_cache(self):
        """統計キャッシュ経由でも policies が消えないこと（消えると1時間表示が空になる）。"""
        F.main()
        self.reset_caches()
        F.main()                       # 2回目はキャッシュを使う経路
        st = self.status()
        self.assertTrue(any("policies" in p for p in st["pairs"]),
                        "キャッシュ経由で policies が落ちている")

    def test_backtest_applies_the_live_mtf_filter(self):
        """本番は上位足と逆行するシグナルを通知しない。
        バックテストが同じ条件でないと、届かないシグナルまで混ざって比較の意味が無くなる。"""
        st = F.compute_signal_stats("USD_JPY")
        self.assertIsNotNone(st)
        # 上位足が常に下降なら「買い」は全部見送られ、採用数が減るはず
        real = F.htf_aligned_series
        F.htf_aligned_series = lambda sym, times, days: [-1] * len(times)
        try:
            self.reset_caches()
            blocked_st = F.compute_signal_stats("USD_JPY")
        finally:
            F.htf_aligned_series = real
        self.assertIsNotNone(blocked_st)
        self.assertGreater(blocked_st.get("mtf_blocked", 0), 0, "見送りが記録されていない")
        self.assertLess(blocked_st["n"], st["n"], "フィルタが採用数に効いていない")

    def test_mtf_series_uses_only_completed_bars(self):
        """過去に当てる時は、終値が確定したバーだけを見ること（未来の終値を覗かない）。"""
        times = [i * 900000 for i in range(200)]        # 15分足200本ぶんの時刻
        got = F.htf_aligned_series("USD_JPY", times, 8)
        self.assertEqual(len(got), len(times))
        self.assertTrue(all(v in (-1, 0, 1) for v in got))
        # 先頭は上位足の履歴が足りないので必ず0（判定不能）になる
        self.assertEqual(got[0], 0)

    def test_score_bands_are_reported(self):
        """スコアの強さ別。ここが右肩上がりでなければ、しきい値を動かしても効かない。"""
        st = F.compute_signal_stats("EUR_JPY")
        self.assertIn("bands", st)
        total = sum(b["n"] for b in st["bands"].values())
        self.assertEqual(total, st["policies"]["tp_sl"]["n"],
                         "スコア帯の合計が採用数と一致しない")
        for b in st["bands"].values():
            for name in F.EXIT_POLICIES:
                if name in b:
                    self.assertIsInstance(b[name], float)

    def test_r_summary_math(self):
        rows = [(1.6, 0.0), (1.6, 0.0), (-1.0, 0.0), (-1.0, 0.0), (-1.0, 0.0)]
        s = F._r_summary(rows)
        self.assertEqual(s["n"], 5)
        self.assertEqual(s["winrate"], 40)
        self.assertAlmostEqual(s["payoff"], 1.6, places=2)
        self.assertAlmostEqual(s["avg_r"], 0.04, places=3)
        self.assertAlmostEqual(s["pf"], 3.2/3.0, places=2)
        self.assertLess(s["ci_lo"], s["avg_r"])
        self.assertGreater(s["ci_hi"], s["avg_r"])

    def test_spread_is_deducted(self):
        """往復スプレッドを引くこと。引かないとSLが狭いほど数字が実態より良く出る。"""
        s = F._r_summary([(1.6, 0.1), (-1.0, 0.1)])
        self.assertAlmostEqual(s["avg_r_gross"], 0.3, places=3)
        self.assertAlmostEqual(s["avg_r"], 0.2, places=3)      # 0.3 - 0.1
        self.assertAlmostEqual(s["cost_r"], 0.1, places=3)

    def test_narrow_stop_costs_more(self):
        """実際の集計でもコストが計上され、控除前より必ず悪くなること。"""
        st = F.compute_signal_stats("GBP_JPY")     # スプレッド0.9pipsで最も重い
        p = st["policies"]["tp_sl"]
        self.assertGreater(p["cost_r"], 0, "コストが計上されていない")
        self.assertLess(p["avg_r"], p["avg_r_gross"])


class SpreadCostTest(RunTestCase):
    """スプレッドが1R(SL幅)に占める割合。実測では scalp が0.347Rで、
       勝率が10pt上がっても取り返せない水準だった。黙って通知を出し続けない。"""

    def test_cost_ratio_is_published_per_pair(self):
        F.main()
        for p in self.status()["pairs"]:
            self.assertIsNotNone(p.get("cost_r"), f"{p['symbol']} にコスト比率が無い")
            self.assertGreater(p["cost_r"], 0)

    def test_narrow_stop_mode_warns(self):
        """SL幅が狭すぎるモードでは警告を出すこと。"""
        self.write(F.MODE_FILE, {"mode": "scalp"})     # 1分足＝SLが最も狭い
        F.main()
        self.assertIn("cost", [w["tag"] for w in F._WARNINGS],
                      "コスト過大なのに警告が出ていない")

    def test_wide_stop_mode_is_quiet(self):
        self.write(F.MODE_FILE, {"mode": "swing"})     # 1時間足＝SLが広い
        F.main()
        self.assertNotIn("cost", [w["tag"] for w in F._WARNINGS])


class LongBacktestTest(RunTestCase):
    """長期バックテスト(backtest.py)。判定に優位性があるかを見るための母数を確保する。"""

    def setUp(self):
        super().setUp()
        import backtest
        self.bt = backtest
        self.out = os.path.join(self.dir, "backtest.json")
        self._orig_out = backtest.OUT
        backtest.OUT = self.out
        self.addCleanup(setattr, backtest, "OUT", self._orig_out)
        # 期間を詰めてテストを速く保つ（仕組みの検証が目的）
        self._orig_win = backtest.WINDOWS
        backtest.WINDOWS = {"scalp": (3, 800), "day": (3, 800), "swing": (3, 800)}
        self.addCleanup(setattr, backtest, "WINDOWS", self._orig_win)

    def test_writes_report_with_policies_and_bands(self):
        self.bt.MODES = ["day"]
        self.bt.main()
        d = self.read(self.out)
        self.assertIn("day", d["modes"])
        m = d["modes"]["day"]
        for k in ("days", "n", "policies", "bands", "symbols"):
            self.assertIn(k, m)
        for name in F.EXIT_POLICIES:
            self.assertIn(name, m["policies"])
        self.assertEqual(sum(b["n"] for b in m["bands"].values()),
                         m["policies"]["tp_sl"]["n"], "スコア帯の合計が採用数と合わない")

    def test_sample_is_larger_than_the_5min_stats(self):
        """長期検証の母数が、通常統計(直近8日)より多いこと。これが導入の目的。"""
        self.bt.WINDOWS = {"day": (12, 3000)}
        self.bt.MODES = ["day"]
        self.bt.main()
        deep = self.read(self.out)["modes"]["day"]["n"]
        self.reset_caches()
        F.MODE = "day"; F.P = F.PARAMS["day"]
        F.STATS_DAYS = {"day": 8}; F.STATS_MAX_BARS = {"day": 1000}
        short = sum((F.compute_signal_stats(s) or {}).get("n", 0) for s in F.SYMBOLS)
        self.assertGreater(deep, short, f"母数が増えていない（長期{deep} vs 通常{short}）")

    def test_engine_globals_are_restored_per_mode(self):
        """モードごとに TECH_W/FUND_W を本番と同じ値に合わせること。"""
        self.bt.MODES = ["swing"]
        self.bt.run_mode("swing")
        self.assertEqual((F.TECH_W, F.FUND_W), (0.45, 0.55))
        self.bt.run_mode("day")
        self.assertEqual((F.TECH_W, F.FUND_W), (0.85, 0.15))

    def test_pooled_ci_matches_the_raw_data(self):
        """通貨をまたいだ信頼区間が、生データから直接計算したものと一致すること
        （各通貨の区間を平均するだけでは正しくない）。"""
        import random
        rnd = random.Random(5)
        groups, allrows = [], []
        for _ in range(4):
            rows = [(rnd.choice([1.6, -1.0]), 0.05) for _ in range(120)]
            groups.append(F._r_summary(rows)); allrows += rows
        pooled = self.bt.pool_summary(groups)
        direct = F._r_summary(allrows)
        self.assertEqual(pooled["n"], direct["n"])
        self.assertAlmostEqual(pooled["avg_r"], direct["avg_r"], places=3)
        self.assertAlmostEqual(pooled["ci_lo"], direct["ci_lo"], places=2)
        self.assertAlmostEqual(pooled["ci_hi"], direct["ci_hi"], places=2)

    def test_report_carries_cost_and_ci(self):
        self.bt.MODES = ["day"]
        self.bt.main()
        pol = self.read(self.out)["modes"]["day"]["policies"]["tp_sl"]
        for k in ("cost_r", "ci_lo", "ci_hi", "avg_r_gross"):
            self.assertIn(k, pol, f"{k} が集計で落ちている")
        self.assertLessEqual(pol["ci_lo"], pol["avg_r"])
        self.assertGreaterEqual(pol["ci_hi"], pol["avg_r"])

    def test_failure_keeps_previous_report(self):
        """集計できない時に既存のレポートを壊さないこと。"""
        self.write(self.out, {"keep": True})
        self.bt.MODES = ["day"]
        mock_api.fail_mode = lambda url, params: mock_api.Resp({}) if url.endswith("/klines") else None
        with self.assertRaises(SystemExit):
            self.bt.main()
        self.assertTrue(self.read(self.out).get("keep"), "既存レポートが壊された")


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
