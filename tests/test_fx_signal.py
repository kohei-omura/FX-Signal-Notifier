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
import json, os, random, re, shutil, subprocess, sys, tempfile, time, types, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
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
        # 前向き検証のファイルもテスト用に逃がす。逃がさないと main() の決着判定が
        # リポジトリの data/ を読み書きしてしまう（テストが実データを壊す）。
        # 差し替える前に元を保存する（後だと差し替えた方を戻してしまう）。
        for _n in ("FWD_LOG_FILE", "FWD_CLOSE_FILE", "SUB_STATE_FILE", "NEAR_FILE"):
            self.addCleanup(setattr, F, _n, getattr(F, _n))
        F.FWD_LOG_FILE = p("forward_log.json")
        F.FWD_CLOSE_FILE = p("forward_close.json")
        F.SUB_STATE_FILE = p("sub_signals.json")
        F.NEAR_FILE = p("near_miss.json")
        self.write(F.MODE_FILE, {"mode": "day"})
        self.write(F.POSITIONS_FILE, {"positions": [
            {"id": "t1", "symbol": "USD_JPY", "side": "long",
             "entry": 158.50, "lot": 10000, "auto": True, "status": "open"}]})
        # バックオフの実待ちでテストが遅くなるのを避ける（眠った回数だけ数える）
        self.slept = []
        self._real_time = F.time
        F.time = types.SimpleNamespace(sleep=self.slept.append)
        self.addCleanup(setattr, F, "time", self._real_time)
        # backtest.run_mode などがモジュール全体の設定を書き換えるため、
        # テストごとに必ず元へ戻す（前のテストの設定が次に漏れないように）
        for name in ("STATS_DAYS", "STATS_MAX_BARS", "MODE", "P", "TECH_W", "FUND_W",
                     "ACCOUNT_JPY", "RISK_CAP_PCT"):
            self.addCleanup(setattr, F, name, getattr(F, name))
        self.sent = {"line": [], "mail": []}
        # 差し替える前の本物を残す。上書きしっぱなしだと、実際の送信処理を
        # 検証したいテストからは二度と本物に触れなくなる。
        self.real_notify_line, self.real_notify_mail = F.notify_line, F.notify_mail
        self.addCleanup(setattr, F, "notify_line", self.real_notify_line)
        self.addCleanup(setattr, F, "notify_mail", self.real_notify_mail)
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
        for n in ("_OHLC_CACHE", "_KLINE_DAY_CACHE", "_MTF_CACHE", "_SCORE_CACHE",
                  "_SERIES_CACHE", "_kline_locks"):
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


    def test_entry_notification_carries_absolute_oco_prices(self):
        """エントリー通知にpips幅だけでなく絶対価格を載せること。
        pipsだけだと受け手が建値から暗算する必要があり、その間に相場が動いて
        発注レベルがずれる（＝画面の目安値と建玉の確定値を取り違える原因）。"""
        # モックの値動き任せだと、シグナルが1本も出ない日があって落ちていた。
        # 検証したいのは通知の文面なので、保有をゼロにしたうえで確実に1本立てる。
        self.write(F.POSITIONS_FILE, {"positions": []})
        self.addCleanup(setattr, F, "entry_side", F.entry_side)
        F.entry_side = lambda sym, total, rv, th, aligned=None, pullback=None: (
            "買い" if sym == "USD_JPY" else None)
        F.main()
        bodies = [b for _, b in self.sent["mail"]]
        entry_bodies = [b for b in bodies if "エントリー目安" in b]
        self.assertTrue(entry_bodies, "エントリー通知が出ていない（前提が崩れている）")
        st = self.status()
        checked = 0
        for pair in st["pairs"]:
            if not pair.get("signal") or pair.get("entry_ref") is None:
                continue
            body = next((b for b in entry_bodies if pair["symbol"] in b), None)
            if body is None:
                continue
            d = 1 if pair["signal"] == "買い" else -1
            ref = float(pair["entry_ref"])
            tp = ref + d * pair["tp_pips"] * F.PIP_SIZE
            sl = ref - d * pair["sl_pips"] * F.PIP_SIZE
            self.assertIn(f"TP {tp:.3f} / SL {sl:.3f}", body,
                          f"{pair['symbol']} の通知に絶対価格が無い")
            checked += 1
        self.assertGreater(checked, 0, "シグナル付きのペアが1つも無い")


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

    def test_report_carries_pooled_atr_bands(self):
        """ツール画面が読む atr_bands が、通貨をまたいで合成された形で出ること。

        件数だけでなく信頼区間まで無いと「値幅が広い方が勝てる」の判定ができない。"""
        self.bt.MODES = ["day"]
        self.bt.main()
        m = self.read(self.out)["modes"]["day"]
        ab = m.get("atr_bands")
        self.assertTrue(ab, "atr_bands が無い")
        labels = [lab for _, lab in F.ATR_REGIME_BANDS]
        for name, row in ab.items():
            self.assertIn(name, labels)
            for pol in F.EXIT_POLICIES:
                if pol in row:
                    for k in ("n", "avg_r", "ci_lo", "ci_hi", "cost_r"):
                        self.assertIn(k, row[pol], f"{name}/{pol} に {k} が無い")
        self.assertEqual(sum(r["n"] for r in ab.values()) + m["atr_bands_warmup"],
                         m["policies"]["tp_sl"]["n"],
                         "値幅帯の合計＋区分なしが採用数と合わない（黙って消えている）")
        # 通貨別にも残っていること（1通貨だけで出ている差を見抜くために必要）
        per = [v for v in m["symbols"].values() if v.get("atr_bands")]
        self.assertTrue(per, "通貨別の atr_bands が落ちている")

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

    def test_threshold_sweep_changes_the_entry_set(self):
        """しきい値を上げれば採用数は必ず減る（事後の切り分けではなく運用ルールとして効く）。"""
        F.MODE = "day"; F.P = F.PARAMS["day"]
        low = F.compute_signal_stats("USD_JPY", th_override=0.40)
        self.reset_caches()
        high = F.compute_signal_stats("USD_JPY", th_override=0.70)
        self.assertIsNotNone(low, "基準のしきい値で採用が0だとこの検証が成立しない")
        # 件数が下限(8件)を割ると None が返る。それも「減った」に含める。
        high_n = high["n"] if high else 0
        self.assertLess(high_n, low["n"], "しきい値を上げても採用数が減っていない")
        if high:
            self.assertEqual(high["th"], 0.7)

    def test_entry_range_splits_without_overlap(self):
        """前半・後半で分けた採用数の合計が、全体とおおむね一致すること。"""
        F.MODE = "day"; F.P = F.PARAMS["day"]
        whole = F.compute_signal_stats("EUR_JPY")
        self.reset_caches()
        a = F.compute_signal_stats("EUR_JPY", entry_range=(0.0, 0.5))
        self.reset_caches()
        b = F.compute_signal_stats("EUR_JPY", entry_range=(0.5, 1.0))
        self.assertIsNotNone(whole)
        got = (a["n"] if a else 0) + (b["n"] if b else 0)
        # 境界をまたぐ1トレードぶんのズレは許容する
        self.assertLessEqual(abs(got - whole["n"]), 3, f"分割の合計が合わない {got} vs {whole['n']}")

    def test_holdout_reports_both_halves(self):
        """前半で選んだしきい値を後半で検証した結果が両方載ること。"""
        self.bt.MODES = ["day"]
        h = self.bt.holdout("day")
        if h is None:
            self.skipTest("母数が足りずホールドアウトを作れない")
        self.assertIn("best_th", h)
        self.assertIn("first_half", h)
        self.assertIn("second_half", h)
        self.assertIn(h["best_th"], self.bt.SWEEP_TH)

    def test_report_includes_sweep_and_coverage(self):
        self.bt.MODES = ["day"]
        self.bt.main()
        m = self.read(self.out)["modes"]["day"]
        self.assertIn("sweep", m)
        self.assertIn("days_covered", m)
        self.assertGreater(m["days_covered"], 0)
        ths = [r["th"] for r in m["sweep"]]
        self.assertEqual(ths, sorted(ths), "しきい値の並びが昇順でない")

    def test_same_interval_modes_reuse_klines(self):
        """day と mtf は同じ15分足を使う。モードを変えても取り直さないこと。
        取り直すと1回の実行が30分のタイムアウトを超える（実際に打ち切られた）。"""
        self.bt.WINDOWS = {"day": (3, 800), "mtf": (3, 800)}
        self.bt.run_mode("day")
        after_day = mock_api.CALLS["klines"]
        self.bt.run_mode("mtf")
        after_mtf = mock_api.CALLS["klines"]
        self.assertEqual(after_mtf, after_day,
                         f"同じ足なのに取り直している（+{after_mtf-after_day}回）")

    def test_unfetched_interval_is_fetched(self):
        """まだ取っていない足のモードは、きちんと取りに行くこと。
        （swingの1時間足は day が上位足フィルタ用に既に取っているため、
        ここでは day が触らない1分足のscalpで確かめる）"""
        self.bt.WINDOWS = {"day": (3, 800), "scalp": (3, 800)}
        self.bt.run_mode("day")
        after_day = mock_api.CALLS["klines"]
        self.assertNotIn("1min", {k[1] for k in F._KLINE_DAY_CACHE},
                         "dayが1分足を取っている（前提が崩れている）")
        self.bt.run_mode("scalp")
        self.assertGreater(mock_api.CALLS["klines"], after_day,
                           "未取得の足なのに取得していない")

    def test_cache_separates_intervals(self):
        """足ごとに別のキーで持つこと（混ざると別の足のデータを使ってしまう）。"""
        self.bt.WINDOWS = {"day": (3, 800)}
        self.bt.run_mode("day")
        got = {k[1] for k in F._KLINE_DAY_CACHE}
        self.assertIn("15min", got)      # 本体
        self.assertIn("1hour", got)      # 上位足フィルタ
        self.assertIn("4hour", got)

    def test_series_cache_is_keyed_by_mode(self):
        """足が同じでも、モードが違えば別の系列として扱うこと。"""
        self.reset_caches()
        F.STATS_DAYS = {m: 3 for m in F.PARAMS}
        F.STATS_MAX_BARS = {m: 800 for m in F.PARAMS}
        with F.use_mode("day"):
            F.compute_signal_stats("USD_JPY")
        keys_day = set(F._SERIES_CACHE)
        with F.use_mode("mtf"):
            F.compute_signal_stats("USD_JPY")
        added = set(F._SERIES_CACHE) - keys_day
        self.assertTrue(added, "モードが違うのに同じ系列を使い回している")

    def test_failure_keeps_previous_report(self):
        """集計できない時に既存のレポートを壊さないこと。"""
        self.write(self.out, {"keep": True})
        self.bt.MODES = ["day"]
        mock_api.fail_mode = lambda url, params: mock_api.Resp({}) if url.endswith("/klines") else None
        with self.assertRaises(SystemExit):
            self.bt.main()
        self.assertTrue(self.read(self.out).get("keep"), "既存レポートが壊された")


class StrategyCompareTest(RunTestCase):
    """判定ロジック候補の比較。指標の調整ではなく構造の違う仮説を同じ手順で比べる。"""

    def setUp(self):
        super().setUp()
        import strategies
        self.S = strategies
        self.S.WINDOWS = {"day": (3, 800), "swing": (3, 800)}
        F.MODE = "day"; F.P = F.PARAMS["day"]

    def test_rule_hook_changes_the_entry_set(self):
        """差し替えたルールが実際にエントリーを決めていること。"""
        never = F.compute_signal_stats("USD_JPY", rule=lambda ctx, i: None)
        self.assertIsNone(never, "エントリーしないルールなのに結果が出た")
        always = F.compute_signal_stats("USD_JPY", rule=lambda ctx, i: "買い")
        base = F.compute_signal_stats("USD_JPY")
        self.assertIsNotNone(always)
        self.assertGreater(always["n"], (base or {}).get("n", 0),
                           "毎バー買うルールが現行より少ないのはおかしい")

    def test_inverse_rule_flips_every_side(self):
        seen = []
        def spy(ctx, i):
            s = self.S.rule_current(ctx, i)
            inv = self.S.rule_inverse(ctx, i)
            if s:
                seen.append((s, inv))
            return None
        F.compute_signal_stats("USD_JPY", rule=spy)
        self.assertTrue(seen, "現行ルールが1度も成立していない")
        for a, b in seen:
            self.assertNotEqual(a, b)
            self.assertIn(b, ("買い", "売り"))

    def test_random_control_lands_near_minus_cost(self):
        """対照群（無作為エントリー）が『-スプレッド』付近に出ること。
        ここがズレると検証装置そのものが信用できない。"""
        rule = self.S.make_random_rule(0.05, seed=3)
        parts = []
        for sym in F.SYMBOLS:
            st = F.compute_signal_stats(sym, rule=rule)
            if st and st.get("policies", {}).get("advice"):
                parts.append(st["policies"]["advice"])
        if not parts:
            self.skipTest("母数不足")
        from backtest import pool_summary
        agg = pool_summary(parts)
        cost = sum(p["cost_r"] * p["n"] for p in parts) / sum(p["n"] for p in parts)
        # 無作為なら期待Rは -コスト。信頼区間がそこを含んでいれば装置は妥当。
        self.assertLessEqual(agg["ci_lo"], -cost)
        self.assertGreaterEqual(agg["ci_hi"], -cost)

    def test_noise_floor_brackets_the_control(self):
        """ノイズの範囲が、単発の対照群の結果を含むこと。
        候補は0を超えたかではなく、この範囲の上端を超えたかで判断する。"""
        nf = self.S.noise_floor("day", 0.05, seeds=6)
        if nf is None:
            self.skipTest("母数不足")
        self.assertGreaterEqual(nf["max"], nf["median"])
        self.assertGreaterEqual(nf["median"], nf["min"])
        one = self.S.evaluate(self.S.make_random_rule(0.05, 1000), "day")
        if one:
            self.assertGreaterEqual(nf["max"], one["avg_r"])
            self.assertLessEqual(nf["min"], one["avg_r"])

    def test_stop_width_sweep_lowers_cost_ratio(self):
        """SL幅を広げればスプレッドが1Rに占める割合は必ず下がること。
        素の優位性がコストと同程度しかない時に、唯一動かせるのがここ。"""
        rows = self.S.stop_width_sweep("day", self.S.rule_current, mults=(1.0, 3.0))
        if len(rows) < 2:
            self.skipTest("母数不足")
        self.assertLess(rows[-1]["cost_r"], rows[0]["cost_r"],
                        "SLを広げてもコスト比率が下がっていない")
        self.assertEqual(F.P, F.PARAMS["day"], "設定が元に戻っていない")

    def test_all_rules_run_without_error(self):
        for key, (rule, label) in self.S.RULES.items():
            with self.subTest(rule=key):
                F.compute_signal_stats("USD_JPY", rule=rule)   # 例外が出ないこと


class MixedModeTest(RunTestCase):
    """デイとスイングの併用。保有ポジションは『建てたときのモード』で評価すること。
       ここを取り違えると、スイング建玉が15分足のATRで測られ、含み益の評価が
       約2.9倍に膨らんでトレール利確が本来より早く発火する。"""

    def _pos(self, mode, entry=158.0):
        return {"id": "m1", "symbol": "USD_JPY", "side": "long", "entry": entry,
                "lot": 10000, "tp_pips": 200, "sl_pips": 100, "status": "open",
                "mode": mode}

    def test_atr_used_matches_the_position_mode(self):
        """含み益は『その建玉のモードのATR』で割られること。

        どちらのATRが大きいかは相場次第なので大小関係は見ない。
        profit_atr が profit / そのモードのATR に一致するかを直接確かめる。"""
        F.MODE = "day"; F.P = F.PARAMS["day"]
        tk = {"USD_JPY": {"bid": 159.0, "ask": 159.005}}
        entry, cur = 158.0, 159.0
        got = {}
        for mode in ("day", "swing"):
            self.reset_caches()
            with F.use_mode(mode):
                sc = F.score_pair("USD_JPY")
                adv = F.position_advice(self._pos(mode, entry), tk, sc, None)
            self.assertIsNotNone(adv, f"{mode} で判定できない")
            self.assertAlmostEqual(
                adv["profit_atr"], round((cur - entry) / sc["atr"], 2), places=2,
                msg=f"{mode} の建玉が {mode} 以外のATRで測られている")
            got[mode] = adv["profit_atr"]
        self.assertNotEqual(got["day"], got["swing"],
                            "モードが違うのに同じ物差しで測っている")

    def test_check_positions_uses_each_position_mode(self):
        self.write(F.POSITIONS_FILE, {"positions": [self._pos("swing")]})
        F.MODE = "day"; F.P = F.PARAMS["day"]
        F.main()
        op = self.status()["open_positions"]
        self.assertTrue(op, "保有ポジションが出ていない")
        self.assertEqual(op[0].get("mode"), "swing",
                         "評価に使われたモードが記録されていない/取り違えている")

    def test_auto_levels_use_the_position_mode(self):
        """auto指定の建玉は、そのモードの足のATRでTP/SLが決まること。"""
        widths = {}
        for mode in ("day", "swing"):
            self.reset_caches()
            data = {"positions": [{"id": "a1", "symbol": "USD_JPY", "side": "long",
                                   "entry": 158.0, "lot": 10000, "auto": True,
                                   "status": "open", "mode": mode}]}
            F.MODE = "day"; F.P = F.PARAMS["day"]
            msgs, changed = F.auto_set_levels(data)
            self.assertTrue(changed, f"{mode} でレベルが設定されない")
            widths[mode] = data["positions"][0]["sl_pips"]
        self.assertGreater(widths["swing"], widths["day"],
                           "スイングのSLがデイより広くない（足を取り違えている）")

    def test_globals_are_restored_after_use_mode(self):
        F.MODE = "day"; F.P = F.PARAMS["day"]
        with F.use_mode("swing"):
            self.assertEqual(F.MODE, "swing")
            self.assertEqual(F.P["interval"], "1hour")
        self.assertEqual(F.MODE, "day")
        self.assertEqual(F.P["interval"], "15min")

    def test_unknown_mode_falls_back(self):
        F.MODE = "day"; F.P = F.PARAMS["day"]
        with F.use_mode("nonexistent"):
            self.assertEqual(F.MODE, "day")
        self.assertEqual(F.pos_mode({"mode": "bogus"}), "day")
        self.assertEqual(F.pos_mode({"entry_mode": "swing"}), "swing")


class MtfModeTest(RunTestCase):
    """上位足で方向を決め、短期の押し目/戻りで入るモード。デイとスイングの併用形。"""

    def setUp(self):
        super().setUp()
        self.write(F.MODE_FILE, {"mode": "mtf"})

    def test_entry_rule_follows_upper_timeframe(self):
        lo, hi = F.MTF_PULLBACK_RSI
        with F.use_mode("mtf"):
            # 上位足が上昇なら、押し目(RSI低)だけ買う
            self.assertEqual(F.entry_side("USD_JPY", 0.0, lo - 1, 0.4, aligned=1), "買い")
            self.assertIsNone(F.entry_side("USD_JPY", 0.0, hi, 0.4, aligned=1))
            # 上位足が下降なら、戻り(RSI高)だけ売る
            self.assertEqual(F.entry_side("USD_JPY", 0.0, hi + 1, 0.4, aligned=-1), "売り")
            self.assertIsNone(F.entry_side("USD_JPY", 0.0, lo, 0.4, aligned=-1))
            # 上位足がレンジなら入らない
            self.assertIsNone(F.entry_side("USD_JPY", 0.0, lo - 1, 0.4, aligned=0))

    def test_score_rule_is_unchanged_for_other_modes(self):
        with F.use_mode("day"):
            self.assertEqual(F.entry_side("USD_JPY", 0.5, 50, 0.4), "買い")
            self.assertEqual(F.entry_side("USD_JPY", -0.5, 50, 0.4), "売り")
            self.assertIsNone(F.entry_side("USD_JPY", 0.1, 50, 0.4))

    def test_stop_is_wider_than_day(self):
        self.assertGreater(F.PARAMS["mtf"]["slm"], F.PARAMS["day"]["slm"],
                           "SL幅がデイより広くない（コスト比率を下げる狙いが効かない）")

    def test_runs_end_to_end(self):
        F.main()
        st = self.status()
        self.assertEqual(st["mode"], "mtf")
        self.assertEqual(len(st["pairs"]), len(F.SYMBOLS))


class StatsModeTest(RunTestCase):
    """統計はモードごとに独立していること。
       混ざると『想定保有4.5時間』のところに別モードの60分が表示される。"""

    def test_every_mode_has_a_stats_window(self):
        """全モードに統計期間の定義があること。無いと既定3日になり、
        シグナル頻度の低いモードは最低件数に届かず統計が作れない。"""
        for m in F.PARAMS:
            self.assertIn(m, F.STATS_DAYS, f"{m} の統計期間が未定義")
            self.assertIn(m, F.STATS_MAX_BARS, f"{m} の上限本数が未定義")

    def test_mtf_window_is_long_enough(self):
        """mtfは1通貨あたり1日0.7件程度。最低8件に届く期間があること。"""
        self.assertGreaterEqual(F.STATS_DAYS["mtf"], 20,
                                "mtfの統計期間が短すぎて統計が作れない")

    def test_other_mode_stats_are_not_reused(self):
        """前のモードの統計を引き継がないこと。"""
        F.MODE = "mtf"; F.P = F.PARAMS["mtf"]
        prev = {"USD_JPY": {"n": 30, "tp_winrate": 20, "hold_tp_min": 60,
                            "hold_sl_min": 82, "stats_ts": 10**12, "stats_mode": "day"}}
        got = F.gather_stats(prev)
        self.assertIsNot(got.get("USD_JPY"), prev["USD_JPY"],
                         "別モード(day)の統計をそのまま使っている")
        if got.get("USD_JPY"):
            self.assertEqual(got["USD_JPY"]["stats_mode"], "mtf")

    def test_same_mode_stats_are_reused(self):
        F.MODE = "day"; F.P = F.PARAMS["day"]
        import datetime
        now = int(datetime.datetime.now(F.JST).timestamp())
        prev = {s: {"n": 30, "tp_winrate": 40, "hold_tp_min": 60, "hold_sl_min": 80,
                    "stats_ts": now, "stats_mode": "day"} for s in F.SYMBOLS}
        got = F.gather_stats(prev)
        self.assertEqual(got["USD_JPY"], prev["USD_JPY"], "同じモードの新しい統計を捨てている")

    def test_status_never_shows_foreign_mode_stats(self):
        self.write(F.MODE_FILE, {"mode": "mtf"})
        F.main()
        for p in self.status()["pairs"]:
            if p.get("stats_mode") is not None:
                self.assertEqual(p["stats_mode"], "mtf",
                                 f"{p['symbol']} に別モードの統計が出ている")


class HoldAlignmentTest(RunTestCase):
    """保有中の判定は『入った根拠』で見ること。
       mtfは押し目/戻り（RSIが低い/高い）で入るので短期スコアは構造的に低い。
       スコアで判定すると、実測1,688件の100%が入った瞬間に『弱化』、
       79%が『逆シグナル』扱いになっていた。"""

    def test_mtf_uses_upper_timeframe_not_score(self):
        with F.use_mode("mtf"):
            # スコアがどれだけ低くても、上位足が順方向なら「継続」
            self.assertEqual(F.hold_alignment("USD_JPY", {"score": -0.9}, 1, aligned=1), 1)
            # 上位足が逆行したら「逆シグナル」
            self.assertEqual(F.hold_alignment("USD_JPY", {"score": 0.9}, 1, aligned=-1), -1)
            # レンジは中立
            self.assertEqual(F.hold_alignment("USD_JPY", {"score": 0.9}, 1, aligned=0), 0)

    def test_other_modes_still_use_score(self):
        with F.use_mode("day"):
            self.assertAlmostEqual(F.hold_alignment("USD_JPY", {"score": 0.6}, 1), 0.6)
            self.assertAlmostEqual(F.hold_alignment("USD_JPY", {"score": 0.6}, -1), -0.6)

    def test_mtf_entry_is_not_flagged_as_weak(self):
        """mtfのエントリー条件を満たす場面が、そのまま弱化扱いにならないこと。"""
        with F.use_mode("mtf"):
            th = F.P["th"]
            # 上位足が上昇＝入った根拠が生きている
            a = F.hold_alignment("USD_JPY", {"score": -0.4}, 1, aligned=1)
            self.assertGreaterEqual(a, th, "入った直後なのに弱化扱いになっている")
            self.assertGreater(a, -F.ADV_OPP, "入った直後なのに逆シグナル扱いになっている")

    def test_short_side_is_symmetric(self):
        with F.use_mode("mtf"):
            self.assertEqual(F.hold_alignment("USD_JPY", {"score": 0}, -1, aligned=-1), 1)
            self.assertEqual(F.hold_alignment("USD_JPY", {"score": 0}, -1, aligned=1), -1)


class RiskGuardTest(RunTestCase):
    """併用は件数が増えるぶんリスクが積み上がる。重ね持ちと総量を止める。"""

    def _hold(self, sym, side="long"):
        self.write(F.POSITIONS_FILE, {"positions": [
            {"id": "h1", "symbol": sym, "side": side, "entry": 159.0, "lot": 10000,
             "tp_pips": 20, "sl_pips": 10, "status": "open", "mode": "day"}]})

    def test_duplicate_direction_is_blocked(self):
        """同じ通貨・同じ方向を既に持っていたら、その向きのシグナルは出さない。"""
        self._hold("USD_JPY", "long")
        data = F.load_positions()
        held = F.held_directions(data)
        self.assertIn(("USD_JPY", "買い"), held)
        self.assertNotIn(("USD_JPY", "売り"), held)

    def test_open_risk_is_summed(self):
        self._hold("USD_JPY")
        # SL10pips × 10,000通貨 = 0.10円 × 10,000 = 1,000円
        self.assertAlmostEqual(F.open_risk_yen(F.load_positions()), 1000.0, places=1)

    def test_risk_cap_suppresses_new_signals(self):
        self._hold("USD_JPY")
        F.ACCOUNT_JPY = 10000; F.RISK_CAP_PCT = 5      # 上限500円 < 保有1,000円
        try:
            F.main()
            st = self.status()
            self.assertTrue(st["risk"]["full"], "上限到達が記録されていない")
            self.assertTrue(all(p["signal"] is None for p in st["pairs"]),
                            "上限到達なのに新規シグナルが出ている")
            self.assertIn("risk-cap", [w["tag"] for w in F._WARNINGS])
        finally:
            F.ACCOUNT_JPY = 0

    def test_no_cap_when_account_unset(self):
        self._hold("USD_JPY")
        F.ACCOUNT_JPY = 0
        F.main()
        self.assertFalse(self.status()["risk"]["full"])


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


class AtrRegimeTest(unittest.TestCase):
    """値幅(ATR)レジームの区分。ここが未来を覗くと『広い時は勝てる』が自明に出てしまう。"""

    def test_percentile_uses_only_past_and_current_bars(self):
        """あるバーのパーセンタイルが、それ以降のATRに一切左右されないこと。"""
        rnd = random.Random(7)
        base = [abs(rnd.gauss(0.1, 0.03)) for _ in range(300)]
        a = F._atr_pct_series(base)
        # 後半を極端な値に差し替えても、前半の値は変わらないはず
        tail = base[:150] + [9.9] * 150
        b = F._atr_pct_series(tail)
        self.assertEqual(a[:150], b[:150], "未来のATRが過去のパーセンタイルを動かしている")

    def test_percentile_matches_a_direct_count(self):
        base = [float(i % 50) + 1 for i in range(260)]
        got = F._atr_pct_series(base, win=100)
        for i in (120, 200, 259):
            w = base[max(0, i - 99):i + 1]
            want = sum(1 for v in w if v <= base[i]) / len(w) * 100
            self.assertAlmostEqual(got[i], want, places=9)

    def test_warmup_bars_have_no_band(self):
        got = F._atr_pct_series([0.1] * 20)
        self.assertTrue(all(v is None for v in got), "本数不足なのに区分を付けている")
        self.assertIsNone(F._atr_band(None))

    def test_bands_cover_the_whole_range_in_order(self):
        self.assertEqual(F._atr_band(0.0), "閑散(〜20%)")
        self.assertEqual(F._atr_band(19.9), "閑散(〜20%)")
        self.assertEqual(F._atr_band(20.0), "適正(20〜80%)")
        self.assertEqual(F._atr_band(79.9), "適正(20〜80%)")
        self.assertEqual(F._atr_band(80.0), "拡大(80〜95%)")
        self.assertEqual(F._atr_band(94.9), "拡大(80〜95%)")
        self.assertEqual(F._atr_band(95.0), "クライマックス(95%〜)")
        self.assertEqual(F._atr_band(100.0), "クライマックス(95%〜)")


class AtrBandStatsTest(RunTestCase):
    def test_stats_report_atr_bands_with_confidence_intervals(self):
        """画面とツールが読む atr_bands が、期待Rと信頼区間まで揃って出ること。"""
        F.MODE = "day"; F.P = F.PARAMS["day"]
        st = F.compute_signal_stats("USD_JPY")
        self.assertIsNotNone(st, "統計が取れない（前提が崩れている）")
        ab = st.get("atr_bands")
        self.assertTrue(ab, "atr_bands が無い＝レジーム別の検証ができない")
        labels = [lab for _, lab in F.ATR_REGIME_BANDS]
        for name, row in ab.items():
            self.assertIn(name, labels, f"未知の区分: {name}")
            self.assertGreater(row["n"], 0)
            for pol in F.EXIT_POLICIES:
                if pol in row:
                    for k in ("n", "winrate", "avg_r", "cost_r", "sd", "ci_lo", "ci_hi"):
                        self.assertIn(k, row[pol], f"{name}/{pol} に {k} が無い")
                    self.assertLessEqual(row[pol]["ci_lo"], row[pol]["avg_r"])
                    self.assertLessEqual(row[pol]["avg_r"], row[pol]["ci_hi"])
        # 件数は全て説明が付くこと（どこかで黙って消えていないこと）
        total = sum(r["n"] for r in ab.values())
        self.assertEqual(total + st["atr_bands_warmup"], st["n"],
                         "レジーム別の件数合計＋区分なしが全体と合わない")


class NotifyFailureVisibilityTest(RunTestCase):
    """通知が送れなかったことを画面から見えるようにする。

    以前は stderr に出すだけで status.json にも画面にも出ず、
    「通知が来ない」の原因を追う手がかりが無かった。
    さらにLINEはHTTPステータスを見ておらず、401でも成功扱いだった。
    """

    def setUp(self):
        super().setUp()
        # 実際の送信処理を検証したいので、基底クラスの差し替えを本物に戻す
        F.notify_line = self.real_notify_line
        os.environ["LINE_CHANNEL_ACCESS_TOKEN"] = "dummy"
        self.addCleanup(os.environ.pop, "LINE_CHANNEL_ACCESS_TOKEN", None)

    def test_line_rejection_is_surfaced(self):
        """LINEが4xxを返したら「送れていない」と分かること。"""
        class Resp:
            status_code = 401
            text = '{"message":"Invalid access token"}'
        F.requests.post = lambda *a, **k: Resp()
        self.addCleanup(lambda: F.requests.__dict__.pop("post", None))
        ok = F.notify_line("test")
        self.assertFalse(ok, "4xxなのに送信成功として扱っている")
        msgs = " ".join(w["msg"] for w in F._WARNINGS)
        self.assertIn("401", msgs, "拒否されたことが警告に残っていない")

    def test_line_exception_is_surfaced(self):
        def boom(*a, **k):
            raise RuntimeError("network down")
        F.requests.post = boom
        self.addCleanup(lambda: F.requests.__dict__.pop("post", None))
        self.assertFalse(F.notify_line("test"))
        self.assertTrue(any(w.get("tag") == "line-send" for w in F._WARNINGS),
                        "送信失敗が警告に残っていない")

    def test_warnings_raised_after_status_is_written_still_reach_the_screen(self):
        """通知の送信は status.json を書いた後。そこで出た警告も画面に届くこと。"""
        F.main()
        before = [w["msg"] for w in (self.status().get("warnings") or [])]
        self.assertFalse(any("メール送信失敗" in m for m in before), "前提が崩れている")
        F.warn("メール送信失敗: テスト", tag="mail-send")
        F.flush_warnings_to_status()
        msgs = [w["msg"] for w in (self.status().get("warnings") or [])]
        self.assertTrue(any("メール送信失敗" in m for m in msgs),
                        "書き込み後に出た警告が status.json に届いていない")

    def test_flush_keeps_the_rest_of_status_intact(self):
        """警告の書き戻しで、画面が読む他の内容を壊さないこと。"""
        F.main()
        before = self.status()
        F.warn("LINE送信が拒否されました (HTTP 429)", tag="line-send")
        F.flush_warnings_to_status()
        after = self.status()
        for k in ("pairs", "open_positions", "generated_at", "mode"):
            self.assertEqual(before.get(k), after.get(k), f"{k} が書き換わっている")


class PullbackSweepTest(RunTestCase):
    """mtfの押し目/戻りRSI基準を検証できること。

    mtfの entry_side は th を見ないため、既存のしきい値スイープでは
    エントリー地点が1つも変わらない（6通りとも同じ結果になる）。
    「60が適切か」を測る手段が無かった。
    """

    def setUp(self):
        super().setUp()
        F.MODE = "mtf"; F.P = F.PARAMS["mtf"]

    def test_score_threshold_does_not_move_mtf_entries(self):
        """前提の確認: mtfでは th を振っても判定が変わらない。"""
        for th in (0.40, 0.90):
            self.assertEqual(F.entry_side("USD_JPY", 0.0, 65.0, th, aligned=-1), "売り")
            self.assertEqual(F.entry_side("USD_JPY", 0.99, 50.0, th, aligned=-1), None)

    def test_pullback_override_moves_entries(self):
        """RSI基準を振ると判定が変わること（これが検証の対象）。"""
        # RSI55 は既定(60)では入らないが、(45,55) なら売りになる
        self.assertIsNone(F.entry_side("USD_JPY", 0.0, 55.0, 0.40, aligned=-1))
        self.assertEqual(
            F.entry_side("USD_JPY", 0.0, 55.0, 0.40, aligned=-1, pullback=(45, 55)), "売り")
        # 厳しくすると既定で入る場面が消える
        self.assertEqual(F.entry_side("USD_JPY", 0.0, 61.0, 0.40, aligned=-1), "売り")
        self.assertIsNone(
            F.entry_side("USD_JPY", 0.0, 61.0, 0.40, aligned=-1, pullback=(30, 70)))

    def test_override_does_not_leak_into_live_settings(self):
        """検証用の差し替えが運用値を書き換えないこと。"""
        before = tuple(F.MTF_PULLBACK_RSI)
        F.entry_side("USD_JPY", 0.0, 55.0, 0.40, aligned=-1, pullback=(45, 55))
        self.assertEqual(tuple(F.MTF_PULLBACK_RSI), before)

    def test_stats_record_which_setting_produced_them(self):
        """どの基準で出た数字かが結果に残ること（後から取り違えないため）。"""
        st = F.compute_signal_stats("USD_JPY", pullback_override=(45, 55))
        if st is None:
            self.skipTest("この足では母数が足りない")
        self.assertEqual(st["pullback"], [45, 55])

    def test_loosening_the_band_never_reduces_entries(self):
        """基準を緩めれば件数は増えこそすれ減らないこと（振り方の向きの確認）。"""
        counts = {}
        for pb in ((30, 70), (40, 60), (48, 52)):
            st = F.compute_signal_stats("USD_JPY", pullback_override=pb)
            counts[pb] = st["n"] if st else 0
        self.assertLessEqual(counts[(30, 70)], counts[(40, 60)])
        self.assertLessEqual(counts[(40, 60)], counts[(48, 52)])


class MtfNotificationTest(RunTestCase):
    """mtfの通知文が、方向と矛盾して見えないこと。

    mtfは上位足の方向へ短期の逆行を狙うので、売りシグナルでもスコアは
    プラスになり理由欄に上昇材料が並ぶ。断り書きが無いと受け手には
    「買い材料しかないのに売れと言われた」としか読めない。
    """

    def _force_mtf_signal(self):
        """mtfの売りが必ず1本立つ状態を作る。

        検証したいのは通知の文面であってエントリー条件ではない。
        モックの値動き任せにするとシグナルが出ずテストが素通りするので、
        上位足の向きだけを固定して確実に成立させる。"""
        self.write(F.MODE_FILE, {"mode": "mtf"})
        # 差し替える前に本物を保存する（後で保存すると差し替え後の関数が
        # 「元の値」として残り、この差し替えが他のテストへ漏れる）
        self.addCleanup(setattr, F, "entry_side", F.entry_side)
        self.addCleanup(setattr, F, "mtf_view", F.mtf_view)
        # USD_JPY は基底のテスト建玉（買い）があるので、両建て回避で止まる。
        # 保有していない通貨でシグナルを立てる。
        F.entry_side = lambda sym, total, rv, th, aligned=None, pullback=None: (
            "売り" if sym == "EUR_JPY" else None)
        F.mtf_view = lambda sym: {"1hour": -1, "4hour": -1,
                                  "label": "1h↓下降 / 4h↓下降", "aligned": -1}

    def test_pullback_notice_explains_the_entry_condition(self):
        self._force_mtf_signal()
        F.main()
        self.assertEqual(self.status()["mode"], "mtf", "前提: mtfで動いていること")
        bodies = [b for _, b in self.sent["mail"] if "エントリー目安" in b]
        self.assertTrue(bodies, "エントリー通知が出ていない（前提が崩れている）")
        for b in bodies:
            self.assertTrue("押し目買い" in b or "戻り売り" in b,
                            "何を待って入ったのかが書かれていない")
            self.assertIn("で入る設定", b, "入る条件（RSI基準）が書かれていない")
            self.assertIn("仕様どおり", b, "スコアが逆に見える件の断りが無い")
            self.assertNotIn("強シグナル", b, "mtfで意味を持たないスコア強弱を出している")
            self.assertNotIn("低スコアほど成績が悪い", b,
                             "mtfではスコアが判定に使われていないのに警告している")

    def test_other_modes_keep_the_score_based_wording(self):
        # モックの値動き任せだとシグナルが出ない日があり、skipされて何も
        # 検証していなかった。確実に1本立てる。
        self.write(F.MODE_FILE, {"mode": "day"})
        self.write(F.POSITIONS_FILE, {"positions": []})
        self.addCleanup(setattr, F, "entry_side", F.entry_side)
        F.entry_side = lambda sym, total, rv, th, aligned=None, pullback=None: (
            "買い" if sym == "USD_JPY" else None)
        F.main()
        self.assertEqual(self.status()["mode"], "day", "前提: dayで動いていること")
        bodies = [b for _, b in self.sent["mail"] if "エントリー目安" in b]
        self.assertTrue(bodies, "エントリー通知が出ていない（前提が崩れている）")
        for b in bodies:
            self.assertIn("スコア", b)
            self.assertNotIn("で入る設定", b, "day に mtf 用の文面が混ざっている")


class TouchDetectionTest(RunTestCase):
    """足の高安でTP/SLを拾う処理。

    ローソク足は BID で取っている。買い建てはBIDで決済するのでそのままでよいが、
    売り建てはASKで買い戻すため、BIDの高安をそのまま比べるとスプレッドのぶんずれる。
    実際「TP到達」の通知が出たのに建玉が残っている（＝GMOのOCOが約定していない）
    という報告があった。
    """

    ENTRY, TP, SL = 153.883, 153.527, 154.105   # 実際の建玉（売り・mtf）

    def _pos(self, side="short"):
        return {"id": "t9", "symbol": "USD_JPY", "side": side, "entry": self.ENTRY,
                "lot": 3000, "tp_pips": 35.6, "sl_pips": 22.2, "status": "open"}

    def _advice(self, bar_low, bar_high, bid, ask, side="short"):
        F.MODE = "mtf"; F.P = F.PARAMS["mtf"]
        F._OHLC_CACHE.clear()
        F._OHLC_CACHE[("USD_JPY", F.P["interval"],
                       max(F.P["ema_s"], F.P["macd"][1], F.P["adx"]*2, F.P["atr"])
                       + F.CHART_POINTS + 30)] = [(bar_high, bar_low, bid)]
        return F.position_advice(self._pos(side), {"USD_JPY": {"bid": bid, "ask": ask}},
                                 {"atr": 0.05, "score": -0.5, "rsi": 45, "adx": 25}, None)

    def test_short_tp_needs_the_ask_to_reach_it(self):
        """売り建ての利確はASKで決まる。BIDだけが届いた足では到達にしない。

        実際に起きた誤報そのもの。GMOの15分足(BID) 2026/09/08 09:30 は
        O153.547 H153.774 L153.527 C153.772 で、安値がTPと1銭も違わなかった。
        BIDで比べると <= が成立してしまうが、売り建てはASKで買い戻すので
        ASKの安値は 153.529〜153.532（スプレッド0.2〜0.5pips）。
        TPには届いておらず、GMOのOCOも約定していなかった。"""
        for spread in (0.002, 0.005):     # チャート表示SP:0.2 / 通知時点の実測0.5
            bid = 153.589
            adv = self._advice(bar_low=self.TP, bar_high=153.774,
                               bid=bid, ask=round(bid + spread, 3))
            self.assertNotIn("TP", adv["reason"],
                             f"スプレッド{spread*100:.1f}pipsでもBIDだけで「TP到達」と判定している")

    def test_short_tp_fires_once_the_ask_reaches_it(self):
        """ASK相当まで届いていれば拾うこと（救済そのものは残す）。"""
        adv = self._advice(bar_low=self.TP - 0.005, bar_high=153.60,
                           bid=153.589, ask=153.594)
        self.assertEqual(adv["level"], "take")
        self.assertIn("TP", adv["reason"])

    def test_short_sl_is_not_missed_by_the_spread(self):
        """売り建ての損切りはASKで決まる。BIDの高値だけで見ると見落とす。"""
        # BIDの高値はSLに0.3pips届かないが、ASKでは超えている → 損切り扱いにする
        adv = self._advice(bar_low=153.90, bar_high=self.SL - 0.003,
                           bid=153.95, ask=153.955)
        self.assertEqual(adv["level"], "cut", "ASKでは刺さっている損切りを見落としている")

    def test_long_side_is_unchanged(self):
        """買い建てはBIDで決済するので、従来どおりそのまま比べること。"""
        pos = {"id": "t9", "symbol": "USD_JPY", "side": "long", "entry": 153.0,
               "lot": 3000, "tp_pips": 20.0, "sl_pips": 12.5, "status": "open"}
        F.MODE = "mtf"; F.P = F.PARAMS["mtf"]
        need = max(F.P["ema_s"], F.P["macd"][1], F.P["adx"]*2, F.P["atr"]) + F.CHART_POINTS + 30
        F._OHLC_CACHE.clear()
        F._OHLC_CACHE[("USD_JPY", F.P["interval"], need)] = [(153.20, 153.0, 153.1)]
        adv = F.position_advice(pos, {"USD_JPY": {"bid": 153.1, "ask": 153.105}},
                                {"atr": 0.05, "score": 0.5, "rsi": 55, "adx": 25}, None)
        self.assertEqual(adv["level"], "take", "買い建てのTP判定が変わっている")

    def test_touched_but_retraced_asks_to_verify_instead_of_closing(self):
        """すでに戻している場合は、決済を促さず約定確認を促すこと。

        本当に到達していればGMOのOCOが自動で約定している。
        戻している足を根拠に「利確推奨」と出すと、約定済みか未約定かを
        取り違えたまま手動で決済してしまう。"""
        adv = self._advice(bar_low=self.TP - 0.010, bar_high=153.70,
                           bid=153.589, ask=153.594)
        self.assertTrue(adv["touched"], "足の高安で拾ったことが記録されていない")
        self.assertIn("接触", adv["label"] + adv["reason"])
        self.assertIn("確認", adv["reason"])
        self.assertIn("153.594", adv["reason"], "現在値が書かれていない")

    def test_subject_distinguishes_touch_from_a_real_fill(self):
        """件名でも区別すること。スマホでは件名しか見ないことが多い。"""
        F.MODE = "mtf"
        self.assertIn("🎯利確 USD/JPY", F.mail_subject([], [("take", "USD_JPY")], 0))
        sub = F.mail_subject([], [("touch", "USD_JPY")], 0)
        self.assertIn("要確認", sub)
        self.assertNotIn("🎯利確 USD/JPY", sub, "接触なのに利確済みに見える件名になっている")

    def test_current_price_hit_still_says_reached(self):
        """現在値そのものが到達している場合は従来どおり「到達」と書くこと。"""
        adv = self._advice(bar_low=153.50, bar_high=153.70, bid=153.515, ask=153.520)
        self.assertFalse(adv["touched"])
        self.assertEqual(adv["reason"], "TP到達")


class EntryLogContextTest(RunTestCase):
    """記録簿に残すのは『エントリーした時』の状態であること。

    記録簿は5分ごとの実行時に書くので、そこで指標を取り直すと
    値動きが出た後の値が残る。実際 mtf の売り6件は RSI 8.9〜36.9 で
    記録されており、売りの基準60とは正反対の値が並んでいた。
    """

    def _pos(self, **kw):
        p = {"id": "e1", "symbol": "USD_JPY", "side": "short", "entry": 158.5,
             "lot": 1000, "status": "open"}
        p.update(kw); return p

    def test_prefers_the_values_captured_at_entry(self):
        F.MODE = "mtf"; F.P = F.PARAMS["mtf"]
        data = {"positions": [self._pos(entry_rsi=62.4, entry_tech=-0.30,
                                        entry_fund=0.40, entry_score=58,
                                        entry_mark="🟡 保留・検討")]}
        self.assertEqual(F.stamp_new_entries(data), 1)
        rec = self.read(F.ENTRY_LOG_FILE)["entries"][-1]
        self.assertEqual(rec["rsi"], 62.4, "記録時点のRSIで上書きしている")
        self.assertEqual(rec["ctx_src"], "entry")
        self.assertEqual(rec["conf_pct"], 58)
        self.assertEqual(rec["conf_mark"], "🟡 保留・検討")
        # スコアはその建玉のモードの比率で組み直す。
        # 以前は差し替えられたグローバルの TECH_W をそのまま使っていたため、
        # 直前に別モードを触っていると比率がずれていた（既定0.9/0.1のまま等）。
        self.assertEqual(rec["mode"], "mtf")
        self.assertAlmostEqual(rec["score"], round(F.clamp(
            0.85 * -0.30 + 0.15 * 0.40), 3), places=3)

    def test_the_entry_log_uses_the_position_mode_not_the_operating_mode(self):
        """建玉のモードで記録すること（運用モードではない）。

        実際に起きたこと: 運用mtfのままスイングの参考通知で建てた USD/JPY 2件が、
        記録簿に mode='mtf' として残っていた。tp_pips 73〜76 はスイングの値幅で、
        mtfなら20〜30pips。モードが違えば足も指標もスコアの配分も閾値も違うので、
        ここを取り違えるとモード別の成績がそのまま混ざる。"""
        F.MODE = "mtf"; F.P = F.PARAMS["mtf"]
        F.TECH_W, F.FUND_W = 0.85, 0.15
        data = {"positions": [self._pos(mode="swing", entry_rsi=62.3,
                                        entry_tech=-0.30, entry_fund=0.40,
                                        tp_pips=75.9, sl_pips=42.2)]}
        self.assertEqual(F.stamp_new_entries(data), 1)
        rec = self.read(F.ENTRY_LOG_FILE)["entries"][-1]
        self.assertEqual(rec["mode"], "swing", "運用モードで記録している")
        # スイングは テク0.45 / ファンダ0.55
        self.assertAlmostEqual(rec["score"], round(F.clamp(
            0.45 * -0.30 + 0.55 * 0.40), 3), places=3)
        # 記録後に運用モードの設定が戻っていること
        self.assertEqual(F.MODE, "mtf")
        self.assertIs(F.P, F.PARAMS["mtf"])
        self.assertEqual((F.TECH_W, F.FUND_W), (0.85, 0.15))

    def test_entry_mode_alone_also_decides_the_log_mode(self):
        F.MODE = "mtf"; F.P = F.PARAMS["mtf"]
        data = {"positions": [self._pos(entry_mode="swing")]}
        self.assertEqual(F.stamp_new_entries(data), 1)
        self.assertEqual(self.read(F.ENTRY_LOG_FILE)["entries"][-1]["mode"], "swing")

    def test_falls_back_to_current_values_without_entry_context(self):
        """アプリ以外から登録された建玉は、従来どおり現在値で記録する。"""
        F.MODE = "mtf"; F.P = F.PARAMS["mtf"]
        self.assertEqual(F.stamp_new_entries({"positions": [self._pos()]}), 1)
        rec = self.read(F.ENTRY_LOG_FILE)["entries"][-1]
        self.assertEqual(rec["ctx_src"], "logged")
        self.assertIsNotNone(rec["rsi"])

    def test_mtf_strength_measures_the_pullback_not_the_score(self):
        """mtfの強弱は押し目/戻りの深さで測ること。

        スコア基準だと RSI8.9 の深い戻り売りが「強」、RSI36.9 が「弱」と、
        押し目の深さと逆の記録になっていた。"""
        F.MODE = "mtf"; F.P = F.PARAMS["mtf"]
        th = F.P["th"]
        # 売り: 基準60を大きく超えるほど深く引きつけている
        self.assertEqual(F._entry_strength(-0.9, 72.0, "short", th), "強")
        self.assertEqual(F._entry_strength(-0.9, 64.0, "short", th), "標準")
        self.assertEqual(F._entry_strength(-0.9, 60.5, "short", th), "弱")
        # 買い: 基準40を大きく下回るほど深い
        self.assertEqual(F._entry_strength(0.9, 28.0, "long", th), "強")
        self.assertEqual(F._entry_strength(0.9, 39.5, "long", th), "弱")
        # スコアが大きくても、浅い戻りなら「弱」であること
        self.assertEqual(F._entry_strength(-0.95, 60.2, "short", th), "弱")

    def test_other_modes_keep_the_score_based_strength(self):
        F.MODE = "day"; F.P = F.PARAMS["day"]
        th = F.P["th"]
        self.assertEqual(F._entry_strength(-0.75, 20.0, "short", th), "強")
        self.assertEqual(F._entry_strength(-0.45, 20.0, "short", th), "標準")
        self.assertEqual(F._entry_strength(-0.10, 20.0, "short", th), "弱")
        self.assertEqual(F._entry_strength(None, 20.0, "short", th), "不明")


class PairBiasTest(RunTestCase):
    """シグナルが出ていない時の表示。

    mtfは売りの直前に必ず短期スコアがプラスになる（RSIが60まで戻るため）。
    それを「買い優勢」と出していたので、同じ瞬間に売りシグナルが出ていても
    矛盾しているようにしか読めなかった。実際そう見えたという報告があった。
    """

    def test_mtf_says_what_it_is_waiting_for(self):
        F.MODE = "mtf"; F.P = F.PARAMS["mtf"]
        down = F.pair_bias(0.53, 42.2, -1)
        self.assertIn("戻り待ち", down)
        self.assertIn("60", down)
        self.assertNotIn("買い優勢", down, "売りを待っているのに買い優勢と出している")
        up = F.pair_bias(-0.53, 58.0, 1)
        self.assertIn("押し目待ち", up)
        self.assertNotIn("売り優勢", up)
        self.assertIn("上位足", F.pair_bias(0.1, 50.0, 0))

    def test_mtf_never_contradicts_the_signal_direction(self):
        """スコアがプラスでも、下降揃いなら『売りを待っている』と出ること。"""
        F.MODE = "mtf"; F.P = F.PARAMS["mtf"]
        for score in (-0.9, 0.0, 0.53, 0.9):
            self.assertIn("売り", F.pair_bias(score, 55.0, -1))
            self.assertIn("買い", F.pair_bias(score, 45.0, 1))

    def test_other_modes_keep_the_score_based_wording(self):
        F.MODE = "day"; F.P = F.PARAMS["day"]
        self.assertEqual(F.pair_bias(0.53, 42.2, -1), "買い優勢")
        self.assertEqual(F.pair_bias(-0.20, 42.2, -1), "売り優勢")
        self.assertEqual(F.pair_bias(0.0, None, None), "買い優勢")

    def test_status_json_carries_the_new_wording(self):
        self.write(F.MODE_FILE, {"mode": "mtf"})
        F.main()
        st = self.status()
        self.assertEqual(st["mode"], "mtf")
        for p in st["pairs"]:
            if p.get("signal"):
                continue
            self.assertNotIn("優勢", p.get("bias") or "",
                             "mtfのカードにスコア基準の文言が残っている")


class ModeAwareWordingTest(RunTestCase):
    """モードで意味が変わるのに、表示だけスコア基準のまま残っていた箇所。

    mtfを後から足したため、スコアを根拠にした文言・区分があちこちに
    残っていた。判定は上位足や押し目の深さで行っているのに、
    理由には無関係なスコアが書かれていた。
    """

    def test_hold_reason_cites_what_the_decision_used(self):
        F.MODE = "mtf"; F.P = F.PARAMS["mtf"]
        self.assertEqual(F._hold_basis(-0.31, -1), "上位足は下降のまま")
        self.assertEqual(F._hold_basis(0.53, 1), "上位足は上昇のまま")
        self.assertIn("レンジ", F._hold_basis(0.53, 0))
        for b in (F._hold_basis(-0.31, -1), F._hold_basis(0.53, 0)):
            self.assertNotIn("スコア", b, "判定に使っていないスコアを理由にしている")
        F.MODE = "day"; F.P = F.PARAMS["day"]
        self.assertEqual(F._hold_basis(-0.31, None), "スコア-0.31")

    def test_bands_split_by_the_actual_entry_criterion(self):
        """mtfの帯は押し目/戻りの深さで分けること。

        スコアで分けると1年分が 弱693/中18/強6 と96%が1つの帯に落ち、
        何も比較できなかった。"""
        F.MODE = "mtf"; F.P = F.PARAMS["mtf"]
        th = F.P["th"]
        # 売り: 基準60をどれだけ超えたか
        self.assertTrue(F._score_band(0.9, th, 71.0, "売り").startswith("強"))
        self.assertTrue(F._score_band(0.9, th, 64.0, "売り").startswith("中"))
        self.assertTrue(F._score_band(0.9, th, 60.5, "売り").startswith("弱"))
        # 買い: 基準40をどれだけ下回ったか
        self.assertTrue(F._score_band(0.9, th, 29.0, "買い").startswith("強"))
        self.assertTrue(F._score_band(0.9, th, 39.5, "買い").startswith("弱"))
        # スコアが大きくても浅ければ「弱」
        self.assertTrue(F._score_band(0.99, th, 60.1, "売り").startswith("弱"))

    def test_other_modes_keep_the_score_bands(self):
        F.MODE = "day"; F.P = F.PARAMS["day"]
        th = F.P["th"]
        self.assertTrue(F._score_band(th*1.6, th, 20.0, "売り").startswith("強"))
        self.assertTrue(F._score_band(th*1.3, th, 20.0, "売り").startswith("中"))
        self.assertTrue(F._score_band(th*1.0, th, 20.0, "売り").startswith("弱"))

    def test_stats_declare_how_the_bands_were_split(self):
        """画面が説明文を出し分けられるよう、分け方を結果に残すこと。"""
        for mode, want in (("mtf", "pullback"), ("day", "score")):
            self.reset_caches()
            F.MODE = mode; F.P = F.PARAMS[mode]
            st = F.compute_signal_stats("USD_JPY")
            if st is None or not st.get("bands"):
                continue
            self.assertEqual(st["band_by"], want)

    def test_position_messages_never_cite_score_in_mtf(self):
        """保有中の通知文にスコア基準の文言が残っていないこと。"""
        F.MODE = "mtf"; F.P = F.PARAMS["mtf"]
        F._OHLC_CACHE.clear()
        # 差し替える前に本物を保存する（保存を後にすると差し替えが他のテストへ漏れる）
        self.addCleanup(setattr, F, "mtf_view", F.mtf_view)
        self.addCleanup(self.reset_caches)
        F.mtf_view = lambda s: {"aligned": -1, "label": "1h↓下降 / 4h↓下降"}
        adv = F.position_advice(
            {"id": "z", "symbol": "USD_JPY", "side": "short", "entry": 153.9,
             "lot": 1000, "tp_pips": 35.6, "sl_pips": 22.2},
            {"USD_JPY": {"bid": 153.80, "ask": 153.805}},
            {"atr": 0.09, "score": -0.31, "rsi": 45, "adx": 30}, None)
        self.assertIsNotNone(adv)
        self.assertNotIn("スコア", adv["reason"],
                         f'判定に使っていないスコアが理由に出ている: {adv["reason"]}')


class DuplicateAndHedgeTest(RunTestCase):
    """同じ通貨を重ねて持たないための見送り。

    同じ方向は同じ値動きへのリスクが倍になる。逆方向は両建てになり、
    建玉が打ち消し合ってスプレッドだけ二重に払う。
    モードを切り替えた直後に「デイの買いを持ったまま mtf の売り」が
    実際に起こりうるため、どちらも止める。
    """

    def _run_with_signal(self, sym, sig, held_side):
        self.write(F.POSITIONS_FILE, {"positions": [
            {"id": "h1", "symbol": "GBP_JPY", "side": held_side, "entry": 208.3,
             "lot": 1000, "tp_pips": 48.9, "sl_pips": 30.5, "status": "open",
             "mode": "day", "auto_set": True}]})
        self.addCleanup(setattr, F, "entry_side", F.entry_side)
        F.entry_side = lambda s_, t_, r_, th_, aligned=None, pullback=None: (
            sig if s_ == sym else None)
        # ここで見たいのは重複/両建ての見送りだけ。上位足フィルタが先に
        # 見送ると skip_reason が「上位足と逆行」になり、日によって
        # 落ちたり通ったりする（モックの上位足が実行日で変わるため）。
        # 差し替える前に元を保存する（後だと差し替えた方を戻してしまう）。
        self.addCleanup(setattr, F, "mtf_view", F.__dict__["mtf_view"])
        F.mtf_view = lambda symbol: {"aligned": 0, "label": "1h→レンジ / 4h→レンジ"}
        F.main()
        st = self.status()
        return next(p for p in st["pairs"] if p["symbol"] == sym)

    def test_same_direction_is_skipped(self):
        p = self._run_with_signal("GBP_JPY", "買い", "long")
        self.assertIsNone(p["signal"])
        self.assertIn("同じ方向", p["skip_reason"])

    def test_opposite_direction_is_skipped_as_a_hedge(self):
        p = self._run_with_signal("GBP_JPY", "売り", "long")
        self.assertIsNone(p["signal"], "買いを持ったまま売りシグナルを通している")
        self.assertIn("両建て", p["skip_reason"])

    def test_other_pairs_are_unaffected(self):
        p = self._run_with_signal("AUD_JPY", "売り", "long")
        self.assertEqual(p["signal"], "売り", "保有していない通貨まで止めている")

    def test_can_be_turned_off(self):
        self.addCleanup(setattr, F, "BLOCK_DUPLICATE", F.BLOCK_DUPLICATE)
        F.BLOCK_DUPLICATE = False
        p = self._run_with_signal("GBP_JPY", "売り", "long")
        self.assertEqual(p["signal"], "売り")


class EntryTimeTouchTest(RunTestCase):
    """TP/SL接触の判定に、建てる前の値動きを使わないこと。

    実際に起きた誤報: 2026-09-10 19:42 に EUR/JPY を 179.114 で買った直後、
    「SLに接触（178.955）」の損切り推奨が届いた。178.95台を付けていたのは
    19:30〜19:33 で、建てる9分前。19:30〜19:45の足の安値が使われていた。
    """

    BAR_MIN = 15
    MS = 60000

    def _bars(self, n):
        # (high, low, close) を n 本。安値だけ意味を持たせる
        return [(179.20, 178.93, 179.00)] * n

    def test_bar_that_started_before_entry_is_dropped(self):
        rec = self._bars(1)
        now = 1000 * 3600 * 24 * 20000 + 19 * 3600000 + 42 * 60000   # 足の途中
        bar_ms = self.BAR_MIN * self.MS
        cur_start = (now // bar_ms) * bar_ms
        opened = cur_start + 9 * self.MS          # 足が始まって9分後に建てた
        self.assertEqual(F.bars_after_entry(rec, self.BAR_MIN, opened, now), [],
                         "建てる前に始まった足を判定に使っている")

    def test_bar_that_started_after_entry_is_kept(self):
        rec = self._bars(1)
        now = 1000 * 3600 * 24 * 20000 + 19 * 3600000 + 42 * 60000
        bar_ms = self.BAR_MIN * self.MS
        cur_start = (now // bar_ms) * bar_ms
        opened = cur_start - 5 * self.MS          # 足が始まる5分前に建てた
        self.assertEqual(F.bars_after_entry(rec, self.BAR_MIN, opened, now), rec)

    def test_keeps_only_the_bars_after_entry(self):
        """複数本を見るモード（スキャル等）でも、建玉より後の足だけ残すこと。"""
        rec = [(1.0 + i, 0.0 + i, 0.5 + i) for i in range(5)]   # 古い→新しい
        now = 1000 * 3600 * 24 * 20000
        bar_ms = 1 * self.MS
        cur_start = (now // bar_ms) * bar_ms
        opened = cur_start - 2 * bar_ms          # 直近3本ぶんが建玉より後
        got = F.bars_after_entry(rec, 1, opened, now)
        self.assertEqual(got, rec[-3:], "残す本数がずれている")

    def test_no_open_time_keeps_everything(self):
        """時刻が分からない建玉は従来どおり（絞り込まない）。"""
        rec = self._bars(2)
        self.assertEqual(F.bars_after_entry(rec, self.BAR_MIN, None), rec)

    def test_open_time_is_read_from_either_field(self):
        self.assertEqual(F.pos_opened_ms({"id": "p1789036924237"}), 1789036924237)
        got = F.pos_opened_ms({"id": "t1", "opened_at": "2026-09-10 19:42 JST"})
        self.assertIsNotNone(got)
        import datetime as _dt
        self.assertEqual(
            _dt.datetime.fromtimestamp(got / 1000, F.JST).strftime("%Y-%m-%d %H:%M"),
            "2026-09-10 19:42")
        self.assertIsNone(F.pos_opened_ms({"id": "manual-1"}))

    def test_the_real_false_alarm_no_longer_fires(self):
        """9/10 の誤報そのものを再現し、出なくなったことを確認する。"""
        F.MODE = "day"; F.P = F.PARAMS["day"]
        pos = {"id": "p1", "symbol": "EUR_JPY", "side": "long", "entry": 179.114,
               "lot": 3000, "tp_pips": 25.4, "sl_pips": 15.9,
               "opened_at": "2026-09-10 19:42 JST"}
        need = (max(F.P["ema_s"], F.P["macd"][1], F.P["adx"]*2, F.P["atr"])
                + F.CHART_POINTS + 30)
        F._OHLC_CACHE.clear()
        # 19:30〜19:45 の足。安値178.93 は SL178.955 を割っているが、
        # それを付けたのは建てる9分前。
        F._OHLC_CACHE[("EUR_JPY", F.P["interval"], need)] = [(179.15, 178.93, 179.10)]
        now = int(F.datetime.datetime(2026, 9, 10, 19, 42, tzinfo=F.JST).timestamp() * 1000)
        kept = F.bars_after_entry([(179.15, 178.93, 179.10)], 15,
                                  F.pos_opened_ms(pos), now)
        self.assertEqual(kept, [], "建てる前の安値を今も使っている")


class AdviceInputAuditTest(RunTestCase):
    """保有判定に入る値の点検。

    これまで「判定の対象範囲」で2件の誤報を出している。
      9/08 売り建てのTP/SLをBIDの高安で見ていた（スプレッドのぶんズレ）
      9/10 建てる前の値動きを接触判定に使っていた
    どちらも勝っている建玉に決済を促す向きの誤りだった。
    同じ種類が戻ってこないよう、成立すべき条件を固定する。
    """

    NEED = None

    def _setup_bars(self, mode, bars):
        F.MODE = mode; F.P = F.PARAMS[mode]
        need = (max(F.P["ema_s"], F.P["macd"][1], F.P["adx"]*2, F.P["atr"])
                + F.CHART_POINTS + 30)
        F._OHLC_CACHE.clear()
        F._OHLC_CACHE[("USD_JPY", F.P["interval"], need)] = bars

    def _adv(self, pos, bid, ask, sc=None):
        return F.position_advice(
            pos, {"USD_JPY": {"bid": bid, "ask": ask}},
            sc or {"atr": 0.05, "score": 0.0, "rsi": 50, "adx": 25}, None)

    # --- 買い建ては BID、売り建ては ASK で決済する ---
    def test_long_uses_bid_for_both_targets(self):
        # TP/SLのどちらにも触れない足にする（触れると接触判定が先に効く）
        self._setup_bars("day", [(157.60, 157.45, 157.55)])
        pos = {"id": "x", "symbol": "USD_JPY", "side": "long", "entry": 157.5,
               "lot": 1000, "tp_pips": 20.0, "sl_pips": 12.5}
        tp, sl = F._tp_sl_prices(pos)
        # BIDがTPちょうど、ASKはその上 → 到達（買いはBIDで決済するので正しい）
        self.assertEqual(self._adv(pos, tp, tp + 0.005)["level"], "take")
        # BIDがSLちょうど → 損切り
        self.assertEqual(self._adv(pos, sl, sl + 0.005)["level"], "cut")

    def test_short_uses_ask_for_both_targets(self):
        self._setup_bars("day", [(157.60, 157.35, 157.50)])
        pos = {"id": "x", "symbol": "USD_JPY", "side": "short", "entry": 157.5,
               "lot": 1000, "tp_pips": 20.0, "sl_pips": 12.5}
        tp, sl = F._tp_sl_prices(pos)
        # ASKがTPに届いていなければ到達にしない（BIDだけ届いていてもダメ）
        a = self._adv(pos, tp - 0.005, tp + 0.001)
        self.assertNotEqual(a["level"], "take", "ASKが届いていないのに利確扱い")
        self.assertEqual(self._adv(pos, tp - 0.005, tp)["level"], "take")
        # 損切りはASKが上抜けたら成立
        self.assertEqual(self._adv(pos, sl - 0.005, sl)["level"], "cut")

    # --- 建てる前の値動きを使わない ---
    def test_no_touch_from_bars_before_entry(self):
        """足の安値がSLを割っていても、建てる前の足なら反応しないこと。"""
        self._setup_bars("day", [(158.5, 157.0, 158.2)])   # 安値157.0
        # 「いま」をそのまま使うと、実行時刻が15分足のちょうど頭（:00/:15/:30/:45）に
        # 当たった1分間だけ、足の開始＝建玉時刻になって足が残り、テストが落ちていた。
        # 建てたのは足が始まった1分後、と固定する（足は必ず建玉より前に始まっている）。
        bar_ms = 15 * 60000
        now_ms = int(F.datetime.datetime.now(F.JST).timestamp() * 1000)
        opened = (now_ms // bar_ms) * bar_ms + 60000
        pos = {"id": "x", "symbol": "USD_JPY", "side": "long", "entry": 158.2,
               "lot": 1000, "tp_pips": 20.0, "sl_pips": 12.5,
               "opened_at": F.datetime.datetime.fromtimestamp(
                   opened / 1000, F.JST).strftime("%Y-%m-%d %H:%M")}
        _, sl = F._tp_sl_prices(pos)
        self.assertLess(157.0, sl, "前提: 足の安値はSLを割っている")
        a = self._adv(pos, 158.2, 158.205)
        self.assertNotEqual(a["level"], "cut",
                            "建てる前の安値で損切り推奨を出している")

    # --- 接触で拾った時は決済ではなく確認を促す ---
    def test_touch_asks_to_verify(self):
        self._setup_bars("day", [(158.5, 157.0, 158.2)])
        pos = {"id": "x", "symbol": "USD_JPY", "side": "long", "entry": 158.2,
               "lot": 1000, "tp_pips": 20.0, "sl_pips": 12.5}   # 時刻なし＝絞り込まない
        a = self._adv(pos, 158.2, 158.205)
        self.assertEqual(a["level"], "cut")
        self.assertTrue(a["touched"])
        self.assertIn("確認", a["reason"])

    # --- MFEは建値より手前に行かない ---
    def test_mfe_never_precedes_entry(self):
        self._setup_bars("day", [(157.60, 157.45, 157.55)])
        pos = {"id": "x", "symbol": "USD_JPY", "side": "long", "entry": 157.5,
               "lot": 1000, "tp_pips": 20.0, "sl_pips": 12.5}
        a = self._adv(pos, 157.40, 157.405)      # 含み損
        self.assertGreaterEqual(a["mfe"], 157.5, "最高益が建値を下回っている")
        pos["side"] = "short"
        a = self._adv(pos, 157.60, 157.605)
        self.assertLessEqual(a["mfe"], 157.5)

    # --- 建玉は必ず自分のモードで評価される ---
    def test_position_is_measured_in_its_own_mode(self):
        for gm in ("day", "mtf", "swing"):
            F.MODE = gm; F.P = F.PARAMS[gm]
            self.assertEqual(F.pos_mode({"mode": "day"}), "day",
                             f"運用が{gm}の時に建玉のモードが無視された")


class EntryRuleParityTest(unittest.TestCase):
    """画面(index.html)の entrySide() と fx_signal.py の entry_side() が同じ答えを返すこと。

    ここがズレると、サーバーの通知と画面の「⚡ライブ」通知が別のルールで動く。
    実際に mtf モードで画面側だけスコア方式のままになり、ライブ通知が出なくなった。
    """

    RULES = (None, "mtf_pullback")
    ALIGNED = (-1, 0, 1)
    RSI = (0, 20, 39.9, 40, 40.1, 50, 59.9, 60, 60.1, 80, 100)
    TOTAL = (-0.9, -0.41, -0.4, -0.39, 0.0, 0.39, 0.4, 0.41, 0.9)

    def test_same_verdict_for_every_input(self):
        node = shutil.which("node") or shutil.which("nodejs")
        if not node:
            self.skipTest("node が無いので画面側の判定を実行できない")
        html = os.path.join(ROOT, "index.html")
        with open(html, encoding="utf-8") as f:
            src = f.read()
        start = src.index("const MTF_PULLBACK_RSI=")
        end = src.index("return total>=P.th", start)
        end = src.index("}\n", src.index("\n", end)) + 2
        cases = [(r, a, v, t) for r in self.RULES for a in self.ALIGNED
                 for v in self.RSI for t in self.TOTAL]
        script = src[start:end] + (
            "const cases=" + json.dumps(cases) + ";\n"
            "console.log(JSON.stringify(cases.map(([rule,al,rv,total])=>"
            "entrySide(total,rv,{th:0.40,rule},al))));\n")
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                         encoding="utf-8") as f:
            f.write(script); path = f.name
        self.addCleanup(os.unlink, path)
        out = subprocess.run([node, path], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        js = json.loads(out.stdout)
        self.assertEqual(len(js), len(cases))

        self.addCleanup(setattr, F, "P", F.P)
        for (rule, aligned, rv, total), got in zip(cases, js):
            F.P = {"th": 0.40} if rule is None else {"th": 0.40, "rule": rule}
            want = F.entry_side("USD_JPY", total, rv, 0.40, aligned=aligned)
            self.assertEqual(
                want, got,
                f"rule={rule} aligned={aligned} rsi={rv} score={total}: "
                f"python={want} / 画面={got}")


class ForwardResolveTest(RunTestCase):
    """前向き検証の決着判定（サーバ側）。

    直したのは4点。どれも「勝ち側に甘く出る」向きの誤りだった。
      1) 買いの建値を bid で記録していた（実際の約定は ask）
      2) 売りの TP/SL を bid で見ていた（売りの決済は ask）
      3) 判定がブラウザ側にあり、開いている間しか見ていなかったので、
         SLに触れてから戻ってTPに行った場合に「TP勝ち」と記録できた
      4) 期限が無く、決着しない記録がその銘柄の記録を永久に止めていた
    """

    MS = 60000
    BAR = 15                                     # day/mtf は15分足

    def _rec(self, side="long", entry=150.000, tp_pips=16.0, sl_pips=10.0,
             mode="day", ts=None):
        d = 1 if side == "long" else -1
        return {"sym": "USD_JPY", "side": side, "mode": mode,
                "ts": ts if ts is not None else self.T0,
                "entry": entry,
                "tp": round(entry + d * tp_pips * 0.01, 5),
                "sl": round(entry - d * sl_pips * 0.01, 5),
                "b": tp_pips / sl_pips, "open": True}

    def setUp(self):
        super().setUp()
        self.T0 = int(F.datetime.datetime(2026, 9, 10, 12, 0,
                                          tzinfo=F.JST).timestamp() * 1000)
        self.spread = F.SPREAD_PIPS["USD_JPY"] * F.PIP_SIZE      # 0.002
        # 差し替える前に元を保存する（後で保存すると差し替えた方を戻してしまう）
        self.addCleanup(setattr, F, "get_ohlc_hist_timed",
                        F.__dict__["get_ohlc_hist_timed"])

    def _bars(self, rows, pre=1):
        """rows=[(high,low,close)] を T0 の次の足から並べ、取得関数を差し替える。

           手前に pre 本ぶんの足を置く。建てた時刻を含む足が無いと
           「どこから後ろを見るか」が決まらないため（実データでは必ずある）。"""
        bar_ms = self.BAR * self.MS
        first = ((self.T0 // bar_ms) + 1) * bar_ms
        times = ([first - (pre - k) * bar_ms for k in range(pre)]
                 + [first + i * bar_ms for i in range(len(rows))])
        oh = [(150.0, 150.0, 150.0)] * pre + list(rows)   # 手前の足は判定に使わない
        F.get_ohlc_hist_timed = lambda sym, days, cap: (times, list(oh))
        return times[-1] + 2 * bar_ms            # この時刻を now にすれば全部確定済み

    # ---- 売り: 決済は ask ----
    def test_short_tp_needs_the_ask_to_reach_it(self):
        """売りのTPは bid が tp に触れただけでは足りない（決済は ask）。"""
        r = self._rec("short")
        tp = r["tp"]
        now = self._bars([(150.05, tp, 150.02)])          # 安値がちょうど tp
        self.assertIsNone(F.resolve_forward_record(r, now),
                          "売りのTPを bid で判定している（ask で決済するので早すぎる）")
        now = self._bars([(150.05, tp - self.spread, 150.02)])
        got = F.resolve_forward_record(r, now)
        self.assertEqual(got["result"], "tp")

    def test_short_sl_fires_when_the_ask_reaches_it(self):
        """売りのSLは bid が sl に届く前（spread ぶん手前）で当たる。"""
        r = self._rec("short")
        sl = r["sl"]
        now = self._bars([(sl - self.spread, 149.95, 150.02)])
        got = F.resolve_forward_record(r, now)
        self.assertIsNotNone(got, "売りのSLを bid で判定している（当たるのが遅すぎる）")
        self.assertEqual(got["result"], "sl")

    def test_short_r_has_no_entry_cost(self):
        """売りは bid で約定するので建値は正しい。Rは設計どおり ±b。"""
        r = self._rec("short")
        now = self._bars([(150.05, r["tp"] - self.spread, 150.0)])
        self.assertAlmostEqual(F.resolve_forward_record(r, now)["R"], 1.6, places=3)
        now = self._bars([(r["sl"] - self.spread, 149.9, 150.0)])
        self.assertAlmostEqual(F.resolve_forward_record(r, now)["R"], -1.0, places=3)

    # ---- 買い: 建値は ask ----
    def test_long_pays_the_spread_at_entry(self):
        """買いの約定は ask。勝ちは b より小さく、負けは 1 より大きくなる。"""
        r = self._rec("long")
        cost = self.spread / (r["entry"] - r["sl"])           # = spread / 1R
        now = self._bars([(r["tp"], 150.0, 150.1)])
        win = F.resolve_forward_record(r, now)
        self.assertEqual(win["result"], "tp")
        self.assertAlmostEqual(win["R"], 1.6 - cost, places=4)
        self.assertAlmostEqual(win["fill"], round(r["entry"] + self.spread, 3), places=4)
        now = self._bars([(150.0, r["sl"], 149.95)])
        lose = F.resolve_forward_record(r, now)
        self.assertAlmostEqual(lose["R"], -1.0 - cost, places=4)

    def test_long_touch_levels_stay_on_the_bid(self):
        """買いの決済(売り)は bid で約定するので、水準は動かさない。"""
        self.assertEqual(F.fwd_touch_levels("long", 1.5, 1.0, 0.002), (1.5, 1.0))
        self.assertEqual(F.fwd_touch_levels("short", 1.5, 1.0, 0.002), (1.498, 0.998))
        self.assertAlmostEqual(F.fwd_fill("long", 150.0, 0.002), 150.002)
        self.assertEqual(F.fwd_fill("short", 150.0, 0.002), 150.0)

    # ---- 順序 ----
    def test_sl_first_then_tp_is_a_loss(self):
        """SLに触れてから戻ってTPに行った場合は負け。

        ブラウザ判定はここを「TP勝ち」にできた。開いていない間の値動きを
        見ておらず、次に見た時の現在値だけで決めていたため。"""
        r = self._rec("long")
        now = self._bars([(150.0, r["sl"], 149.95),        # 先にSL
                          (r["tp"] + 0.1, 150.0, 150.3)])   # 後からTP
        got = F.resolve_forward_record(r, now)
        self.assertEqual(got["result"], "sl", "SLが先なのに勝ちにしている")
        self.assertEqual(got["bars"], 1)

    def test_same_bar_touching_both_counts_as_a_loss(self):
        """同じ足で両方に触れたら、どちらが先かは分からない。負けに倒す。"""
        r = self._rec("long")
        now = self._bars([(r["tp"] + 0.1, r["sl"] - 0.1, 150.0)])
        self.assertEqual(F.resolve_forward_record(r, now)["result"], "sl")

    def test_stays_open_when_neither_level_is_reached(self):
        """どちらにも届いていない記録は決着させない。"""
        r = self._rec("long")
        now = self._bars([(r["tp"] - 0.02, r["sl"] + 0.02, 150.0)] * 3)
        self.assertIsNone(F.resolve_forward_record(r, now))

    def test_the_forming_bar_is_not_used(self):
        """形成中の足の高安は使わない（まだ確定していない）。"""
        r = self._rec("long")
        bar_ms = self.BAR * self.MS
        first = ((self.T0 // bar_ms) + 1) * bar_ms
        F.get_ohlc_hist_timed = lambda s, d, c: (
            [first - bar_ms, first], [(150.0, 150.0, 150.0), (r["tp"] + 0.1, 150.0, 150.2)])
        self.assertIsNone(F.resolve_forward_record(r, first + 5 * self.MS),
                          "形成中の足で決着させている")

    # ---- 期限 ----
    def test_expires_after_the_mode_limit(self):
        """期限を過ぎたら、その足の終値で手仕舞う。

        期限が無いと、決着しない記録が1件あるだけでその銘柄は二度と
        記録されなくなる（fwdRecord は銘柄ごとにopenを1件しか持たない）。"""
        r = self._rec("long")
        n = F.FWD_MAX_BARS["day"]
        flat = (r["tp"] - 0.02, r["sl"] + 0.02, 150.05)
        now = self._bars([flat] * (n + 5))
        got = F.resolve_forward_record(r, now)
        self.assertEqual(got["result"], "time")
        self.assertEqual(got["bars"], n)
        cost = self.spread / (r["entry"] - r["sl"])
        self.assertAlmostEqual(got["R"], (150.05 - 150.0) / 0.10 - cost, places=4)

    def test_short_time_exit_buys_at_the_ask(self):
        r = self._rec("short")
        n = F.FWD_MAX_BARS["day"]
        now = self._bars([(r["sl"] - self.spread - 0.02, r["tp"] + 0.02, 149.95)] * (n + 2))
        got = F.resolve_forward_record(r, now)
        self.assertEqual(got["result"], "time")
        # 売りの手仕舞いは ask（= 終値 + spread）で買い戻す
        self.assertAlmostEqual(got["R"], (150.0 - (149.95 + self.spread)) / 0.10, places=4)

    def test_each_mode_uses_its_own_limit(self):
        self.assertEqual(set(F.FWD_MAX_BARS), set(F.PARAMS))

    # ---- ファイルの受け渡し ----
    # ---- 実運用の出口（利確推奨）でのR ----
    def test_advice_policy_is_recorded_alongside(self):
        """TP/SL到達だけでなく、利確推奨で降りた場合のRも残すこと。

        実運用は利確推奨で決済している。バックテスト実測では mtf で
        tp_sl +0.034R に対し advice +0.069R と出口の違いが期待値の半分を
        占めるので、TP/SLだけだと運用していない戦略の成績になる。"""
        self.addCleanup(setattr, F, "htf_aligned_series",
                        F.__dict__["htf_aligned_series"])
        F.htf_aligned_series = lambda sym, times, days: [0] * len(times)
        r = self._rec("long")
        # 指標の助走ぶんを手前に置き、その後じわじわ上げてTPに届かせる
        warm = [(150.0 + i * 0.001, 149.99 + i * 0.001, 150.0 + i * 0.001)
                for i in range(150)]
        after = [(150.15 + i * 0.02, 150.10 + i * 0.02, 150.14 + i * 0.02)
                 for i in range(12)]
        bar_ms = self.BAR * self.MS
        first = ((self.T0 // bar_ms) + 1) * bar_ms
        times = ([first - (len(warm) - k) * bar_ms for k in range(len(warm))]
                 + [first + i * bar_ms for i in range(len(after))])
        F.get_ohlc_hist_timed = lambda sym, days, cap: (times, warm + after)
        got = F.resolve_forward_record(r, times[-1] + 2 * bar_ms)
        self.assertIsNotNone(got)
        self.assertIn("policies", got, "決済ポリシー別のRが残っていない")
        self.assertIn("advice", got["policies"])
        for v in got["policies"].values():
            self.assertIsInstance(v, float)

    def test_policy_failure_does_not_block_the_verdict(self):
        """ポリシー再現が落ちても、本体の決着判定は止めないこと。"""
        self.addCleanup(setattr, F, "fwd_policy_r", F.__dict__["fwd_policy_r"])
        def boom(*a, **k):
            raise RuntimeError("わざと")
        F.fwd_policy_r = boom
        r = self._rec("long")
        now = self._bars([(r["tp"], 150.0, 150.1)])
        got = F.resolve_forward_record(r, now)
        self.assertEqual(got["result"], "tp")
        self.assertNotIn("policies", got)

    def test_no_policy_without_enough_warmup(self):
        """指標の助走が足りない記録では、無理に数字を作らないこと。"""
        r = self._rec("long")
        now = self._bars([(r["tp"], 150.0, 150.1)])
        self.assertNotIn("policies", F.resolve_forward_record(r, now))

    def test_key_matches_the_dashboard(self):
        """Python の fwd_key() と画面の fwdCloseKey() が同じ形であること。

        ここがズレると、サーバが判定しても画面が結果を拾えない。"""
        rec = {"ts": 1789000000000, "sym": "USD_JPY", "side": "long", "entry": 154.115}
        self.assertEqual(F.fwd_key(rec), "1789000000000|USD_JPY|long")
        node = shutil.which("node") or shutil.which("nodejs")
        if not node:
            self.skipTest("node が無いので画面側を実行できない")
        with open(os.path.join(ROOT, "index.html"), encoding="utf-8") as f:
            src = f.read()
        i = src.index("function fwdCloseKey(")
        js = src[i:src.index("\n", i)]
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                         encoding="utf-8") as f:
            f.write(js.split("//")[0] + "\nconsole.log(fwdCloseKey("
                    + json.dumps(rec) + "));\n")
            path = f.name
        self.addCleanup(os.unlink, path)
        out = subprocess.run([node, path], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), F.fwd_key(rec))

    def test_resolve_forward_only_writes_the_close_file(self):
        """forward_log.json（アプリが書く）には触らないこと。

        同じファイルを両方から書くと、5分ごとのコミットが rebase -X ours で
        アプリ側を優先し、サーバの判定が毎回捨てられる。"""
        d = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, d)
        log_p = os.path.join(d, "forward_log.json")
        close_p = os.path.join(d, "forward_close.json")
        r = self._rec("long")
        body = {"updated": "x", "entries": [r]}
        with open(log_p, "w", encoding="utf-8") as f:
            json.dump(body, f)
        with open(log_p, encoding="utf-8") as f:
            before = f.read()
        self.addCleanup(setattr, F, "FWD_LOG_FILE", F.FWD_LOG_FILE)
        self.addCleanup(setattr, F, "FWD_CLOSE_FILE", F.FWD_CLOSE_FILE)
        F.FWD_LOG_FILE, F.FWD_CLOSE_FILE = log_p, close_p
        self.assertEqual(F.resolve_forward(self._bars([(r["tp"], 150.0, 150.1)])), 1)
        with open(log_p, encoding="utf-8") as f:
            self.assertEqual(f.read(), before, "アプリが書くファイルを書き換えている")
        with open(close_p, encoding="utf-8") as f:
            got = json.load(f)["closes"]
        self.assertEqual(list(got), [F.fwd_key(r)])
        self.assertEqual(got[F.fwd_key(r)]["result"], "tp")
        # 判定済みは作り直さない（古い足を毎回取りに行かないため）
        F.get_ohlc_hist_timed = lambda *a: (_ for _ in ()).throw(
            AssertionError("判定済みの記録を取り直している"))
        self.assertEqual(F.resolve_forward(), 0)

    def test_close_entries_for_deleted_records_are_dropped(self):
        d = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, d)
        log_p = os.path.join(d, "forward_log.json")
        close_p = os.path.join(d, "forward_close.json")
        with open(log_p, "w", encoding="utf-8") as f:
            json.dump({"entries": [self._rec("long")]}, f)
        with open(close_p, "w", encoding="utf-8") as f:
            json.dump({"closes": {"999|EUR_JPY|short": {"result": "tp", "R": 1.6}}}, f)
        self.addCleanup(setattr, F, "FWD_LOG_FILE", F.FWD_LOG_FILE)
        self.addCleanup(setattr, F, "FWD_CLOSE_FILE", F.FWD_CLOSE_FILE)
        F.FWD_LOG_FILE, F.FWD_CLOSE_FILE = log_p, close_p
        r = self._rec("long")
        F.resolve_forward(self._bars([(r["tp"], 150.0, 150.1)]))
        with open(close_p, encoding="utf-8") as f:
            got = json.load(f)["closes"]
        self.assertNotIn("999|EUR_JPY|short", got, "消した記録の判定が残っている")

    def test_missing_log_file_is_not_an_error(self):
        self.addCleanup(setattr, F, "FWD_LOG_FILE", F.FWD_LOG_FILE)
        F.FWD_LOG_FILE = os.path.join(tempfile.mkdtemp(), "nope.json")
        self.assertEqual(F.resolve_forward(), 0)

    def test_recorded_spread_is_preferred_over_the_default(self):
        """記録時に実測したスプレッドがあればそれを使う。桁が変な値は無視する。"""
        r = self._rec("short"); r["sp"] = 0.010          # 1.0pips（既定は0.2pips）
        now = self._bars([(150.05, r["tp"] - 0.002, 150.02)])
        self.assertIsNone(F.resolve_forward_record(r, now),
                          "記録済みのスプレッドを使っていない")
        now = self._bars([(150.05, r["tp"] - 0.010, 150.02)])
        self.assertEqual(F.resolve_forward_record(r, now)["result"], "tp")
        r["sp"] = 9.9                                    # 明らかに桁がおかしい
        now = self._bars([(150.05, r["tp"] - self.spread, 150.02)])
        self.assertEqual(F.resolve_forward_record(r, now)["result"], "tp",
                         "壊れた値を信用している")

    def test_a_level_the_bars_never_reached_is_not_a_win(self):
        """足が届いていない水準を勝ちにしないこと。

        値は 9/8 の AUD/JPY を5分ごとのbidスナップショットで追った時の
        高値111.259・安値110.912（TPは111.279で、2.0pips届いていない）。
        実際にはこの記録は15分足の高値がTPに届いており勝ちで正しかったが、
        「観測できた範囲で届いていないなら決着させない」ことをここで固定する。"""
        r = {"sym": "AUD_JPY", "side": "long", "mode": "day", "ts": self.T0,
             "entry": 110.996, "tp": 111.279, "sl": 110.819, "b": 1.6, "open": True}
        now = self._bars([(111.259, 110.912, 111.200)] * 6)   # 実測の高値・安値
        self.assertIsNone(F.resolve_forward_record(r, now),
                          "足が届いていない水準で勝ちにしている")


class ForwardClusterTest(unittest.TestCase):
    """相関の補正：同時・同方向の記録を1つの賭けとして数えること。

    4通貨すべてが対円なので、円が動けば同方向の記録は一斉に同じ結果になる。
    実際の最初の5件は 9/8 に3件・9/10 に2件が同時進行で、独立した検証は2回
    しかなかった。件数のまま Wilson に入れると、足りていない段階で
    「エッジ実証」と表示してしまう。
    """

    # 実際の記録（サーバ判定後の ts / closed）。すべて買い。
    REAL = [
        {"sym": "GBP_JPY", "side": "long", "ts": 1788853090277,
         "closed": 1788866100000, "R": 1.5738, "open": False},
        {"sym": "AUD_JPY", "side": "long", "ts": 1788853090279,
         "closed": 1788866100000, "R": 1.5706, "open": False},
        {"sym": "USD_JPY", "side": "long", "ts": 1788856209992,
         "closed": 1788866100000, "R": 1.5913, "open": False},
        {"sym": "USD_JPY", "side": "long", "ts": 1789040076197,
         "closed": 1789043400000, "R": 1.5886, "open": False},
        {"sym": "EUR_JPY", "side": "long", "ts": 1789040977638,
         "closed": 1789043400000, "R": 1.5753, "open": False},
    ]

    def _run(self, rows):
        node = shutil.which("node") or shutil.which("nodejs")
        if not node:
            self.skipTest("node が無いので画面側を実行できない")
        with open(os.path.join(ROOT, "index.html"), encoding="utf-8") as f:
            src = f.read()
        i = src.index("function fwdClusters(")
        end = src.index("\n}", i) + 2
        script = src[i:end] + (
            "\nconsole.log(JSON.stringify(fwdClusters(" + json.dumps(rows) + ")));\n")
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                         encoding="utf-8") as f:
            f.write(script); path = f.name
        self.addCleanup(os.unlink, path)
        out = subprocess.run([node, path], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_the_real_five_records_are_two_independent_events(self):
        got = self._run(self.REAL)
        self.assertEqual(len(got), 2, f"独立イベント数が違う: {got}")
        self.assertEqual(sorted(c["n"] for c in got), [2, 3])
        self.assertTrue(all(c["win"] for c in got))

    def test_opposite_sides_are_not_merged(self):
        """同じ時間帯でも方向が逆なら別の賭け。"""
        rows = [dict(self.REAL[0]), dict(self.REAL[1])]
        rows[1]["side"] = "short"
        self.assertEqual(len(self._run(rows)), 2)

    def test_non_overlapping_records_stay_separate(self):
        a = {"sym": "USD_JPY", "side": "long", "ts": 1000, "closed": 2000, "R": 1.6}
        b = {"sym": "USD_JPY", "side": "long", "ts": 3000, "closed": 4000, "R": -1.0}
        self.assertEqual(len(self._run([a, b])), 2)

    def test_a_chain_of_overlaps_becomes_one_event(self):
        """A-B-C と数珠つなぎに重なる場合も1つにまとめること。

        AとCは直接は重なっていないが、Bを介して同じ相場を見ている。"""
        rows = [
            {"sym": "USD_JPY", "side": "long", "ts": 1000, "closed": 2000, "R": 1.6},
            {"sym": "EUR_JPY", "side": "long", "ts": 1900, "closed": 3000, "R": 1.6},
            {"sym": "GBP_JPY", "side": "long", "ts": 2900, "closed": 4000, "R": -1.0},
        ]
        got = self._run(rows)
        self.assertEqual(len(got), 1, f"数珠つなぎがまとまっていない: {got}")
        self.assertEqual(got[0]["n"], 3)
        # まとまりのRは平均。2勝1敗なら (1.6+1.6-1.0)/3 = +0.733 で勝ち扱い
        self.assertAlmostEqual(got[0]["R"], (1.6 + 1.6 - 1.0) / 3, places=4)
        self.assertTrue(got[0]["win"])

    def test_a_cluster_that_nets_negative_is_a_loss(self):
        rows = [
            {"sym": "USD_JPY", "side": "long", "ts": 1000, "closed": 2000, "R": 1.6},
            {"sym": "EUR_JPY", "side": "long", "ts": 1000, "closed": 2000, "R": -1.0},
            {"sym": "GBP_JPY", "side": "long", "ts": 1000, "closed": 2000, "R": -1.0},
        ]
        got = self._run(rows)
        self.assertEqual(len(got), 1)
        self.assertFalse(got[0]["win"], "負け越しているまとまりを勝ちにしている")


class FastProfileTest(RunTestCase):
    """初動プロファイル：型の判定と、表示専用であること。

    実測(1年)で「行き過ぎた所ほど初動は速いが、期待値は上がらない」と出た。
    近いTPへ置き換えても改善しなかったので、判定には一切使わず表示だけに使う。
    ここが判定に混ざると、根拠の無いルールで売買することになる。
    """

    PROF = {
        "bars": 4, "bar_min": 15, "need_r": 1.0,
        "stretch": {"追いかけ(1.5ATR〜)": {"n": 1681, "fast_rate": 37, "mfe": 0.979,
                                     "advice": {"avg_r": -0.04, "ci_lo": -0.1, "ci_hi": 0.02}},
                    "初動(-0.3〜0.3ATR)": {"n": 94, "fast_rate": 17, "mfe": 0.575,
                                       "advice": {"avg_r": -0.103, "ci_lo": -0.35, "ci_hi": 0.143}},
                    "逆張り(-0.3ATR〜)": {"n": 200, "fast_rate": 20, "mfe": 0.65,
                                      "advice": {"avg_r": 0.02, "ci_lo": -0.1, "ci_hi": 0.14}}},
        "adx": {"適正(30〜40)": {"n": 986, "fast_rate": 31, "mfe": 0.824,
                              "advice": {"avg_r": -0.139, "ci_lo": -0.215, "ci_hi": -0.063}}},
        "rsi": {"強め(65〜75)": {"n": 2486, "fast_rate": 34, "mfe": 0.894,
                              "advice": {"avg_r": -0.058, "ci_lo": -0.107, "ci_hi": -0.009}}},
    }

    def test_stretch_band_follows_the_distance_from_the_ema(self):
        sc = {"price": 154.30, "ef": 154.10, "atr": 0.10, "rsi": 70.0, "adx": 33.0}
        got = F.fast_profile(sc, "買い", prof=self.PROF)     # 2.0ATR 上
        self.assertEqual(got["stretch"]["band"], "追いかけ(1.5ATR〜)")
        self.assertEqual(got["stretch"]["fast_rate"], 37)
        self.assertEqual(got["stretch"]["avg_r"], -0.04)
        sc2 = {"price": 154.11, "ef": 154.10, "atr": 0.10, "rsi": 70.0, "adx": 33.0}
        self.assertEqual(F.fast_profile(sc2, "買い", prof=self.PROF)["stretch"]["band"],
                         "初動(-0.3〜0.3ATR)")

    def test_the_distance_is_measured_in_the_trade_direction(self):
        """売りは『EMAより下』が行き過ぎ。方向を掛けないと逆に出る。"""
        sc = {"price": 153.90, "ef": 154.10, "atr": 0.10, "rsi": 30.0, "adx": 33.0}
        self.assertEqual(F.fast_profile(sc, "売り", prof=self.PROF)["stretch"]["band"],
                         "追いかけ(1.5ATR〜)")
        # 同じ場面でも買いから見れば「EMAより2ATR下」＝逆張り側になる
        self.assertEqual(F.fast_profile(sc, "買い", prof=self.PROF)["stretch"]["band"],
                         "逆張り(-0.3ATR〜)")

    def test_missing_backtest_gives_nothing_rather_than_a_guess(self):
        self.assertIsNone(F.fast_profile({"price": 1, "ef": 1, "atr": 0.1}, "買い", prof={}))
        self.assertIsNone(F.fast_profile(None, "買い", prof=self.PROF))

    def test_unknown_band_is_skipped_not_faked(self):
        prof = {"bars": 4, "bar_min": 15, "need_r": 1.0, "adx": {"適正(30〜40)": {}}}
        self.assertIsNone(F.fast_profile({"price": 1.0, "ef": 1.0, "atr": 0.1,
                                          "adx": 99.0, "rsi": 50.0}, "買い", prof=prof))

    def test_profile_never_changes_the_verdict(self):
        """判定に混ざっていないこと。backtest.json が無くても signal は同じ。"""
        self.addCleanup(setattr, F, "BT_FILE", F.BT_FILE)
        F._FAST_PROFILE.clear()
        self.addCleanup(F._FAST_PROFILE.clear)
        F.BT_FILE = os.path.join(self.dir, "nope.json")
        F.MODE = "day"; F.P = F.PARAMS["day"]
        sides = [F.entry_side("USD_JPY", v, 55.0, F.P["th"]) for v in (-0.9, 0.0, 0.9)]
        self.assertEqual(sides, ["売り", None, "買い"])
        self.assertIsNone(F.fast_profile({"price": 1, "ef": 1, "atr": 0.1}, "買い"))


class SubNotifyTest(RunTestCase):
    """副通知：運用モード以外からの参考シグナル。

    mtfは「戻りを待つ」ルールなので、戻りの無い一方向相場では通知が
    一度も出ない（9/3〜9/9 は全通貨500pips前後の一方向で、7,088サンプル中
    上位足は全期間4h↓、15分足RSIが60以上は5.8%だけだった）。
    そこで別モードの合図を参考として足せるようにした。ただし
    デイ全体は -0.077R [-0.115,-0.039] で負けが確定しているので、
    前半後半の検証で唯一そのまま通用した ADX40以上 を既定の絞り込みにする。
      絞り込みなし 前半 -0.083 → 後半 -0.071
      ADX40以上   前半 +0.043 → 後半 +0.041
    """

    def _mode_file(self, body):
        self.write(F.MODE_FILE, body)

    def test_no_sub_setting_means_no_sub_notification(self):
        self._mode_file({"mode": "mtf"})
        self.assertEqual(F.sub_modes(), [])
        self.assertEqual(F.sub_mode_signals({"positions": []}), ([], []))

    def test_the_operating_mode_is_never_duplicated(self):
        """運用モードと同じ副モードは落とす（同じ通知が二重に飛ぶため）。"""
        F.MODE = "mtf"
        self._mode_file({"mode": "mtf", "sub": [{"mode": "mtf"}, {"mode": "day"}]})
        self.assertEqual([x["mode"] for x in F.sub_modes()], ["day"])

    def test_day_defaults_to_the_adx_filter(self):
        """デイを指定したら、既定で ADX40以上 に絞る。

        絞り込み無しのデイは1年4,069件で負けが確定しているので、
        既定を「そのまま全部流す」にしてはいけない。"""
        F.MODE = "mtf"
        self._mode_file({"mode": "mtf", "sub": ["day"]})
        self.assertEqual(F.sub_modes(), [{"mode": "day", "filter": "adx40"}])

    def test_unknown_mode_and_filter_are_rejected(self):
        F.MODE = "mtf"
        self._mode_file({"mode": "mtf", "sub": [{"mode": "nope"},
                                                {"mode": "day", "filter": "でたらめ"}]})
        self.assertEqual(F.sub_modes(), [{"mode": "day", "filter": "all"}])

    def test_adx_filter_blocks_weak_trends(self):
        self.assertTrue(F.SUB_FILTERS["adx40"][0]({"adx": 40.0}))
        self.assertTrue(F.SUB_FILTERS["adx40"][0]({"adx": 55.0}))
        self.assertFalse(F.SUB_FILTERS["adx40"][0]({"adx": 39.9}))
        self.assertFalse(F.SUB_FILTERS["adx40"][0]({"adx": None}))

    def _fake_score(self, adx, side="買い"):
        return {"price": 154.0, "adx": adx, "rsi": 60.0, "score": 0.55,
                "tp_pips": 24.0, "sl_pips": 15.0, "side": side,
                "tech": 0.5, "fund": 0.1}

    def test_filtered_out_signals_are_not_notified(self):
        self.addCleanup(setattr, F, "score_pair", F.__dict__["score_pair"])
        self.addCleanup(setattr, F, "in_blackout", F.__dict__["in_blackout"])
        F.in_blackout = lambda sym: False
        F.MODE = "mtf"; F.P = F.PARAMS["mtf"]
        F.score_pair = lambda sym, oh: self._fake_score(20.0)
        parts, _ = F.sub_mode_signals({"positions": []},
                                      subs=[{"mode": "day", "filter": "adx40"}])
        self.assertEqual(parts, [], "ADXが足りないのに参考通知を出している")
        F.score_pair = lambda sym, oh: self._fake_score(45.0)
        parts, ev = F.sub_mode_signals({"positions": []},
                                       subs=[{"mode": "day", "filter": "adx40"}])
        self.assertEqual(len(parts), len(F.SYMBOLS))
        self.assertEqual(len(ev), len(F.SYMBOLS))

    def test_the_text_says_it_is_not_the_operating_mode(self):
        """参考であること・実測値・登録時のモードを必ず本文に入れること。

        ここが抜けると、運用モードの合図と同じものだと思って入ってしまう。"""
        self.addCleanup(setattr, F, "score_pair", F.__dict__["score_pair"])
        self.addCleanup(setattr, F, "in_blackout", F.__dict__["in_blackout"])
        F.in_blackout = lambda sym: False
        F.MODE = "mtf"; F.P = F.PARAMS["mtf"]
        F.score_pair = lambda sym, oh: self._fake_score(45.0)
        txt = F.sub_mode_signals({"positions": []},
                                 subs=[{"mode": "day", "filter": "adx40"}])[0][0]
        self.assertIn("参考", txt)
        self.assertIn("運用は", txt)
        # 記録されないと書いてはいけない。参考モードの合図も、アプリを開いていれば
        # 前向き検証に残る（subLiveSignals が fwdRecord を呼ぶ）。
        self.assertNotIn("記録されません", txt)
        self.assertIn("前向き検証にも残ります", txt)
        self.assertIn("ADX40以上に限定", txt)
        self.assertIn("+0.04R", txt)
        self.assertIn("-0.077R", txt)
        self.assertIn("デイ", txt)

    def test_held_symbols_are_skipped(self):
        """保有中の通貨は本体と同じ理由で見送る（重複・両建てを避ける）。"""
        self.addCleanup(setattr, F, "score_pair", F.__dict__["score_pair"])
        self.addCleanup(setattr, F, "in_blackout", F.__dict__["in_blackout"])
        F.in_blackout = lambda sym: False
        F.MODE = "mtf"; F.P = F.PARAMS["mtf"]
        F.score_pair = lambda sym, oh: self._fake_score(45.0)
        data = {"positions": [{"symbol": F.SYMBOLS[0], "side": "short",
                               "status": "open"}]}
        parts, _ = F.sub_mode_signals(data, subs=[{"mode": "day", "filter": "adx40"}])
        self.assertEqual(len(parts), len(F.SYMBOLS) - 1)
        self.assertNotIn(F.SYMBOLS[0], "".join(parts))

    def test_the_same_signal_is_not_repeated_every_run(self):
        """合図が続いているあいだ、5分ごとに同じ通知を出さないこと。

        本体は status.json の前回値で抑えているが、副通知は status.json に
        載らないので自前の状態ファイルで抑える。"""
        self.addCleanup(setattr, F, "score_pair", F.__dict__["score_pair"])
        self.addCleanup(setattr, F, "in_blackout", F.__dict__["in_blackout"])
        self.addCleanup(setattr, F, "mtf_view", F.__dict__["mtf_view"])
        F.in_blackout = lambda sym: False
        F.mtf_view = lambda sym: {"aligned": 0}
        F.MODE = "mtf"; F.P = F.PARAMS["mtf"]
        F.score_pair = lambda sym, oh: self._fake_score(45.0)
        subs = [{"mode": "day", "filter": "adx40"}]
        first, _ = F.sub_mode_signals({"positions": []}, subs=subs)
        self.assertEqual(len(first), len(F.SYMBOLS))
        again, _ = F.sub_mode_signals({"positions": []}, subs=subs)
        self.assertEqual(again, [], "同じ合図を毎回送り直している")
        # 向きが変われば新しい合図として送る
        F.score_pair = lambda sym, oh: self._fake_score(45.0, side="売り")
        flipped, _ = F.sub_mode_signals({"positions": []}, subs=subs)
        self.assertEqual(len(flipped), len(F.SYMBOLS))

    def test_the_operating_mode_verdict_is_untouched(self):
        """副通知を出しても、運用モードの設定が書き換わっていないこと。"""
        self.addCleanup(setattr, F, "score_pair", F.__dict__["score_pair"])
        self.addCleanup(setattr, F, "in_blackout", F.__dict__["in_blackout"])
        F.in_blackout = lambda sym: False
        F.MODE = "mtf"; F.P = F.PARAMS["mtf"]
        F.score_pair = lambda sym, oh: self._fake_score(45.0)
        F.sub_mode_signals({"positions": []}, subs=[{"mode": "day", "filter": "adx40"}])
        self.assertEqual(F.MODE, "mtf")
        self.assertIs(F.P, F.PARAMS["mtf"])

    def test_at_most_two_sub_modes(self):
        F.MODE = "mtf"
        self._mode_file({"mode": "mtf", "sub": ["day", "swing", "scalp"]})
        self.assertEqual(len(F.sub_modes()), 2)


class SubFilterParityTest(unittest.TestCase):
    """参考通知の絞り込みが、サーバー(Python)と⚡ライブ(画面)で一致すること。

    LINEへの経路は2つある。サーバー通知は api.line.me へ直接送っていて
    Cloudflare Worker を通らず、⚡ライブは Worker 経由。最初はサーバー側
    にしか参考通知を入れていなかった。
    絞り込みがズレると、同じ場面でサーバーからは届くのにライブからは
    届かない（逆も）という状態になる。entrySide で実際に起きた事故なので
    ここで固定する。
    """

    ADX = (None, 0, 19.9, 20, 25, 30, 39.9, 40, 40.1, 55, 70, 99)

    def test_adx_filter_matches_the_dashboard(self):
        node = shutil.which("node") or shutil.which("nodejs")
        if not node:
            self.skipTest("node が無いので画面側を実行できない")
        with open(os.path.join(ROOT, "index.html"), encoding="utf-8") as f:
            src = f.read()
        i = src.index("function subFilterOK(")
        end = src.index("\n}", i) + 2
        cases = [{"adx": a} for a in self.ADX]
        script = src[i:end] + (
            "\nconsole.log(JSON.stringify(" + json.dumps(cases)
            + ".map(sc=>subFilterOK('adx40',sc))));\n")
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                         encoding="utf-8") as f:
            f.write(script); path = f.name
        self.addCleanup(os.unlink, path)
        out = subprocess.run([node, path], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        js = json.loads(out.stdout)
        fn = F.SUB_FILTERS["adx40"][0]
        for (a, got) in zip(self.ADX, js):
            self.assertEqual(fn({"adx": a}), got, f"ADX={a}: python={fn({'adx': a})} / 画面={got}")

    def test_the_dashboard_knows_the_same_sub_modes(self):
        """画面の SUB_INFO と Python の既定の絞り込みが食い違わないこと。"""
        with open(os.path.join(ROOT, "index.html"), encoding="utf-8") as f:
            src = f.read()
        i = src.index("var SUB_INFO=")
        chunk = src[i:src.index("function subLabel", i)]
        for mode, fname in F.SUB_DEFAULT_FILTER.items():
            if mode == "mtf":
                continue                       # mtfは運用モード側なので画面の一覧には無い
            self.assertIn(mode + ":{", chunk, f"画面に {mode} の説明が無い")
            self.assertIn("filter:'" + fname + "'", chunk,
                          f"{mode} の既定の絞り込みが画面とズレている")


class StatsShortfallTest(unittest.TestCase):
    """統計が出せない時に、件数不足なのか取得失敗なのかが分かること。

    「USD/JPYだけ想定保有時間とTP勝率が出ない」という形で分からなくなった。
    原因は件数不足だけで判定には影響しないが、欄がまるごと消えるため
    不具合と区別が付かなかった。実測(1年261日)の1銘柄あたり20日の件数は
      スイング USD/JPY 9.0 / EUR/JPY 5.4 / GBP/JPY 8.8 / AUD/JPY 6.3
    で、表示に必要な8件をまたぐ。出たり出なかったりするのが正常。
    """

    def _run(self, js_tail):
        node = shutil.which("node") or shutil.which("nodejs")
        if not node:
            self.skipTest("node が無いので画面側を実行できない")
        with open(os.path.join(ROOT, "index.html"), encoding="utf-8") as f:
            src = f.read()
        i = src.index("function simStats(sym,oh,P,barMin,sides,out){")
        end = src.index("\n// 拡張v2", i)
        head = ("function median(a){if(!a.length)return null;const s=a.slice()"
                ".sort((x,y)=>x-y),m=s.length>>1;"
                "return s.length%2?s[m]:(s[m-1]+s[m])/2;}\nconst PS_=0.01;\n")
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                         encoding="utf-8") as f:
            f.write(head + src[i:end] + js_tail); path = f.name
        self.addCleanup(os.unlink, path)
        out = subprocess.run([node, path], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def _bars(self, n):
        # 高安終。使うのは sides を外から渡すので中身は単調で良い
        return [[100.0 + i * 0.05, 99.9 + i * 0.05, 100.0 + i * 0.05] for i in range(n)]

    def test_the_count_is_reported_even_when_it_is_too_small(self):
        """8件に満たなくても、件数は out に書き戻すこと。"""
        P = {"ema_s": 5, "macd": [3, 6, 3], "adx": 3, "atr": 5}
        tail = ("\nconst P=" + json.dumps(P) + ";\n"
                "const oh=" + json.dumps(self._bars(120)) + ";\n"
                "const sides=oh.map((_,i)=>(i>=20&&i%20===0)"
                "?{side:'買い',tpp:5,slp:5}:null);\n"
                "const out={};const st=simStats('USD_JPY',oh,P,60,sides,out);\n"
                "console.log(JSON.stringify({n:out.n,st:st===null?null:st.n}));\n")
        got = self._run(tail)
        self.assertIsNotNone(got["n"], "件数が書き戻されていない")
        self.assertLess(got["n"], 8)
        self.assertIsNone(got["st"], "8件未満なのに統計を返している")

    def test_enough_samples_still_return_stats(self):
        """従来どおり、8件以上なら統計を返すこと（しきい値は変えていない）。"""
        P = {"ema_s": 5, "macd": [3, 6, 3], "adx": 3, "atr": 5}
        tail = ("\nconst P=" + json.dumps(P) + ";\n"
                "const oh=" + json.dumps(self._bars(400)) + ";\n"
                "const sides=oh.map((_,i)=>(i>=20&&i%20===0)"
                "?{side:'買い',tpp:5,slp:5}:null);\n"
                "const out={};const st=simStats('USD_JPY',oh,P,60,sides,out);\n"
                "console.log(JSON.stringify({n:out.n,ok:!!st,wr:st&&st.winRate}));\n")
        got = self._run(tail)
        self.assertGreaterEqual(got["n"], 8)
        self.assertTrue(got["ok"], "8件以上あるのに統計が出ていない")


class PositionModeStickyTest(unittest.TestCase):
    """建玉は、画面のモードを切り替えても『建てた時のモードの物差し』で判定すること。

    実際に起きたこと: スイングで建てた USD/JPY を、画面を運用モード(mtf)へ
    戻したとたん「入った根拠が弱まった」に変わった。サーバーは同じ時刻に
    🟢ホールド「入った根拠が続いている（スコア+0.47）」と判定していた。

    原因は computeOpen() が建玉の mode と、サーバーの adv_* を
    落としていたこと。holdAligned/holdBasis/posAdvice はどれも
    p.mode を読む作りだったが、無いので全部【画面のモード】に
    フォールバックしていた。用意してあったサーバー判定の代替も、
    adv_label が落ちているので働いていなかった。
    """

    def _eval(self, tail, extra=""):
        node = shutil.which("node") or shutil.which("nodejs")
        if not node:
            self.skipTest("node が無いので画面側を実行できない")
        with open(os.path.join(ROOT, "index.html"), encoding="utf-8") as f:
            src = f.read()
        i = src.index("function computeOpen(){")
        end = src.index("function posMode(p){")
        end = src.index("\n", end) + 1
        head = ("const PS=0.01;\nfunction tpsl(p){return[null,null];}\n"
                "let PRICE={},S={mode:'mtf',open_positions:[]},POS={positions:[]};\n")
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                         encoding="utf-8") as f:
            f.write(head + src[i:end] + extra + tail); path = f.name
        self.addCleanup(os.unlink, path)
        out = subprocess.run([node, path], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    REAL_POS = {"id": "p1789085633538", "symbol": "USD_JPY", "side": "long",
                "entry": 154.388, "lot": 3000, "status": "open",
                "entry_mode": "swing", "mode": "swing"}
    REAL_SRV = {"id": "p1789085633538", "symbol": "USD_JPY",
                "adv_level": "hold", "adv_label": "🟢 ホールド",
                "adv_reason": "入った根拠が続いている（スコア+0.47）",
                "mode": "swing"}

    def test_the_position_keeps_its_own_mode(self):
        """画面がmtfでも、建玉のモードはスイングのまま読めること。"""
        tail = ("\nPOS={positions:[" + json.dumps(self.REAL_POS) + "]};\n"
                "const o=computeOpen()[0];\n"
                "console.log(JSON.stringify({mode:o.mode,pm:posMode(o)}));\n")
        got = self._eval(tail)
        self.assertEqual(got["mode"], "swing", "建玉のモードが落ちている")
        self.assertEqual(got["pm"], "swing", "画面のモードに引きずられている")

    def test_the_server_verdict_reaches_the_card(self):
        """サーバーが出した判定が建玉に届くこと（代替が働く前提）。"""
        tail = ("\nPOS={positions:[" + json.dumps(self.REAL_POS) + "]};\n"
                "S.open_positions=[" + json.dumps(self.REAL_SRV) + "];\n"
                "const o=computeOpen()[0];\n"
                "console.log(JSON.stringify({lab:o.adv_label,lv:o.adv_level,rs:o.adv_reason}));\n")
        got = self._eval(tail)
        self.assertEqual(got["lab"], "🟢 ホールド", "サーバー判定が届いていない")
        self.assertEqual(got["lv"], "hold")
        self.assertIn("根拠が続いている", got["rs"])

    def test_a_position_without_a_mode_falls_back_to_the_screen(self):
        """モードを持たない古い建玉は従来どおり画面のモードで見る。"""
        old = dict(self.REAL_POS); old.pop("mode"); old.pop("entry_mode")
        tail = ("\nPOS={positions:[" + json.dumps(old) + "]};\n"
                "console.log(JSON.stringify({pm:posMode(computeOpen()[0])}));\n")
        self.assertEqual(self._eval(tail)["pm"], "mtf")

    def test_entry_mode_alone_is_enough(self):
        """mode が無くても entry_mode があればそちらを使う。"""
        only = dict(self.REAL_POS); only.pop("mode")
        tail = ("\nPOS={positions:[" + json.dumps(only) + "]};\n"
                "console.log(JSON.stringify({pm:posMode(computeOpen()[0])}));\n")
        self.assertEqual(self._eval(tail)["pm"], "swing")


class ModeCompareTest(unittest.TestCase):
    """ツール画面のモード別くらべ。混ぜてはいけないものを混ぜないこと。

    4つのモードは1年ぶんの実測で期待値がまるで違う。
      mtf +0.063R / スイング +0.018R / デイ -0.077R / スキャル -0.300R
    これを1つの集計にまとめると、どの数字も意味を失う。
    """

    # modeSrc='position' は「建玉自身のモードを保存した記録」。
    # これが無いものは、当時の画面モードを写しただけかもしれないので数えない。
    TRADES = [
        # 同じ時間帯・同じ方向のmtf 2件（円が動けば一斉に同じ結果＝独立1回）
        {"mode": "mtf", "modeSrc": "position", "side": "買い", "yen": 820,
         "pips": 12.4, "sl_pips": 21.0,
         "opened_at": "2026-09-08 16:38 JST", "closed_at": "2026-09-08 20:10 JST"},
        {"mode": "mtf", "modeSrc": "position", "side": "買い", "yen": -640,
         "pips": -9.8, "sl_pips": 21.0,
         "opened_at": "2026-09-08 16:40 JST", "closed_at": "2026-09-08 20:02 JST"},
        {"mode": "swing", "modeSrc": "position", "side": "買い", "yen": 99,
         "pips": 3.3, "sl_pips": 42.2,
         "opened_at": "2026-09-11 11:15 JST", "closed_at": "2026-09-11 11:22 JST"},
        {"mode": "day", "modeSrc": "position", "side": "売り", "yen": -725,
         "pips": -8.1, "sl_pips": 15.0,
         "opened_at": "2026-09-11 00:37 JST", "closed_at": "2026-09-11 01:10 JST"},
        {"side": "買い", "yen": 300, "pips": 5.0},          # モード不明
    ]
    # 実際の backtest.json と同じ4モード（取引が1件も無いモードも検証値は出す）
    BT = {"modes": {
        "mtf": {"policies": {"advice": {"avg_r": 0.063, "ci_lo": -0.025,
                                        "ci_hi": 0.150, "n": 709}}},
        "swing": {"policies": {"advice": {"avg_r": 0.018, "ci_lo": -0.105,
                                          "ci_hi": 0.141, "n": 386}}},
        "day": {"policies": {"advice": {"avg_r": -0.077, "ci_lo": -0.115,
                                        "ci_hi": -0.039, "n": 4069}}},
        "scalp": {"policies": {"advice": {"avg_r": -0.300, "ci_lo": -0.338,
                                          "ci_hi": -0.262, "n": 4077}}}}}

    def _render(self):
        node = shutil.which("node") or shutil.which("nodejs")
        if not node:
            self.skipTest("node が無いのでツール画面を実行できない")
        with open(os.path.join(ROOT, "tools.js"), encoding="utf-8") as f:
            src = f.read()
        part1 = src[src.index("function _tJst"):src.index("function _scoreBand")]
        part2 = src[src.index("var SPREAD_PIPS_T"):src.index("function renderCostPanel")]
        script = (
            "let OUT='';\n"
            "global.document={getElementById:()=>({set innerHTML(v){OUT=v;}})};\n"
            + part1 + part2
            + "function loadTrades(){return " + json.dumps(self.TRADES) + ";}\n"
            + "BT_CACHE=" + json.dumps(self.BT) + ";\n"
            "renderModeCompare();\nconsole.log(OUT);\n")
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                         encoding="utf-8") as f:
            f.write(script); path = f.name
        self.addCleanup(os.unlink, path)
        out = subprocess.run([node, path], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout

    def test_every_mode_has_its_own_row(self):
        """取引が1件も無いモードも、検証値だけは並べること。

        スキャルは実測 -0.300R で、触っていなくても「触らない方が良い」
        ことが一覧で分かる必要がある。"""
        html = self._render()
        for lab in ("mtf(上位足押し目)", "スイング(1時間)", "デイ(15分)", "スキャル(1分)"):
            self.assertIn(lab, html, f"{lab} の行が無い")
        self.assertIn("-0.3R", html.replace("-0.300R", "-0.3R"))

    def test_simultaneous_same_direction_trades_count_as_one(self):
        """同時刻・同方向のmtf2件は独立1回として数えること。"""
        html = self._render()
        self.assertIn("2件（独立1）", html)

    def test_the_layout_has_no_fixed_width_that_can_overflow(self):
        """棒を固定幅にしないこと。

        最初は8列の表＋104px固定幅の棒で作ったため、スマホで右端が
        画面外へはみ出し、検証Rの列が見えなくなった。
        棒は残り幅いっぱい(flex)に伸びる .mtrack を使う。"""
        html = self._render()
        self.assertIn('class="mtrack"', html)
        self.assertNotIn("<table", html, "表に戻すと幅が固定されてはみ出す")
        self.assertNotIn("width:104px", html)
        self.assertNotIn("display:inline-block;width:", html)

    def test_unknown_mode_is_excluded_and_reported(self):
        """モード不明は集計に混ぜず、件数を出して気づけるようにすること。"""
        html = self._render()
        self.assertIn("モード不明が1件", html)

    def test_a_mode_without_provenance_is_not_counted(self):
        """出所の無いモード札は数えないこと。

        実際に起きたこと: 画面には mtf 14件と出ていたが、その札は
        「当時の運用モード」を写しただけで、mtfの取引ではなかった。"""
        keep = self.TRADES
        try:
            self.TRADES = [dict(x) for x in keep]
            for x in self.TRADES:
                x.pop("modeSrc", None)
            html = self._render()
        finally:
            self.TRADES = keep
        self.assertIn("モード不明が5件", html)
        self.assertNotIn("2件（独立1）", html, "出所の無い札をモード別に数えている")

    def test_a_small_sample_says_when_and_which_way(self):
        """件数が少ないモードは、期間と売買の内訳を必ず添えること。

        実際に起きたこと: mtf 5件・勝率80%・+0.61R と出たが、
        中身は9/02〜9/03のすべて売りで、実質ひとつの相場だった。
        平均だけ見せると、5回別々に確かめたように読めてしまう。"""
        html = self._render()
        self.assertIn("すべて買い", html, "売買の内訳が出ていない")
        self.assertIn("9/8", html.replace("9月8日", "9/8"), "期間が出ていない")

    def test_a_large_sample_does_not_carry_the_note(self):
        """件数が十分なモードには付けない（意味が無いうえに場所を食う）。"""
        keep = self.TRADES
        try:
            self.TRADES = [dict(keep[3]) for _ in range(25)]
            for i, x in enumerate(self.TRADES):
                x["opened_at"] = "2026-09-%02d 00:37 JST" % (i % 28 + 1)
            html = self._render()
        finally:
            self.TRADES = keep
        self.assertNotIn("すべて売り", html)

    def test_the_unknown_note_does_not_promise_a_link_that_cannot_happen(self):
        """記録簿に無い取引を「押せば紐付く」と書かないこと。"""
        html = self._render()
        self.assertIn("材料そのものがありません", html)

    def test_the_backtest_expectation_is_shown_next_to_it(self):
        """実測のとなりにバックテストの期待値を置くこと。

        件数が少ないうちは実測より検証値の方が当てになるので、
        必ず並べて出す。"""
        html = self._render()
        self.assertIn("+0.063R", html)
        self.assertIn("-0.077R", html)
        self.assertIn("検証", html)

    def test_r_uses_the_trade_own_stop_width(self):
        """1Rはその取引のSL幅。モードの目安で代用しないこと。

        スイングの +3.3pips は SL42.2pips なので +0.08R。
        デイの目安(9pips)で割ると +0.37R になり、4倍以上ずれる。"""
        html = self._render()
        self.assertIn("+0.08R", html)
        self.assertNotIn("+0.37R", html)


class TechFundWeightTest(unittest.TestCase):
    """テク:ファンダの配分が、画面のモードではなく計算対象のモードに従うこと。

    実害が出た例: スイングの建玉を画面mtfで見ると
    「損切り推奨（スコア-0.30）」と出て、画面スイングでは別の判定になった。
    スイングの配分(0.45:0.55)なら買いのスコアの下限は
      0.45×(-1) + 0.55×0.5 = -0.175
    なので -0.30 はスイングでは到達できない。損切りのしきい値は -0.25 なので、
    配分を取り違えると届かないはずの判定が出てしまう。
    """

    def _tfw(self, screen, P):
        node = shutil.which("node") or shutil.which("nodejs")
        if not node:
            self.skipTest("node が無いので画面側を実行できない")
        with open(os.path.join(ROOT, "index.html"), encoding="utf-8") as f:
            src = f.read()
        i = src.index("function tfw(P){")
        line = src[i:src.index("\n", i)]
        script = ("let S=" + json.dumps({"mode": screen}) + ";\n" + line
                  + "\nconsole.log(JSON.stringify(tfw(" + json.dumps(P) + ")));\n")
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                         encoding="utf-8") as f:
            f.write(script); path = f.name
        self.addCleanup(os.unlink, path)
        out = subprocess.run([node, path], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def _py(self, mode):
        with F.use_mode(mode):
            return {"t": F.TECH_W, "f": F.FUND_W}

    def test_matches_python_for_every_mode(self):
        for mode in F.PARAMS:
            want = self._py(mode)
            got = self._tfw("mtf", {"mode": mode})       # 画面はわざと別モード
            self.assertAlmostEqual(got["t"], want["t"], places=6,
                                   msg=f"{mode}: テク配分が Python と違う")
            self.assertAlmostEqual(got["f"], want["f"], places=6,
                                   msg=f"{mode}: ファンダ配分が Python と違う")

    def test_the_screen_mode_does_not_leak_in(self):
        """画面をどのモードにしても、渡したモードの配分が返ること。"""
        for screen in ("scalp", "day", "swing", "mtf"):
            self.assertEqual(self._tfw(screen, {"mode": "swing"}),
                             {"t": 0.45, "f": 0.55}, f"画面{screen}で漏れている")
            self.assertEqual(self._tfw(screen, {"mode": "mtf"}),
                             {"t": 0.85, "f": 0.15}, f"画面{screen}で漏れている")

    def test_falls_back_to_the_screen_when_no_mode_is_given(self):
        """モードを渡さない古い呼び出しは従来どおり画面のモードで動く。"""
        self.assertEqual(self._tfw("swing", None), {"t": 0.45, "f": 0.55})
        self.assertEqual(self._tfw("day", None), {"t": 0.85, "f": 0.15})

    def test_a_swing_buy_can_never_reach_the_cut_threshold(self):
        """スイングの買いはスコア-0.175より下に行けない＝あの-0.30は別配分だった。

        これは不具合ではなく配分の帰結だが、ここが崩れると
        「スイングで損切り推奨が出た＝配分が壊れている」と判断できなくなる。"""
        w = self._py("swing")
        fund_bias = 0.5                      # index.html の FUND_BIAS[USD_JPY]
        floor = w["t"] * -1 + w["f"] * fund_bias
        self.assertAlmostEqual(floor, -0.175, places=6)
        self.assertGreater(floor, -0.25, "スイング買いが損切りのしきい値に届いている")

    def test_every_mode_entry_carries_its_name(self):
        """JS_PARAMS の各モードが自分の名前を持つこと（tfw の判断材料）。"""
        with open(os.path.join(ROOT, "index.html"), encoding="utf-8") as f:
            src = f.read()
        i = src.index("const JS_PARAMS={")
        chunk = src[i:src.index("function holdTxt", i)]
        for mode in F.PARAMS:
            self.assertIn(mode + ":{mode:'" + mode + "'", chunk,
                          f"{mode} に mode 名が無い")


class ReachableThresholdTest(unittest.TestCase):
    """しきい値が、その値の到達範囲の外に置かれていないこと。

    スコアは total = TECH_W*tech + FUND_W*fund で、tech は [-1,1]、
    fund は FUND_BIAS の固定値。つまり到達範囲は
      [TECH_W*(-1)+FUND_W*fund,  TECH_W*(+1)+FUND_W*fund]
    に限られる。この外にしきい値を置くと、その分岐は一度も通らない。

    実際に2件あった（どちらもスイング・4通貨すべて）。
      売りシグナル  total<=-0.45 が必要だが下限は -0.175〜-0.230
        → 1年386件すべて買い。売りは構造上1件も出せなかった。
      買いの損切り  total<=-0.25 が必要だが下限は同上
        → 「入った根拠が消えた」の損切り推奨が出ない。
    黙って死んだ分岐になるので、ここで数値として固定する。
    """

    ADV_OPP = 0.25          # fx_signal.py / index.html で共有

    def _range(self, mode, sym):
        with F.use_mode(mode):
            t, f = F.TECH_W, F.FUND_W
        fb = F.FUND_BIAS.get(sym, 0.0)
        return (t * -1 + f * fb, t * 1 + f * fb)

    def test_entry_thresholds_are_reachable_in_both_directions(self):
        dead = []
        for mode in F.PARAMS:
            if F.PARAMS[mode].get("rule") == "mtf_pullback":
                continue                      # mtfはRSIで判定するので th を使わない
            th = F.PARAMS[mode]["th"]
            for sym in F.SYMBOLS:
                lo, hi = self._range(mode, sym)
                if hi < th:
                    dead.append(f"{mode}/{sym} 買い(>= {th})")
                if lo > -th:
                    dead.append(f"{mode}/{sym} 売り(<= -{th})")
        # スイングの売りは現状すべて到達不能。直したらこの期待値を更新すること。
        self.assertEqual(
            sorted(dead),
            sorted(f"swing/{s} 売り(<= -0.45)" for s in F.SYMBOLS),
            "到達できないエントリー判定が増減した")

    def test_the_cut_threshold_is_reachable(self):
        dead = []
        for mode in F.PARAMS:
            for sym in F.SYMBOLS:
                lo, hi = self._range(mode, sym)
                if lo > -self.ADV_OPP:
                    dead.append(f"{mode}/{sym} 買いの損切り")
                if -hi > -self.ADV_OPP:
                    dead.append(f"{mode}/{sym} 売りの損切り")
        self.assertEqual(
            sorted(dead),
            sorted(f"swing/{s} 買いの損切り" for s in F.SYMBOLS),
            "到達できない損切り判定が増減した")

    def test_the_measured_swing_trades_are_all_buys(self):
        """実測でも裏が取れていること（1年386件すべて買い）。

        backtest.json が無い環境では飛ばす。"""
        path = os.path.join(ROOT, "data", "backtest.json")
        if not os.path.exists(path):
            self.skipTest("backtest.json が無い")
        with open(path, encoding="utf-8") as f:
            sw = (json.load(f).get("modes") or {}).get("swing") or {}
        side = ((sw.get("fast") or {}).get("side")) or {}
        if not side:
            self.skipTest("side の集計がまだ無い")
        self.assertNotIn("売り", side, "売りが出た＝到達範囲が変わっている")


class CanSignalTest(RunTestCase):
    """出せない向きを「優勢」とだけ出さないこと。

    スイングは構造上、売りシグナルを出せない（スコアの下限 -0.175〜-0.230、
    売りに必要なのは -0.45）。それを「売り優勢」とだけ表示すると、
    絶対に出ない合図をユーザーに待たせることになる。
    """

    def test_score_range_is_fixed_per_mode(self):
        F.MODE = "swing"; F.P = F.PARAMS["swing"]; F.TECH_W, F.FUND_W = 0.45, 0.55
        lo, hi = F.score_range("USD_JPY")
        self.assertAlmostEqual(lo, -0.175, places=6)
        self.assertAlmostEqual(hi, 0.725, places=6)
        F.MODE = "day"; F.P = F.PARAMS["day"]; F.TECH_W, F.FUND_W = 0.85, 0.15
        lo, hi = F.score_range("USD_JPY")
        self.assertAlmostEqual(lo, -0.775, places=6)
        self.assertAlmostEqual(hi, 0.925, places=6)

    def test_swing_cannot_sell_but_others_can(self):
        F.MODE = "swing"; F.P = F.PARAMS["swing"]; F.TECH_W, F.FUND_W = 0.45, 0.55
        self.assertTrue(F.can_signal(1))
        self.assertFalse(F.can_signal(-1), "スイングで売りが出せることになっている")
        for m in ("scalp", "day"):
            F.MODE = m; F.P = F.PARAMS[m]; F.TECH_W, F.FUND_W = 0.85, 0.15
            self.assertTrue(F.can_signal(1), m)
            self.assertTrue(F.can_signal(-1), m)

    def test_mtf_is_always_allowed(self):
        """mtfはRSIで判定するので th を使わない＝範囲の制約を受けない。"""
        F.MODE = "mtf"; F.P = F.PARAMS["mtf"]; F.TECH_W, F.FUND_W = 0.85, 0.15
        self.assertTrue(F.can_signal(1))
        self.assertTrue(F.can_signal(-1))

    def test_the_card_says_so_instead_of_just_sell_bias(self):
        F.MODE = "swing"; F.P = F.PARAMS["swing"]; F.TECH_W, F.FUND_W = 0.45, 0.55
        self.assertIn("買いしか出せません", F.pair_bias(-0.10, 40.0, 0))
        self.assertEqual(F.pair_bias(0.30, 60.0, 0), "買い優勢")
        F.MODE = "day"; F.P = F.PARAMS["day"]; F.TECH_W, F.FUND_W = 0.85, 0.15
        self.assertEqual(F.pair_bias(-0.10, 40.0, 0), "売り優勢")

    def test_the_dashboard_agrees(self):
        """画面側 canSignalJs() と Python can_signal() が一致すること。"""
        node = shutil.which("node") or shutil.which("nodejs")
        if not node:
            self.skipTest("node が無い")
        with open(os.path.join(ROOT, "index.html"), encoding="utf-8") as f:
            src = f.read()
        i = src.index("function scoreRangeJs(")
        end = src.index("\n}", src.index("function canSignalJs(")) + 2
        tfw = src[src.index("function tfw(P){"):src.index("\n", src.index("function tfw(P){"))]
        head = ("const SYMS=" + json.dumps(list(F.SYMBOLS)) + ";\n"
                "const FUND_BIAS=" + json.dumps(F.FUND_BIAS) + ";\nlet S={mode:'day'};\n")
        cases = [{"mode": m, "th": F.PARAMS[m]["th"],
                  **({"rule": "mtf_pullback"} if F.PARAMS[m].get("rule") else {})}
                 for m in F.PARAMS]
        script = (head + tfw + "\n" + src[i:end]
                  + "\nconsole.log(JSON.stringify(" + json.dumps(cases)
                  + ".map(P=>[canSignalJs(1,P),canSignalJs(-1,P)])));\n")
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                         encoding="utf-8") as f:
            f.write(script); path = f.name
        self.addCleanup(os.unlink, path)
        out = subprocess.run([node, path], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        js = json.loads(out.stdout)
        for mode, (jb, js_) in zip(F.PARAMS, js):
            with F.use_mode(mode):
                self.assertEqual(F.can_signal(1), jb, f"{mode} 買い")
                self.assertEqual(F.can_signal(-1), js_, f"{mode} 売り")


class LiveModeRaceTest(unittest.TestCase):
    """⚡ライブが、途中でモードが変わっても混ざらないこと。

    liveSignals() は足の取得で await する。以前は P（足と係数）だけを先に
    確定させ、判定ブロック・通知文のラベル・総合判定の重み・統計の参照は
    await の【後】に S.mode を読んでいた。その間に S.mode が変わると、
    デイの係数で計算した合図に mtf のラベルが付き、
    「画面と運用が違うなら送らない」ガードも新しい S.mode で評価されて素通りする。

    実際に起きた例: 2026-09-11 20:46 EUR/JPY
      「⚡ライブ EUR_JPY 売り（上位足フォロー）TP+25.5p / SL-15.9p」
      mtfのSLは15分ATR×1.95、デイは×1.3。同じ足・同じATRなので比は必ず
      1.50 になるはずが、同時刻のデイのカード(SL15.5p)との比は 1.026 だった。
    """

    def _src(self):
        with open(os.path.join(ROOT, "index.html"), encoding="utf-8") as f:
            return f.read()

    def _live_body(self):
        """liveSignals() の本体を波括弧の対応で切り出す。"""
        src = self._src()
        i = src.index("async function liveSignals(){")
        j = src.index("{", i)
        depth, k = 0, j
        while k < len(src):
            if src[k] == "{":
                depth += 1
            elif src[k] == "}":
                depth -= 1
                if depth == 0:
                    return src[j:k + 1]
            k += 1
        self.fail("liveSignals の終わりが見つからない")

    def test_the_mode_is_captured_once(self):
        body = self._live_body()
        self.assertIn("const MODE0=S.mode;", body, "モードを1回で確定させていない")

    def test_no_bare_screen_mode_after_the_await(self):
        """await より後で S.mode を直に読まないこと（読み直すと混ざる）。

        許されるのは「変わっていたら捨てる」ガードだけ。"""
        body = self._live_body()
        after = body[body.index("await Promise.all("):]
        reads = [ln.strip() for ln in after.split("\n") if "S.mode" in ln]
        self.assertEqual(reads, ["if(S.mode!==MODE0)return;"],
                         f"await の後で S.mode を読んでいる: {reads}")

    def test_the_cycle_is_dropped_when_the_mode_changed(self):
        body = self._live_body()
        self.assertIn("if(S.mode!==MODE0)return;", body,
                      "モードが変わった回を捨てていない")

    def test_the_label_and_the_block_use_the_captured_mode(self):
        body = self._live_body()
        self.assertIn("MODE_LABEL[MODE0]", body, "ラベルが画面のモードのまま")
        self.assertIn("liveNotifyBlock(MODE0)", body, "ガードが画面のモードのまま")
        self.assertIn("STATS[MODE0+sym]", body, "統計の参照が画面のモードのまま")

    def test_block_uses_the_given_mode(self):
        node = shutil.which("node") or shutil.which("nodejs")
        if not node:
            self.skipTest("node が無い")
        src = self._src()
        i = src.index("function liveNotifyBlock(mode){")
        end = src.index("\n}", i) + 2
        head = ("const MODES=" + json.dumps(list(F.PARAMS)) + ";\n"
                "const MODE_LABEL={scalp:'スキャル',day:'デイ',swing:'スイング',mtf:'上位足フォロー'};\n"
                "let S={mode:'mtf'},MODEJSON='mtf';\n")
        script = (head + src[i:end]
                  + "\nconsole.log(JSON.stringify({"
                  "same:liveNotifyBlock('mtf')," 
                  "diff:liveNotifyBlock('day'),"
                  "screen:liveNotifyBlock()}));\n")
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                         encoding="utf-8") as f:
            f.write(script); path = f.name
        self.addCleanup(os.unlink, path)
        out = subprocess.run([node, path], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        r = json.loads(out.stdout)
        self.assertIsNone(r["same"], "運用と同じモードなのに止めている")
        self.assertIsNotNone(r["diff"], "計算したモードが運用と違うのに素通りした")
        self.assertIn("デイ", r["diff"])
        self.assertIsNone(r["screen"], "モード省略時は従来どおり画面で判定すること")

    def test_mtf_has_explicit_confluence_weights(self):
        """mtf が総合判定の配点を黙って swing に落ちないこと。

        CONFLUENCE_W_BY_MODE に mtf が無く、undefined で swing に
        フォールバックしていた。合計はどちらも12なので画面からは気づけない。"""
        src = self._src()
        self.assertIn("CONFLUENCE_W_FALLBACK={mtf:'swing'}", src,
                      "mtf の配点が暗黙のフォールバックのまま")
        self.assertIn("function confW(mode){", src, "confW がモードを取れない")
        self.assertIn("function confluence(pair,st,mode){", src,
                      "confluence がモードを取れない")


class ForwardRecordModeTest(unittest.TestCase):
    """前向き検証の記録が、計算に使ったモードで残ること。

    fwdRecord は S.mode を読み直していたため、足の取得中にモードが変わった
    回に、デイの係数で作った合図を mtf として記録してしまった。
    実例 2026-09-11 20:46 EUR/JPY 売り: SL幅15.9pips はデイの係数
    （15分ATR×1.3）なのに mode:'mtf' で記録された。mtfなら×1.95で
    約23pipsになるはずで、同じ足・同じATRからは出ない値。
    運用mtf・画面デイの場面なので、本来は記録されない回だった。
    """

    def _src(self):
        with open(os.path.join(ROOT, "index.html"), encoding="utf-8") as f:
            return f.read()

    def test_the_caller_passes_the_computed_mode(self):
        src = self._src()
        self.assertIn("function fwdRecord(pair,mode){", src,
                      "fwdRecord がモードを受け取らない")
        self.assertIn("fwdRecord(pair,MODE0)", src,
                      "計算に使ったモードを渡していない")

    def test_the_record_does_not_reread_the_screen_mode(self):
        """fwdRecord の中で S.mode を直に使わないこと（受け取った m を使う）。"""
        src = self._src()
        i = src.index("function fwdRecord(pair,mode){")
        end = src.index("saveFwd(f);}catch(e){}}", i)
        body = src[i:end]
        # コメント中の記述は数えない（説明文にも S.mode が出てくる）
        code = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
        code = re.sub(r"//[^\n]*", "", code)
        # 既定値として一度だけ読むのは可
        self.assertEqual(code.count("S.mode"), 1,
                         f"fwdRecord が画面のモードを読み直している: "
                         f"{[l.strip() for l in code.split(chr(10)) if 'S.mode' in l]}")
        self.assertIn("const m=mode||S.mode;", body)
        self.assertIn("mode:m,", body, "記録するモードが受け取った値でない")

    def test_the_mislabelled_record_is_repaired_on_load(self):
        """取り違えたモードを読み込み時に直すこと（捨てない）。

        中身はデイとして完全に整合していて、総合判定の配点も day と swing で
        同一（合計12）だった。壊れていたのは mode の札だけなので、
        捨てると実際にあった検証が1件失われる。"""
        src = self._src()
        self.assertIn("FWD_FIX={'1789127207637|EUR_JPY|short':'day'}", src,
                      "取り違えた記録の直し方が登録されていない")
        i = src.index("function loadFwd(){")
        self.assertIn("FWD_FIX[", src[i:i + 500], "loadFwd が直していない")
        self.assertNotIn("FWD_DROP", src, "捨てる実装が残っている")

    def test_the_repo_copy_has_it_with_the_right_mode(self):
        path = os.path.join(ROOT, "data", "forward_log.json")
        if not os.path.exists(path):
            self.skipTest("forward_log.json が無い")
        with open(path, encoding="utf-8") as f:
            ent = (json.load(f) or {}).get("entries") or []
        got = [x for x in ent if x.get("ts") == 1789127207637]
        self.assertEqual(len(got), 1, "直した記録がリポジトリ側に無い")
        self.assertEqual(got[0]["mode"], "day", "モードが直っていない")
        # SL幅がデイの係数(15分ATR×1.3)と辻褄が合うこと
        sl_pips = abs(got[0]["entry"] - got[0]["sl"]) / 0.01
        self.assertAlmostEqual(sl_pips, 15.9, places=1)

    def test_every_remaining_record_matches_its_mode_stop_width(self):
        """残っている記録のSL幅が、そのモードの係数と辻褄が合うこと。

        同じ通貨・近い時刻の記録どうしでATRは大きく変わらない。
        mtf(×1.95)とデイ(×1.3)ではSL幅が1.5倍違うので、混ざっていれば
        ここで気づける。"""
        path = os.path.join(ROOT, "data", "forward_log.json")
        if not os.path.exists(path):
            self.skipTest("forward_log.json が無い")
        with open(path, encoding="utf-8") as f:
            ent = (json.load(f) or {}).get("entries") or []
        slm = {"scalp": 1.0, "day": 1.3, "swing": 1.8, "mtf": 1.95}
        for x in ent:
            m = x.get("mode")
            if m not in slm or x.get("entry") is None or x.get("sl") is None:
                continue
            atr = abs(x["entry"] - x["sl"]) / slm[m]
            # 円ペアの15分/1時間ATRが取り得るおおよその幅。桁違いを拾うための粗い網。
            self.assertTrue(0.01 <= atr <= 1.0,
                            f"{x['sym']} {m}: SL幅から逆算したATR {atr:.4f} が現実的でない")


class EdgeNeedTest(unittest.TestCase):
    """学習補正が出ない理由を、画面で区別できること。

    「全く反映されない」のが、条件が厳しいからなのか、勝率が基準と
    変わらないからなのかが分からなかった。実データ427件では
    15区分すべてが「偶然の範囲」で、補正は0だった。
    今の勝率のまま件数だけ増えた場合に補正が出るまでの件数を出す。
    """

    def _run(self, cases):
        node = shutil.which("node") or shutil.which("nodejs")
        if not node:
            self.skipTest("node が無い")
        with open(os.path.join(ROOT, "tools.js"), encoding="utf-8") as f:
            src = f.read()
        wil = src[src.index("function wilsonCI("):src.index("function _eStat(")]
        i = src.index("  var needFor=function(st){")
        end = src.index("  var out=function(m){", i)
        script = ("var EDGE_MIN=30,EDGE_Z=1.96;\n" + wil
                  + "var baseWr=39.6;\n" + src[i:end]
                  + "console.log(JSON.stringify(" + json.dumps(cases)
                  + ".map(function(c){return needFor(c);})));\n")
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                         encoding="utf-8") as f:
            f.write(script); path = f.name
        self.addCleanup(os.unlink, path)
        out = subprocess.run([node, path], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_a_rate_equal_to_the_base_never_qualifies(self):
        """基準勝率と同じ区分は、件数を貯めても出ない（差が無いということ）。"""
        got = self._run([{"n": 194, "wr": 39.6}, {"n": 500, "wr": 39.4}])
        self.assertEqual(got, [None, None], "差が無いのに『あと◯件』と出している")

    def test_a_clear_difference_reports_a_reachable_count(self):
        """実データのUSD/JPY(147件46%)は、あと数十件で条件を満たす。"""
        got = self._run([{"n": 147, "wr": 46.0}])
        self.assertIsNotNone(got[0])
        self.assertGreater(got[0], 147, "今の件数で既に出ていることになっている")
        self.assertLess(got[0], 400, "到達が非現実的な件数になっている")

    def test_small_buckets_start_from_the_minimum(self):
        """30件未満の区分は、まず30件から数えること。"""
        got = self._run([{"n": 1, "wr": 0.0}])
        self.assertIsNotNone(got[0])
        self.assertGreaterEqual(got[0], 30)

    def test_the_column_exists(self):
        with open(os.path.join(ROOT, "tools.html"), encoding="utf-8") as f:
            html = f.read()
        self.assertIn("<th>あと</th>", html, "『あと』の列が無い")
        with open(os.path.join(ROOT, "tools.js"), encoding="utf-8") as f:
            js = f.read()
        self.assertIn("need:needFor(st)", js, "profile に need を載せていない")
        self.assertIn("colspan=5", js, "列を増やしたのに空表示の colspan が古い")

    def test_the_note_explains_the_multiple_comparison_risk(self):
        """条件を緩めたら何が起きるかを書いておくこと。

        区分15前後×5% ≒ 0.75区分は、差が無くても「差あり」と出る。"""
        with open(os.path.join(ROOT, "tools.js"), encoding="utf-8") as f:
            js = f.read()
        self.assertIn("平均0.75区分", js)
        self.assertIn("条件を緩めると", js)


class ForwardSubModeTest(unittest.TestCase):
    """参考通知に設定したモードの合図も前向き検証に記録すること。

    実際に起きたこと: 運用mtf・参考通知にスイングとデイを設定した状態で、
    スイングで🟢OKが3通貨出たのに1件も記録されなかった。
    記録の条件が「運用モードと画面モードが一致」だけだったため。
    参考通知を入れた今は、スイングもデイも実際に合図が飛ぶ＝入る対象なので、
    まさに入ろうとしている合図が検証から漏れていた。
    """

    def _src(self):
        with open(os.path.join(ROOT, "index.html"), encoding="utf-8") as f:
            return f.read()

    def _body(self):
        src = self._src()
        i = src.index("function fwdRecord(pair,mode){")
        return src[i:src.index("saveFwd(f);}catch(e){}}", i)]

    def test_the_operating_mode_is_still_recorded(self):
        body = self._body()
        self.assertIn("if(m!==MODEJSON){", body,
                      "運用モードが素通りする道が無い")

    def test_a_configured_sub_mode_is_recorded(self):
        body = self._body()
        self.assertIn("SUBNOTIFY", body, "参考通知の設定を見ていない")
        self.assertIn("if(!sub)return;", body,
                      "設定していないモードまで記録してしまう")

    def test_a_sub_mode_filter_is_applied(self):
        """参考通知に絞り込みがあれば、記録も同じ条件にそろえること。

        デイは ADX40以上に絞って通知している。そろえないと
        「通知しない場面」まで検証に混ざり、測る母集団がずれる。"""
        body = self._body()
        self.assertIn("subFilterOK(sub.filter,{adx:pair.adx})", body,
                      "参考通知の絞り込みを記録に適用していない")

    def test_open_records_are_limited_per_symbol_and_mode(self):
        """開いている記録は銘柄×モードで1件。モードが違えば塞がないこと。

        以前は銘柄だけで見ていたので、mtfのUSD/JPYが
        スイングのUSD/JPYを締め出していた。"""
        body = self._body()
        self.assertIn("(f[i].mode||m)===m", body,
                      "銘柄だけで締め出している")

    def test_the_record_remembers_where_it_came_from(self):
        src = self._src()
        self.assertIn("src:(sub?'sub':'main')", src, "出所を残していない")
        self.assertIn("subFilter:(sub?sub.filter:null)", src,
                      "どの絞り込みで記録したかを残していない")

    def test_it_still_fails_closed_without_the_operating_mode(self):
        body = self._body()
        self.assertIn("if(!MODEJSON||!MODES.includes(MODEJSON))return;", body,
                      "運用モードが分からない時に記録してしまう")

    def test_it_actually_records_the_right_cases(self):
        """ソースの見た目ではなく、実際に動かして通る/通らないを確かめる。

        運用mtf・参考通知[スイング(絞り込みなし), デイ(ADX40以上)] の設定で、
        どの合図が記録されるか。"""
        node = shutil.which("node") or shutil.which("nodejs")
        if not node:
            self.skipTest("node が無い")
        src = self._src()

        def grab(a, b):
            i = src.index(a)
            return src[i:src.index(b, i) + len(b)]

        harness = (
            "let store={};\n"
            "global.localStorage={getItem:k=>store[k]||null,setItem:(k,v)=>{store[k]=v}};\n"
            "global.MODES=['scalp','day','swing','mtf'];global.PS_=0.01;global.PRICE={};\n"
            "global.FWD_SPREAD_PIPS={USD_JPY:0.2,EUR_JPY:0.4,GBP_JPY:0.9,AUD_JPY:0.5};\n"
            "global.FWD_SPREAD_DEF=0.5;global.S={mode:'mtf'};global.STATS={};\n"
            "global.confluence=()=>({pct:71,rawPct:71,edge:0});global.edgeOn=()=>false;\n"
            "global.fwdCloudSaveLater=()=>{};\n"
            + grab("var FWD_FIX=", "}catch(e){return[];}}") + "\n"
            "function saveFwd(a){try{localStorage.setItem('fxnavi_forward_v1',"
            "JSON.stringify(a.slice(-300)));}catch(e){}}\n"
            + grab("function subFilterOK(fname,sc){", "  return true;\n}") + "\n"
            + grab("function fwdRecord(pair,mode){", "saveFwd(f);}catch(e){}}") + "\n"
            "global.MODEJSON='mtf';\n"
            "global.SUBNOTIFY=[{mode:'swing',filter:'all'},{mode:'day',filter:'adx40'}];\n"
            "const P=(sym,adx)=>({symbol:sym,signal:'買い',entry_ref:100.0,"
            "tp_pips:20,sl_pips:12,adx:adx,rsi:60});\n"
            "const cs=[['mtf','USD_JPY',30],['swing','AUD_JPY',25],['day','EUR_JPY',35],"
            "['day','EUR_JPY',45],['scalp','GBP_JPY',50],['swing','USD_JPY',25],"
            "['mtf','USD_JPY',30]];\n"
            "const out=[];\n"
            "for(const [m,sym,adx] of cs){\n"
            "  const b=JSON.parse(localStorage.getItem('fxnavi_forward_v1')||'[]').length;\n"
            "  fwdRecord(P(sym,adx),m);\n"
            "  const a=JSON.parse(localStorage.getItem('fxnavi_forward_v1')||'[]');\n"
            "  out.push(a.length>b?(a[a.length-1].mode+'/'+a[a.length-1].src):null);\n"
            "}\n"
            "console.log(JSON.stringify(out));\n")
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                         encoding="utf-8") as f:
            f.write(harness); path = f.name
        self.addCleanup(os.unlink, path)
        r = subprocess.run([node, path], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        got = json.loads(r.stdout)
        self.assertEqual(got, [
            "mtf/main",     # 運用モードは記録する
            "swing/sub",    # 参考通知のスイング（絞り込みなし）
            None,           # デイ ADX35 は参考通知の絞り込みで出ない
            "day/sub",      # デイ ADX45 は条件を満たす
            None,           # スキャルは参考通知に設定していない
            "swing/sub",    # 同じ銘柄でもモードが違えば別枠
            None,           # 同じ銘柄×同じモードは締め出す
        ], "記録される場面が想定と違う")

    def test_the_panel_text_matches_the_new_rule(self):
        src = self._src()
        self.assertNotIn("記録は「運用モードと画面モードが一致」", src,
                         "説明が古いルールのまま")
        self.assertIn("運用モード＋参考通知に設定したモード", src)


class ForwardBackfillTest(unittest.TestCase):
    """後から復元した記録が、正しい形で入っていて、それと分かること。

    2026-09-18 にスイングで🟢OKが3通貨出たが、当時の記録ルールが
    運用モードだけを対象にしていたため1件も残らなかった。
    画面の写しに時刻と建値が写っており、リポジトリには5分ごとの
    スナップショット履歴があるので、両方を突き合わせて復元した。
    生で取れた記録より弱い証拠なので、混ぜたまま黙らないこと。
    """

    EXPECT = {
        "AUD_JPY": {"entry": 112.336, "tp": 113.130, "sl": 111.895},
        "EUR_JPY": {"entry": 181.119, "tp": 182.249, "sl": 180.491},
        "GBP_JPY": {"entry": 210.844, "tp": 212.280, "sl": 210.046},
    }

    def _entries(self):
        path = os.path.join(ROOT, "data", "forward_log.json")
        if not os.path.exists(path):
            self.skipTest("forward_log.json が無い")
        with open(path, encoding="utf-8") as f:
            return (json.load(f) or {}).get("entries") or []

    def _backfilled(self):
        return [x for x in self._entries() if x.get("src") == "backfill"]

    def test_the_three_swing_signals_are_there(self):
        got = self._backfilled()
        self.assertEqual(len(got), 3, "復元した記録の数が違う")
        self.assertEqual(sorted(x["sym"] for x in got),
                         ["AUD_JPY", "EUR_JPY", "GBP_JPY"])
        for x in got:
            self.assertEqual(x["mode"], "swing")
            self.assertEqual(x["side"], "long")
            self.assertTrue(x["open"], "決着済で作ってはいけない（サーバが判定する）")

    def test_the_levels_match_the_screenshots(self):
        """建値とTP/SLが画面の写しどおりであること。"""
        for x in self._backfilled():
            want = self.EXPECT[x["sym"]]
            self.assertAlmostEqual(x["entry"], want["entry"], places=3, msg=x["sym"])
            self.assertAlmostEqual(x["tp"], want["tp"], places=3, msg=x["sym"])
            self.assertAlmostEqual(x["sl"], want["sl"], places=3, msg=x["sym"])

    def test_the_stop_width_matches_the_swing_multiplier(self):
        """SL幅がスイングの係数(1時間ATR×1.8)と辻褄が合うこと。

        別モードの値を取り違えて復元していないかの検算。"""
        for x in self._backfilled():
            atr = abs(x["entry"] - x["sl"]) / 1.8
            self.assertTrue(0.15 <= atr <= 0.8,
                            f"{x['sym']}: 逆算した1時間足ATR {atr:.4f} が現実的でない")
            self.assertAlmostEqual((x["tp"] - x["entry"]) / (x["entry"] - x["sl"]),
                                   1.8, places=2, msg=f"{x['sym']} のTP:SL比")

    def test_the_buy_fill_pays_the_spread(self):
        """買いの約定はask。建値より不利な側に入っていること。"""
        for x in self._backfilled():
            self.assertGreater(x["fill"], x["entry"], x["sym"])
            self.assertAlmostEqual(x["fill"] - x["entry"], x["sp"], places=5)

    def test_the_timestamps_are_on_the_right_day(self):
        import datetime as _dt
        jst = _dt.timezone(_dt.timedelta(hours=9))
        for x in self._backfilled():
            t = _dt.datetime.fromtimestamp(x["ts"] / 1000, jst)
            self.assertEqual(t.strftime("%Y-%m-%d"), "2026-09-18", x["sym"])

    def test_the_panel_says_how_many_were_backfilled(self):
        """後から復元した件数を必ず画面に出すこと。"""
        with open(os.path.join(ROOT, "index.html"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("後から復元した記録です", src)
        self.assertIn("x.src==='backfill'", src)
        self.assertIn("'(後追い'", src, "モード別の内訳で区別していない")


class ModeProvenanceTest(unittest.TestCase):
    """記録簿のモードが、建玉自身のものだと言えるかを区別すること。

    2026-09-11 より前は stamp_new_entries が MODE（運用モード）を
    そのまま書いていた。運用mtfのまま画面をデイに切り替えて入った取引も
    「mtf」と記録される。間違ったモードはモード不明より悪い。

    ただしTP/SL幅はモードごとの係数だけで決まり、入った瞬間の値が
    そのまま残っているので、そこからモードを割り出せる
    （engine/recover_modes.py）。割り出せたものは信用してよい。
    """

    def _entries(self):
        path = os.path.join(ROOT, "data", "entry_log.json")
        if not os.path.exists(path):
            self.skipTest("entry_log.json が無い")
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d["entries"] if isinstance(d, dict) else d

    def test_every_record_declares_where_its_mode_came_from(self):
        miss = [x.get("logged_at") for x in self._entries() if not x.get("mode_src")]
        self.assertEqual(miss, [], f"出所の無い記録がある: {miss[:5]}")

    def test_the_source_is_one_of_the_known_kinds(self):
        ok = {"position", "recovered", "operating"}
        bad = [(x.get("logged_at"), x.get("mode_src"))
               for x in self._entries() if x.get("mode_src") not in ok]
        self.assertEqual(bad, [], f"知らない出所がある: {bad[:5]}")

    def test_records_the_engine_stamped_itself_are_never_downgraded(self):
        """建玉ごとにモードを保存し始めた後の記録は position のままにすること。

        割り出しの方が確かなわけではないので、上書きしない。"""
        import datetime as _dt
        fix = _dt.datetime(2026, 9, 11, 12, 0)
        for x in self._entries():
            la = x.get("logged_at")
            if not la:
                continue
            t = _dt.datetime.strptime(la[:16], "%Y-%m-%d %H:%M")
            if t >= fix and x.get("mode_src") == "operating":
                self.fail(f"{la}: 建玉のモードを保存できる時期なのに運用モードのまま")

    def test_no_mtf_record_predates_the_mtf_mode(self):
        """mtfと判定された記録が、mtf実装より前に無いこと。

        mtf は 2026-09-02 14:53 JST が初出。それ以前に mtf の建玉は存在しない。"""
        import datetime as _dt
        born = _dt.datetime(2026, 9, 2, 14, 53)
        for x in self._entries():
            if x.get("mode") != "mtf" or x.get("mode_src") == "operating":
                continue
            t = _dt.datetime.strptime(x["logged_at"][:16], "%Y-%m-%d %H:%M")
            self.assertGreaterEqual(t, born, f"{x['logged_at']}: mtf実装前のmtf記録")

    def test_the_engine_stamps_the_provenance(self):
        with open(os.path.join(ROOT, "engine", "fx_signal.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn('"mode_src": "position",', src,
                      "エンジンが出所を残していない")

    def test_the_tools_screen_only_counts_a_known_source(self):
        with open(os.path.join(ROOT, "tools.js"), encoding="utf-8") as f:
            js = f.read()
        self.assertIn("MODE_SRC_OK", js,
                      "出所が当てにならない取引をモード別に混ぜている")
        self.assertIn("{position:1, recovered:1}", js)
        self.assertIn("x.modeSrc=e.mode_src", js, "出所を取引へ持ち回っていない")


class ModeFromLevelsTest(unittest.TestCase):
    """TP/SL幅からモードを割り出せること（engine/recover_modes.py）。

    sl_pips = slm * ATR(そのモードの足) / pip、tp_pips = sl_pips * tsr。
      scalp slm1.00 tsr1.5 ／ day slm1.30 tsr1.6
      swing slm1.80 tsr1.8 ／ mtf slm1.95 tsr1.6
    TP/SL比だけで scalp と swing は決まる。day と mtf は比が同じなので
    15分ATRとの比（1.30 か 1.95 か）で分ける。
    """

    def setUp(self):
        sys.path.insert(0, os.path.join(ROOT, "engine"))
        import recover_modes
        self.rm = recover_modes

    def test_the_ratio_alone_identifies_swing_and_scalp(self):
        self.assertEqual(self.rm.mode_from_levels(89.7, 49.8)[0], "swing")
        self.assertEqual(self.rm.mode_from_levels(9.0, 6.0)[0], "scalp")

    def test_day_and_mtf_need_the_atr(self):
        """比が1.6のものは、ATRが無ければ割り出さない（当てずっぽうにしない）。"""
        import datetime as _dt
        after = _dt.datetime(2026, 9, 8, 8, 30)
        got, why = self.rm.mode_from_levels(35.6, 22.2, after, None)
        self.assertIsNone(got, why)

    def test_the_atr_multiple_separates_day_from_mtf(self):
        import datetime as _dt
        after = _dt.datetime(2026, 9, 8, 8, 30)
        # 15分ATRが11.4pips なら day は14.8、mtf は22.2
        self.assertEqual(self.rm.mode_from_levels(23.7, 14.8, after, 11.4)[0], "day")
        self.assertEqual(self.rm.mode_from_levels(35.6, 22.2, after, 11.4)[0], "mtf")

    def test_nothing_is_mtf_before_the_mtf_mode_existed(self):
        import datetime as _dt
        before = _dt.datetime(2026, 8, 19, 4, 46)
        got, why = self.rm.mode_from_levels(35.6, 22.2, before, 11.4)
        self.assertEqual(got, "day", why)

    def test_a_shape_that_matches_nothing_is_left_alone(self):
        got, why = self.rm.mode_from_levels(30.0, 10.0)
        self.assertIsNone(got, why)

    def test_it_reproduces_every_known_good_label(self):
        """建玉自身のモードが分かっている記録を、幅だけで言い当てられること。

        これが崩れたら割り出しは信用できない。"""
        path = os.path.join(ROOT, "data", "entry_log.json")
        if not os.path.exists(path):
            self.skipTest("entry_log.json が無い")
        with open(path, encoding="utf-8") as f:
            rows = json.load(f)["entries"]
        known = [x for x in rows if x.get("mode_src") == "position"]
        self.assertGreaterEqual(len(known), 10, "検証の材料が足りない")
        import datetime as _dt
        bad = []
        for x in known:
            t = _dt.datetime.strptime(x["logged_at"], "%Y-%m-%d %H:%M:%S")
            # day と mtf を分ける必要があるものは、比だけで判定できる形に限る
            got, why = self.rm.mode_from_levels(x["tp_pips"], x["sl_pips"], t, None)
            if got is None and abs(x["tp_pips"] / x["sl_pips"] - 1.6) < 0.06:
                continue      # ATRが要る＝ここでは判定対象外
            if got != x["mode"]:
                bad.append((x["logged_at"], x["mode"], got, why))
        self.assertEqual(bad, [], f"幅から割り出せない既知の記録がある: {bad[:3]}")


class HoldTimeRegimeTest(unittest.TestCase):
    """想定保有時間を、いまの値幅で読み替えられるようにすること。

    2026-09-18 のスイング3件はすべて発注レベルどおりのOCO約定だったのに、
    想定保有時間よりかなり早く決着した。
      GBP/JPY 11:59→14:14 TP+89.7pips 到達（2h15m）
      EUR/JPY 14:19→15:43 SL-45.4pips 到達（1h24m）
      AUD/JPY 15:48→17:13 TP+72.1pips 到達（1h25m）
    TP/SLは【建てた時点のATR】で決まるが、想定保有時間は直近20日の中央値
    ＝普段の値幅が前提。この日は1時間足ATRが
      GBP/JPY 0.277(11:59) → 0.443(20:10)  約1.6倍
    と広がっており（画面にも レジーム クライマックス96〜99% と出ていた）、
    同じ距離を短い時間で到達した。不具合ではなく前提の違い。
    """

    def _src(self):
        with open(os.path.join(ROOT, "index.html"), encoding="utf-8") as f:
            return f.read()

    def test_the_regime_reports_how_wide_it_is_now(self):
        src = self._src()
        i = src.index("function atrRegime(o,P){")
        body = src[i:src.index("\n// 機能4", i)]
        self.assertIn("var rel=med?cur/med:1;", body,
                      "普段の値幅との比を出していない")
        self.assertIn("rel:Math.round(rel*100)/100", body,
                      "比を返していない")

    def test_the_card_rescales_the_expected_time(self):
        src = self._src()
        self.assertIn("st.tpMin/_rel", src, "利確までの時間を読み替えていない")
        self.assertIn("st.slMin/_rel", src, "損切りまでの時間を読み替えていない")
        self.assertIn("Math.abs(_rel-1)>=0.15", src,
                      "普段と変わらない時まで出している")

    def test_the_rescale_matches_the_real_day(self):
        """実例の倍率で読み替えると、実際の決着時間に近づくこと。

        距離÷速度なので、値幅がr倍なら時間は1/r倍。"""
        node = shutil.which("node") or shutil.which("nodejs")
        if not node:
            self.skipTest("node が無い")
        src = self._src()
        i = src.index("function atrRegime(o,P){")
        fn = src[i:src.index("\n// 機能4", i)]
        # 直近200本のうち、最後だけ普段の1.6倍にしたATR系列を作って渡す
        script = ("function atrSeries(){return SER;}\n"
                  "const SER=[];for(let k=0;k<200;k++)SER.push(0.277);"
                  "SER.push(0.443);\n" + fn
                  + "\nconsole.log(JSON.stringify(atrRegime([], {atr:14})));\n")
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                         encoding="utf-8") as f:
            f.write(script); path = f.name
        self.addCleanup(os.unlink, path)
        r = subprocess.run([node, path], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        got = json.loads(r.stdout)
        self.assertAlmostEqual(got["rel"], 1.6, places=1,
                               msg="普段の値幅との比が合わない")
        self.assertEqual(got["lab"], "クライマックス")

    def test_the_september_18_levels_were_oco_hits(self):
        """あの3件が発注レベルどおりの約定だったことを記録に残す。

        私は当初『手前で手動決済された』と述べたが誤りだった。
        記録簿の発注レベルと実約定は一致している。"""
        path = os.path.join(ROOT, "data", "entry_log.json")
        if not os.path.exists(path):
            self.skipTest("entry_log.json が無い")
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        ent = d["entries"] if isinstance(d, dict) else d
        want = {  # 建値: (tp_pips, sl_pips, 実際の決済pips, どちらに当たったか)
            209.318: (89.7, 49.8, 89.7, "tp"),
            180.610: (81.7, 45.4, -45.5, "sl"),
            111.668: (72.1, 40.1, 72.1, "tp"),
        }
        for entry, (tp, sl, real, side) in want.items():
            rec = [x for x in ent if abs((x.get("entry") or 0) - entry) < 0.001]
            self.assertTrue(rec, f"建値{entry}の記録が無い")
            e = rec[0]
            self.assertAlmostEqual(e["tp_pips"], tp, places=1, msg=str(entry))
            self.assertAlmostEqual(e["sl_pips"], sl, places=1, msg=str(entry))
            hit = tp if side == "tp" else -sl
            self.assertAlmostEqual(real, hit, places=0,
                                   msg=f"建値{entry}: 発注レベルと実約定が一致しない")


class ModeSrcRelinkTest(unittest.TestCase):
    """モードの出所を、既に紐付け済みの取引にも付け直すこと。

    実際に起きたこと: 「当時の運用モード」の札をモード不明に回す修正を入れたのに、
    画面のモード別くらべは mtf 14件のまま変わらなかった。
    entryLogAttach が「まだ紐付いていない取引」だけを見ていたため、
    端末に既にある取引には modeSrc が入らず、判定が一度も働かなかった。
    """

    def _node(self, script):
        node = shutil.which("node") or shutil.which("nodejs")
        if not node:
            self.skipTest("node が無い")
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                         encoding="utf-8") as f:
            f.write(script); path = f.name
        self.addCleanup(os.unlink, path)
        out = subprocess.run([node, path], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def _src(self):
        with open(os.path.join(ROOT, "tools.js"), encoding="utf-8") as f:
            return f.read()

    def _attach(self, trades, log):
        """本物の entryLogAttach を、localStorage を差し替えて動かす。"""
        src = self._src()
        i = src.index("function entryLogAttach(){")
        body = src[i:src.index("\n/* CSV取込だけで", i)]
        script = ("var _S={};var localStorage={getItem:function(k){return _S[k]||null;},"
                  "setItem:function(k,v){_S[k]=String(v);}};\n"
                  "var ENTRYLOG_KEY='fxnavi_entrylog';\n"
                  + src[src.index("function loadTrades(){"):src.index("function saveTrades(t){")]
                  + "function saveTrades(t){localStorage.setItem('fxnavi_trades',JSON.stringify(t));}\n"
                  + "function _elLoad(){return JSON.parse(localStorage.getItem(ENTRYLOG_KEY)||'[]');}\n"
                  + body
                  + "\nlocalStorage.setItem('fxnavi_trades'," + json.dumps(json.dumps(trades)) + ");\n"
                  + "localStorage.setItem(ENTRYLOG_KEY," + json.dumps(json.dumps(log)) + ");\n"
                  + "entryLogAttach();\n"
                  + "console.log(JSON.stringify(loadTrades()));\n")
        return self._node(script)

    def _classify(self, trades):
        """本物の renderModeCompare の振り分けだけを動かす。"""
        src = self._src()
        i = src.index("  var by={}, unknown=[], stale=0;")
        block = src[i:src.index("  var rows='', any=false;", i)]
        script = ("var MODE_LABEL_T={scalp:'s',day:'d',swing:'w',mtf:'m'};\n"
                  + src[src.index("var MODE_SRC_OK"):src.index("\n", src.index("var MODE_SRC_OK"))] + "\n"
                  "var t=" + json.dumps(trades) + ";\n" + block
                  + "console.log(JSON.stringify({by:Object.keys(by).reduce("
                  "function(a,k){a[k]=by[k].length;return a;},{}),"
                  "unknown:unknown.length,stale:stale}));\n")
        return self._node(script)

    def test_an_already_linked_trade_gets_its_mode_source(self):
        """紐付け済み（elSrc あり・modeSrc なし）でも出所が入ること。"""
        trades = [{"pair": "USD/JPY", "entry": 147.123, "mode": "mtf",
                   "elSrc": "entrylog", "markSrc": "entrylog"}]
        log = [{"symbol": "USD_JPY", "entry": 147.123, "mode": "mtf",
                "mode_src": "operating", "logged_at": "2026-09-05T10:00"}]
        got = self._attach(trades, log)
        self.assertEqual(got[0].get("modeSrc"), "operating",
                         "紐付け済みだからと素通りして、出所が入っていない")

    def test_the_entry_log_overrides_a_snapshot_mode(self):
        """記録簿のモードは、スナップショット由来のモードより確か。"""
        trades = [{"pair": "EUR/JPY", "entry": 180.610, "mode": "day",
                   "modeSrc": "snap-operating", "elSrc": "entrylog"}]
        log = [{"symbol": "EUR_JPY", "entry": 180.610, "mode": "swing",
                "mode_src": "position", "logged_at": "2026-09-18T14:19"}]
        got = self._attach(trades, log)
        self.assertEqual(got[0]["mode"], "swing")
        self.assertEqual(got[0]["modeSrc"], "position")

    def test_attaching_twice_changes_nothing(self):
        """二度押しても結果が変わらないこと（押すたびに増減しては困る）。"""
        trades = [{"pair": "USD/JPY", "entry": 147.123, "mode": "mtf",
                   "elSrc": "entrylog"}]
        log = [{"symbol": "USD_JPY", "entry": 147.123, "mode": "mtf",
                "mode_src": "operating", "logged_at": "2026-09-05T10:00"}]
        one = self._attach(trades, log)
        two = self._attach(one, log)
        self.assertEqual(one, two)

    def test_only_a_confirmed_mode_is_counted(self):
        """出所が確認できたものだけを、モード別に数えること。

        position  = 建玉ごとにモードを保存したもの
        recovered = 記録されたTP/SL幅から割り出したもの
        それ以外（当時の画面モードの写し・出所不明）は数えない。"""
        got = self._classify([
            {"mode": "swing", "modeSrc": "position"},
            {"mode": "mtf", "modeSrc": "recovered"},
            {"mode": "day", "modeSrc": "operating"},
            {"mode": "day", "modeSrc": "snap-operating"},
            {"mode": "day"},                     # 出所不明の古い記録
            {},                                  # モードそのものが無い
        ])
        self.assertEqual(got["by"], {"swing": 1, "mtf": 1},
                         "確認できないモードを数えている")
        self.assertEqual(got["stale"], 3)
        self.assertEqual(got["unknown"], 4)

    def test_the_snapshot_path_records_its_source(self):
        """スナップショットからモードを埋める時も、出所を残すこと。"""
        src = self._src()
        i = src.index("function attachSnapScores(")
        body = src[i:src.index("function _elLoad(", i)]
        self.assertIn("x.modeSrc='snap-operating'", body,
                      "画面モードをそのまま建玉のモードとして残している")


class ConfirmedBarTest(unittest.TestCase):
    """上位足の判定に、形成中の足を混ぜないこと。

    実際に起きたこと: 運用mtfで10日近く合図が1件も出なかった。
    9/10〜9/19 の status.json 3,139サンプル（上位足が揃っていた分）を数えると、
    押し目/戻りのRSI条件を満たしたものは0件。RSIが必要な60に最も近づいた値は
    52.5〜55.5で、毎回あと5〜8足りない。原因はRSIではなく、揃っている状態が
    続かないことだった。
    htf_trend はAPIが返す最後の足＝【いま形成中】の足まで含めてEMAの傾きを
    見ていたため、現在値が少し動くだけで上位足の向きが反転していた。
    バックテスト(htf_aligned_series)は最初から確定足だけで判定しているので、
    ライブだけが検証していない別のルールを動かしていたことになる。
    """

    H = 3600 * 1000

    def test_the_forming_bar_is_dropped(self):
        now = 10 * self.H + 30 * 60000            # 10時半 → 10時台の足は形成中
        rows = {i * self.H: 100.0 for i in range(11)}
        got = F.confirmed_bars(rows, "1hour", now)
        self.assertEqual(got[-1], 9 * self.H, "形成中の足が残っている")
        self.assertEqual(len(got), 10)

    def test_a_just_closed_bar_is_kept(self):
        now = 10 * self.H                          # ちょうど10時＝9時台の足は確定
        rows = {i * self.H: 100.0 for i in range(10)}
        got = F.confirmed_bars(rows, "1hour", now)
        self.assertEqual(got[-1], 9 * self.H, "確定した足まで捨てている")

    def test_at_most_one_bar_is_dropped(self):
        """時計がずれても系列を空にしないこと。

        全部落とすと上位足が判定できず、黙って『レンジ』に倒れる
        ＝上位足フィルタが効かなくなる。"""
        rows = {i * self.H: 100.0 for i in range(10)}
        got = F.confirmed_bars(rows, "1hour", 0)   # 時計が大きく過去にずれた状況
        self.assertEqual(len(got), 9)

    def test_the_four_hour_bar_uses_its_own_length(self):
        dur = 4 * self.H
        rows = {i * dur: 100.0 for i in range(6)}
        now = 5 * dur + 60000                      # 最後の足は始まったばかり
        self.assertEqual(F.confirmed_bars(rows, "4hour", now)[-1], 4 * dur)

    def _trend(self, closes, extra=None):
        """htf_trend を、与えた終値列だけで動かす。

        足の時刻は【いまの時刻】を基準に並べる。過去の適当な時刻にすると
        最後の足まで「確定済み」に見えてしまい、何も確かめられない。
        extra は、いま形成中の足の現在値。"""
        H = self.H
        now = int(time.time() * 1000)
        cur = (now // H) * H                       # いま形成中の足の開始時刻
        n = len(closes)
        rows = {cur - (n - i) * H: c for i, c in enumerate(closes)}
        if extra is not None:
            rows[cur] = extra                      # 形成中の足
        self.addCleanup(setattr, F, "_htf_closes", F.__dict__["_htf_closes"])
        F._htf_closes = lambda sym, interval, keys: dict(rows)
        return F.htf_trend("USD_JPY", "1hour"), cur

    def test_the_forming_bar_cannot_flip_the_trend(self):
        """形成中の足が逆に動いても、上位足の向きが変わらないこと。

        ef[-1]>ef[-2] は現在値ひとつで反転する。ここが反転すると
        『上位足が揃っている』状態が消え、mtfの合図がその場で流れる。"""
        up = [100.0 + i * 0.5 for i in range(60)]        # はっきりした上昇
        base, _ = self._trend(up)
        self.assertEqual(base, 1, "上昇と判定できていない（前提が崩れた）")
        # 最後に、形成中の足として大きく下げた現在値を足す
        drop, _ = self._trend(up, extra=up[-1] - 6.0)
        self.assertEqual(drop, 1,
                         "形成中の足で上位足の向きが反転した（mtfの合図が消える原因）")

    def test_a_confirmed_reversal_still_flips_the_trend(self):
        """確定した足で本当に転換したら、ちゃんと向きは変わること。"""
        up = [100.0 + i * 0.5 for i in range(60)]
        down = up + [up[-1] - 3.0 * i for i in range(1, 12)]
        got, _ = self._trend(down, extra=down[-1])
        self.assertEqual(got, -1, "確定足の転換まで無視している")


class JsPythonParamsTest(unittest.TestCase):
    """画面(JS)とサーバー(Python)のモード設定が一致していること。

    同じ数字を2か所に書いている以上、片方だけ直すといつか必ずズレる。
    ズレると、画面が出したTP/SLとサーバーが出したTP/SLが食い違い、
    どちらの実績なのか誰にも分からなくなる。
    """

    KEYS = ("interval", "ema_f", "ema_s", "rsi", "adx", "atr", "th", "slm", "tsr")

    def _js(self):
        with open(os.path.join(ROOT, "index.html"), encoding="utf-8") as f:
            src = f.read()
        i = src.index("const JS_PARAMS={")
        body = src[i:src.index("};", i) + 2]
        out = {}
        for m in re.finditer(r"(\w+):\{mode:'(\w+)'(.*?)\}(?=,\n|\};)", body, re.S):
            name, fields = m.group(1), m.group(3)
            d = {}
            for k, v in re.findall(r"(\w+):(-?[\d.]+|'[^']*'|\[[^\]]*\])", fields):
                d[k] = v.strip("'")
            out[name] = d
        return out

    def test_every_mode_exists_on_both_sides(self):
        self.assertEqual(set(self._js()), set(F.PARAMS), "モードの顔ぶれが違う")

    def test_the_numbers_match(self):
        js = self._js()
        bad = []
        for mode, py in F.PARAMS.items():
            for k in self.KEYS:
                if k not in py:
                    continue
                want = py[k]
                got = js[mode].get(k)
                if got is None:
                    bad.append(f"{mode}.{k}: JS に無い")
                elif isinstance(want, str):
                    if got != want:
                        bad.append(f"{mode}.{k}: JS={got} / Python={want}")
                elif abs(float(got) - float(want)) > 1e-9:
                    bad.append(f"{mode}.{k}: JS={got} / Python={want}")
        self.assertEqual(bad, [], "画面とサーバーで設定が食い違っている: " + str(bad))

    def test_the_pullback_rule_is_marked_on_both_sides(self):
        js = self._js()
        for mode, py in F.PARAMS.items():
            self.assertEqual(js[mode].get("rule"), py.get("rule"),
                             f"{mode}: ルール名が食い違っている")


class SubNotifyGateTest(unittest.TestCase):
    """参考通知は、画面をどのモードで見ていても出ること。

    実際に起きたこと: 運用mtf・参考通知にスイングとデイを入れていたのに、
    画面をスイングにして確認している間は参考通知が1件も飛ばなかった。
    subLiveSignals が本体と同じ liveNotifyBlock()（画面と運用が一致していないと
    送らない）を通していたため。参考通知は別モードの合図を出す仕組みなので、
    このガードは「いちばん見ている時にいちばん止まる」という形で効いていた。
    """

    def _body(self):
        with open(os.path.join(ROOT, "index.html"), encoding="utf-8") as f:
            src = f.read()
        i = src.index("async function subLiveSignals(base){")
        return src[i:src.index("\nasync function liveSignals(){", i)]

    def test_it_does_not_gate_on_the_screen_mode(self):
        body = re.sub(r"/\*.*?\*/", "", self._body(), flags=re.S)   # 説明文は除く
        body = re.sub(r"//[^\n]*", "", body)
        self.assertNotIn("liveNotifyBlock", body,
                         "画面と運用の一致を要求している（別モードの合図なのに）")

    def test_it_still_needs_the_operating_mode(self):
        """運用モードが確定していない時は送らないこと（文面に載せるため）。"""
        self.assertIn("if(!MODEJSON||!MODES.includes(MODEJSON)) return;", self._body())

    def test_it_records_into_the_forward_test(self):
        """参考通知の合図も前向き検証に残すこと。実際に入る対象なので。"""
        b = self._body()
        self.assertIn("fwdRecord(pair,cfg.mode)", b, "記録していない")
        self.assertIn("cf.cls==='ok'", b, "本体と条件がそろっていない（🟢だけ）")

    def test_the_text_does_not_claim_it_is_unrecorded(self):
        self.assertNotIn("前向き検証には記録されません", self._body(),
                         "記録しているのに『記録されません』と書いている")

    def test_it_builds_the_same_materials_as_the_card(self):
        """総合判定の材料を揃えること。揃えないと参考通知だけ点が低く出る。"""
        b = self._body()
        for fn in ("dowStructure(oh)", "granville(oh,P,cfg.mode)", "LONGENV[sym]"):
            self.assertIn(fn, b, f"{fn} を作っていない")


class SlFallbackTest(unittest.TestCase):
    """SL幅の代用値を、固定値ではなく検証結果から出すこと。

    実際に起きたこと: デイのSL幅を 9.0pips 固定にしていたが、1年の実測は12.3、
    いまの値幅では25前後。SL幅で割るR倍数が2倍以上ずれ、実測の棒が
    検証の棒と比べものにならなくなっていた。
    バックテストは1件ごとに spread/SL幅 を cost_r として残しているので、
    スプレッドを割り戻せば通貨ごとの平均SL幅が出る。
    """

    def _run(self, bt, cases):
        node = shutil.which("node") or shutil.which("nodejs")
        if not node:
            self.skipTest("node が無い")
        with open(os.path.join(ROOT, "tools.js"), encoding="utf-8") as f:
            src = f.read()
        part = src[src.index("var SPREAD_PIPS_T"):src.index("function _tradeClusters")]
        script = (part + "BT_CACHE=" + json.dumps(bt) + ";\n"
                  + "console.log(JSON.stringify(" + json.dumps(cases)
                  + ".map(function(c){return slFallback(c[0],c[1]);})));\n")
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                         encoding="utf-8") as f:
            f.write(script); path = f.name
        self.addCleanup(os.unlink, path)
        out = subprocess.run([node, path], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    BT = {"modes": {"day": {"symbols": {
        "USD_JPY": {"policies": {"advice": {"cost_r": 0.018}}},
        "GBP_JPY": {"policies": {"advice": {"cost_r": 0.058}}}}}}}

    def test_it_inverts_the_cost_per_symbol(self):
        got = self._run(self.BT, [["day", "USD/JPY"], ["day", "GBP/JPY"]])
        self.assertAlmostEqual(got[0], 0.2 / 0.018, places=3)   # 約11.1pips
        self.assertAlmostEqual(got[1], 0.9 / 0.058, places=3)   # 約15.5pips

    def test_symbols_differ_enough_to_matter(self):
        """通貨をまとめて1つの値にしてはいけないこと（1.4倍違う）。"""
        got = self._run(self.BT, [["day", "USD/JPY"], ["day", "GBP/JPY"]])
        self.assertGreater(got[1] / got[0], 1.3)

    def test_it_falls_back_when_the_backtest_is_missing(self):
        got = self._run({}, [["day", "USD/JPY"], ["swing", "EUR/JPY"]])
        self.assertAlmostEqual(got[0], 12.3, places=3)
        self.assertAlmostEqual(got[1], 33.1, places=3)

    def test_the_constants_are_not_the_old_wrong_ones(self):
        with open(os.path.join(ROOT, "tools.js"), encoding="utf-8") as f:
            js = f.read()
        self.assertNotIn("{scalp:1.5, day:9.0", js, "古い固定値が残っている")


class RiskCapTest(unittest.TestCase):
    """合計リスク上限(riskCap)を、資金設定の保存で消さないこと。"""

    def test_set_risk_carries_it_over(self):
        with open(os.path.join(ROOT, "index.html"), encoding="utf-8") as f:
            src = f.read()
        i = src.index("function setRisk(){")
        body = src[i:src.index("function ", i + 10)]
        self.assertIn("riskCap:", body, "保存のたびに riskCap が消える")


class AdxBandScoreTest(unittest.TestCase):
    """総合判定のADX採点が、実測と逆を向いていないこと。

    もとは全モード共通で「30〜40が満点・25〜30と40〜50が半分・他は0点」。
    1年ぶんの実測ではその満点の帯がどのモードでも一番悪かった。
      デイ  適正(30〜40) -0.132 / 強(40〜50) +0.043 / 極端(50〜) +0.040
      mtf   適正(30〜40) -0.084（唯一のマイナス）/ 弱(20〜25) +0.150
      swing 強(40〜50) -0.127 / 弱め(25〜30) +0.436
    ただし帯ごとの95%区間はほとんど0をまたぐ。6帯×4モード=24通り見ているので
    符号だけで決めると偶然を拾う。区間が0を跨がず、かつ150件以上ある帯だけを
    ✓/✗にし、残りは△(半分)とする。
    """

    def _run(self, cases, table=None):
        node = shutil.which("node") or shutil.which("nodejs")
        if not node:
            self.skipTest("node が無い")
        with open(os.path.join(ROOT, "index.html"), encoding="utf-8") as f:
            src = f.read()
        i = src.index("const ADX_BANDS_JS=")
        body = src[i:src.index("/* backtest.json は大きいので", i)]
        pre = ("var localStorage={getItem:function(){return null;},setItem:function(){}};\n")
        post = ""
        if table is not None:
            post = "ADX_BAND_R=" + json.dumps(table, ensure_ascii=False) + ";\n"
        script = (pre + body + post + "console.log(JSON.stringify("
                  + json.dumps(cases) + ".map(function(c){return adxScore(c[0],c[1]);})));\n")
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                         encoding="utf-8") as f:
            f.write(script); path = f.name
        self.addCleanup(os.unlink, path)
        out = subprocess.run([node, path], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_a_clearly_losing_band_scores_zero(self):
        """区間がまるごとマイナスなら0点。デイの30〜40がこれ。"""
        self.assertEqual(self._run([[35, "day"]])[0], 0)

    def test_a_clearly_winning_band_scores_full(self):
        t = {"day": {"強(40〜50)": [500, 0.20, 0.05, 0.35]}}
        self.assertEqual(self._run([[45, "day"]], t)[0], 1)

    def test_an_interval_crossing_zero_is_half(self):
        """0をまたぐ＝分からない。断定しないこと。"""
        t = {"day": {"強(40〜50)": [500, 0.20, -0.05, 0.45]}}
        self.assertEqual(self._run([[45, "day"]], t)[0], 0.5)

    def test_too_few_samples_is_half(self):
        """件数が少ない帯は、区間が0を外れていても断定しない（多重比較）。"""
        t = {"swing": {"弱め(25〜30)": [76, 0.436, 0.147, 0.725]}}
        self.assertEqual(self._run([[27, "swing"]], t)[0], 0.5)

    def test_it_no_longer_rewards_the_worst_band(self):
        """実測で一番悪い帯に満点を付けないこと（これが元の壊れ方）。"""
        for mode in ("day", "mtf", "swing"):
            got = self._run([[35, mode]])[0]
            self.assertNotEqual(got, 1, f"{mode}: 適正(30〜40)に満点を付けている")

    def test_mtf_uses_its_own_measurements(self):
        """mtfは配点こそswingを借りるが、ADXの実測は自分のものを使うこと。"""
        t = {"mtf": {"無風(〜20)": [325, 0.30, 0.10, 0.50]},
             "swing": {"無風(〜20)": [325, -0.30, -0.50, -0.10]}}
        self.assertEqual(self._run([[15, "mtf"]], t)[0], 1)

    def test_an_unknown_adx_is_not_scored(self):
        self.assertIsNone(self._run([[None, "day"]])[0])

    def test_the_bands_match_the_engine(self):
        """帯の区切りをサーバーと同じにしておくこと。"""
        with open(os.path.join(ROOT, "index.html"), encoding="utf-8") as f:
            js = f.read()
        i = js.index("const ADX_BANDS_JS=")
        body = js[i:js.index(";", i)]
        for need, label in F.ADX_BANDS:
            self.assertIn(f"[{need},'{label}']", body.replace(" ", "").replace("\n", ""),
                          f"{label} の区切りが食い違っている")

    def test_the_old_hardcoded_rule_is_gone(self):
        with open(os.path.join(ROOT, "index.html"), encoding="utf-8") as f:
            js = f.read()
        self.assertNotIn("pair.adx>=30&&pair.adx<=40", js, "決め打ちの採点が残っている")


class SubNotifyNoDuplicateTest(unittest.TestCase):
    """参考通知をアプリからも送らないこと。

    fx_signal.py（5分ごと・サーバー側）が既に参考通知を送っている。
    アプリからも送ると同じ合図が2通届く。しかもサーバー側はアプリを
    開いていなくても動くので、通知はサーバーに任せた方が確実。
    アプリ側の仕事は、参考モードの合図を前向き検証に残すことだけにする。
    """

    def _body(self):
        with open(os.path.join(ROOT, "index.html"), encoding="utf-8") as f:
            src = f.read()
        i = src.index("async function subLiveSignals(base){")
        return src[i:src.index("\nasync function liveSignals(){", i)]

    def test_the_app_does_not_send_it(self):
        body = re.sub(r"/\*.*?\*/", "", self._body(), flags=re.S)
        body = re.sub(r"//[^\n]*", "", body)
        self.assertNotIn("notifyLive", body, "サーバーと二重に送っている")

    def test_the_server_still_sends_it(self):
        with open(os.path.join(ROOT, "engine", "fx_signal.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("🔎 参考：", src, "サーバー側の参考通知が消えている")

    def test_the_app_still_records_it(self):
        self.assertIn("fwdRecord(pair,cfg.mode)", self._body(),
                      "通知もしないし記録もしないなら、この処理は何もしていない")

    def test_it_is_shown_on_the_operating_screen(self):
        """参考モードの合図を、運用モードの画面にも出すこと。

        モードを切り替えないと他モードの様子が分からない、という状態をなくす。"""
        with open(os.path.join(ROOT, "index.html"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn('id="subsig"', src, "置き場所が無い")
        self.assertIn("function renderSubSignals()", src, "描画していない")
        i = src.index("function render(){")
        self.assertIn("renderSubSignals()", src[i:i + 400], "render から呼んでいない")

    def test_it_does_not_refetch_every_cycle(self):
        """本体と同じ45秒間隔で足を取り直さないこと。

        参考モードは2モード×4通貨＝8本ぶん取る。通知はサーバーが送っていて
        同じ合図は3分まとめるので、それより短く回しても通信が増えるだけ。"""
        b = self._body()
        self.assertIn("SUB_RUN_AT", b, "間隔の制御が無い")
        self.assertIn("Promise.all", b, "4通貨を直列で取っている")


class NotifyKeySecretTest(unittest.TestCase):
    """公開リポジトリに通知キーを書かないこと。

    このリポジトリは公開されている。ソースに書いた通知キーは誰でも読めるし、
    それがあれば Worker 経由で本人のLINEへ好きな文面を送れる。
    ?action=cron-test で GitHub Actions を叩くこともできる（GH_PATはWorker側）。
    端末ごとに⚙から入力し、localStorage に置く。
    ※過去に書いてあった値はコミット履歴に残り続けるので、
      Worker側の NOTIFY_KEY を作り直すまでは古い値が生きている。
    """

    FILES = ("index.html", "tools.html", "tools.js", "worker.js", "README.md")

    def test_no_literal_key_is_committed(self):
        bad = []
        for f in self.FILES:
            path = os.path.join(ROOT, f)
            if not os.path.exists(path):
                continue
            with open(path, encoding="utf-8") as fh:
                src = fh.read()
            for m in re.finditer(r"NOTIFY_KEY\s*[=:]\s*([\"'])(.*?)\1", src):
                if m.group(2):
                    bad.append(f"{f}: {m.group(2)[:8]}...")
            for m in re.finditer(r"const\s+NOTIFY_KEY\s*=\s*([\"'])(.*?)\1", src):
                if m.group(2):
                    bad.append(f"{f}: {m.group(2)[:8]}...")
        self.assertEqual(bad, [], f"ソースに通知キーが書かれている: {bad}")

    def test_the_key_comes_from_the_device(self):
        with open(os.path.join(ROOT, "index.html"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("function notifyKey()", src, "端末から読む口が無い")
        self.assertIn('id="s_notifykey"', src, "入力欄が無い")
        self.assertIn("notifyKey:", src, "保存していない")

    def test_it_does_not_send_without_a_key(self):
        """キーが無いまま送ろうとしないこと（Workerに弾かれるだけで理由が残らない）。"""
        with open(os.path.join(ROOT, "index.html"), encoding="utf-8") as f:
            src = f.read()
        i = src.index("async function notifyLive(text){")
        body = src[i:i + 900]
        self.assertIn("if(!_k)", body, "未設定でも送りに行っている")
        self.assertIn("通知キーが未設定", body, "理由を残していない")

    def test_the_worker_still_checks_it(self):
        with open(os.path.join(ROOT, "worker.js"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("env.NOTIFY_KEY", src, "Worker側の照合が消えている")


class NotifyKeyCheckTest(unittest.TestCase):
    """通知キーが本当に効いているかを、アプリから確かめられること。

    値を入れ替えたつもりで入れ替わっていない、という間違いは静かに起きる。
    - Cloudflare側だけ直して端末に入れ忘れた → ⚡ライブ通知が黙って止まる
    - 端末だけ直してCloudflareを直していない → 同上
    - どちらも「同じ値」のまま保存し直した → 何も変わっていないのに直した気になる
    確認にはWorkerへ実際に投げるが、文面は総合判定40%（50%未満）なので
    LINEには1通も届かない。
    """

    def _run(self, key, responses, old_sha_of=None):
        node = shutil.which("node") or shutil.which("nodejs")
        if not node:
            self.skipTest("node が無い")
        with open(os.path.join(ROOT, "tools.js"), encoding="utf-8") as f:
            src = f.read()
        i = src.index("var WORKER_PROBE =")
        body = src[i:src.index("async function workerCheck(){", i)]
        stub = ("""
var CALLS=[];
var RESP=""" + json.dumps(responses) + """;
function notifyKey(){ return """ + json.dumps(key) + """; }
global.fetch=async function(url,opt){
  var m=String(url).match(/[?&]key=([^&]*)/);
  var k=m?decodeURIComponent(m[1]):'';
  CALLS.push({key:k, body:JSON.parse(opt.body).text});
  var r=RESP[k===''?'nokey':'withkey'];
  return {status:r.status, json:async function(){ return r.body; }};
};
""")
        # 指紋の一致分岐を試す時だけ、比較対象をその場で差し替える
        swap = ""
        if old_sha_of is not None:
            swap = ("OLD_NOTIFY_KEY_SHA=require('crypto').createHash('sha256')"
                    ".update(" + json.dumps(old_sha_of) + ").digest('hex');\n")
        script = (stub + body + swap
                  + "keyCheckLines('https://w.example').then(function(o){"
                  "console.log(JSON.stringify({lines:o,calls:CALLS}));});\n")
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                         encoding="utf-8") as f:
            f.write(script); path = f.name
        self.addCleanup(os.unlink, path)
        out = subprocess.run([node, path], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    GOOD = {"nokey": {"status": 401, "body": {"error": "unauthorized"}},
            "withkey": {"status": 200, "body": {"ok": True, "skipped": "live-low-score(40%)"}}}

    def test_a_correct_setup_reports_all_green(self):
        got = self._run("8Kq2vR7xTmP4wZbN9sLdF6yHgJ3cA5eU", self.GOOD)
        self.assertTrue(all(l.startswith("✅") for l in got["lines"]),
                        "正しく設定してあるのに警告が出ている: " + str(got["lines"]))

    def test_it_never_sends_a_deliverable_message(self):
        """確認のたびにLINEへ届いてはいけない。文面は必ず50%未満であること。"""
        got = self._run("newkey", self.GOOD)
        for c in got["calls"]:
            self.assertIn("40%", c["body"], "50%以上の文面で確認している（LINEに届く）")
            self.assertTrue(c["body"].startswith("⚡ライブ"),
                            "ライブ以外の文面はWorkerのフィルタを通過して届いてしまう")

    def test_a_missing_key_on_the_worker_is_reported(self):
        """キー無しで通ってしまう＝誰でも送れる状態を見つけること。"""
        r = {"nokey": {"status": 200, "body": {"ok": True, "skipped": "live-low-score(40%)"}},
             "withkey": {"status": 200, "body": {"ok": True, "skipped": "live-low-score(40%)"}}}
        got = self._run("newkey", r)
        self.assertTrue(any("誰でも" in l for l in got["lines"]), str(got["lines"]))

    def test_a_mismatched_key_is_reported(self):
        r = {"nokey": {"status": 401, "body": {"error": "unauthorized"}},
             "withkey": {"status": 401, "body": {"error": "unauthorized"}}}
        got = self._run("wrong", r)
        self.assertTrue(any("一致していません" in l for l in got["lines"]), str(got["lines"]))

    def test_reusing_the_leaked_key_is_reported(self):
        """作り直したつもりで同じ値のままなら、はっきり言うこと。

        旧キーの平文はここにも書かない（履歴には残っているが、現在のツリーには
        戻さない）。代わりに、指紋が一致した時の分岐そのものを確かめる。"""
        with open(os.path.join(ROOT, "tools.js"), encoding="utf-8") as f:
            js = f.read()
        i = js.index("var OLD_NOTIFY_KEY_SHA = '")
        sha = js[i + len("var OLD_NOTIFY_KEY_SHA = '"):].split("'")[0]
        self.assertRegex(sha, r"^[0-9a-f]{64}$", "指紋の形が違う")
        got = self._run("__OLD__", self.GOOD, old_sha_of="__OLD__")
        self.assertTrue(any("公開されていた値のままです" in l for l in got["lines"]),
                        str(got["lines"]))

    def test_an_empty_key_is_reported(self):
        got = self._run("", self.GOOD)
        self.assertTrue(any("未設定" in l for l in got["lines"]), str(got["lines"]))
        self.assertEqual(got["calls"], [], "キーが無いのにWorkerへ投げている")

    def test_a_new_key_is_not_flagged(self):
        """作り直した値を『古いままだ』と言わないこと。"""
        got = self._run("8Kq2vR7xTmP4wZbN9sLdF6yHgJ3cA5eU", self.GOOD)
        self.assertFalse(any("公開されていた値のままです" in l for l in got["lines"]),
                         str(got["lines"]))


class NearMissTest(unittest.TestCase):
    """合図が出ない日に「どこまで近づいたか」を残すこと。

    実際に起きたこと: mtfが10日近く沈黙し、それが不具合なのか相場なのか
    区別が付かなかった。原因は上位足の判定に形成中の足が混ざっていたことで、
    相場のせいではなかったが、判断材料が無いので取り違えた。
    毎日「あと何ポイントだったか」が残っていれば、黙っていることが正常かを
    その場で判断できる。
    """

    def setUp(self):
        self.addCleanup(setattr, F, "MODE", F.MODE)
        self.addCleanup(setattr, F, "P", F.P)
        self._orig_near = F.NEAR_FILE
        self.addCleanup(setattr, F, "NEAR_FILE", self._orig_near)
        self.tmp = tempfile.mkdtemp()
        F.NEAR_FILE = os.path.join(self.tmp, "near_miss.json")

    def _mtf(self):
        F.MODE = "mtf"; F.P = F.PARAMS["mtf"]

    def _day(self):
        F.MODE = "day"; F.P = F.PARAMS["day"]

    def test_mtf_buy_counts_down_to_the_pullback(self):
        """上位足が上昇なら、RSIが40に下がるまでの不足分を出すこと。"""
        self._mtf()
        g = F.entry_gap(0.1, 48.6, 1)
        self.assertEqual(g["label"], "RSI")
        self.assertEqual(g["need"], 40)
        self.assertAlmostEqual(g["gap"], 8.6, places=1)

    def test_mtf_sell_counts_up_to_the_bounce(self):
        self._mtf()
        g = F.entry_gap(0.1, 52.5, -1)
        self.assertEqual(g["need"], 60)
        self.assertAlmostEqual(g["gap"], 7.5, places=1)

    def test_a_met_condition_is_not_positive(self):
        """条件を満たしていれば不足分は0以下になること。"""
        self._mtf()
        self.assertLessEqual(F.entry_gap(0.1, 38.0, 1)["gap"], 0)
        self.assertLessEqual(F.entry_gap(0.1, 61.0, -1)["gap"], 0)

    def test_a_range_higher_timeframe_has_no_condition(self):
        """上位足がレンジなら、そもそも合図が出ようがない＝Noneであること。

        ここを0扱いにすると『あと0で出る』と読めてしまう。"""
        self._mtf()
        self.assertIsNone(F.entry_gap(0.1, 50.0, 0))
        self.assertIsNone(F.entry_gap(0.1, 50.0, None))

    def test_score_modes_count_down_to_the_threshold(self):
        self._day()
        g = F.entry_gap(-0.31, 50.0, 0)
        self.assertEqual(g["label"], "スコア")
        self.assertAlmostEqual(g["need"], 0.40, places=2)
        self.assertAlmostEqual(g["gap"], 0.09, places=3)   # 売り方向でも不足分は同じ

    def test_it_keeps_the_closest_of_the_day(self):
        self._mtf()
        F.update_near_miss({"USD_JPY": F.entry_gap(0, 55.0, 1)}, set())
        F.update_near_miss({"USD_JPY": F.entry_gap(0, 40.8, 1)}, set())
        F.update_near_miss({"USD_JPY": F.entry_gap(0, 58.0, 1)}, set())
        d = json.load(open(F.NEAR_FILE, encoding="utf-8"))
        self.assertAlmostEqual(d["pairs"]["USD_JPY"]["best"], 0.8, places=1)
        self.assertAlmostEqual(d["pairs"]["USD_JPY"]["now"], 40.8, places=1)

    def test_it_counts_the_signals_that_did_fire(self):
        self._mtf()
        F.update_near_miss({"USD_JPY": F.entry_gap(0, 38.0, 1)}, {"USD_JPY"})
        F.update_near_miss({"USD_JPY": F.entry_gap(0, 37.0, 1)}, {"USD_JPY"})
        d = json.load(open(F.NEAR_FILE, encoding="utf-8"))
        self.assertEqual(d["pairs"]["USD_JPY"]["fired"], 2)

    def test_it_starts_over_when_the_mode_changes(self):
        """別モードの最接近を混ぜないこと（足も条件も別物）。"""
        self._mtf()
        F.update_near_miss({"USD_JPY": F.entry_gap(0, 40.8, 1)}, set())
        self._day()
        F.update_near_miss({"USD_JPY": F.entry_gap(0.30, 50.0, 0)}, set())
        d = json.load(open(F.NEAR_FILE, encoding="utf-8"))
        self.assertEqual(d["mode"], "day")
        self.assertEqual(d["pairs"]["USD_JPY"]["label"], "スコア")

    def test_the_card_shows_it(self):
        with open(os.path.join(ROOT, "index.html"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("function nearBox(p)", src, "画面に出していない")
        self.assertIn("${nearBox(p)}", src, "カードに差し込んでいない")

    def test_the_workflow_commits_it(self):
        with open(os.path.join(ROOT, ".github", "workflows", "fx-signal.yml"),
                  encoding="utf-8") as f:
            y = f.read()
        self.assertIn("data/near_miss.json", y, "保存しても push されない")


class StateFilePathTest(unittest.TestCase):
    """エンジンが書き出す状態ファイルを、テストが必ず逃がしていること。

    同じ事故を何度も起こしている。逃がし忘れると、テストを走らせただけで
    リポジトリの data/ に偽のデータが書かれ、そのままコミットされる
    （実際 near_miss.json が mode:"day" の架空データで commit された）。
    新しい状態ファイルを足した時に、ここが落ちて気づけるようにする。
    """

    def test_every_written_state_file_is_sandboxed(self):
        with open(os.path.join(ROOT, "engine", "fx_signal.py"), encoding="utf-8") as f:
            src = f.read()
        # data_path(...) で作られ、書き出しにも使われる定数を拾う
        written = set()
        for m in re.finditer(r"^([A-Z][A-Z0-9_]*)\s*=\s*data_path\(", src, re.M):
            name = m.group(1)
            # 読むだけのもの（backtest.json など）は対象外。書き出す形だけを拾う。
            if re.search(r"open\(\s*" + name + r"\s*,\s*[\"']w", src) or \
               re.search(r"\b" + name + r"\s*\+\s*[\"']\.tmp", src) or \
               re.search(r"write_json\(\s*" + name + r"\b", src):
                written.add(name)
        self.assertTrue(written, "書き出す状態ファイルを1つも見つけられていない")
        with open(os.path.join(ROOT, "tests", "test_fx_signal.py"), encoding="utf-8") as f:
            tsrc = f.read()
        i = tsrc.index("class RunTestCase")
        setup = tsrc[i:tsrc.index("\n    def write(", i)]
        missing = [n for n in sorted(written) if n not in setup]
        self.assertEqual(missing, [],
                         f"テストが逃がしていない状態ファイルがある: {missing}")


class NotifyTestSendTest(unittest.TestCase):
    """LINEまで実際に届くかを、1通だけ送って確かめられること。

    疎通確認(WORKER_PROBE)はWorkerの50%フィルタで必ず止まるので、
    「Workerが受け取った」ところまでしか分からない。そこから先、
    WorkerがLINEへ渡す部分は別の鍵(LINE_TOKEN)を使っていて、
    サーバー(GitHub Actions)の通知とは経路が違う。切れていても
    本物の合図が出るまで誰も気づけない。
    """

    def _body(self):
        with open(os.path.join(ROOT, "tools.js"), encoding="utf-8") as f:
            src = f.read()
        i = src.index("async function notifyTestSend(){")
        return src[i:src.index("\nasync function workerCheck(){", i)]

    def test_it_asks_before_sending(self):
        """勝手に送らないこと（人の電話が鳴る）。"""
        self.assertIn("confirm(", self._body(), "確認せずに送っている")

    def test_the_text_is_not_mistaken_for_a_signal(self):
        """本物の合図と見間違える文面にしないこと。"""
        b = self._body()
        self.assertIn("売買の合図ではありません", b)
        self.assertNotIn("'⚡ライブ", b, "ライブ通知の体裁にしている")

    def test_the_text_passes_the_worker_filter(self):
        """Workerの50%フィルタに引っかからないこと（引っかかると届かない）。

        worker.js の allowToLine を実際に動かして確かめる。"""
        node = shutil.which("node") or shutil.which("nodejs")
        if not node:
            self.skipTest("node が無い")
        with open(os.path.join(ROOT, "worker.js"), encoding="utf-8") as f:
            w = f.read()
        w = w[:w.index("export default")]
        text = "🧪 FX Navi 送信テスト\nこれは動作確認です。売買の合図ではありません。\n2026-09-21 14:05:00"
        script = (w + "console.log(JSON.stringify(allowToLine(" + json.dumps(text) + ")));\n")
        with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False,
                                         encoding="utf-8") as f:
            f.write(script); path = f.name
        self.addCleanup(os.unlink, path)
        out = subprocess.run([node, path], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertTrue(json.loads(out.stdout).get("ok"),
                        "テスト文面がWorkerに遮断される＝押しても永久に届かない")

    def test_a_worker_error_points_at_the_line_token(self):
        """届かない時に、どこを見ればよいかを書いてあること。"""
        b = self._body()
        self.assertIn("LINE_TOKEN", b)
        self.assertIn("別の鍵", b, "サーバー側の鍵と混同させない説明が無い")

    def test_the_button_exists(self):
        with open(os.path.join(ROOT, "tools.html"), encoding="utf-8") as f:
            h = f.read()
        self.assertIn("notifyTestSend()", h, "押す場所が無い")
        self.assertIn("実際に1通届きます", h, "届くことを書いていない")


class SubSignalStripTest(unittest.TestCase):
    """『参考モードの合図』が、いま出ているものだけを出すこと。

    実際に起きたこと: スイング画面では合図が無いのに、『参考モードの合図』には
    22:56に出たスイングGBP/JPYの買いが残ったままだった。
    合図が出た時に足すだけで、消える側の処理が無かったため。
    スイングの有効時間は3時間あるので、消えた合図が3時間表示され続けていた。
    出ていないものを出ていると見せるのは、出ないことより悪い。
    """

    def _body(self):
        with open(os.path.join(ROOT, "index.html"), encoding="utf-8") as f:
            src = f.read()
        i = src.index("async function subLiveSignals(base){")
        return src[i:src.index("\nasync function liveSignals(){", i)]

    def test_every_skip_path_removes_the_entry(self):
        """合図が出なくなる道すべてで、表示からも消すこと。

        道が1本でも漏れると、その条件の時だけ幽霊が残る。"""
        body = self._body()
        i = body.index("for(const {sym,oh} of datas){")
        loop = body[i:]
        conts = re.findall(r"(.{0,40})\bcontinue;", loop)
        self.assertTrue(conts, "ループの中に continue が見つからない（前提が変わった）")
        bad = [c.strip() for c in conts if "drop()" not in c]
        self.assertEqual(bad, [], f"消さずに抜けている道がある: {bad}")

    def test_it_records_when_the_signal_started(self):
        """いつから続いている合図かを持つこと（毎周更新すると起点が消える）。"""
        b = self._body()
        self.assertIn("since:", b)
        self.assertIn("was.side===sc.side", b, "向きが変わっても起点を引き継いでいる")

    def test_it_no_longer_throttles_the_refresh(self):
        """3分間まとめる処理を残さないこと。

        通知を送らなくなったので、まとめる理由が無い。残すと最後に確認した
        時刻が更新されず、古い合図と区別が付かなくなる。"""
        b = self._body()
        self.assertNotIn("180000", b, "3分のまとめが残っている")
        self.assertNotIn("saveNotified", b, "送らないのに通知履歴を書いている")

    def test_the_display_hides_unconfirmed_entries(self):
        """確認が途切れたものは出さないこと（裏に回っていた間は不明）。"""
        with open(os.path.join(ROOT, "index.html"), encoding="utf-8") as f:
            src = f.read()
        i = src.index("function renderSubSignals(){")
        r = src[i:src.index("\nfunction render(){", i)]
        self.assertIn("SUB_FRESH_MS", r, "古さで切っていない")
        self.assertNotIn("validMin", r, "合図の有効時間で残している（消えても残る）")

    def test_the_freshness_window_is_declared_before_use(self):
        """使う場所より前で宣言すること（この画面は TDZ で事故った前例がある）。"""
        with open(os.path.join(ROOT, "index.html"), encoding="utf-8") as f:
            src = f.read()
        self.assertLess(src.index("var SUB_FRESH_MS"),
                        src.index("if(now-v.ts>SUB_FRESH_MS)"))

    def test_the_wording_says_it_is_current(self):
        with open(os.path.join(ROOT, "index.html"), encoding="utf-8") as f:
            src = f.read()
        i = src.index("function renderSubSignals(){")
        r = src[i:src.index("\nfunction render(){", i)]
        self.assertIn("いま出ている", r, "いまの状態だと分かる見出しになっていない")
        self.assertIn("🔴見送りでも合図自体は出ています", r,
                      "マークと合図の有無を取り違える書き方になっている")


class SpreadBlowoutTest(unittest.TestCase):
    """スプレッドが開いただけで「損切り推奨」を出さないこと。

    実際に起きたこと（2026-09-22 05:51 AUD/JPY・SL幅8.2pipsの建玉）:
      05:41 bid 112.052 / ask 112.059（0.7pips）仲値 112.056
      05:51 bid 111.999 / ask 112.073（7.4pips）仲値 112.036  ← SL111.999に「到達」
    仲値は2.0pipsしか下げていないのに売値は5.3pips下げている。
    相場が下げたのではなく、ロールオーバーでスプレッドが10倍に開いただけ。
    買い建てのSLは売値で判定するので、こうなると勝手にSLへ届く。
    GMO側のOCOは約定しておらず、建玉は残ったままだった。

    1週間の実測では 6〜8時台と23時台に毎日起きている
    （AUD/JPY 通常0.7→最大7.4pips ／ GBP/JPY 通常0.9→最大14.8pips）。
    """

    TICK_WIDE = {"AUD_JPY": {"bid": 111.999, "ask": 112.073}}
    TICK_CALM = {"AUD_JPY": {"bid": 111.999, "ask": 112.006}}
    POS = {"id": "x", "symbol": "AUD_JPY", "side": "long", "entry": 112.081,
           "lot": 4000, "status": "open", "tp_pips": 13.1, "sl_pips": 8.2,
           "opened_at": "2026-09-22 04:38 JST"}

    def setUp(self):
        self.addCleanup(setattr, F, "MODE", F.MODE)
        self.addCleanup(setattr, F, "P", F.P)
        F.MODE = "mtf"; F.P = F.PARAMS["mtf"]
        self.addCleanup(setattr, F, "get_ohlc", F.__dict__["get_ohlc"])
        F.get_ohlc = lambda sym: []          # 足の高安による救済は使わない
        self.addCleanup(setattr, F, "mtf_view", F.__dict__["mtf_view"])
        F.mtf_view = lambda sym: {"aligned": 1, "label": "1h↑ / 4h↑"}
        self.addCleanup(setattr, F, "upcoming_news", F.__dict__["upcoming_news"])
        F.upcoming_news = lambda sym: None

    SC = {"atr": 0.0418, "score": -0.1, "rsi": 45, "adx": 20, "sl_pips": 8.2, "tp_pips": 13.1}

    def test_the_spread_state_is_measured(self):
        sp = F.spread_state("AUD_JPY", self.TICK_WIDE)
        self.assertAlmostEqual(sp["pips"], 7.4, places=1)
        self.assertTrue(sp["wide"], "普段の10倍でも『拡大中』と見ていない")
        sp2 = F.spread_state("AUD_JPY", self.TICK_CALM)
        self.assertFalse(sp2["wide"])

    def test_a_wide_spread_is_not_a_stop_loss(self):
        """仲値がまだSLの手前なら、損切り推奨にしないこと。"""
        adv = F.position_advice(self.POS, self.TICK_WIDE, self.SC)
        self.assertEqual(adv["level"], "watch", adv["reason"])
        self.assertIn("スプレッド", adv["label"])
        self.assertIn("仲値", adv["reason"])

    def test_it_does_not_notify_for_that(self):
        """通知が飛ぶのは take / cut と『利確検討』だけ。ここに混ぜないこと。"""
        adv = F.position_advice(self.POS, self.TICK_WIDE, self.SC)
        actionable = adv["level"] in ("take", "cut") or (
            adv["level"] == "watch" and "利確検討" in adv["label"])
        self.assertFalse(actionable, "スプレッド拡大で損切り通知が飛ぶ")

    def test_a_real_drop_is_still_a_stop_loss(self):
        """本当に相場が下げた時は、今までどおり損切り推奨を出すこと。"""
        adv = F.position_advice(self.POS, self.TICK_CALM, self.SC)
        self.assertEqual(adv["level"], "cut", adv["reason"])
        self.assertIn("損切り", adv["label"])

    def test_a_wide_spread_below_the_stop_is_still_a_stop_loss(self):
        """スプレッドが開いていても、仲値がSLを割っていれば本物の損切り。"""
        tick = {"AUD_JPY": {"bid": 111.950, "ask": 112.024}}   # 仲値111.987 < SL111.999
        adv = F.position_advice(self.POS, tick, self.SC)
        self.assertEqual(adv["level"], "cut", adv["reason"])

    def test_a_wide_spread_blocks_a_new_signal(self):
        """検証していない場面（スプレッドが1Rの15%超）では新規を出さないこと。"""
        sp = F.spread_state("AUD_JPY", self.TICK_WIDE)
        why = F.spread_blocks_entry(sp, 8.2)
        self.assertIsNotNone(why, "SL幅の90%をスプレッドが食うのに通している")
        self.assertIn("スプレッド", why)

    def test_a_normal_spread_does_not_block(self):
        sp = F.spread_state("AUD_JPY", self.TICK_CALM)
        self.assertIsNone(F.spread_blocks_entry(sp, 8.2))

    def test_a_tight_stop_is_blocked_before_a_wide_one(self):
        """同じスプレッドでも、SL幅が広ければ通ること（割合で見ている）。"""
        sp = F.spread_state("AUD_JPY", self.TICK_WIDE)
        self.assertIsNotNone(F.spread_blocks_entry(sp, 8.2))    # 90%
        self.assertIsNone(F.spread_blocks_entry(sp, 60.0))      # 12%

    def test_the_card_warns_when_the_spread_is_wide(self):
        """画面にも、開いていることをはっきり出すこと。"""
        with open(os.path.join(ROOT, "index.html"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("スプレッド拡大中", src, "画面に出していない")
        self.assertIn("${_sprBox}", src, "カードに差し込んでいない")
        self.assertIn("_spNow.wide", src, "サーバーの判定を使っていない")

    def test_the_card_cost_uses_the_stop_width(self):
        """コスト%の分母をSL幅(=1R)にそろえること。

        以前はTP幅で割っていたため、同じアプリの中でサーバーの cost_r と
        別の数字が並んでいた。"""
        with open(os.path.join(ROOT, "index.html"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("(_sprPips&&p.sl_pips)?(_sprPips/p.sl_pips*100)", src)
        self.assertNotIn("_spr/p.tp_pips*100", src, "TP幅で割る式が残っている")

    def test_the_cost_uses_the_live_spread(self):
        """カードのコスト表示を固定表で出さないこと。

        固定表(0.5pips)だと、実際7.4pips開いている場面でも2%と出る（実際は90%）。"""
        with open(os.path.join(ROOT, "engine", "fx_signal.py"), encoding="utf-8") as f:
            src = f.read()
        i = src.index('"spread": spw,')          # カードに載せている所だけを見る
        card = src[i - 700:i + 100]
        self.assertIn('spw["pips"]', card, "実スプレッドを使っていない")
        self.assertIn('"cost_r_base"', card, "固定表での値も残していない（比較できない）")


class CrossModeAdviceTest(unittest.TestCase):
    """建玉のモードと画面のモードが違っても、判定欄を黙って消さないこと。

    実際に起きたこと（2026-09-22 15:30）: デイで建てた USD/JPY が、
    デイ画面では「🟢 ホールド」と出るのに mtf画面では判定欄ごと消えていた。
    中身は「建玉モードの指標がまだ手元に無い」だけだったが、
    画面上は不具合と区別が付かない。
      ・同じモードの画面 … その場で計算できる（すぐ出る）
      ・違うモードの画面 … posModeLive が取った足か、サーバーの判定(5分ごと)が要る
    posModeLive は liveSignals の末尾でしか動かず、liveSignals には
    「30秒以内は再実行しない」「取得中にモードが変わったら丸ごと捨てる」という
    ガードがあるため、モード切替直後・建玉登録直後はどちらも間に合わなかった。
    """

    def _run(self, screen_mode, server_adv):
        node = shutil.which("node") or shutil.which("nodejs")
        if not node:
            self.skipTest("node が無い")
        with open(os.path.join(ROOT, "index.html"), encoding="utf-8") as f:
            src = f.read()

        def cut(a, b):
            i = src.index(a)
            return src[i:src.index(b, i)]

        pos = {"id": "p1", "symbol": "USD_JPY", "side": "long", "entry": 157.6414,
               "lot": 3000, "status": "open", "tp_pips": 13.3, "sl_pips": 8.3,
               "entry_mode": "day", "mode": "day"}
        srv = [{"id": "p1", "adv_level": "hold", "adv_label": "🟢 ホールド",
                "adv_reason": "入った根拠が続いている（スコア+0.45）"}] if server_adv else []
        head = ("""
var S={mode:%s, pairs:[{symbol:'USD_JPY',bid:157.638,ask:157.640,score:0.45,rsi:60,adx:25,atr:0.0637}],
       open_positions:%s};
var POS={positions:[%s]};
var PRICE={USD_JPY:{bid:157.638,ask:157.640}};
var PS=0.01, PS_=0.01;
var MODES=['scalp','day','swing','mtf'];
var MODE_LABEL={scalp:'スキャル',day:'デイ',swing:'スイング',mtf:'上位足フォロー'};
var ADV_OPP=0.25, ADV_SUPP=0.15, TRAIL_ATR=1.0, PROFIT_ATR=1.0, ADX_WEAK=20.0;
var POSMODE_SIG={};
var MTF_BY_SYM={};
function loadMfe(){return {};} function saveMfe(){}
function tpsl(p){return [p.entry+p.tp_pips*PS, p.entry-p.sl_pips*PS];}
function pairSig(sym){var p=(S.pairs||[])[0]; return {score:p.score,rsi:p.rsi,adx:p.adx,atr:p.atr};}
""" % (json.dumps(screen_mode), json.dumps(srv), json.dumps(pos)))
        body = (cut("const JS_PARAMS={", "function holdTxt")
                + cut("function computeOpen(){", "/* ---------- 保有ポジションの利確")
                + cut("function pairSigFor(p){", "function loadMfe(")
                + cut("function holdAligned(p,ps,dir){", "function posAdvice(p){")
                + cut("function posAdvice(p){", "function checkPosNotify("))
        script = (head + body
                  + "var a=posAdvice(computeOpen()[0]);\n"
                  "console.log(JSON.stringify(a?{label:a.label,src:a.src||'local',"
                  "level:a.level,reason:a.reason}:null));\n")
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                         encoding="utf-8") as f:
            f.write(script); path = f.name
        self.addCleanup(os.unlink, path)
        out = subprocess.run([node, path], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_the_same_mode_screen_computes_it(self):
        got = self._run("day", server_adv=False)
        self.assertIsNotNone(got, "同じモードの画面なのに出ていない")
        self.assertIn("ホールド", got["label"])

    def test_another_mode_screen_uses_the_server_judgement(self):
        got = self._run("mtf", server_adv=True)
        self.assertIsNotNone(got, "サーバーの判定があるのに出ていない")
        self.assertEqual(got["src"], "server")
        self.assertIn("ホールド", got["label"])

    def test_it_never_goes_silent_right_after_registering(self):
        """サーバーがまだ拾っていない数分間、判定欄を空にしないこと。

        これが「デイでは出るのに mtf では消える」の正体だった。"""
        got = self._run("mtf", server_adv=False)
        self.assertIsNotNone(got, "判定欄が丸ごと消えている（不具合と見分けが付かない）")
        self.assertIn("準備中", got["label"])
        self.assertIn("デイ", got["reason"], "どのモードの玉か書いていない")
        self.assertNotIn(got["level"], ("cut", "take"),
                         "準備中なのに決済を促している")

    def test_the_placeholder_is_not_shown_for_the_same_mode(self):
        """同じモードなら普通に計算できるので、準備中でごまかさないこと。"""
        got = self._run("day", server_adv=False)
        self.assertNotIn("準備中", got["label"])

    def test_the_indicators_are_fetched_outside_the_live_loop(self):
        """モード切替と建玉登録の直後に、その場で取りに行くこと。

        liveSignals の中だけだと30秒ガードとモード変更の破棄に当たって動かない。"""
        with open(os.path.join(ROOT, "index.html"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("async function refreshPosModeSig()", src, "別口の入口が無い")
        i = src.index("async function setMode(m){")
        self.assertIn("refreshPosModeSig()", src[i:i + 500], "モード切替で呼んでいない")
        j = src.index("toast('追加しました'")
        self.assertIn("refreshPosModeSig()", src[j:j + 300], "建玉登録で呼んでいない")


if __name__ == "__main__":
    unittest.main(verbosity=2)
