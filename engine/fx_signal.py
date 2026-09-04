#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FX Signal & Position Navigator  — スキャル/デイトレ用 加重スコア版
"""

import os, sys, json, math, time, bisect, smtplib, datetime, threading, contextlib
from concurrent.futures import ThreadPoolExecutor
from email.mime.text import MIMEText
from email.utils import formatdate
from zoneinfo import ZoneInfo
import requests

# 生成物・状態ファイルはすべて data/ にまとめる。実行ディレクトリに依存しないよう
# リポジトリのルート（このファイルの1つ上）から解決する。
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


def data_path(name):
    return os.path.join(DATA_DIR, name)

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
    # 上位足(1h/4h)で方向を決め、15分足の押し目/戻りで入る。デイとスイングの併用形。
    # 候補6種の検証で、唯一ノイズ帯を安定して超えた構造（1年781件・素の実力+0.052R）。
    # SL幅はデイの1.5倍。素の実力を保ったままスプレッド比率を4.3%→2.8%に下げられる
    # （4通りの中で最良だったが、統計的に有意な差ではない点は承知のうえで採る）。
    # ※手取りの95%区間は0をまたぐ。プラス確定ではない。
    "mtf":   {"interval":"15min","ema_f":9,"ema_s":21,"rsi":14,"macd":(12,26,9),
              "bb":(20,2.0),"adx":14,"atr":14,"th":0.40,"slm":1.95,"tsr":1.6,
              "rule":"mtf_pullback"},
}
MODE_LABEL = {"scalp": "スキャル", "day": "デイ", "swing": "スイング",
              "mtf": "上位足フォロー"}
# mtf_pullback の押し目/戻り判定。上位足が上昇ならRSIがこの下限以下、下降なら上限以上。
MTF_PULLBACK_RSI = (40, 60)
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
# モード別の統計期間。シグナル頻度が低いモードほど長く取らないと
# 最低件数(8件)に届かず統計が作れない。
# mtf は上位足と押し目が揃ったときだけ入るため 1通貨あたり1日0.7件しか出ない。
# 3日(既定)では平均2件で毎回 None になり、前のモードの統計が残り続けていた。
STATS_DAYS = {"scalp": 3, "day": 8, "swing": 20, "mtf": 45}
STATS_MAX_BARS = {"scalp": 1000, "day": 1000, "swing": 1000, "mtf": 4500}

NEWS_BLACKOUT = []          # 手書きの予備リスト（"YYYY-MM-DD HH:MM"・全通貨一律）。通常は空でOK
BLACKOUT_MIN = 15           # 発表前後この分数はエントリー見送り
WARN_BEFORE_MIN = 60        # 保有ポジションは発表この分前から「まもなく発表」警告
NEWS_FILE = data_path("news_blackout.json")   # news-calendar.yml が毎日生成する自動取得カレンダー
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

# 往復コスト(pips)。index.html の SPREAD と同じ値。バックテストで引かないと
# 「1Rが数pips」のスキャルほど結果が実態より良く出てしまう。
SPREAD_PIPS = {"USD_JPY": 0.2, "EUR_JPY": 0.4, "GBP_JPY": 0.9, "AUD_JPY": 0.5}
DEFAULT_SPREAD_PIPS = 0.5
# 1Rに対するスプレッド比率がこれを超えたら警告する。実測でscalpは0.347だった。
COST_R_WARN = 0.15
# 併用時のリスク管理
BLOCK_DUPLICATE = True      # 同じ通貨・同じ方向を複数モードで同時に持たない
RISK_CAP_PCT = float(os.environ.get("RISK_CAP_PCT", "5"))   # 合計リスクの上限（資金比%）
ACCOUNT_JPY = float(os.environ.get("ACCOUNT_JPY", "0"))     # 資金。0なら上限判定しない

PIP_SIZE = 0.01
DEFAULT_LOT = 10000
POSITIONS_FILE = data_path("positions.json")
STATUS_FILE = data_path("status.json")
# ===== エントリー記録簿 =====
# ポジションを削除しても消えない追記専用ログ。GMOのCSV(銘柄名＋建単価)と後から突き合わせ、
# 「上位足と逆行していたエントリーの成績」などを検証するために使う。
ENTRY_LOG_FILE = data_path("entry_log.json")
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
_SERIES_CACHE = {}        # (symbol, 足, 本数) -> 指標の系列（しきい値スイープで使い回す）
_WARNINGS = []            # 実行中に起きた異常。status.json に載せて画面から見えるようにする
_tls = threading.local()
_warn_lock = threading.Lock()
_kline_lock = threading.Lock()
_kline_locks = {}


@contextlib.contextmanager
def use_mode(mode):
    """一時的に別モードの設定で計算する。

       保有ポジションは『そのポジション自身のモード』の物差しで評価しないといけない。
       例えばスイング建玉(SL=1時間ATR×1.8≒36pips)をデイの15分ATRで見ると、
       含み益の評価が約2.9倍に膨らみ、トレール利確が本来より早く発火する。"""
    global MODE, P, TECH_W, FUND_W
    prev = (MODE, P, TECH_W, FUND_W)
    if mode in PARAMS:
        MODE = mode
        P = PARAMS[mode]
        TECH_W, FUND_W = (0.45, 0.55) if mode == "swing" else (0.85, 0.15)
    try:
        yield MODE
    finally:
        MODE, P, TECH_W, FUND_W = prev


def pos_mode(p):
    """ポジションが属するモード。無ければ現在のモード。"""
    m = (p.get("mode") or p.get("entry_mode") or "").lower()
    return m if m in PARAMS else MODE


def warn(msg, tag=None, surface=True):
    """警告をログに出す。surface=True のものだけ status.json 経由で画面にも出す。

       画面に出すのは「ユーザーが見ているものが実際に劣化した時」だけにする。
       内部のリトライやフォールバックで自動的に埋め合わせが効く失敗まで出すと、
       正常に動いているのにエラーが出ているように見えてしまう。"""
    print(f"[WARN] {msg}", file=sys.stderr)
    if not surface:
        return
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
                # status も messages も無い想定外の応答は、中身そのものを残さないと後で追えない
                detail = j.get("messages") if j.get("messages") is not None else j
                last = f"status={j.get('status')} {str(detail)[:120]}"
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
        if n < retries:
            time.sleep(min(2 ** (n-1), 4))
    _fail_streak[0] += 1
    if _fail_streak[0] == API_BREAKER_AFTER:
        warn(f"API連続失敗{API_BREAKER_AFTER}回。以降は再試行を打ち切って早く抜ける", tag="breaker")
    warn(f"API失敗 {path} {params or ''}: {last}", tag=f"api:{path}", surface=not quiet)
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
        # 1日ぶんが取れなくても前日以前で埋まるので、ここでは画面に出さない（ログには残る）。
        # 例: JSTの日付が変わった直後は当日ぶんがまだ無く、毎日必ず1回は空振りする。
        j = api_get("/klines", {"symbol": symbol, "priceType": PRICE_TYPE,
                                "interval": interval, "date": datestr}, quiet=True)
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
    need = max(P["ema_s"], P["macd"][1], P["adx"]*2, P["atr"]) + CHART_POINTS + 30
    # 足の種類と必要本数ごとに持つ。足が同じでもモードで必要本数が違うことがあり、
    # 一緒にすると本数の足りないリストを使い回してしまう。
    key = (symbol, P["interval"], need)
    if key in _OHLC_CACHE:
        return _OHLC_CACHE[key]
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
    _OHLC_CACHE[key] = out
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


def entry_side(symbol, total, rv, th, aligned=None, pullback=None):
    """そのモードのエントリー判定。ライブもバックテストもここを通す。

       aligned は上位足の一致方向。バックテストは過去の値を渡し、
       ライブは省略して mtf_view() の現在値を使う。
       pullback は押し目/戻りのRSI基準 (lo, hi)。検証で振るためだけの差し替え口で、
       省略すれば運用値 MTF_PULLBACK_RSI を使う。

       mtf_pullback は th（スコアしきい値）を一切見ない。
       そのためスコアのしきい値スイープはこのモードでは何も動かせず、
       代わりにここを振る必要がある（sweep_pullback）。"""
    if P.get("rule") == "mtf_pullback":
        al = aligned if aligned is not None else (mtf_view(symbol) or {}).get("aligned")
        lo, hi = pullback or MTF_PULLBACK_RSI
        if al == 1 and rv is not None and rv <= lo:
            return "買い"
        if al == -1 and rv is not None and rv >= hi:
            return "売り"
        return None
    return "買い" if total >= th else ("売り" if total <= -th else None)


def suggest_tp_sl(a):
    sl_pips = a * P.get("slm", SL_ATR_MULT) / PIP_SIZE
    tp_pips = sl_pips * P.get("tsr", TP_SL_RATIO)
    return (round(tp_pips, 1), round(sl_pips, 1))


def score_pair(symbol, ohlc=None):
    """1回の実行内では同じ足から同じスコアになるのでメモ化する（同一シンボルの再計算をやめる）。"""
    key = (symbol, MODE)
    if key in _SCORE_CACHE:
        return _SCORE_CACHE[key]
    r = _score_pair_uncached(symbol, get_ohlc(symbol) if ohlc is None else ohlc)
    _SCORE_CACHE[key] = r
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
    side = entry_side(symbol, total, rv, th)
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
    return get_ohlc_hist_timed(symbol, days, cap)[1]


def get_ohlc_hist_timed(symbol, days, cap):
    """(時刻リスト, 足リスト) を返す。上位足との突き合わせに時刻が要る。"""
    today = datetime.datetime.now(JST).date()
    rows = {}
    for back in range(0, days+2):
        d = today - datetime.timedelta(days=back)
        rows.update(klines_day(symbol, d.strftime("%Y%m%d")))
        if len(rows) >= cap:
            break
    ts = sorted(rows)
    if len(ts) > cap:
        ts = ts[-cap:]
    return ts, [rows[t] for t in ts]


def _htf_window_closes(symbol, interval, days):
    """バックテスト期間ぶんの上位足終値。klines_day のキャッシュを共有する。"""
    today = datetime.datetime.now(JST).date()
    if interval in ("4hour", "8hour", "12hour", "1day"):
        keys = [{"date": str(today.year)}, {"date": str(today.year - 1)}]
    else:
        keys = [{"date": (today - datetime.timedelta(days=b)).strftime("%Y%m%d")}
                for b in range(0, days + 2)]
    return _htf_closes(symbol, interval, keys)


def htf_aligned_series(symbol, times, days):
    """各時刻における上位足の一致方向(1/-1/0)。htf_trend と同じ判定を過去に当てる。

       ただし『その時点で終値が確定しているバー』だけを使う。ライブは形成中のバーを
       見ているが、過去に対して同じことをすると未来の終値を覗くことになるため。"""
    if MTF_MODE == "off" or not times:
        return [0] * len(times)
    ef_p, es_p = MTF_EMA
    per_tf = []
    for tf in MTF_TFS:
        rows = _htf_window_closes(symbol, tf, days)
        ts = sorted(rows)
        closes = [rows[t] for t in ts]
        ef, es = ema_series(closes, ef_p), ema_series(closes, es_p)
        tr = []
        for i in range(len(ts)):
            if i < 1 or ef[i] is None or es[i] is None or ef[i-1] is None:
                tr.append(0)
            elif ef[i] > es[i] and ef[i] > ef[i-1]:
                tr.append(1)
            elif ef[i] < es[i] and ef[i] < ef[i-1]:
                tr.append(-1)
            else:
                tr.append(0)
        per_tf.append((ts, tr, BARMIN.get(tf, 60) * 60000))
    out = []
    for t in times:
        vals = []
        for ts, tr, dur in per_tf:
            i = bisect.bisect_right(ts, t - dur) - 1      # 終値が確定している最後のバー
            vals.append(tr[i] if 0 <= i < len(tr) else 0)
        out.append(vals[0] if (vals and all(v == vals[0] and v != 0 for v in vals)) else 0)
    return out


def _median(a):
    if not a:
        return None
    s = sorted(a); m = len(s)//2
    return s[m] if len(s) % 2 else (s[m-1]+s[m])/2


def compute_signal_stats(symbol, th_override=None, entry_range=None, rule=None,
                         pullback_override=None):
    """過去バーを歩いて『シグナル→TP/SLのどちらに先に当たったか』を数える。

       th_override: しきい値を差し替えて検証する（「0.60で運用したら」を実際に回すため。
                    事後にスコア帯で切り分けるのとは別物で、エントリー地点も変わる）。
       pullback_override: mtfの押し目/戻りRSI基準 (lo, hi) を差し替える。
                    mtfは th を見ないので、しきい値検証はこちらで行う。
       rule: エントリー判定を差し替える。rule(ctx, i) -> "買い"/"売り"/None。
             別の仮説を、同じ検証手順（コスト控除・信頼区間・アウトオブサンプル）で
             比べるための差し替え口。None なら現行ロジック。
       entry_range: (下限, 上限) を0〜1の割合で指定し、エントリーする区間を限定する。
                    前半で決めたルールを後半で試す（アウトオブサンプル検証）ために使う。
                    指標は常に全期間で計算するので、区切りによる境界の歪みは出ない。
       指標はバーごとに作り直さず、全バーぶんを1回だけ計算して参照する（O(n^2)→O(n)）。
       判定式は _compose_score に一本化してあるのでライブ判定と必ず一致する。"""
    days = STATS_DAYS.get(MODE, 3); cap = STATS_MAX_BARS.get(MODE, 1000)
    times, oh = get_ohlc_hist_timed(symbol, days, cap)
    if len(oh) < 120:
        return None
    closes = [r[2] for r in oh]
    # 本番は上位足と逆行するシグナルを通知しない。ここで同じ条件にしないと
    # 「実際には届かないシグナル」まで混ざった数字になり、比較の意味が無くなる。
    blocked = 0
    warm = max(P["ema_s"], P["macd"][1], P["adx"]*2) + 5
    min_len = max(P["ema_s"], P["macd"][1], P["adx"]*2) + 2
    bar_min = BARMIN.get(P["interval"], 1)
    th = P["th"] if th_override is None else th_override
    # しきい値スイープでは同じ足に対して何度も呼ばれる。指標はしきい値に依存しないので
    # 1回だけ作って使い回す（スイープ18通りぶんの作り直しをやめる）。
    # モードを含める。足が同じでも指標の設定が違えば別の系列になるため。
    ck = (symbol, MODE, P["interval"], len(oh), days)
    cached = _SERIES_CACHE.get(ck)
    if cached is None:
        _atr = atr_series(oh, P["atr"])
        cached = (ema_series(closes, P["ema_f"]), ema_series(closes, P["ema_s"]),
                  rsi_series(closes, P["rsi"]), macd_hist_series(closes, *P["macd"]),
                  bb_series(closes, P["bb"][0], P["bb"][1]),
                  _atr, adx_series(oh, P["adx"]),
                  htf_aligned_series(symbol, times, days), _atr_pct_series(_atr))
        _SERIES_CACHE[ck] = cached
    ef_s, es_s, rsi_s, md_s, bb_s, atr_s, adx_s, aligned_s, atrpct_s = cached
    wins, losses = [], []
    policy_r = {}; band_r = {}; atr_r = {}; atr_skipped = [0]
    n = len(oh); i = warm
    i_end = n - 1
    if entry_range:
        lo, hi = entry_range
        i = max(i, int(n * lo))
        i_end = min(i_end, int(n * hi))
    ctx = {"symbol": symbol, "oh": oh, "closes": closes, "times": times, "th": th,
           "ef": ef_s, "es": es_s, "rsi": rsi_s, "macd": md_s, "bb": bb_s,
           "atr": atr_s, "adx": adx_s, "aligned": aligned_s, "min_len": min_len}
    while i < i_end:
        side = None; total = 0.0
        a = atr_s[i]
        if a is not None and i+1 >= min_len:
            if rule is not None:
                side = rule(ctx, i)
            else:
                ef, es, rv = ef_s[i], es_s[i], rsi_s[i]
                md, bb, ax = md_s[i], bb_s[i], adx_s[i]
                if None not in (ef, es, rv) and None not in (md, bb, ax):
                    total = _compose_score(symbol, closes[i], ef, es, rv,
                                           md[0], md[1], bb[0], bb[1], a, ax[0])["total"]
                    side = entry_side(symbol, total, rv, th, aligned=aligned_s[i],
                                      pullback=pullback_override)
        if not side:
            i += 1; continue
        want = 1 if side == "買い" else -1
        al = aligned_s[i] if i < len(aligned_s) else 0
        if (MTF_MODE == "block_opposite" and al == -want) or \
           (MTF_MODE == "filter" and al != want):
            blocked += 1; i += 1; continue          # 本番と同じく見送る
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
        # 同じシグナルを別の決済ポリシーで回した場合のRも記録する（比較の公平性のため
        # エントリー地点は共通、出口だけ変える）
        sim = _simulate_exit_policies(
            symbol, oh, closes, i, side, entry, tp, sl, sl_pips,
            ef_s, es_s, rsi_s, md_s, bb_s, atr_s, adx_s, th, aligned_s=aligned_s)
        # 往復スプレッドは1Rに対する比率で効く。SLが狭いほど重い。
        cost = SPREAD_PIPS.get(symbol, DEFAULT_SPREAD_PIPS) / sl_pips if sl_pips else 0.0
        for name, r in sim.items():
            policy_r.setdefault(name, []).append((r, cost))
        band_r.setdefault(_score_band(abs(total), th), []).append((sim, cost))
        # 値幅が広い時ほど勝ちやすい、という体感を検証できるようにレジーム別にも残す。
        # 期間の頭は順位を出すだけの本数が無く区分が付かない。黙って落とすと
        # 帯の合計が採用数と合わなくなるので、件数を数えて表に出せるようにする。
        ab = _atr_band(atrpct_s[i] if i < len(atrpct_s) else None)
        if ab:
            slot = atr_r.setdefault(ab, {})
            for name, r in sim.items():
                slot.setdefault(name, []).append((r, cost))
        else:
            atr_skipped[0] += 1
        i = xj + 1
    nn = len(wins) + len(losses)
    if nn < 8:
        return None
    tm = _median(wins); sm = _median(losses)
    out = {"n": nn, "th": round(th, 3), "tp_winrate": round(len(wins)/nn*100),
           "hold_tp_min": round(tm*bar_min) if tm is not None else None,
           "hold_sl_min": round(sm*bar_min) if sm is not None else None,
           "stats_ts": int(datetime.datetime.now(JST).timestamp()), "stats_mode": MODE}
    if P.get("rule") == "mtf_pullback":
        out["pullback"] = list(pullback_override or MTF_PULLBACK_RSI)
    pol = {k: _r_summary(v) for k, v in policy_r.items() if v}
    if pol:
        out["policies"] = pol
    if blocked:
        out["mtf_blocked"] = blocked          # 上位足フィルタで見送った数
    bands = {}
    for band, rows in band_r.items():
        bands[band] = {"n": len(rows)}
        for name in EXIT_POLICIES:
            nets = [x[name] - c for x, c in rows if name in x]
            if nets:
                bands[band][name] = round(sum(nets)/len(nets), 3)   # スプレッド控除後
    if bands:
        out["bands"] = bands
    atr_bands = {}
    for band, per in atr_r.items():
        row = {"n": max(len(v) for v in per.values())}
        for name, rows in per.items():
            if rows:
                row[name] = _r_summary(rows)
        atr_bands[band] = row
    if atr_bands:
        out["atr_bands"] = atr_bands
        out["atr_bands_warmup"] = atr_skipped[0]   # 本数不足で区分が付かなかった件数
    return out


# スコアの強さ別に分ける。しきい値の何倍かで見る（モードが変わっても意味が保てる）。
SCORE_BANDS = ((1.5, "強(1.5倍〜)"), (1.2, "中(1.2〜1.5倍)"), (1.0, "弱(1.0〜1.2倍)"))


# 値幅（ATR）の状態別に分ける。画面の「レジーム」チップと同じ区切りにしてある。
# 直近ATR_PCT_WINDOW本の中でのATRの順位(%)で見るので、通貨やモードが変わっても意味が保てる。
ATR_PCT_WINDOW = 200
ATR_REGIME_BANDS = ((95, "クライマックス(95%〜)"), (80, "拡大(80〜95%)"),
                    (20, "適正(20〜80%)"), (0, "閑散(〜20%)"))


def _atr_pct_series(atr_s, win=ATR_PCT_WINDOW):
    """各バーのATRが『直近win本の中で何%の位置にいるか』。

       その時点までの値だけで計算する（未来のATRを見て順位を付けない）。
       画面の atrRegime() と同じ定義: 自分以下の本数 / 窓の本数。"""
    out = [None] * len(atr_s)
    for i, cur in enumerate(atr_s):
        if cur is None:
            continue
        lo = max(0, i - win + 1)
        w = [v for v in atr_s[lo:i+1] if v is not None]
        if len(w) < 30:
            continue
        out[i] = sum(1 for v in w if v <= cur) / len(w) * 100
    return out


def _atr_band(pct):
    if pct is None:
        return None
    for lo, label in ATR_REGIME_BANDS:
        if pct >= lo:
            return label
    return ATR_REGIME_BANDS[-1][1]


def _score_band(abs_score, th):
    for mult, label in SCORE_BANDS:
        if abs_score >= th * mult:
            return label
    return SCORE_BANDS[-1][1]


# ===== 決済ポリシーの比較 =====
# 実測(406件)で「設計ペイオフ1.6 → 実現1.19」と2割以上目減りしていた。
# 勝ちだけ早く切って負けは-1Rまで走らせると必ずこうなるので、
# 同じシグナルに対して出口だけ変えた場合のRを並べて比較できるようにする。
#   tp_sl        … TP/SLに当たるまで持つ（設計どおり）
#   advice       … 現行の「🎯利確推奨 / 🛑損切り推奨」で降りる（LINE+メールで届く分）
#   advice_watch … 「🟡利確検討」でも降りる（メールに届く分に全部従った場合）
EXIT_POLICIES = ("tp_sl", "advice", "advice_watch")


def _simulate_exit_policies(symbol, oh, closes, i, side, entry, tp, sl, sl_pips,
                            ef_s, es_s, rsi_s, md_s, bb_s, atr_s, adx_s, th,
                            aligned_s=None):
    """1つのシグナルを各決済ポリシーで最後まで回し、R倍率を返す。
       position_advice() と同じ順序・同じ閾値で判定する（指標接近だけは過去再現できないので除外）。"""
    n = len(oh)
    d = 1 if side == "買い" else -1
    risk = sl_pips * PIP_SIZE
    out = {}
    # 設計どおり（TP/SLのみ）
    for j in range(i+1, n):
        h, l, _ = oh[j]
        if (side == "買い" and l <= sl) or (side == "売り" and h >= sl):
            out["tp_sl"] = -1.0; break
        if (side == "買い" and h >= tp) or (side == "売り" and l <= tp):
            out["tp_sl"] = P.get("tsr", TP_SL_RATIO); break
    # アドバイスに従って降りる場合
    for name in ("advice", "advice_watch"):
        use_watch = (name == "advice_watch")
        mfe = entry
        for j in range(i+1, n):
            h, l, c = oh[j]
            # まず価格でTP/SLに触れていないか（ライブと同じく到達を優先）
            if (side == "買い" and l <= sl) or (side == "売り" and h >= sl):
                out[name] = -1.0; break
            if (side == "買い" and h >= tp) or (side == "売り" and l <= tp):
                out[name] = P.get("tsr", TP_SL_RATIO); break
            a, ax, md, bb = atr_s[j], adx_s[j], md_s[j], bb_s[j]
            ef, es, rv = ef_s[j], es_s[j], rsi_s[j]
            if None in (ef, es, rv, a) or None in (md, bb, ax):
                continue
            score = _compose_score(symbol, c, ef, es, rv, md[0], md[1],
                                   bb[0], bb[1], a, ax[0])["total"]
            adx_v = ax[0]
            profit = (c - entry) * d
            aligned = hold_alignment(symbol, {"score": score}, d,
                                     aligned=aligned_s[j] if aligned_s else None)
            mfe = max(mfe, c) if side == "買い" else min(mfe, c)
            retrace = (mfe - c) if side == "買い" else (c - mfe)
            hit = None
            if aligned <= -ADV_OPP and profit <= 0:
                hit = "cut"
            elif a and profit/a >= PROFIT_ATR and retrace/a >= TRAIL_ATR:
                hit = "take"
            elif profit > 0 and aligned <= -ADV_OPP:
                hit = "take"
            elif use_watch and profit > 0 and (
                    aligned < th
                    or (side == "買い" and rv >= 70) or (side == "売り" and rv <= 30)
                    or adx_v < ADX_WEAK):
                hit = "watch"
            if hit:
                out[name] = (profit / risk) if risk else 0.0
                break
    return out


def _r_summary(rows):
    """[(グロスR, 往復コストR)] を 勝率 / 期待R / PF にまとめる。

       avg_r はスプレッド控除後の値。ここを控除しないと、1Rが数pipsのスキャルで
       実態よりずっと良い数字が出てしまう。
       ci_lo/ci_hi は期待Rの95%信頼区間。件数が少ないうちに小さなプラスを
       「優位性あり」と読み違えないための歯止め。"""
    n = len(rows)
    if not n:
        return {"n": 0}
    rs = [r for r, _ in rows]
    nets = [r - c for r, c in rows]
    win = [r for r in rs if r > 0]; lose = [r for r in rs if r <= 0]
    gp = sum(win); gl = -sum(lose)
    avg_win = (gp/len(win)) if win else 0.0
    avg_lose = (gl/len(lose)) if lose else 0.0
    mean = sum(nets)/n
    var = sum((x-mean)**2 for x in nets)/(n-1) if n > 1 else 0.0
    se = (var/n) ** 0.5
    return {"n": n,
            "winrate": round(len(win)/n*100),
            "avg_r": round(mean, 3),                 # スプレッド控除後の期待R
            "avg_r_gross": round(sum(rs)/n, 3),      # 控除前
            "cost_r": round(sum(c for _, c in rows)/n, 3),
            "sd": round(var ** 0.5, 4),              # 通貨をまたいで合算する時に要る
            "ci_lo": round(mean - 1.96*se, 3),
            "ci_hi": round(mean + 1.96*se, 3),
            "payoff": round(avg_win/avg_lose, 2) if avg_lose else None,
            "pf": round(gp/gl, 2) if gl else None}


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
                                    "stats_ts": p.get("stats_ts"), "stats_mode": p.get("stats_mode"),
                                    "policies": p.get("policies"), "bands": p.get("bands"),
                                    "mtf_blocked": p.get("mtf_blocked")}
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
        fresh = (c and c.get("stats_ts") and c.get("stats_mode") == MODE
                 and (now_ts - c["stats_ts"] < STATS_TTL_SEC))
        # 前回値を使うのは「同じモードの」統計だけ。別モードの数字を出すと、
        # 想定保有時間や勝率が実態と何時間もズレて表示される。
        stats[sym] = c if (c and c.get("stats_mode") == MODE) else None
        if not fresh:
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
        # ここで0を返すと「レンジ」と区別が付かず、上位足フィルタが黙って効かなくなる
        warn(f"{symbol} の{interval}足が{len(closes)}本しか取れず、上位足の判定ができません",
             tag=f"mtf:{interval}")
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
        if not p.get("symbol"):
            continue
        # そのポジションのモードの足でATRを取る（デイの物差しでスイング建玉を測らない）
        with use_mode(pos_mode(p)) as m:
            a = atr(get_ohlc(p["symbol"]), P["atr"])
            if not a:
                continue
            tp_pips, sl_pips = suggest_tp_sl(a)
        p["tp_pips"], p["sl_pips"], p["atr_used"], p["auto_set"] = tp_pips, sl_pips, round(a, 3), True
        p["mode"] = m
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


def hold_alignment(symbol, sc, d, aligned=None):
    """保有中に『入った根拠がまだ生きているか』を -1〜+1 で返す。

       モードによって根拠が違うので、判定もそれに合わせる。
       mtf は押し目/戻り（RSIが低い/高い）で入る設計なので、短期スコアは
       構造的に低い。実測では1,688件すべてがエントリー時点で『弱化』扱い、
       79%が『逆シグナル』扱いになっていた。入った瞬間に降りる判定が出るのは
       根拠と判定がズレているため。mtf では入った根拠そのもの（上位足の方向）で見る。
       aligned は過去検証用。省略時は現在の上位足を使う。"""
    if P.get("rule") == "mtf_pullback":
        al = aligned if aligned is not None else (mtf_view(symbol) or {}).get("aligned")
        return (al or 0) * d
    return sc.get("score", 0.0) * d


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
    aligned = hold_alignment(sym, sc, d)
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
        # 建てたときのモードの物差しで見る。混在運用でここを取り違えると、
        # スイング建玉が15分足のATRで測られて早々に利確推奨になる。
        with use_mode(pos_mode(p)) as pmode:
            sc = score_pair(p["symbol"])
            adv = position_advice(p, ticker, sc, prev.get("mfe"))
        if adv:
            adv["mode"] = pmode
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
                mtag = f"[{adv.get('mode', MODE)}] "
                body = (f"{adv['label']} {mtag}{info['symbol']} ({'買い' if side=='long' else '売り'})\n"
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


def open_risk_yen(data):
    """保有中ポジションの合計リスク額（円）。SL幅×数量の合計。"""
    total = 0.0
    for p in data.get("positions", []):
        if p.get("status", "open") != "open":
            continue
        try:
            sl = float(p.get("sl_pips") or 0); lot = float(p.get("lot", DEFAULT_LOT))
        except (TypeError, ValueError):
            continue
        total += sl * PIP_SIZE * lot
    return total


def held_directions(data):
    """保有中の (通貨, 方向) の集合。併用時の重複エントリー防止に使う。"""
    out = set()
    for p in data.get("positions", []):
        if p.get("status", "open") != "open" or not p.get("symbol"):
            continue
        out.add((p["symbol"], "買い" if p.get("side", "long") == "long" else "売り"))
    return out


def build_status(ticker, data, market_open, stats=None, advice_map=None, prev_signals=None):
    """status.json を書き出し、(通知本文リスト, 通知したシグナルの一覧) を返す。"""
    pairs, notify, sig_events = [], [], []
    held = held_directions(data) if BLOCK_DUPLICATE else set()
    risk_now = open_risk_yen(data)
    risk_cap = ACCOUNT_JPY * RISK_CAP_PCT / 100 if ACCOUNT_JPY > 0 else 0
    risk_full = bool(risk_cap and risk_now >= risk_cap)
    if risk_full:
        warn(f"合計リスクが上限に達しています（{risk_now:,.0f}円 / 上限{risk_cap:,.0f}円）。"
             f"新規シグナルは見送ります", tag="risk-cap")
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
        # 併用時、同じ通貨・同じ方向を重ねると同じ値動きへのリスクが倍になる
        if sig and (sym, sig) in held:
            sig = None; skip_reason = "同じ方向を既に保有中（重複を回避）"
        if sig and risk_full:
            sig = None; skip_reason = "合計リスクが上限のため見送り"
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
            # スプレッドが1R(=SL幅)の何割を食うか。SLが狭いほど致命的になる。
            # 実データでは scalp が平均0.347R（勝率が10pt上がっても取り返せない水準）。
            "cost_r": round(SPREAD_PIPS.get(sym, DEFAULT_SPREAD_PIPS) / sc["sl_pips"], 3)
                      if sc.get("sl_pips") else None,
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
            for k in ("policies", "bands", "mtf_blocked"):
                if st.get(k) is not None:
                    pair[k] = st[k]                    # 決済ポリシー比較・スコア帯別（画面が読む）
        pairs.append(pair)

        if market_open and sig and entry and sig != (prev_signals or {}).get(sym):
            rtxt = " / ".join(sc["reasons"])
            arrow = "以下" if sig == "買い" else "以上"
            # mtfは「上位足の方向へ、短期の逆行が一定まで進んだところ」で入る設計。
            # そのため売りシグナルでもスコアはプラスになり、理由欄にも上昇の材料が並ぶ。
            # スコア基準の強弱判定はこのモードでは意味を持たないので、
            # 何を見て入るのかをそのまま書く（受け手が矛盾と誤解しないように）。
            pull = P.get("rule") == "mtf_pullback"
            if pull:
                lo, hi = MTF_PULLBACK_RSI
                need = f"RSI{lo}以下" if sig == "買い" else f"RSI{hi}以上"
                kind = "押し目買い" if sig == "買い" else "戻り売り"
                s_mark = f"↩︎{kind}（{need}で入る設定 → 今RSI{sc['rsi']}）"
            elif abs(sc["score"]) >= P["th"] * STRONG_MULT:
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
            # 通知価格でそのまま建てた場合のOCO価格。pips幅だけだと受け手が毎回暗算する必要があり、
            # その間に相場が動く＝発注レベルがずれる。絶対価格を先に出して取り違えを防ぐ。
            _ref = float(entry["entry_ref"])
            _d = 1 if sig == "買い" else -1
            _tp_ref = _ref + _d * sc["tp_pips"] * PIP_SIZE
            _sl_ref = _ref - _d * sc["sl_pips"] * PIP_SIZE
            # mtfではスコアと理由が「逆方向」に見えるのが正常。先に断っておく。
            if pull:
                _dir = "上昇" if sc["score"] >= 0 else "下降"
                score_txt = (f"  {s_mark}\n"
                             f"  ⚠️短期は{_dir}中（スコア{sc['score']:+.2f}）"
                             f"— 短期の勢いに逆らって入る形が仕様どおりです\n"
                             f"  （参考）{rtxt}\n")
            else:
                score_txt = (f"  スコア{sc['score']:+.2f}"
                             f"（テク{sc['tech']:+.2f}/ファンダ{sc['fund']:+.2f}） {s_mark}\n"
                             f"  {rtxt}\n")
            notify.append(f"{'🟢' if sig=='買い' else '🔴'} {sym} {sig}"
                          f"（{MODE_LABEL.get(MODE, MODE)}）\n"
                          + score_txt
                          + f"  推奨 TP:+{sc['tp_pips']}pips / SL:-{sc['sl_pips']}pips\n"
                          f"  📍{entry['entry_ref']}で建てた場合のOCO → TP {_tp_ref:.3f} / SL {_sl_ref:.3f}\n"
                          f"  ※建値が変われば発注価格も変わります。確定値は登録後の保有カード「発注レベル」を参照\n"
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
                           "mfe":adv.get("mfe"), "profit_atr":adv.get("profit_atr"),
                           "mode":adv.get("mode")})
            open_pos.append(op)
        elif p.get("status") == "closed":
            closed_pos.append({k:p.get(k) for k in
                ("id","symbol","side","entry","close_price","close_pips","close_yen","close_reason","closed_at")})

    costs = [p["cost_r"] for p in pairs if p.get("cost_r") is not None]
    if costs and sum(costs)/len(costs) >= COST_R_WARN:
        warn(f"スプレッドが1リスクの{sum(costs)/len(costs)*100:.0f}%を占めています"
             f"（{MODE}モードはSL幅が狭すぎます）", tag="cost")
    status = {
        "risk": {"open_yen": round(risk_now), "cap_yen": round(risk_cap),
                 "pct": round(risk_now/risk_cap*100) if risk_cap else None,
                 "full": risk_full},
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


def flush_warnings_to_status():
    """通知の送信は status.json を書いた後に行うため、そこで出た警告は
       そのままだと画面に届かない（プロセスが終わって消える）。
       送信後にもう一度 status.json へ書き戻して、届かなかったことを可視化する。"""
    if not (_WARNINGS and os.path.exists(STATUS_FILE)):
        return
    st = read_json(STATUS_FILE)
    if not isinstance(st, dict):
        return
    if st.get("warnings") == list(_WARNINGS):
        return
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
    """LINEへ送る。失敗は必ず warn() を通す。

       以前は stderr に出すだけで status.json にも画面にも出ず、
       送れていないことに気づく手段が無かった。
       ステータスコードも見ていなかったため、401(トークン失効)や
       429(上限超過)でも「送信した」ことになっていた。"""
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
    if not token:
        print("[INFO] LINE未設定。スキップ"); return False
    try:
        r = requests.post("https://api.line.me/v2/bot/message/broadcast",
                          headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                          json={"messages": [{"type": "text", "text": text}]}, timeout=15)
        print(f"[INFO] LINE送信 status={r.status_code} {r.text[:120]}")
        if r.status_code // 100 != 2:
            warn(f"LINE送信が拒否されました (HTTP {r.status_code}): {r.text[:120]}", tag="line-send")
            return False
        return True
    except Exception as e:
        warn(f"LINE送信失敗: {e}", tag="line-send")
        return False


def notify_mail(subject, body):
    addr = os.environ.get("GMAIL_ADDRESS"); pw = os.environ.get("GMAIL_APP_PASSWORD")
    to = os.environ.get("MAIL_TO") or addr
    if not (addr and pw):
        print("[INFO] Gmail未設定。スキップ"); return False
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"], msg["From"], msg["To"] = subject, addr, to
        msg["Date"] = formatdate(localtime=True)
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as s:
            s.login(addr, pw); s.send_message(msg)
        print("[INFO] メール送信完了")
        return True
    except Exception as e:
        # 送れていないことは画面から見えないと分からない（LINEと同じ理由）
        warn(f"メール送信失敗: {e}", tag="mail-send")
        return False


MODE_FILE = data_path("mode.json")
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
    open_pos_list = [p for p in data.get("positions", []) if p.get("status", "open") == "open"]
    market_open, ticker = warm_up(list(SYMBOLS) + [p.get("symbol") for p in open_pos_list])
    # 保有ポジションが別モードなら、その足も先に取っておく（監視で使うため）
    other = {pos_mode(p) for p in open_pos_list} - {MODE}
    for m in other:
        with use_mode(m):
            warm_up([p["symbol"] for p in open_pos_list if pos_mode(p) == m and p.get("symbol")])
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
    # 送信で出た警告を画面へ回す（送れていないことに気づけるように）
    flush_warnings_to_status()


if __name__ == "__main__":
    main()
