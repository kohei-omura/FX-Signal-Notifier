#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GMO Public API のオフライン模擬。

テストをネットワークから切り離すための最小実装。requests の代わりに
sys.modules['requests'] へ差し込んで使う。値は決定論的（PYTHONHASHSEED非依存）。
"""
import json, random, time, zlib

BASE_PRICE = {"USD_JPY": 159.0, "EUR_JPY": 185.0, "GBP_JPY": 214.0, "AUD_JPY": 114.0}
BARS_PER_DAY = {"1min": 1440, "5min": 288, "10min": 144, "15min": 96,
                "30min": 48, "1hour": 24, "4hour": 1500, "1day": 250}
LATENCY = 0.0                 # 1リクエストあたりの往復時間(秒)。速度計測時だけ上げる
CALLS = {"klines": 0, "ticker": 0, "status": 0}
_SERIES = {}
fail_mode = None              # None / callable(url, params) -> レスポンス or 例外送出


class Resp:
    def __init__(self, payload, status_code=200):
        self.status_code = status_code
        self._p = payload
        self.text = json.dumps(payload) if payload is not None else ""

    def json(self):
        if self._p is None:
            raise ValueError("Expecting value: not a JSON document")
        return self._p


def reset():
    CALLS.update({"klines": 0, "ticker": 0, "status": 0})


def _series(symbol, key, n):
    ck = (symbol, key, n)
    if ck in _SERIES:
        return _SERIES[ck]
    rnd = random.Random(zlib.crc32(f"{symbol}|{key}".encode()))
    px = BASE_PRICE.get(symbol, 150.0)
    t0 = (zlib.crc32(str(key).encode()) % 1000) * 10_000_000
    out = []
    for i in range(n):
        px += rnd.gauss(0, 0.02) + 0.004
        hi = px + abs(rnd.gauss(0, 0.01)); lo = px - abs(rnd.gauss(0, 0.01))
        out.append({"openTime": str(t0 + i*60000), "open": f"{px:.4f}",
                    "high": f"{hi:.4f}", "low": f"{lo:.4f}", "close": f"{px:.4f}"})
    _SERIES[ck] = out
    return out


def get(url, timeout=None, params=None, **kw):
    params = params or {}
    if LATENCY:
        time.sleep(LATENCY)
    if fail_mode is not None:
        forced = fail_mode(url, params)      # 例外を投げるか Resp を返す
        if forced is not None:
            return forced
    if url.endswith("/status"):
        CALLS["status"] += 1
        return Resp({"status": 0, "data": {"status": "OPEN"}})
    if url.endswith("/ticker"):
        CALLS["ticker"] += 1
        return Resp({"status": 0, "data": [{"symbol": s, "bid": f"{v:.3f}", "ask": f"{v+0.005:.3f}"}
                                           for s, v in BASE_PRICE.items()]})
    if url.endswith("/klines"):
        CALLS["klines"] += 1
        n = BARS_PER_DAY.get(params.get("interval"), 96)
        return Resp({"status": 0, "data": _series(params["symbol"], params.get("date", "0"), n)})
    return Resp({"status": 0, "data": []})


def post(*a, **k):
    return Resp({})


class Session:
    """requests.Session 互換の最小実装（接続の使い回しは模擬しない）。"""
    headers = {}

    def get(self, url, **kw):
        return get(url, **kw)

    def mount(self, *a, **k):
        pass

    def close(self):
        pass
