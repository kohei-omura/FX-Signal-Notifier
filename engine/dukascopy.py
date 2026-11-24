#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""デューカスコピーの公開ヒストリカルデータ（1分足・BID）を取り、長期検証の足にする。

GMOのAPIは約3年ぶんしか遡れない。勝ち・負けを確定させるには件数が要るので、
2003年から1分足があるデューカスコピーの公開データで5年検証を回す。

  URL: https://datafeed.dukascopy.com/datafeed/{SYM}/{YYYY}/{MM0}/{DD}/BID_candles_min_1.bi5
       MM0 は 0始まりの月（1月=00）。1ファイル＝UTCの1日ぶんの1分足。
  中身: LZMA圧縮。1本24バイト（ビッグエンディアン）
       time(秒, その日の0:00 UTCから) / open / close / low / high（整数, 価格×1000 ※円ペア） / volume(float)

判定ロジックは fx_signal.py のものをそのまま使う。ここは「足をどこから持ってくるか」だけを差し替える
（fx_signal.klines_day と同じ形 {openTime(ms): (high, low, close)} を返す）。

価格はGMOと少しずれる（業者ごとに気配が違う）。コストは今までどおりGMOのスプレッド表で引く。
出来高0の足（週末・休場中の横ばい足）は捨てる。GMOの足にはその時間が無いため。
"""
import collections, datetime, lzma, os, struct, sys, threading, time
from concurrent.futures import ThreadPoolExecutor

BASE = "https://datafeed.dukascopy.com/datafeed"
SYMBOLS = {"USD_JPY": "USDJPY", "EUR_JPY": "EURJPY", "GBP_JPY": "GBPJPY", "AUD_JPY": "AUDJPY"}
POINT = {"USD_JPY": 1000.0, "EUR_JPY": 1000.0, "GBP_JPY": 1000.0, "AUD_JPY": 1000.0}
CACHE_DIR = os.environ.get("DUKAS_CACHE", os.path.join(os.path.dirname(__file__), "..", ".dukas"))
REFETCH_DAYS = 3            # 直近は途中までしか無いことがあるので取り直す
INTERVAL_MIN = {"1min": 1, "5min": 5, "15min": 15, "30min": 30, "1hour": 60, "4hour": 240, "1day": 1440}
JST = datetime.timezone(datetime.timedelta(hours=9))
_REC = struct.Struct(">5if")


def _path(sym, d):
    return os.path.join(CACHE_DIR, SYMBOLS[sym], f"{d:%Y}", f"{d:%m}", f"{d:%d}.bi5")


def _url(sym, d):
    return f"{BASE}/{SYMBOLS[sym]}/{d.year:04d}/{d.month - 1:02d}/{d.day:02d}/BID_candles_min_1.bi5"


def decode(raw, sym, day):
    """bi5 の中身 → [(openTime_ms, open, high, low, close, volume)]。出来高0の足は捨てる。"""
    if not raw:
        return []
    data = lzma.decompress(raw)
    t0 = int(datetime.datetime(day.year, day.month, day.day, tzinfo=datetime.timezone.utc).timestamp()) * 1000
    pt = POINT[sym]
    out = []
    for k in range(len(data) // _REC.size):
        t, o, c, lo, hi, v = _REC.unpack_from(data, k * _REC.size)
        if v <= 0:
            continue
        o, c, lo, hi = o / pt, c / pt, lo / pt, hi / pt
        if not (lo <= min(o, c) + 1e-9 and hi >= max(o, c) - 1e-9):
            # 並び(open,close,low,high)が想定と違えば、黙って間違った足を作らない
            raise ValueError(f"{sym} {day}: 足の並びが想定と違います o={o} c={c} l={lo} h={hi}")
        out.append((t0 + t * 1000, o, hi, lo, c, v))
    return out


# ---------------- 取得（GitHub Actions で実行） ----------------
def fetch(years=5, workers=8, symbols=None, end=None, session=None):
    """足りない日だけ取る。戻り値: (取った数, 失敗した数)"""
    import requests
    s = session or requests.Session()
    end = end or (datetime.datetime.now(datetime.timezone.utc).date() - datetime.timedelta(days=1))
    start = end - datetime.timedelta(days=int(365.25 * years) + 7)
    recent = end - datetime.timedelta(days=REFETCH_DAYS)
    jobs = []
    for sym in (symbols or SYMBOLS):
        d = start
        while d <= end:
            p = _path(sym, d)
            if d.weekday() != 5 and (not os.path.exists(p) or d >= recent):   # 土曜(UTC)は丸ごと休場
                jobs.append((sym, d))
            d += datetime.timedelta(days=1)
    print(f"[dukascopy] 取得 {len(jobs)} 日ぶん（{start}〜{end}）", flush=True)
    lock = threading.Lock(); stat = collections.Counter()

    def one(job):
        sym, d = job
        for k in range(5):
            try:
                r = s.get(_url(sym, d), timeout=30)
                if r.status_code == 404:
                    body = b""
                elif r.status_code == 200:
                    body = r.content
                    decode(body, sym, d)           # 壊れたファイルを保存しない
                else:
                    raise RuntimeError(f"HTTP {r.status_code}")
                p = _path(sym, d)
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p + ".tmp", "wb") as f:
                    f.write(body)
                os.replace(p + ".tmp", p)
                with lock:
                    stat["ok"] += 1
                return
            except Exception as e:
                err = e
                time.sleep(1.5 * (k + 1))
        with lock:
            stat["fail"] += 1
        print(f"[dukascopy] 失敗 {sym} {d}: {err}", file=sys.stderr, flush=True)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, _ in enumerate(ex.map(one, jobs), 1):
            if i % 500 == 0:
                print(f"[dukascopy]  {i}/{len(jobs)}", flush=True)
    print(f"[dukascopy] 完了 ok={stat['ok']} fail={stat['fail']}", flush=True)
    return stat["ok"], stat["fail"]


# ---------------- 読み込み（fx_signal.klines_day の差し替え） ----------------
class Source:
    """キャッシュ済みの1分足から、指定の足と日付ぶんを作る。"""

    def __init__(self, cache_dir=None, keep=16):
        self.dir = cache_dir or CACHE_DIR
        self._lru = collections.OrderedDict(); self._keep = keep
        self._lock = threading.Lock()
        self.days_with_data = collections.defaultdict(set)

    def minutes(self, sym, day):
        key = (sym, day)
        with self._lock:
            if key in self._lru:
                self._lru.move_to_end(key)
                return self._lru[key]
        p = os.path.join(self.dir, SYMBOLS[sym], f"{day:%Y}", f"{day:%m}", f"{day:%d}.bi5")
        rows = []
        if os.path.exists(p):
            with open(p, "rb") as f:
                rows = decode(f.read(), sym, day)
        if rows:
            self.days_with_data[sym].add(day)
        with self._lock:
            self._lru[key] = rows
            while len(self._lru) > self._keep:
                self._lru.popitem(last=False)
        return rows

    def _aggregate(self, sym, utc_days, interval, keep_fn):
        step = INTERVAL_MIN[interval] * 60000
        out = {}
        for d in utc_days:
            for t, o, h, l, c, v in self.minutes(sym, d):
                b = t - t % step
                if not keep_fn(b):
                    continue
                cur = out.get(b)
                out[b] = (h, l, c) if cur is None else (max(cur[0], h), min(cur[1], l), c)
        return out

    def klines(self, sym, datestr, interval):
        """GMOの klines_day と同じ形。datestr は YYYYMMDD（JSTの1日）か YYYY（JSTの1年）。"""
        if len(datestr) == 4:
            y = int(datestr)
            a = datetime.datetime(y, 1, 1, tzinfo=JST); b = datetime.datetime(y + 1, 1, 1, tzinfo=JST)
        else:
            d = datetime.datetime.strptime(datestr, "%Y%m%d")
            a = datetime.datetime(d.year, d.month, d.day, tzinfo=JST); b = a + datetime.timedelta(days=1)
        lo, hi = int(a.timestamp() * 1000), int(b.timestamp() * 1000)
        days, u = [], a.astimezone(datetime.timezone.utc).date()
        while u <= b.astimezone(datetime.timezone.utc).date():
            days.append(u); u += datetime.timedelta(days=1)
        return self._aggregate(sym, days, interval, lambda t: lo <= t < hi)


def install(F, source=None):
    """fx_signal の足の取得先をデューカスコピーに差し替える（検証の実行中だけ）。"""
    src = source or Source()
    F._KLINE_DAY_CACHE.clear()

    def klines_day(symbol, datestr, interval=None):
        interval = interval or F.P["interval"]
        key = (symbol, interval, datestr)
        hit = F._KLINE_DAY_CACHE.get(key)
        if hit is None:
            hit = src.klines(symbol, datestr, interval)
            F._KLINE_DAY_CACHE[key] = hit
        return hit

    F.klines_day = klines_day          # 上位足(_htf_closes)も klines_day を通るので一緒に切り替わる
    return src


if __name__ == "__main__":
    yrs = float(os.environ.get("DUKAS_YEARS", "5"))
    ok, fail = fetch(years=yrs, workers=int(os.environ.get("DUKAS_WORKERS", "8")))
    # 一部の日が取れないのは許容する（休日など）。大半が失敗したら止める。
    sys.exit(1 if fail > max(50, ok * 0.05) else 0)
