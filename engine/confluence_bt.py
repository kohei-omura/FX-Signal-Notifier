# -*- coding: utf-8 -*-
"""総合判定（🟢エントリーOK / 🟡保留・検討 / 🔴見送り）をバックテストで再現する。

index.html の confluence() をそのまま移植したもの。

なぜ要るか:
    このマークは、画面で売買を決める時にいちばん見られている数字なのに、
    一度も検証されていない。バックテストは「合図が出たか」しか見ておらず、
    マークはブラウザの中でしか計算されていなかった。
    実際、直近の建玉は3件続けて 🔴見送り で入っている。🟢と🔴で本当に
    成績が違うのかが分からなければ、マークに従う理由も、無視する理由も無い。

移植の方針:
    - 各部品は画面と同じ式にする（テストで画面の実装と突き合わせる）
    - 未来を覗かない。上位足は終値が確定した足だけ、期待値は決着済みの取引だけを使う
    - 過去に再現できないもの（重要指標の予定）は「警戒なし＝✓」として扱う
      （全件に同じ点が入るだけなので、マーク同士の比較には影響しない）
"""
import bisect
import datetime
from zoneinfo import ZoneInfo

TZ_JST = ZoneInfo("Asia/Tokyo")
TZ_LDN = ZoneInfo("Europe/London")
TZ_NY = ZoneInfo("America/New_York")

# index.html の CONFLUENCE_W_BY_MODE（mtf は swing の配点を借りる＝CONFLUENCE_W_FALLBACK）
WEIGHTS = {
    "scalp": {"longEnv": 3, "adxBand": 2, "expectancy": 2, "dow": 2, "zone": 2,
              "granville": 0.5, "blackout": 1.5},
    "day":   {"longEnv": 3, "adxBand": 2, "expectancy": 2, "dow": 2, "zone": 1.5,
              "granville": 0.5, "blackout": 1},
    "swing": {"longEnv": 3, "adxBand": 2, "expectancy": 2, "dow": 2, "zone": 1.5,
              "granville": 0.5, "blackout": 1},
}
WEIGHT_FALLBACK = {"mtf": "swing"}
# 画面の統計（期待値の判定に使う）が見ている期間。画面は JS_PARAMS.days 日ぶんの足、
# mtf はサーバーの統計(STATS_DAYS)を使う。バックテスト中は STATS_DAYS が書き換わるので
# ここに運用時の値を持っておく。
LIVE_STATS_DAYS = {"scalp": 3, "day": 8, "swing": 20, "mtf": 45}
LIVE_STATS_MIN_N = 8          # simStats が統計を出す最低件数
ADX_BAND_MIN_N = 150          # index.html の ADX_BAND_MIN_N
ADX_BANDS = ((50, "極端(50〜)"), (40, "強(40〜50)"), (30, "適正(30〜40)"),
             (25, "弱め(25〜30)"), (20, "弱(20〜25)"), (0, "無風(〜20)"))
MARK_OK, MARK_HOLD, MARK_NO = "🟢 エントリーOK", "🟡 保留・検討", "🔴 見送り"


def weights_for(mode):
    return WEIGHTS.get(WEIGHT_FALLBACK.get(mode, mode), WEIGHTS["swing"])


# ---------------- 部品（画面の関数と1対1） ----------------
def ema_series(v, p):
    """emaSeries と同じ（最初の p 本の単純平均から始める）。"""
    out = [None] * len(v)
    if len(v) < p:
        return out
    k = 2.0 / (p + 1)
    e = sum(v[:p]) / p
    out[p - 1] = e
    for i in range(p, len(v)):
        e = v[i] * k + e * (1 - k)
        out[i] = e
    return out


def atr_c(o, p):
    """atrC と同じ（Wilder平滑・系列全体）。o は (高, 安, 終)。"""
    if len(o) < p + 1:
        return None
    tr = []
    for i in range(1, len(o)):
        h, l, pc = o[i][0], o[i][1], o[i - 1][2]
        tr.append(max(h - l, abs(h - pc), abs(l - pc)))
    a = sum(tr[:p]) / p
    for i in range(p, len(tr)):
        a = (a * (p - 1) + tr[i]) / p
    return a


def dow_trend(o):
    """dowStructure と同じ。'up' / 'down' / 'range'。"""
    n = len(o)
    if n < 11:
        return "range"
    H, L = [], []
    for i in range(2, n - 2):
        h, l = o[i][0], o[i][1]
        if h > o[i-1][0] and h > o[i-2][0] and h > o[i+1][0] and h > o[i+2][0]:
            H.append(h)
        if l < o[i-1][1] and l < o[i-2][1] and l < o[i+1][1] and l < o[i+2][1]:
            L.append(l)
    if len(H) < 2 or len(L) < 2:
        return "range"
    hh = H[-1] > H[-2]; ll = L[-1] < L[-2]; hl = L[-1] > L[-2]; lh = H[-1] < H[-2]
    if hh and hl:
        return "up"
    if lh and ll:
        return "down"
    return "range"


def granville_no(o, atr_p, mode):
    """granville と同じ。候補のうち採用された番号（1〜4）か None。"""
    c = [r[2] for r in o]
    base = 200 if (mode == "swing" and len(c) >= 200) else 75
    if len(c) < base + 6:
        return None
    ma = ema_series(c, base)
    atr = atr_c(o, atr_p) or 0
    n = len(c); price = c[-1]; m = ma[-1]
    if m is None or not atr:
        return None
    slope = ma[n-1] - ma[n-4]
    up, down = slope > 0, slope < 0
    dev = (price - m) / atr
    cu = c[n-2] <= ma[n-2] and price > m
    cd = c[n-2] >= ma[n-2] and price < m
    cands = []
    if up and cu: cands.append((1, 1))
    if up and c[n-2] < ma[n-2] and price > m: cands.append((2, 1))
    if up and abs(dev) <= 1 and price >= m: cands.append((3, 0))
    if dev <= -2: cands.append((4, 0))
    if down and cd: cands.append((1, 1))
    if down and c[n-2] > ma[n-2] and price < m: cands.append((2, 1))
    if down and abs(dev) <= 1 and price <= m: cands.append((3, 0))
    if dev >= 2: cands.append((4, 0))
    if not cands:
        return None
    cands.sort(key=lambda x: (-x[1], x[0]))
    return cands[0][0]


def is_gotobi(ts_ms):
    """_isGotobi と同じ（5・10日。土日なら前の営業日に繰り上がる）。"""
    d = datetime.datetime.fromtimestamp(ts_ms / 1000, TZ_JST)
    biz = d.weekday() < 5
    if d.day % 5 == 0 and biz:
        return True
    for k in (1, 2):
        f = d + datetime.timedelta(days=k)
        if f.day % 5 == 0 and f.weekday() >= 5 and biz:
            return True
    return False


def zone_of(ts_ms):
    """zoneNow を任意の時刻に当てたもの。'gotobi'/'golden'/'avoid'/'eu'/'normal'。"""
    u = datetime.datetime.fromtimestamp(ts_ms / 1000, datetime.timezone.utc)
    ldn = u.astimezone(TZ_LDN); ny = u.astimezone(TZ_NY); jp = u.astimezone(TZ_JST)
    lh = ldn.hour + ldn.minute / 60.0; nh = ny.hour + ny.minute / 60.0
    ldn_open = 8 <= lh < 16.5; ny_open = 8 <= nh < 17
    if is_gotobi(ts_ms) and jp.hour == 9 and jp.minute <= 55:
        return "gotobi"
    if ldn_open and ny_open:
        return "golden"
    if 6 <= jp.hour < 8 or 12 <= jp.hour < 15:
        return "avoid"
    if ldn_open and not ny_open:
        return "eu"
    return "normal"


def long_env_dir(ts4, c4, t_ms, dur_ms=4 * 3600 * 1000):
    """refreshLongEnv と同じ判定（4時間足EMA200の7本前との差）を時刻 t に当てる。

       画面は形成中の足も含めて見ているが、過去に当てる時は確定した足だけにする。"""
    k = bisect.bisect_right(ts4, t_ms - dur_ms) - 1
    if k + 1 < 210:
        return None
    es = ema_series(c4[:k + 1], 200)
    cur, prev = es[-1], es[-7]
    if cur is None or prev is None:
        return None
    s = cur - prev
    return "up" if s > 0 else ("down" if s < 0 else "flat")


def adx_score(adx, mode, table):
    """adxScore と同じ（区間が0を跨がず150件以上の帯だけ ✓/✗、他は△）。"""
    if adx is None:
        return None
    name = next((lab for need, lab in ADX_BANDS if adx >= need), None)
    b = (table.get(mode) or {}).get(name) if table else None
    if not b or b[0] < ADX_BAND_MIN_N:
        return 0.5
    if b[2] > 0:
        return 1
    if b[3] < 0:
        return 0
    return 0.5


def score(parts, weights):
    """confluence() の add() と同じ集計。parts の値は True/False/数値/None。"""
    got = 0.0; total = 0.0
    for k, w in weights.items():
        total += w
        v = parts.get(k)
        if v is True:
            got += w
        elif isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
            got += w * v
    return got / total if total else 0.0


def mark_of(pct, has_signal=True):
    if pct >= 0.70:
        return MARK_OK if has_signal else MARK_HOLD
    if pct >= 0.50:
        return MARK_HOLD
    return MARK_NO


class MarkContext:
    """1通貨ぶんのバックテストの間、部品の計算に要るものを持ち回る。"""

    def __init__(self, mode, symbol, times, oh, bar_min, atr_p, ts4=None, c4=None,
                 adx_table=None):
        self.mode = mode; self.symbol = symbol
        self.times = times; self.oh = oh; self.bar_min = bar_min; self.atr_p = atr_p
        self.ts4 = ts4 or []; self.c4 = c4 or []
        self.adx_table = adx_table or {}
        self.w = weights_for(mode)
        self.win = max(60, int(LIVE_STATS_DAYS.get(mode, 8) * 1440 / max(1, bar_min)))
        self.done = []        # 決着済みの取引 (エントリー位置, 決着位置, 'tp'/'sl')
        self._le = {}         # 4時間足の位置 → 向き（同じ足の間は何度も計算しない）

    def long_env(self, t_ms, dur_ms=4 * 3600 * 1000):
        k = bisect.bisect_right(self.ts4, t_ms - dur_ms) - 1
        if k not in self._le:
            self._le[k] = long_env_dir(self.ts4, self.c4, t_ms, dur_ms)
        return self._le[k]

    def add_trade(self, i, xj, res):
        self.done.append((i, xj, res))

    def expectancy_ok(self, i, tp_pips, sl_pips):
        """expectancyOK と同じ。直近の窓で決着済みの取引のTP勝率が損益分岐を超えるか。"""
        lo = i - self.win
        rows = [r for r in self.done if r[0] >= lo and r[1] <= i]
        if len(rows) < LIVE_STATS_MIN_N or not tp_pips or not sl_pips:
            return None
        wr = 100.0 * sum(1 for r in rows if r[2] == "tp") / len(rows)
        be = 100.0 / (1 + tp_pips / sl_pips)
        return wr > be

    def parts(self, i, side, adx, tp_pips, sl_pips, with_adx=True):
        o = self.oh[max(0, i - self.win + 1):i + 1]
        t = self.times[i]
        want = "up" if side == "買い" else "down"
        le = self.long_env(t) if self.ts4 else None
        dw = dow_trend(o)
        gn = granville_no(o, self.atr_p, self.mode)
        z = zone_of(t)
        return {
            "longEnv": (None if le is None else le == want),
            "adxBand": (adx_score(adx, self.mode, self.adx_table) if with_adx else 0.5),
            "expectancy": self.expectancy_ok(i, tp_pips, sl_pips),
            "dow": dw == want,
            "zone": z in ("golden", "eu", "gotobi"),
            "granville": (None if gn is None else gn in (1, 2)),
            "blackout": True,
        }

    def mark(self, i, side, adx, tp_pips, sl_pips, with_adx=True):
        p = self.parts(i, side, adx, tp_pips, sl_pips, with_adx)
        return mark_of(score(p, self.w))

    def marks(self, i, side, adx, tp_pips, sl_pips):
        """(画面と同じ判定, ADXの配点を抜いた判定, 部品ごとの結果)。部品は1回だけ計算する。"""
        p = self.parts(i, side, adx, tp_pips, sl_pips, with_adx=True)
        parts = dict(p)
        full = mark_of(score(p, self.w))
        p["adxBand"] = 0.5
        return full, mark_of(score(p, self.w)), parts


def part_label(v):
    """部品の結果を、成績を分ける時の区分名にする（✓ / △ / ✗ / —）。"""
    if v is True or (isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 1):
        return "✓"
    if v is None:
        return "—"
    if v is False or v == 0:
        return "✗"
    return "△"
