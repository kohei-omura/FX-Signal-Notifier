#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""通知に添える「株×FX地合い」（fx_signal.xmarket_note）のテスト。ネットワーク不要。

守ること:
  - 姉妹アプリ（株オラクル・GLOBE ORACLE・Carry Navi）のファイルが読めなくても、通知の本文は従来どおり
  - 読めた時だけ2行足す。FXの判定・通知の可否には関わらない
"""
import datetime, json, os, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mock_api                                   # noqa: E402
sys.modules["requests"] = mock_api                # fx_signal の import より前に差し込む
os.environ.pop("MODE", None)
import fx_signal as F                             # noqa: E402

JST = datetime.timezone(datetime.timedelta(hours=9))


def _now(fmt):
    return datetime.datetime.now(JST).strftime(fmt)


class XmarketNoteTest(unittest.TestCase):
    def setUp(self):
        self._dir = F.DATA_DIR
        self.tmp = tempfile.mkdtemp()
        F.DATA_DIR = self.tmp
        self.addCleanup(setattr, F, "DATA_DIR", self._dir)
        mock_api.fail_mode = None
        self.addCleanup(setattr, mock_api, "fail_mode", None)

    def _write_status(self, h=-1):
        st = {"generated_at": _now("%Y-%m-%d %H:%M JST"), "market_open": True, "pairs": [
            {"symbol": s, "bid": 150.0, "mtf": {"1hour": h, "4hour": h}}
            for s in ("USD_JPY", "EUR_JPY", "GBP_JPY", "AUD_JPY")]}
        with open(os.path.join(self.tmp, "status.json"), "w", encoding="utf-8") as f:
            json.dump(st, f)

    def test_empty_when_sister_apps_unreadable(self):
        """既定の模擬APIは姉妹アプリのファイルを返さない → 何も足さない"""
        self._write_status()
        self.assertEqual(F.xmarket_note(), "")

    def test_empty_when_network_raises(self):
        self._write_status()

        def dead(url, params):
            raise ConnectionError("down")
        mock_api.fail_mode = dead
        self.assertEqual(F.xmarket_note(), "")

    def test_empty_when_own_status_missing(self):
        self.assertEqual(F.xmarket_note(), "")

    def test_two_lines_when_stock_apps_readable(self):
        self._write_status(h=-1)                      # 円ペアは全部下向き＝円高

        def sisters(url, params):
            if "kabu-oracle" in url:
                return mock_api.Resp({"generated": _now("%Y.%m.%d %H:%M"),
                                      "regime": {"label": "下落基調", "chg1": -1.2, "chg20": -5.0, "price": 60000}})
            if "GLOBE-ORACLE" in url:
                return mock_api.Resp({"asof": _now("%Y-%m-%d %H:%M"), "idx": {
                    "SP500": {"price": 7500, "chg": -1.1}, "NASDAQ": {"price": 25000, "chg": -1.5},
                    "DOW": {"price": 52000, "chg": -0.8}}})
            return None
        mock_api.fail_mode = sisters
        t = F.xmarket_note()
        self.assertTrue(t.startswith("\n\n🌐 株×FX地合い: ☔ リスクオフ（株:弱い／円:円高）"), t)
        self.assertIn("買いシグナルは逆風", t)
        self.assertEqual(t.count("\n"), 3)             # 空行＋2行だけ


if __name__ == "__main__":
    unittest.main()
