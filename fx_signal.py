#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FX Signal & Position Navigator  — スキャル/デイトレ用 加重スコア版
"""

import os, sys, json, math, time, smtplib, datetime, threading
from concurrent.futures import ThreadPoolExecutor
from email.mime.text import MIMEText
from email.utils import formatdate
from zoneinfo import ZoneInfo
import requests

JST = ZoneInfo("Asia/Tokyo")
SYMBOLS = ["USD_JPY", "EUR_JPY", "GBP_JPY", "AUD_JPY"]
PRICE_TYPE = "BID"

MODE = os.environ.get("MODE", "scalp").lower()
PARAMS = {
    "scalp": {"interval":"1min","ema_f":5,"ema_s":13,"rsi":7,"macd":(6,13,5),
              "bb":(20,2.0),"adx":14,"atr":14,"th":0.35,"slm":1.0,"tsr":1.5},
    "day":   {"interval":"15min","ema_f":9,"ema_s":21,"rsi":14,"macd":(12,26,9),
              "bb":(20,2.0),"adx":14,"atr":14,"th":0.40,"slm":1.3,"tsr":1.6},
    "swing": {"interval":"1hour","ema_f":12,"ema_s":26,"rsi":14,"macd":(12,26,9),
              "bb":(20,2.0),"adx":14,"atr":14,"th":0.45,"slm":1.8,"tsr":1.8},
}
BARMIN = {"1min":1,"5min":5,"10min":10,"15min":15,"30min":30,"1hour":60,"4hour":240,"1day":1440}
P = PARAMS.get(MODE, PARAMS["scalp"])

W_EMA, W_MACD, W_RSI, W_BB = 0.35, 0.25, 0.20, 0.20
TECH_W, FUND_W = 0.9, 0.1
FUND_BIAS = {"USD_JPY":0.5, "EUR_JPY":0.4, "GBP_JPY":0.5, "AUD_JPY":0.4}

SL_ATR_MULT = 1.0
TP_SL_RATIO = 1.5

# ===== 通知の宛先ルーティング =====
# LINE : シグナル（エントリー）通知だけを送る。画面(index.html)発の「⚡ライブ」通知はWorker側で遮断。
# メール: エントリー・推奨レベル設定・保有中の利確/損切り/利確検討を、すべて送る（取りこぼし防止）。
LINE_ENABLED = True             # LINE無料枠が復活したのでON。枠が厳しくなったらFalseで全停止できる
NOTIFY_ENTRY_TO_LINE = True     # エントリーシグナルをLINEへ
NOTIFY_ENTRY_TO_MAIL = True     # エントリーシグナルをメールへ
NOTIFY_POSITION_TO_LINE = False # 保有中の利確/損切りサインもLINEに欲しくなったら True（メールには常に届く）

VALID_BARS = 3
MAX_CHASE_RATIO = 0.5

# ===== 保有中の利確/損切り判定（建値ベース・シグナル監視） =====
ADV_OPP   = 0.25   # スコア×保有方向 がこの値以下なら「逆シグナル」
ADV_SUPP  = 0.15   # この値以上なら順方向継続（ホールド）
ADV_DECAY = 0.10   # |スコア| がこの値未満なら勢い減衰
TRAIL_ATR = 1.0    # 最高益から ×ATR 押し戻したらトレール利確
TOUCH_LOOKBACK_MIN = 12  # B: cron間隔(最短5分)＋遅延を吸収。直近この分数の足の高安でTP/SLタッチを検出
PROFIT_ATR = 1.0   # この含み益(×ATR)以上＋反転でしっかり利確
ADX_WEAK  = 20.0   # ADXがこの値未満で勢い喪失

# ===== シグナル統計（想定保有時間・TP勝率）：重いので約60分キャッシュ =====
STATS_TTL_SEC = 3600
STATS_DAYS = {"scalp": 3, "day": 8, "swing": 20}
STATS_MAX_BARS = {"scalp": 1000, "day": 1000, "swing": 1000}

NEWS_BLACKOUT = []          # 手書きの予備リスト（"YYYY-MM-DD HH:MM"・全通貨一律）。通常は空でOK
BLACKOUT_MIN = 15           # 発表前後この分数はエントリー見送り
WARN_BEFORE_MIN = 60        # 保有ポジションは発表この分前から「まもなく発表」警告
NEWS_FILE = "news_blackout.json"   # news-calendar.yml が毎日生成する自動取得カレンダー
NEWS_STALE_DAYS = 3         # カレンダーがこの日数より古かったら警告（回避が黙って無効化されるのを防ぐ）
# 通貨ペア → 影響する国/通貨。クロス円なのでJPYは全ペア共通。AUDは最大輸出先の中国(CNY)も対象。
PAIR_COUNTRIES = {
    "USD_JPY": {"USD", "JPY"},
    "EUR_JPY": {"EUR", "JPY"},
    "GBP_JPY": {"GBP", "JPY"},
    "AUD_JPY": {"AUD", "JPY", "CNY"},
}
_NEWS_CACHE = None

# ===== MTF（マルチタイムフレーム）=====
# "show"  … 上位足トレンドを表示するだけ（エントリーは止めない）※推奨・初期値
# "filter"… 上位足と方向が一致しないエントリーを見送る（効果を実データで確認してから）
# "off"   … 何もしない
MTF_MODE = "block_opposite"
# "show"           … 表示のみ（エントリーを止めない）
# "block_opposite" … ★上位足と逆行するエントリーだけ見送る（レンジは通す）※実トレード337件で検証済み
#                    順張り46%/レンジ32%/逆行25%（勝率）、逆行は平均-66円で順張りの5.5倍の損失だった
# "filter"         … 順張り時のみ許可（最も厳格）
# "off"            … 無効
MTF_TFS = ("1hour", "4hour")   # 中期足・長期足
MTF_EMA = (12, 26)             # 上位足のトレンド判定EMA

# ===== 時間帯フィルタ（現在は無効） =====
# 【撤去の経緯】当初は「実績で負けが集中した時間帯」を避ける目的で導入したが、
#   ① その集計は GMO CSV に建玉時刻が無いため“決済時刻”ベースになっていた（実データで55%の取引が別の時台に計上）
#   ② エントリー時刻で集計し直したところ、遮断していた 16時台(+333円) と 21時台(+716円) は
#      実際にはプラスで、止めるべきではなかった
#   ③ 24時台すべてで勝率のWilson95%信頼区間が基準勝率を跨いでおり、
#      「有意に悪い時間帯」は1つも無い（＝学習補正で偶然を排除したのと同じ基準では採用できない）
# 以上より、統計的根拠のないフィルタとして無効化した。
# 再検討するなら、エントリー時刻ベースで各時台30件以上を貯め、Wilson下限/上限で判定すること。
HOUR_FILTER_ON = False
BAD_HOURS = set()   # JSTの時台。HOUR_FILTER_ON=True にする場合のみ使う

PIP_SIZE = 0.01
DEFAULT_LOT = 10000
POSITIONS_FILE = "positions.json"
STATUS_FILE = "status.json"
# ===== エントリー記録簿 =====
# ポジションを削除しても消えない追記専用ログ。GMOのCSV(銘柄名＋建単価)と後から突き合わせ、
# 「上位足と逆行していたエントリーの成績」などを検証するために使う。
ENTRY_LOG_FILE = "entry_log.json"
ENTRY_LOG_MAX = 2000            # 古いものから間引く上限
STRONG_MULT = 1.5               # スコアが新規閾値のこの倍以上なら「強シグナル」扱い
CHART_POINTS = 60
BASE = "https://forex-api.coin.z.com/public/v1"
API_TIMEOUT = 15
API_WORKERS = int(os.environ.get("API_WORKERS", "4"))   # 同時接続数。増やしすぎるとAPI側に弾かれる
_OHLC_CACHE = {}
_KLINE_DAY_CACHE = {}     # (symbol, interval, date) -> {openTime: (high,low,close)}
_MTF_CACHE = {}           # symbol -> mtf_view の結果
_SCORE_CACHE = {}         # symbol -> score_pair の結果
_WARNINGS = []            # 実行中に起きた異常。status.json に載せて画面から見えるようにする
_tls = threading.local()
_warn_lock = threading.Lock()
_kline_lock = threading.Lock()
_kline_locks = {}


def warn(msg, tag=None):
    """警告を出しつつ記録する。status.json 経由で画面にも出す（黙って劣化させない）。"""
    print(f"[WARN] {msg}", file=sys.stderr)
    with _warn_lock:
        if tag and any(w.get("tag") == tag for w in _WARNINGS):
            return
        if len(_WARNINGS) < 20:
            _WARNINGS.append({"tag": tag or "warn", "msg": msg})


def _session():
    """スレッドごとに使い回すHTTPセッション。TLSハンドシェイクの繰り返しを避ける。"""
    s = getattr(_tls, "sess", None)
    if s is None:
        s = requests.Session()
        try:
            s.headers.update({"User-Agent": "fx-signal-notifier"})
        except Exception:
            pass
        _tls.sess = s
    return s


API_BREAKER_AFTER = 5     # 連続でこの回数コケたらAPI全体がダメとみなし、以降は1発勝負にする
_fail_streak = [0]


def api_get(path, params=None, retries=3, timeout=API_TIMEOUT, quiet=False):
    """GMO Public API を叩く共通口。接続を使い回し、失敗は指数バックオフで再試行する。
       status!=0 / 5xx / 429 / 例外 のいずれもリトライ対象。全滅なら None を返す。

       API側が完全に落ちている時に全リクエストで律儀に待つと1回の実行が何分もかかるので、
       連続失敗が続いたらリトライを打ち切る（サーキットブレーカー）。
       1件でも成功したら通常動作に戻す。"""
    if _fail_streak[0] >= API_BREAKER_AFTER:
        retries = 1
    last = None
    for n in range(1, retries+1):
        try:
            r = _session().get(f"{BASE}{path}", params=params, timeout=timeout)
            code = getattr(r, "status_code", 200)
            if code == 429:
                last = "HTTP 429 (レート制限)"
                warn("APIにレート制限されています。環境変数 API_WORKERS を下げてください"
                     f"（現在 {API_WORKERS}）", tag="rate-limit")
            elif code >= 500:
                last = f"HTTP {code}"
            else:
                j = r.json()
                if j.get("status") == 0:
                    _fail_streak[0] = 0
                    return j
                last = f"status={j.get('status')} {str(j.get('messages'))[:80]}"
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
        if n < retries:
            time.sleep(min(2 ** (n-1), 4))
    _fail_streak[0] += 1
    if _fail_streak[0] == API_BREAKER_AFTER:
        warn(f"API連続失敗{API_BREAKER_AFTER}回。以降は再試行を打ち切って早く抜ける", tag="breaker")
    if not quiet:
        warn(f"API失敗 {path} {params or ''}: {last}", tag=f"api:{path}")
    return None


def klines_day(symbol, datestr, interval=None):
    """1日(または1年)ぶんのローソク足。同じ日を二度取りに行かないよう実行中はキャッシュする。
       並列取得でも同じ日に同時に飛ばないようキー単位でロックする。"""
    interval = interval or P["interval"]
    key = (symbol, interval, datestr)
    hit = _KLINE_DAY_CACHE.get(key)
    if hit is not None:
        return hit
    with _kline_lock:
        lk = _kline_locks.setdefault(key, threading.Lock())
    with lk:
        hit = _KLINE_DAY_CACHE.get(key)
        if hit is not None:
            return hit
        rows = {}
        j = api_get("/klines", {"symbol": symbol, "priceType": PRICE_TYPE,
                                "interval": interval, "date": datestr})
        if j:
            for k in j.get("data", []):
                rows[int(k["openTime"])] = (float(k["high"]), float(k["low"]), float(k["close"]))
        _KLINE_DAY_CACHE[key] = rows
        return rows


def warm_up(symbols):
    """1回の実行で要る外部データ（市場状態・現在値・ローソク足・上位足）をまとめて並列取得する。
       APIは待ち時間が支配的なので、ここで束ねるだけで実行時間が大きく縮む。
       戻り値: (market_open, ticker)"""
    syms = [x for x in dict.fromkeys(symbols) if x]
    with ThreadPoolExecutor(max_workers=max(1, API_WORKERS)) as ex:
        f_open = ex.submit(market_is_open)
        f_tick = ex.submit(fetch_ticker)
        futs = [ex.submit(get_ohlc, x) for x in syms]
        if MTF_MODE != "off":
            futs += [ex.submit(mtf_view, x) for x in syms]
        for f in futs:
            try:
                f.result()
            except Exception as e:
                warn(f"先読み失敗: {e}", tag="prefetch")
        try:
            mo = f_open.result()
        except Exception:
            mo = True
        try:
            tk = f_tick.result()
        except Exception as e:
            warn(f"ticker取得失敗: {e}", tag="ticker"); tk = {}
    return mo, tk


def get_ohlc(symbol):
    if symbol in _OHLC_CACHE:
        return _OHLC_CACHE[symbol]
    need = max(P["ema_s"], P["macd"][1], P["adx"]*2, P["atr"]) + CHART_POINTS + 30
    today = datetime.datetime.now(JST).date()
    rows = {}
    for back in range(0, 7):
        d = today - datetime.timedelta(days=back)
        rows.update(klines_day(symbol, d.strftime("%Y%m%d")))
        if len(rows) >= need:
            break
    out = [rows[t] for t in sorted(rows)]
    if not out:
        warn(f"{symbol} のローソク足が取得できませんでした", tag=f"ohlc:{symbol}")
    _OHLC_CACHE[symbol] = out
    return out


def fetch_ticker():
    """現在値を取る。1件も取れないと画面から価格も保有ポジションも消えるため、必ず再試行する。"""
    out = {}
    j = api_get("/ticker", retries=3, timeout=10)
    for d in ((j or {}).get("data") or []):
        try:
            out[d["symbol"]] = {"bid": float(d["bid"]), "ask": float(d["ask"])}
        except (KeyError, TypeError, ValueError):
            continue
    if not out:
        warn("現在値(ticker)を取得できませんでした", tag="ticker")
    return out


def market_is_open():
    j = api_get("/status", retries=2, timeout=10, quiet=True)
    if not j:
        return True   # 判定できない時は開いている前提（判定を止めない）
    return (j.get("data") or {}).get("status") == "OPEN"


def read_json(path, default=None):
    """JSONを読む。開いたファイルは必ず閉じる。読めなければ default。"""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def write_json(path, obj, indent=2):
    """JSONを書く。開いたファイルは必ず閉じる。"""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=indent)


def clamp(x, lo=-1.0, hi=1.0):
    return max(lo, min(hi, x))


def ema_series(v, p):
    if len(v) < p:
        return [None]*len(v)
    k = 2/(p+1); out = [None]*len(v); e = sum(v[:p])/p; out[p-1] = e
    for i in range(p, len(v)):
        e = v[i]*k + e*(1-k); out[i] = e
    return out


def ema(v, p):
    s = ema_series(v, p)
    return s[-1] if s else None


def rsi(v, p):
    if len(v) < p+1:
        return None
    d = [v[i]-v[i-1] for i in range(1, len(v))]
    g = [max(x, 0.0) for x in d]; l = [max(-x, 0.0) for x in d]
    ag, al = sum(g[:p])/p, sum(l[:p])/p
    for i in range(p, len(d)):
        ag = (ag*(p-1)+g[i])/p; al = (al*(p-1)+l[i])/p
    return 100.0 if al == 0 else 100.0 - 100.0/(1.0+ag/al)


def macd(v, f, s, sig):
    ef, es = ema_series(v, f), ema_series(v, s)
    ml = [ (ef[i]-es[i]) if (ef[i] is not None and es[i] is not None) else None for i in range(len(v))]
    vals = [m for m in ml if m is not None]
    if len(vals) < sig+1:
        return None
    ss = ema_series(vals, sig)
    if ss[-1] is None or ss[-2] is None:
        return None
    hist = vals[-1]-ss[-1]; hist_prev = vals[-2]-ss[-2]
    return vals[-1], ss[-1], hist, hist_prev


def bollinger(v, p, k):
    if len(v) < p:
        return None
    w = v[-p:]; mid = sum(w)/p
    sd = (sum((x-mid)**2 for x in w)/p) ** 0.5
    return mid, mid+k*sd, mid-k*sd, sd


def atr(ohlc, p):
    if len(ohlc) < p+1:
        return None
    trs = []
    for i in range(1, len(ohlc)):
        h, l, _ = ohlc[i]; pc = ohlc[i-1][2]
        trs.append(max(h-l, abs(h-pc), abs(l-pc)))
    a = sum(trs[:p])/p
    for i in range(p, len(trs)):
        a = (a*(p-1)+trs[i])/p
    return a


def adx(ohlc, p):
    if len(ohlc) < 2*p+1:
        return None
    pdm, mdm, tr = [], [], []
    for i in range(1, len(ohlc)):
        h, l, c = ohlc[i]; ph, pl, pc = ohlc[i-1]
        up, dn = h-ph, pl-l
        pdm.append(up if (up > dn and up > 0) else 0.0)
        mdm.append(dn if (dn > up and dn > 0) else 0.0)
        tr.append(max(h-l, abs(h-pc), abs(l-pc)))
    def wilder(a):
        s = sum(a[:p]); out = [s]
        for i in range(p, len(a)):
            s = s - s/p + a[i]; out.append(s)
        return out
    atrs, pds, mds = wilder(tr), wilder(pdm), wilder(mdm)
    pdi = [100*pds[i]/atrs[i] if atrs[i] else 0 for i in range(len(atrs))]
    mdi = [100*mds[i]/atrs[i] if atrs[i] else 0 for i in range(len(atrs))]
    dx = [100*abs(pdi[i]-mdi[i])/((pdi[i]+mdi[i]) or 1) for i in range(len(pdi))]
    if len(dx) < p:
        av = sum(dx)/len(dx)
    else:
        av = sum(dx[:p])/p
        for i in range(p, len(dx)):
            av = (av*(p-1)+dx[i])/p
    return av, pdi[-1], mdi[-1]


# ---------------- 指標の「系列版」 ----------------
# EMA/RSI/MACD/BB/ATR/ADX はいずれも causal（そのバーまでの値だけで決まる）。
# よって末尾までまとめて計算した系列の i 番目は、oh[:i+1] で計算し直した値と一致する。
# バックテストでバーごとに全指標を作り直す O(n^2) を O(n) にするための土台。

def rsi_series(v, p):
    out = [None]*len(v)
    if len(v) < p+1:
        return out
    d = [v[i]-v[i-1] for i in range(1, len(v))]
    g = [max(x, 0.0) for x in d]; l = [max(-x, 0.0) for x in d]
    ag, al = sum(g[:p])/p, sum(l[:p])/p
    out[p] = 100.0 if al == 0 else 100.0 - 100.0/(1.0+ag/al)
    for i in range(p, len(d)):
        ag = (ag*(p-1)+g[i])/p; al = (al*(p-1)+l[i])/p
        out[i+1] = 100.0 if al == 0 else 100.0 - 100.0/(1.0+ag/al)
    return out


def atr_series(ohlc, p):
    out = [None]*len(ohlc)
    if len(ohlc) < p+1:
        return out
    trs = []
    for i in range(1, len(ohlc)):
        h, l, _ = ohlc[i]; pc = ohlc[i-1][2]
        trs.append(max(h-l, abs(h-pc), abs(l-pc)))
    a = sum(trs[:p])/p
    out[p] = a
    for i in range(p, len(trs)):
        a = (a*(p-1)+trs[i])/p
        out[i+1] = a
    return out


def adx_series(ohlc, p):
    """各バー末尾での (ADX, +DI, -DI)。データ不足のバーは None（adx()の判定条件と同じ）。"""
    out = [None]*len(ohlc)
    if len(ohlc) < 2*p+1:
        return out
    pdm, mdm, tr = [], [], []
    for i in range(1, len(ohlc)):
        h, l, c = ohlc[i]; ph, pl, pc = ohlc[i-1]
        up, dn = h-ph, pl-l
        pdm.append(up if (up > dn and up > 0) else 0.0)
        mdm.append(dn if (dn > up and dn > 0) else 0.0)
        tr.append(max(h-l, abs(h-pc), abs(l-pc)))
    def wilder(a):
        s = sum(a[:p]); res = [s]
        for i in range(p, len(a)):
            s = s - s/p + a[i]; res.append(s)
        return res
    atrs, pds, mds = wilder(tr), wilder(pdm), wilder(mdm)
    pdi = [100*pds[i]/atrs[i] if atrs[i] else 0 for i in range(len(atrs))]
    mdi = [100*mds[i]/atrs[i] if atrs[i] else 0 for i in range(len(atrs))]
    dx = [100*abs(pdi[i]-mdi[i])/((pdi[i]+mdi[i]) or 1) for i in range(len(pdi))]
    av = sum(dx[:p])/p
    # dx[j] は「oh[:j+p+1] まで見た時点」の値。adx()のガード(2p+1本必要)に合わせて j>=p から出す。
    for j in range(p, len(dx)):
        av = (av*(p-1)+dx[j])/p
        out[j+p] = (av, pdi[j], mdi[j])
    return out


def macd_hist_series(v, f, s, sig):
    """各バーの (ヒストグラム, 1本前のヒストグラム)。データ不足は None。"""
    out = [None]*len(v)
    ef, es = ema_series(v, f), ema_series(v, s)
    idx, vals = [], []
    for i in range(len(v)):
        if ef[i] is not None and es[i] is not None:
            idx.append(i); vals.append(ef[i]-es[i])
    if len(vals) < sig+1:
        return out
    ss = ema_series(vals, sig)
    for j in range(1, len(vals)):
        if ss[j] is None or ss[j-1] is None:
            continue
        out[idx[j]] = (vals[j]-ss[j], vals[j-1]-ss[j-1])
    return out


def bb_series(v, p, k):
    """各バーの (中心線, 標準偏差)。bollinger()と同じ式（丸め誤差も含めて一致させる）。"""
    out = [None]*len(v)
    for i in range(p-1, len(v)):
        w = v[i-p+1:i+1]
        mid = sum(w)/p
        sd = (sum((x-mid)**2 for x in w)/p) ** 0.5
        out[i] = (mid, sd)
    return out


def _compose_score(symbol, price, ef, es, rv, hist, hist_prev, bb_mid, bb_sd, a, adx_val):
    """スコア合成の唯一の実装。ライブ判定(score_pair)とバックテストが必ず同じ式を通る。"""
    adxf = max(clamp(adx_val/40.0, 0.0, 1.0), 0.25)
    ema_sig = clamp((ef-es)/(a if a else 1e-9))
    macd_sig = clamp(hist/(0.6*a if a else 1e-9))
    if hist > hist_prev: macd_sig = clamp(macd_sig+0.1)
    elif hist < hist_prev: macd_sig = clamp(macd_sig-0.1)
    rsi_sig = clamp((rv-50)/50.0)
    bb_sig = clamp((price-bb_mid)/(P["bb"][1]*bb_sd if bb_sd else 1e-9))
    tech = clamp(W_EMA*ema_sig*adxf + W_MACD*macd_sig*adxf + W_RSI*rsi_sig + W_BB*bb_sig)
    fund = clamp(FUND_BIAS.get(symbol, 0.0))
    total = clamp(TECH_W*tech + FUND_W*fund)
    return {"tech": tech, "fund": fund, "total": total,
            "ema_sig": ema_sig, "macd_sig": macd_sig, "bb_sig": bb_sig}


def suggest_tp_sl(a):
    sl_pips = a * P.get("slm", SL_ATR_MULT) / PIP_SIZE
    tp_pips = sl_pips * P.get("tsr", TP_SL_RATIO)
    return (round(tp_pips, 1), round(sl_pips, 1))


def score_pair(symbol, ohlc=None):
    """1回の実行内では同じ足から同じスコアになるのでメモ化する（同一シンボルの再計算をやめる）。"""
    if symbol in _SCORE_CACHE:
        return _SCORE_CACHE[symbol]
    r = _score_pair_uncached(symbol, get_ohlc(symbol) if ohlc is None else ohlc)
    _SCORE_CACHE[symbol] = r
    return r


def _score_pair_uncached(symbol, ohlc):
    closes = [r[2] for r in ohlc]
    if len(closes) < max(P["ema_s"], P["macd"][1], P["adx"]*2) + 2:
        return None
    price = closes[-1]
    ef, es = ema(closes, P["ema_f"]), ema(closes, P["ema_s"])
    rv = rsi(closes, P["rsi"])
    md = macd(closes, *P["macd"])
    bb = bollinger(closes, P["bb"][0], P["bb"][1])
    a = atr(ohlc, P["atr"])
    ax = adx(ohlc, P["adx"])
    if None in (ef, es, rv, a) or md is None or bb is None or ax is None:
        return None
    adx_val, pdi, mdi = ax
    r = _compose_score(symbol, price, ef, es, rv, md[2], md[3], bb[0], bb[3], a, adx_val)
    ema_sig, macd_sig, bb_sig = r["ema_sig"], r["macd_sig"], r["bb_sig"]
    tech, fund, total = r["tech"], r["fund"], r["total"]

    reasons = []
    if abs(ema_sig) > 0.2: reasons.append(f"EMA{'上' if ema_sig>0 else '下'}({P['ema_f']}vs{P['ema_s']})")
    if abs(macd_sig) > 0.2: reasons.append(f"MACDヒスト{'+' if md[2]>0 else '-'}")
    reasons.append(f"RSI{rv:.0f}")
    reasons.append(f"ADX{adx_val:.0f}{'(強)' if adx_val>=25 else '(弱)'}")
    _win = closes[-1-P["adx"]:-1]
    _hh = max(_win) if _win else price; _ll = min(_win) if _win else price
    _method = (("ブレイク" if (price>_hh or price<_ll) else "順張りMA") if adx_val>=25
               else ("レンジ逆張り" if abs(bb_sig)>=0.8 else "様子見"))
    reasons.insert(0, "手法:"+_method)

    th = P["th"]
    side = "買い" if total >= th else ("売り" if total <= -th else None)
    tp_pips, sl_pips = suggest_tp_sl(a)
    return {"price":price, "ef":ef, "es":es, "rsi":round(rv,1), "adx":round(adx_val,1),
            "atr":round(a,4), "tp_pips":tp_pips, "sl_pips":sl_pips,
            "tech":round(tech,3), "fund":round(fund,3), "score":round(total,3),
            "side":side, "reasons":reasons,
            "ef_series":[round(x,3) if x is not None else None for x in ema_series(closes,P["ema_f"])[-CHART_POINTS:]],
            "es_series":[round(x,3) if x is not None else None for x in ema_series(closes,P["ema_s"])[-CHART_POINTS:]],
            "closes":[round(c,3) for c in closes[-CHART_POINTS:]]}


# ---------------- シグナル統計（想定保有時間・TP勝率） ----------------
def get_ohlc_hist(symbol, days, cap):
    """統計用の長めの足。日次キャッシュ経由なので get_ohlc が取った日は取り直さない。"""
    today = datetime.datetime.now(JST).date()
    rows = {}
    for back in range(0, days+2):
        d = today - datetime.timedelta(days=back)
        rows.update(klines_day(symbol, d.strftime("%Y%m%d")))
        if len(rows) >= cap:
            break
    out = [rows[t] for t in sorted(rows)]
    return out[-cap:] if len(out) > cap else out


def _median(a):
    if not a:
        return None
    s = sorted(a); m = len(s)//2
    return s[m] if len(s) % 2 else (s[m-1]+s[m])/2


def compute_signal_stats(symbol):
    """過去バーを歩いて『シグナル→TP/SLのどちらに先に当たったか』を数える。
       指標はバーごとに作り直さず、全バーぶんを1回だけ計算して参照する（O(n^2)→O(n)）。
       判定式は _compose_score に一本化してあるのでライブ判定と必ず一致する。"""
    days = STATS_DAYS.get(MODE, 3); cap = STATS_MAX_BARS.get(MODE, 1000)
    oh = get_ohlc_hist(symbol, days, cap)
    if len(oh) < 120:
        return None
    closes = [r[2] for r in oh]
    warm = max(P["ema_s"], P["macd"][1], P["adx"]*2) + 5
    min_len = max(P["ema_s"], P["macd"][1], P["adx"]*2) + 2
    bar_min = BARMIN.get(P["interval"], 1)
    th = P["th"]
    ef_s = ema_series(closes, P["ema_f"]); es_s = ema_series(closes, P["ema_s"])
    rsi_s = rsi_series(closes, P["rsi"]); md_s = macd_hist_series(closes, *P["macd"])
    bb_s = bb_series(closes, P["bb"][0], P["bb"][1])
    atr_s = atr_series(oh, P["atr"]); adx_s = adx_series(oh, P["adx"])
    wins, losses = [], []
    n = len(oh); i = warm
    while i < n-1:
        side = None
        if i+1 >= min_len:
            ef, es, rv = ef_s[i], es_s[i], rsi_s[i]
            md, bb, a, ax = md_s[i], bb_s[i], atr_s[i], adx_s[i]
            if None not in (ef, es, rv, a) and None not in (md, bb, ax):
                total = _compose_score(symbol, closes[i], ef, es, rv,
                                       md[0], md[1], bb[0], bb[1], a, ax[0])["total"]
                side = "買い" if total >= th else ("売り" if total <= -th else None)
        if not side:
            i += 1; continue
        sl_pips = a * P.get("slm", SL_ATR_MULT) / PIP_SIZE
        tp_pips = sl_pips * P.get("tsr", TP_SL_RATIO)
        entry = closes[i]
        tp = entry + tp_pips*PIP_SIZE if side == "買い" else entry - tp_pips*PIP_SIZE
        sl = entry - sl_pips*PIP_SIZE if side == "買い" else entry + sl_pips*PIP_SIZE
        res = None; xj = None
        for j in range(i+1, n):
            h, l, _ = oh[j]
            if side == "買い":
                if l <= sl:
                    res = "sl"; xj = j; break
                if h >= tp:
                    res = "tp"; xj = j; break
            else:
                if h >= sl:
                    res = "sl"; xj = j; break
                if l <= tp:
                    res = "tp"; xj = j; break
        if res is None:
            break
        (wins if res == "tp" else losses).append(xj - i)
        i = xj + 1
    nn = len(wins) + len(losses)
    if nn < 8:
        return None
    tm = _median(wins); sm = _median(losses)
    return {"n": nn, "tp_winrate": round(len(wins)/nn*100),
            "hold_tp_min": round(tm*bar_min) if tm is not None else None,
            "hold_sl_min": round(sm*bar_min) if sm is not None else None,
            "stats_ts": int(datetime.datetime.now(JST).timestamp()), "stats_mode": MODE}


def load_prev_stats():
    """前回 status.json からキャッシュ済み統計を読む（再計算間隔の節約）。"""
    out = {}
    if not os.path.exists(STATUS_FILE):
        return out
    try:
        prev = read_json(STATUS_FILE) or {}
        for p in prev.get("pairs", []):
            if p.get("stats_ts"):
                out[p["symbol"]] = {"n": p.get("stats_n"), "tp_winrate": p.get("tp_winrate"),
                                    "hold_tp_min": p.get("hold_tp_min"), "hold_sl_min": p.get("hold_sl_min"),
                                    "stats_ts": p.get("stats_ts"), "stats_mode": p.get("stats_mode")}
    except Exception:
        pass
    return out


def gather_stats(prev):
    """新鮮なキャッシュは再利用、古い/無いものだけ再計算する。
       再計算は通貨ごとに独立なので並列に回す（1時間に1回の重い処理をここで畳む）。"""
    stats = {}
    now_ts = int(datetime.datetime.now(JST).timestamp())
    todo = []
    for sym in SYMBOLS:
        c = prev.get(sym)
        stats[sym] = c        # 再計算できなかった時は前回値を残す
        if not (c and c.get("stats_ts") and c.get("stats_mode") == MODE
                and (now_ts - c["stats_ts"] < STATS_TTL_SEC)):
            todo.append(sym)
    if not todo:
        return stats

    def one(sym):
        try:
            return sym, compute_signal_stats(sym)
        except Exception as e:
            warn(f"{sym} 統計計算失敗: {e}", tag=f"stats:{sym}")
            return sym, None
    with ThreadPoolExecutor(max_workers=max(1, API_WORKERS)) as ex:
        for sym, st in ex.map(one, todo):
            if st:
                stats[sym] = st
    return stats


def _htf_closes(symbol, interval, keys):
    """指定キー（日付 or 年）でklinesを取り、openTime->終値 の辞書を返す。"""
    rows = {}
    for extra in keys:
        for t, hlc in klines_day(symbol, extra["date"], interval).items():
            rows[t] = hlc[2]
    return rows


def htf_trend(symbol, interval):
    """上位足のトレンド方向を返す。1=上昇 / -1=下降 / 0=どちらでもない(レンジ)。
       EMA(12/26)の位置関係と、速いEMAの傾きの両方が揃った時だけ方向を確定する。"""
    today = datetime.datetime.now(JST).date()
    ef_p, es_p = MTF_EMA
    need = es_p + 3
    # 4時間足以上は年指定、1時間足以下は日付指定（GMO APIの仕様）
    year_mode = interval in ("4hour", "8hour", "12hour", "1day")
    if year_mode:
        keys = [{"date": str(today.year)}]
    else:
        keys = [{"date": (today - datetime.timedelta(days=b)).strftime("%Y%m%d")} for b in range(0, 5)]
    rows = _htf_closes(symbol, interval, keys)
    # 年指定の足は年明け直後だと当年のバーが足りず、常に0(レンジ)を返してしまう
    # ＝上位足フィルタが黙って効かなくなる。足りない時だけ前年も取りに行く。
    if year_mode and len(rows) < need:
        rows.update(_htf_closes(symbol, interval, [{"date": str(today.year - 1)}]))
    closes = [rows[t] for t in sorted(rows)]
    if len(closes) < need:
        return 0
    ef = ema_series(closes, ef_p); es = ema_series(closes, es_p)
    if ef[-1] > es[-1] and ef[-1] > ef[-2]:
        return 1
    if ef[-1] < es[-1] and ef[-1] < ef[-2]:
        return -1
    return 0


def mtf_view(symbol):
    """ペアの上位足トレンドをまとめて返す。{"1hour":1, "4hour":-1, "label":"1h↑ / 4h↓"}
       1回の実行内では同じ結果になるので、記録簿と画面生成で二重取得しないようメモ化する。"""
    if MTF_MODE == "off":
        return None
    if symbol in _MTF_CACHE:
        return _MTF_CACHE[symbol]
    out = {}
    for tf in MTF_TFS:
        try:
            out[tf] = htf_trend(symbol, tf)
        except Exception as e:
            print(f"[WARN] {symbol} {tf} MTF判定失敗: {e}", file=sys.stderr)
            out[tf] = 0
    arrow = {1: "↑上昇", -1: "↓下降", 0: "→レンジ"}
    short = {"1hour": "1h", "4hour": "4h", "1day": "日足"}
    out["label"] = " / ".join(f"{short.get(tf, tf)}{arrow[out[tf]]}" for tf in MTF_TFS)
    vals = [out[tf] for tf in MTF_TFS]
    # 全上位足が同じ方向に揃っていれば「目線が固定」できている状態
    out["aligned"] = vals[0] if (vals and all(v == vals[0] and v != 0 for v in vals)) else 0
    _MTF_CACHE[symbol] = out
    return out


def load_news_events():
    """news_blackout.json を読み、(country, datetime(JST), title) のリストを返す（1回キャッシュ）。"""
    global _NEWS_CACHE
    if _NEWS_CACHE is not None:
        return _NEWS_CACHE
    out = []
    try:
        if not os.path.exists(NEWS_FILE):
            warn(f"{NEWS_FILE} が無いため指標回避が働きません", tag="news-missing")
        else:
            d = read_json(NEWS_FILE) or {}
            # 日次で取り直す前提のファイル。取得が続けて失敗すると中身が過去の予定だけになり、
            # in_blackout() が常にFalse＝回避が黙って無効化される。古くなったら気づけるようにする。
            gen = str(d.get("generated_at", ""))[:10]
            try:
                age = (datetime.datetime.now(JST).date()
                       - datetime.datetime.strptime(gen, "%Y-%m-%d").date()).days
                if age > NEWS_STALE_DAYS:
                    warn(f"経済指標カレンダーが{age}日前のままです（指標回避が効いていない可能性）",
                         tag="news-stale")
            except ValueError:
                warn(f"{NEWS_FILE} の generated_at を読めません", tag="news-genat")
            for e in d.get("events", []):
                try:
                    t = datetime.datetime.strptime(e["time"], "%Y-%m-%d %H:%M").replace(tzinfo=JST)
                    out.append((e.get("country"), t, e.get("title", "")))
                except Exception:
                    pass
    except Exception as ex:
        warn(f"{NEWS_FILE}読込失敗: {ex}", tag="news-read")
    _NEWS_CACHE = out
    return out


def in_blackout(symbol=None):
    """発表前後 BLACKOUT_MIN 分はエントリー見送り。
       symbol指定時は、そのペアに影響する国(PAIR_COUNTRIES)の指標だけで判定（通貨別）。"""
    now = datetime.datetime.now(JST)
    countries = PAIR_COUNTRIES.get(symbol) if symbol else None
    # 自動取得分（news_blackout.json）
    for c, t, _title in load_news_events():
        if countries is not None and c not in countries:
            continue
        if abs((now - t).total_seconds()) <= BLACKOUT_MIN*60:
            return True
    # 手書き予備分（NEWS_BLACKOUT・全通貨一律・従来互換）
    naive = now.replace(tzinfo=None)
    for s in NEWS_BLACKOUT:
        try:
            t = datetime.datetime.strptime(s, "%Y-%m-%d %H:%M")
            if abs((naive - t).total_seconds()) <= BLACKOUT_MIN*60:
                return True
        except Exception:
            pass
    return False


def upcoming_news(symbol):
    """保有ペアに影響する直近の重要指標を返す。
       発表 WARN_BEFORE_MIN 分前 〜 発表後 BLACKOUT_MIN 分 の範囲にあれば (country, dt, title, 残り分) を返す。"""
    now = datetime.datetime.now(JST)
    countries = PAIR_COUNTRIES.get(symbol, set())
    best = None
    for c, t, title in load_news_events():
        if c not in countries:
            continue
        dmin = (t - now).total_seconds() / 60.0
        if -BLACKOUT_MIN <= dmin <= WARN_BEFORE_MIN:
            if best is None or dmin < best[3]:
                best = (c, t, title, dmin)
    return best


def load_positions():
    if not os.path.exists(POSITIONS_FILE):
        return {"positions": []}
    try:
        data = read_json(POSITIONS_FILE)
        if data is None:
            raise ValueError("読み込めません")
        return data if "positions" in data else {"positions": []}
    except Exception as e:
        print(f"[WARN] positions.json読込失敗: {e}", file=sys.stderr)
        return {"positions": []}


def save_positions(data):
    write_json(POSITIONS_FILE, data)


def _tp_sl_prices(p):
    side = p.get("side", "long"); entry = float(p["entry"])
    tp, sl = p.get("tp"), p.get("sl")
    if tp is None and p.get("tp_pips") is not None:
        tp = entry + float(p["tp_pips"])*PIP_SIZE if side == "long" else entry - float(p["tp_pips"])*PIP_SIZE
    if sl is None and p.get("sl_pips") is not None:
        sl = entry - float(p["sl_pips"])*PIP_SIZE if side == "long" else entry + float(p["sl_pips"])*PIP_SIZE
    return (float(tp) if tp is not None else None, float(sl) if sl is not None else None)


def load_entry_log():
    """追記専用のエントリー記録簿を読む。{"entries":[...]}"""
    if not os.path.exists(ENTRY_LOG_FILE):
        return {"entries": []}
    try:
        d = read_json(ENTRY_LOG_FILE)
        if d is None:
            raise ValueError("読み込めません")
        if isinstance(d.get("entries"), list):
            return d
    except Exception as e:
        print(f"[WARN] {ENTRY_LOG_FILE}読込失敗: {e}", file=sys.stderr)
    return {"entries": []}


def stamp_new_entries(data):
    """新しく登録された保有ポジションに、その時点の判断材料をスタンプして記録簿へ追記する。
       ポジションを『削除』しても記録は残るので、後からGMOのCSVと突き合わせて検証できる。
       突き合わせキー: symbol（銘柄名）＋ entry（建単価）。
       戻り値: 追記した件数。"""
    log = load_entry_log()
    known = {e.get("pos_id") for e in log["entries"] if e.get("pos_id")}
    # 建値でも重複判定（アプリ側でidが振り直された場合の保険）
    known_key = {(e.get("symbol"), e.get("entry")) for e in log["entries"]}
    added = 0
    for p in data.get("positions", []):
        if p.get("status", "open") != "open":
            continue
        pid = p.get("id"); sym = p.get("symbol")
        try:
            entry = float(p.get("entry"))
        except (TypeError, ValueError):
            continue
        if (pid and pid in known) or ((sym, entry) in known_key):
            continue
        sc = score_pair(sym, get_ohlc(sym))
        mtf = mtf_view(sym) or {}
        side = p.get("side", "long")
        want = 1 if side == "long" else -1
        # 判定材料が取れなかった回に 0.0 を書くと「スコア0の弱シグナルで入った」記録が
        # 残り、後の検証（強さ別の成績）が狂う。取れない時は None のまま残す。
        score = sc.get("score") if sc else None
        th = P.get("th", 0.40)
        rec = {
            "pos_id": pid,
            "logged_at": datetime.datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": sym,                      # ← CSVの「銘柄名」と対応
            "side": "買" if side == "long" else "売",
            "entry": entry,                     # ← CSVの「建単価」と対応（突き合わせキー）
            "mode": MODE,
            "score": round(score, 3) if score is not None else None,
            "tech": round(sc["tech"], 3) if sc else None,
            "fund": round(sc["fund"], 3) if sc else None,
            "rsi": sc.get("rsi") if sc else None, "adx": sc.get("adx") if sc else None,
            "strength": ("不明" if score is None else
                         ("強" if abs(score) >= th*STRONG_MULT else
                          ("標準" if abs(score) >= th else "弱"))),
            "mtf_label": mtf.get("label"),
            "mtf_aligned": mtf.get("aligned"),
            # 上位足との関係：順張り / 逆行 / レンジ
            "mtf_vs_entry": ("順張り" if mtf.get("aligned") == want else
                             ("逆行" if mtf.get("aligned") == -want else "レンジ")),
            "news_near": bool(upcoming_news(sym)),
            "tp_pips": p.get("tp_pips"), "sl_pips": p.get("sl_pips"),
        }
        log["entries"].append(rec)
        known.add(pid); known_key.add((sym, entry))
        added += 1
        stxt = f"{score:+.2f}" if score is not None else "取得失敗"
        print(f"[INFO] 記録簿に追記: {sym} {rec['side']} {entry} "
              f"/ スコア{stxt}({rec['strength']}) / 上位足{rec['mtf_vs_entry']}")
    if added:
        log["entries"] = log["entries"][-ENTRY_LOG_MAX:]
        log["updated_at"] = datetime.datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
        try:
            write_json(ENTRY_LOG_FILE, log, indent=1)
        except Exception as e:
            print(f"[WARN] {ENTRY_LOG_FILE}保存失敗: {e}", file=sys.stderr)
            return 0
    return added


def auto_set_levels(data):
    msgs, changed = [], False
    for p in data.get("positions", []):
        if p.get("status", "open") != "open" or not p.get("auto") or p.get("auto_set"):
            continue
        a = atr(get_ohlc(p.get("symbol")), P["atr"]) if p.get("symbol") else None
        if not a:
            continue
        tp_pips, sl_pips = suggest_tp_sl(a)
        p["tp_pips"], p["sl_pips"], p["atr_used"], p["auto_set"] = tp_pips, sl_pips, round(a, 3), True
        changed = True
        side = p.get("side", "long"); entry = float(p["entry"]); tp_pr, sl_pr = _tp_sl_prices(p)
        msgs.append(f"🧭 推奨レベル設定 {p['symbol']} ({'買い' if side=='long' else '売り'})\n"
                    f"  建値:{entry} / ATR:{a:.3f}（{MODE}）\n"
                    f"  TP:+{tp_pips}pips({tp_pr:.3f}) / SL:-{sl_pips}pips({sl_pr:.3f})")
    return msgs, changed


def position_pl(p, ticker):
    sym, side = p.get("symbol"), p.get("side", "long")
    entry, lot = float(p["entry"]), float(p.get("lot", DEFAULT_LOT))
    bid, ask = ticker[sym]["bid"], ticker[sym]["ask"]
    cur = bid if side == "long" else ask
    diff = (cur-entry) if side == "long" else (entry-cur)
    tp_pr, sl_pr = _tp_sl_prices(p)
    return {"id":p.get("id"), "symbol":sym, "side":side, "entry":entry, "lot":lot,
            "current":round(cur,3), "pips":round(diff/PIP_SIZE,1), "yen":round(diff*lot),
            "tp_price":round(tp_pr,3) if tp_pr else None, "sl_price":round(sl_pr,3) if sl_pr else None}


def position_advice(p, ticker, sc, prev_mfe=None):
    """保有中の利確/損切り判定。最高益(MFE)は前回status.json由来の値から更新して返す
       （positions.jsonは書き換えない＝アプリとのコミット競合を避ける）。
       未来予測ではなく、現在価格＋ライブシグナルから『降りるサインが出たか』を判定。"""
    sym = p.get("symbol"); side = p.get("side", "long")
    if sym not in ticker or not sc:
        return None
    entry = float(p["entry"]); bid, ask = ticker[sym]["bid"], ticker[sym]["ask"]
    cur = bid if side == "long" else ask
    d = 1 if side == "long" else -1
    a = sc.get("atr") or 0.0
    score = sc.get("score", 0.0); rsi_v = sc.get("rsi", 50); adx_v = sc.get("adx", 0)
    th = P.get("th", 0.40)   # 新規エントリー閾値。保有中スコアがこれを割る=シグナル弱化
    profit = (cur - entry) * d
    profit_atr = (profit / a) if a else 0.0
    aligned = score * d
    # 最高益(MFE)を更新（保存先はstatus.json側）。
    # 初回は建値だけで初期化すると、その回すでに乗っていた含み益のピークを取りこぼす
    # （＝トレール利確の押し戻し量を過小評価する）ので、現在値も必ず取り込む。
    mfe = entry if prev_mfe is None else prev_mfe
    mfe = max(mfe, cur) if side == "long" else min(mfe, cur)
    retrace = (mfe - cur) if side == "long" else (cur - mfe)
    retrace_atr = (retrace / a) if a else 0.0
    tp_pr, sl_pr = _tp_sl_prices(p)
    hit_tp = tp_pr is not None and ((side == "long" and bid >= tp_pr) or (side == "short" and ask <= tp_pr))
    hit_sl = sl_pr is not None and ((side == "long" and bid <= sl_pr) or (side == "short" and ask >= sl_pr))
    # B: cron実行の合間にTP/SLへ「タッチ」していたかを直近の足の高値/安値で救済（現在値が戻っていても拾う）
    bm = BARMIN.get(P["interval"], 1)
    nb = max(1, -(-TOUCH_LOOKBACK_MIN // bm))
    rec = (get_ohlc(sym) or [])[-nb:]
    if rec:
        hi = max(b[0] for b in rec); lo = min(b[1] for b in rec)
        if side == "long":
            if tp_pr is not None and hi >= tp_pr: hit_tp = True
            if sl_pr is not None and lo <= sl_pr: hit_sl = True
        else:
            if tp_pr is not None and lo <= tp_pr: hit_tp = True
            if sl_pr is not None and hi >= sl_pr: hit_sl = True
    rsi_against = (side == "long" and rsi_v >= 70) or (side == "short" and rsi_v <= 30)
    nw = upcoming_news(sym)   # このペアに効く重要指標が接近していれば手仕舞い検討

    if hit_sl:
        level, label, reason = "cut", "🛑 損切り推奨", "SL到達"
    elif aligned <= -ADV_OPP and profit <= 0:
        level, label, reason = "cut", "🛑 損切り推奨", f"逆シグナル（スコア{score:+.2f}）で含み損"
    elif hit_tp:
        level, label, reason = "take", "🎯 利確推奨", "TP到達"
    elif nw is not None:
        when = f"約{int(nw[3])}分後" if nw[3] >= 0 else f"発表中(±{BLACKOUT_MIN}分)"
        level, label, reason = "watch", "🟡 利確検討", f"まもなく重要指標（{nw[2]}/{when}）"
    elif profit_atr >= PROFIT_ATR and retrace_atr >= TRAIL_ATR:
        level, label, reason = "take", "🎯 利確推奨", f"高値から{retrace_atr:.1f}ATR押し戻し（トレール）"
    elif profit > 0 and aligned <= -ADV_OPP:
        level, label, reason = "take", "🎯 利確推奨", f"利益中に逆シグナル（スコア{score:+.2f}）"
    elif profit > 0 and (aligned < th or rsi_against or adx_v < ADX_WEAK):
        rs = []
        if aligned < th: rs.append(f"シグナル弱化(スコア{score:+.2f}/新規基準{th}未満)")
        if rsi_against: rs.append("RSI過熱")
        if adx_v < ADX_WEAK: rs.append("ADX低下")
        level, label, reason = "watch", "🟡 利確検討", "・".join(rs)
    elif aligned >= ADV_SUPP:
        level, label, reason = "hold", "🟢 ホールド", f"シグナル順方向（スコア{score:+.2f}）"
    else:
        level, label, reason = "watch", "🟡 様子見", "明確なサインなし"
    return {"level": level, "label": label, "reason": reason,
            "score": round(score, 3), "rsi": rsi_v, "adx": adx_v,
            "profit_atr": round(profit_atr, 2), "retrace_atr": round(retrace_atr, 2), "mfe": round(mfe, 3)}


def load_prev_signals():
    """前回 status.json の pairs から各通貨の signal を読む（同じシグナルの再通知＝送りすぎを防ぐ）。"""
    out = {}
    if not os.path.exists(STATUS_FILE):
        return out
    try:
        prev = read_json(STATUS_FILE) or {}
        for pr in prev.get("pairs", []):
            if pr.get("symbol"):
                out[pr["symbol"]] = pr.get("signal")
    except Exception:
        pass
    return out


def load_prev_state():
    """前回 status.json の open_positions から判定レベルとMFEを読む
       （MFE/判定はstatus.jsonに保存＝positions.jsonを毎回書き換えないことで競合を防ぐ）。"""
    out = {}
    if not os.path.exists(STATUS_FILE):
        return out
    try:
        prev = read_json(STATUS_FILE) or {}
        for op in prev.get("open_positions", []):
            if op.get("id"):
                out[op["id"]] = {"adv_level": op.get("adv_level"), "mfe": op.get("mfe")}
    except Exception:
        pass
    return out


def check_positions(data, ticker, prev_state=None):
    """保有ポジションを評価して通知メッセージと判定マップを返す。
       戻り値は (mail_msgs, line_msgs, advice_map, pos_events)。
       メール: 利確/損切り(take/cut) ＋『利確検討』(watch) を、判定が変わった時に送る（取りこぼし防止）。
       LINE : 最重要の take/cut だけに絞る（無料枠の節約）。『様子見(明確なサインなし)』は通知しない。
       MFE/判定はstatus.json側に保存するため、ここではpositions.jsonを書き換えない。"""
    prev_state = prev_state or {}
    mail_msgs, line_msgs = [], []
    pos_events = []          # 実際に通知した内容（メール件名の組み立て用）
    advice_map = {}
    for p in data.get("positions", []):
        if p.get("status", "open") != "open" or p.get("symbol") not in ticker:
            continue
        info = position_pl(p, ticker); side = info["side"]
        prev = prev_state.get(p.get("id"), {})
        sc = score_pair(p["symbol"], get_ohlc(p["symbol"]))
        adv = position_advice(p, ticker, sc, prev.get("mfe"))
        print(f"[INFO] {info['symbol']} {side} 建値{info['entry']} 現在{info['current']} "
              f"{info['pips']:+}pips {info['yen']:+,}円" + (f" [{adv['label']} {adv['reason']}]" if adv else ""))
        if adv:
            adv["symbol"] = info["symbol"]
            advice_map[p.get("id")] = adv
            prev_level = prev.get("adv_level")
            changed = prev_level != adv["level"]
            # 「利確検討(watch)」のうち“利確検討”ラベルだけ拾う（“様子見/明確なサインなし”は除外＝ノイズ抑制）
            is_watch_actionable = adv["level"] == "watch" and "利確検討" in adv["label"]
            if changed and (adv["level"] in ("take", "cut") or is_watch_actionable):
                body = (f"{adv['label']} {info['symbol']} ({'買い' if side=='long' else '売り'})\n"
                        f"  {adv['reason']}\n"
                        f"  建値:{info['entry']} → 現在:{info['current']} / {info['pips']:+}pips / {info['yen']:+,}円")
                tail = ("\n  ※GMOで決済後、アプリに実際の結果を登録してください"
                        if adv["level"] in ("take", "cut") else "")
                # メールには全部（利確/損切り/利確検討）
                mail_msgs.append(body + tail)
                pos_events.append((adv["level"], info["symbol"]))
                # LINEには最重要(take/cut)だけ
                if adv["level"] in ("take", "cut"):
                    line_msgs.append(body + tail)
    return mail_msgs, line_msgs, advice_map, pos_events


def build_status(ticker, data, market_open, stats=None, advice_map=None, prev_signals=None):
    """status.json を書き出し、(通知本文リスト, 通知したシグナルの一覧) を返す。"""
    pairs, notify, sig_events = [], [], []
    any_blackout = False
    now = datetime.datetime.now(JST)
    bar_min = BARMIN.get(P["interval"], 1)
    valid_min = VALID_BARS * bar_min
    for sym in SYMBOLS:
        sc = score_pair(sym, get_ohlc(sym))
        if not sc:
            continue
        blackout = in_blackout(sym)          # 通貨別：このペアに効く指標の発表前後だけ停止
        if blackout:
            any_blackout = True
        nw = upcoming_news(sym)              # このペアに近接する重要指標（画面表示用）
        mtf = mtf_view(sym)                  # 上位足(1h/4h)のトレンド方向
        sig = None if blackout else sc["side"]
        skip_reason = None
        if sig and mtf:
            want = 1 if sig == "買い" else -1
            al = mtf.get("aligned")
            if MTF_MODE == "block_opposite" and al == -want:
                sig = None; skip_reason = "上位足と逆行"
            elif MTF_MODE == "filter" and al != want:
                sig = None; skip_reason = "上位足が順張りでない"
        if sig and HOUR_FILTER_ON and now.hour in BAD_HOURS:
            sig = None; skip_reason = f"{now.hour}時台は除外設定のため見送り"
        bias = "買い優勢" if sc["score"] >= 0 else "売り優勢"

        entry = {}
        if market_open and sig:
            ref = ticker.get(sym, {}).get("bid")
            if ref is not None:
                maxchase = round(sc["sl_pips"] * MAX_CHASE_RATIO, 1)
                limit = round(ref + maxchase*PIP_SIZE, 3) if sig == "買い" else round(ref - maxchase*PIP_SIZE, 3)
                until = now + datetime.timedelta(minutes=valid_min)
                entry = {"entry_ref": round(ref, 3), "entry_limit": limit, "maxchase_pips": maxchase,
                         "valid_minutes": valid_min, "valid_until": until.strftime("%H:%M"),
                         "valid_until_ts": int(until.timestamp()*1000)}

        st = stats.get(sym) if stats else None
        pair = {
            "symbol":sym, "bid":ticker.get(sym,{}).get("bid"), "ask":ticker.get(sym,{}).get("ask"),
            "rsi":sc["rsi"], "adx":sc["adx"], "ema_f":round(sc["ef"],3), "ema_s":round(sc["es"],3),
            "atr":sc["atr"], "tp_pips":sc["tp_pips"], "sl_pips":sc["sl_pips"],
            "score":sc["score"], "tech":sc["tech"], "fund":sc["fund"],
            "signal":sig, "bias":bias, "reasons":sc["reasons"],
            "closes":sc["closes"], "ema_f_series":sc["ef_series"], "ema_s_series":sc["es_series"],
            "blackout": blackout,
            "mtf": mtf,
            "skip_reason": skip_reason,
            "next_news": ({"title": nw[2], "time": nw[1].strftime("%H:%M"),
                           "in_min": int(nw[3]), "country": nw[0]} if nw else None),
            **entry,
        }
        if st and st.get("n"):
            pair.update({"hold_tp_min":st.get("hold_tp_min"), "hold_sl_min":st.get("hold_sl_min"),
                         "tp_winrate":st.get("tp_winrate"), "stats_n":st.get("n"),
                         "stats_ts":st.get("stats_ts"), "stats_mode":st.get("stats_mode")})
        pairs.append(pair)

        if market_open and sig and entry and sig != (prev_signals or {}).get(sym):
            rtxt = " / ".join(sc["reasons"])
            arrow = "以下" if sig == "買い" else "以上"
            strong_th = P["th"] * STRONG_MULT
            if abs(sc["score"]) >= strong_th:
                s_mark = f"⭐強シグナル（{P['th']*STRONG_MULT:.2f}以上）"
            else:
                s_mark = f"⚠️標準シグナル — 過去データでは低スコアほど成績が悪い傾向"
            mtf_txt = ""
            if mtf and mtf.get("label"):
                want = 1 if sig == "買い" else -1
                mark = "✅順張り" if mtf.get("aligned") == want else (
                       "⚠️上位足と逆行" if mtf.get("aligned") == -want else "🟡上位足はレンジ")
                mtf_txt = f"\n  🔭上位足: {mtf['label']} — {mark}"
            stat_txt = ""
            if st and st.get("n"):
                stat_txt = (f"\n  ⏱想定保有: 利確まで約{st.get('hold_tp_min','?')}分 / 損切りまで約{st.get('hold_sl_min','?')}分"
                            f"\n  📊TP勝率 {st.get('tp_winrate')}%（直近{st.get('n')}回）")
            notify.append(f"{'🟢' if sig=='買い' else '🔴'} {sym} {sig}（{MODE}）\n"
                          f"  スコア{sc['score']:+.2f}（テク{sc['tech']:+.2f}/ファンダ{sc['fund']:+.2f}） {s_mark}\n"
                          f"  {rtxt}\n  推奨 TP:+{sc['tp_pips']}pips / SL:-{sc['sl_pips']}pips\n"
                          f"  ▶エントリー目安: 通知価格 {entry['entry_ref']}\n"
                          f"   ・{valid_min}分以内（{entry['valid_until']}まで）\n"
                          f"   ・現在値が {entry['entry_limit']} {arrow}なら可"
                          f"（+{entry['maxchase_pips']}pipsまで追い、超過は見送り）"
                          + mtf_txt + stat_txt)
            sig_events.append((sig, sym))

    open_pos, closed_pos = [], []
    for p in data.get("positions", []):
        if p.get("status","open") == "open" and p.get("symbol") in ticker:
            op = position_pl(p, ticker)
            adv = (advice_map or {}).get(p.get("id"))
            if adv:
                op.update({"adv_level":adv["level"], "adv_label":adv["label"], "adv_reason":adv["reason"],
                           "mfe":adv.get("mfe"), "profit_atr":adv.get("profit_atr")})
            open_pos.append(op)
        elif p.get("status") == "closed":
            closed_pos.append({k:p.get(k) for k in
                ("id","symbol","side","entry","close_price","close_pips","close_yen","close_reason","closed_at")})

    status = {
        "generated_at": datetime.datetime.now(JST).strftime("%Y-%m-%d %H:%M JST"),
        "market_open": market_open, "mode": MODE, "blackout": any_blackout,
        "weights": {"tech": TECH_W, "fund": FUND_W},
        "pairs": pairs, "open_positions": open_pos, "closed_positions": closed_pos[-20:],
    }
    # 異常があった回だけ載せる（正常時にキーを増やして毎回コミットを起こさない）。
    # 画面はこれを読んで「なぜ表示がおかしいか」を出す。
    if _WARNINGS:
        status["warnings"] = list(_WARNINGS)
    write_json(STATUS_FILE, status)
    return notify, sig_events


def save_degraded_status():
    """価格が取れず status.json を作り直せない回。前回の内容はそのまま残し、
       『今おかしい』ことだけを書き足して画面から見えるようにする。
       同じ警告が続く間は書き換えない（無駄なコミットを増やさない）。"""
    if not (_WARNINGS and os.path.exists(STATUS_FILE)):
        return
    st = read_json(STATUS_FILE)
    if not isinstance(st, dict):
        return
    if st.get("degraded") and st.get("warnings") == list(_WARNINGS):
        return
    st["degraded"] = True
    st["degraded_at"] = datetime.datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")
    st["warnings"] = list(_WARNINGS)
    try:
        write_json(STATUS_FILE, st)
    except Exception as e:
        print(f"[WARN] status.json への警告書き込みに失敗: {e}", file=sys.stderr)


def mail_subject(sig_events, pos_events, level_count):
    """件名だけで何が起きたか分かるようにする（スマホの通知を開かずに判断できるように）。"""
    def f(sym):
        return sym.replace("_", "/")
    def pick(lv):
        return [s for l, s in pos_events if l == lv]
    bits = []
    for lv, mark in (("cut", "🛑損切り"), ("take", "🎯利確")):
        got = pick(lv)
        if got:
            bits.append(f"{mark} " + "・".join(f(x) for x in got))
    for sg, mark in (("買い", "🟢買い"), ("売り", "🔴売り")):
        got = [s for g, s in sig_events if g == sg]
        if got:
            bits.append(f"{mark} " + "・".join(f(x) for x in got))
    if pick("watch"):
        bits.append("🟡利確検討 " + "・".join(f(x) for x in pick("watch")))
    if level_count:
        bits.append(f"🧭推奨レベル{level_count}件")
    body = " / ".join(bits) if bits else "シグナル通知"
    if len(body) > 70:
        body = body[:69] + "…"
    return f"【FX/{MODE}】{body}"


def notify_line(text):
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
    if not token:
        print("[INFO] LINE未設定。スキップ"); return
    try:
        r = requests.post("https://api.line.me/v2/bot/message/broadcast",
                          headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                          json={"messages": [{"type": "text", "text": text}]}, timeout=15)
        print(f"[INFO] LINE送信 status={r.status_code} {r.text[:120]}")
    except Exception as e:
        print(f"[WARN] LINE送信失敗: {e}", file=sys.stderr)


def notify_mail(subject, body):
    addr = os.environ.get("GMAIL_ADDRESS"); pw = os.environ.get("GMAIL_APP_PASSWORD")
    to = os.environ.get("MAIL_TO") or addr
    if not (addr and pw):
        print("[INFO] Gmail未設定。スキップ"); return
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"], msg["From"], msg["To"] = subject, addr, to
        msg["Date"] = formatdate(localtime=True)
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as s:
            s.login(addr, pw); s.send_message(msg)
        print("[INFO] メール送信完了")
    except Exception as e:
        print(f"[WARN] メール送信失敗: {e}", file=sys.stderr)


MODE_FILE = "mode.json"
def get_selected_mode():
    """モードの唯一の指示元は mode.json（ダッシュボードのスタイルボタンが書き込む）。
       mode.json が無い/壊れている時だけ env MODE、それも無ければ既定 scalp。
       どこから決めたかをログに必ず出す（設定の取り違えを一目で分かるように）。"""
    try:
        if os.path.exists(MODE_FILE):
            m = (read_json(MODE_FILE) or {}).get("mode", "").lower()
            if m in PARAMS:
                print(f"[INFO] モード採用元: mode.json → {m}")
                return m
            print(f"[WARN] mode.jsonのmode値が不正: '{m}'。env/既定にフォールバック")
    except Exception as e:
        print(f"[INFO] mode.json読込スキップ: {e}")
    src = "env MODE" if os.environ.get("MODE") else "既定"
    print(f"[INFO] モード採用元: {src} → {MODE}（mode.json無し）")
    return MODE


def main():
    global MODE, P, TECH_W, FUND_W
    sel = get_selected_mode()
    if sel != MODE:
        print(f"[INFO] モード切替: {MODE} → {sel}")
    MODE = sel; P = PARAMS.get(MODE, PARAMS["scalp"])
    # スタイル別 テク:ファンダ 比率（短期=テク重視 / スイング=ファンダ重視）
    TECH_W, FUND_W = (0.45, 0.55) if MODE == "swing" else (0.85, 0.15)
    now_str = datetime.datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")
    if os.environ.get("TEST_NOTIFY", "").lower() == "true":
        m = f"✅ テスト通知\n時刻: {now_str}\nLINEとメールの疎通確認です。"
        print(m); notify_line(m); notify_mail("【FX】テスト通知", m); return

    data = load_positions()
    # 外部データは待ち時間が支配的。必要なぶんを最初にまとめて並列取得しておく。
    market_open, ticker = warm_up(
        list(SYMBOLS) + [p.get("symbol") for p in data.get("positions", [])
                         if p.get("status", "open") == "open"])
    prev_stats = load_prev_stats()
    prev_state = load_prev_state()
    prev_signals = load_prev_signals()
    stats = gather_stats(prev_stats)
    m1, c1 = auto_set_levels(data)
    try:
        stamp_new_entries(data)   # 新規ポジションを記録簿へ追記（削除されても残る）
    except Exception as e:
        print(f"[WARN] エントリー記録簿の追記に失敗: {e}", file=sys.stderr)
    m2_mail, m2_line, advice_map, pos_events = check_positions(data, ticker, prev_state)
    if c1:  # positions.jsonの書込はauto_set（新規autoのTP/SL設定）時のみ＝競合を最小化
        save_positions(data)
    if ticker:
        notify, sig_events = build_status(ticker, data, market_open, stats, advice_map, prev_signals)
    else:
        # 価格が1件も取れない回に status.json を書き換えると、画面から価格も保有ポジションも
        # 消えてしまう（bid/ask=null・open_positions=[]）。前回の内容を残し、次回実行で作り直す。
        notify, sig_events = [], []
        warn("価格取得に失敗。status.jsonは更新せず前回の表示を維持する（次回再生成）", tag="skip-status")
        save_degraded_status()   # 表示内容は残したまま「今おかしい」ことだけ画面に伝える
    # m1=推奨レベル設定（情報）, m2=保有中の利確/損切り/利確検討（要判断）, notify=エントリーシグナル
    # LINE: 無料枠オーバー中(LINE_ENABLED=False)は一切送らない。Trueでも保有中の最重要(take/cut)だけ。
    # LINE: シグナル通知だけ。保有中サインは NOTIFY_POSITION_TO_LINE=True の時のみ追加。
    line_parts = (((list(notify) if NOTIFY_ENTRY_TO_LINE else [])
                   + (list(m2_line) if NOTIFY_POSITION_TO_LINE else [])) if LINE_ENABLED else [])
    # メール: 推奨レベル設定 + 保有監視(利確/損切り/利確検討) + エントリー、すべて送る。
    mail_parts = list(m1) + list(m2_mail) + (list(notify) if NOTIFY_ENTRY_TO_MAIL else [])

    if not market_open:
        print(f"[INFO] {now_str} 市場クローズ（エントリー判定スキップ）")
    if not (line_parts or mail_parts):
        print(f"[INFO] {now_str} 通知なし（mode={MODE}）。"); return
    subject = mail_subject(sig_events, pos_events, len(m1))
    # 1行目に要約を置く。LINEの通知プレビューとメール件名の両方が中身で分かるようになる。
    head = f"{subject[len(f'【FX/{MODE}】'):]}\n📊 FX通知 [{MODE}] {now_str}\n\n"
    tail = "\n\n※スコアは目安です。最適値の保証ではなく自己責任で。"
    if line_parts:
        print("[LINE]\n" + "\n\n".join(line_parts))
        notify_line(head + "\n\n".join(line_parts) + tail)
    if mail_parts:
        notify_mail(subject, head + "\n\n".join(mail_parts) + tail)


if __name__ == "__main__":
    main()
