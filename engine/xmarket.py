# -*- coding: utf-8 -*-
"""
株×FX 連動（クロスマーケット）— 4アプリ共通モジュール
---------------------------------------------------------------------
株オラクル(日本株) / GLOBE ORACLE(米国株) / FX Navi(主要円ペア) / Carry Navi(金利差) が
それぞれ公開しているJSONを読み、「株の地合い × 円の方向」から今の相場の型を判定する。

  株高＋円安 → リスクオン      株安＋円高 → リスクオフ
  株安＋円安 / 株高＋円高 → 例外（金利が主役の相場）

※このファイルは FX-Signal-Notifier(engine/) / Carry-Navi / kabu-oracle(kabu/) に同じ内容を置く。
  画面側の xmarket.js と同じ式（tests/test_xmarket.py が一致を確認する）。変える時は両方とも直す。
※ここで作るのは「補助情報」だけ。各アプリの判定（シグナル・ランキング・TP/SL）は一切変えない。
※取得に失敗しても例外は出さない（通知に1行足されないだけで、各アプリは従来どおり動く）。
"""
import datetime
import math
import re
import sys

XM_VERSION = "1"

RAW = "https://raw.githubusercontent.com/kohei-omura/"
URLS = {
    "kabu": RAW + "kabu-oracle/main/docs/data.json",        # 日経平均の地合い
    "globe": RAW + "GLOBE-ORACLE/main/docs/prices.json",    # 米国指数・ドル円・米10年金利
    "fx": RAW + "FX-Signal-Notifier/main/data/status.json", # 主要円ペアの上位足(1h/4h)
    "carry": RAW + "Carry-Navi/main/carry_status.json",     # 円の強さ
}
TIMEOUT = 6

# これより古いデータは判定に使わない（止まっているアプリの古い値で誤判定しないため）
STALE_MIN = {"kabu": 5 * 1440, "globe": 4 * 1440, "fx": 90, "carry": 180}

TH = 0.25                 # 株・円とも、これ以上で「方向あり」
US_FULL = 1.0             # 米国株の前日比がこの%で満点(±1)
FX_FULL = 0.8             # ドル円の前日比がこの%で満点(±1)（FX Naviが読めない時の代用）
W_H4, W_H1 = 0.6, 0.4     # 上位足の重み（Carry Navi の円の強さと同じ）
US_W = (("sp", 0.4), ("nq", 0.4), ("dow", 0.2))
SOX_TH = 1.5              # 半導体指数の注記を出す前日比(%)
RATE_BP = 0.05            # 米10年金利の注記を出す前日差(%ポイント)
JP_LABEL = {"上昇基調": 1, "もみ合い": 0, "下落基調": -1}

# code: (lv, 表示名, アイコン, 説明)
REGIMES = {
    "on":         (2, "リスクオン", "☀", "株高＋円安：基本どおりの追い風。株も高金利通貨も買われやすい"),
    "off":        (-2, "リスクオフ", "☔", "株安＋円高：安全資産の円が買われる局面。株もクロス円も下げやすい"),
    "on_soft":    (1, "ややリスクオン", "🌤", "株は強いが、円は方向感なし"),
    "off_soft":   (-1, "ややリスクオフ", "☁", "株は弱いが、円は方向感なし"),
    "yen_weak":   (1, "円安・株は様子見", "⛅", "円安が進行。株は方向感なし"),
    "yen_strong": (-1, "円高・株は様子見", "⛅", "円高が進行。株は方向感なし"),
    "flat":       (0, "中立", "⛅", "株・為替とも方向感なし。連動より個別の材料で動きやすい"),
    "rate_dn":    (-1, "例外：株安＋円安", "⚠", "通常と逆の動き（金利・政策が主役の可能性）。株と為替の連動は当てにしない"),
    "rate_up":    (0, "例外：株高＋円高", "⚠", "通常と逆の動き。輸出株は円高が重荷、内需株が相対的に優位"),
}
RATE_DESC = {
    "rate_dn": "金利上昇が主役（金利差で円安、株は金利高を嫌気）。株と為替の連動は当てにしない",
    "rate_up": "米金利の低下が主役（金利差の縮小で円高、株は金利安を好感）。輸出株は上値が重くなりやすい",
}
ADVICE = {
    "kabu": {
        "on": "輸出株（自動車・機械・電機）に追い風。買い候補は通常どおり",
        "off": "全面安に警戒。新規買いは急がず、保有の損切ラインを確認",
        "on_soft": "地合いは良好。為替の追い風は無いので銘柄の中身で選ぶ",
        "off_soft": "地合いが弱い。新規買いは数量を控えめに",
        "yen_weak": "円安は輸出株の支え。内需株より輸出株が相対的に優位",
        "yen_strong": "円高は輸出株の重荷。内需株（小売・食品・通信）が相対的に優位",
        "flat": "為替・地合いの影響は小さい。決算など個別の材料を重視",
        "rate_dn": "円安でも輸出株が買われにくい局面。金利・政策の発表を確認",
        "rate_up": "輸出株は上値が重く、内需・グロース株が優位になりやすい",
    },
    "globe": {
        "on": "米国株に追い風。円安で円建ての評価額は増えるが、新規のドル買いは割高",
        "off": "株安＋円高で、円建ての評価額は二重に目減りしやすい。損切ラインを確認",
        "on_soft": "株は堅調。為替の影響は小さい",
        "off_soft": "株は軟調。新規買いは数量を控えめに",
        "yen_weak": "円安で円建ての評価額は増える。新規のドル買いは割高",
        "yen_strong": "円高で円建ての評価額は目減り。買い増し・ドル転には有利",
        "flat": "為替の影響は小さい。銘柄の中身で判断",
        "rate_dn": "金利上昇は株の重荷。高PERのグロース株は特に注意",
        "rate_up": "株高でも、円建ての上昇は円高で削られやすい",
    },
    "fx": {
        "on": "クロス円の買いシグナルは地合いと同じ向き。売りシグナルは逆風",
        "off": "クロス円の売りシグナルは地合いと同じ向き。買いシグナルは逆風",
        "on_soft": "株は強く円売り方向がやや有利。ただし為替はまだ追随していない",
        "off_soft": "株が弱く円買いが進みやすい。買いシグナルは慎重に",
        "yen_weak": "円安トレンド。株の後押しは弱いので伸びを欲張らない",
        "yen_strong": "円高トレンド。株の後押しは弱いので伸びを欲張らない",
        "flat": "株・為替とも方向感なし。レンジ前提で無理に追わない",
        "rate_dn": "金利が主役。株との連動は当てにせず、指標・要人発言の時間を確認",
        "rate_up": "金利が主役。株との連動は当てにせず、指標・要人発言の時間を確認",
    },
    "carry": {
        "on": "キャリー（金利差の買い持ち）に追い風",
        "off": "キャリーの巻き戻し（急落）に警戒。新規は見送りか数量を控えめに",
        "on_soft": "株高はキャリーの支え。円の動きを確認",
        "off_soft": "株安はキャリーの逆風になりやすい。円高への転換に注意",
        "yen_weak": "円安はキャリーの追い風",
        "yen_strong": "円高はキャリーの逆風。新規は数量を控えめに",
        "flat": "大きな追い風・逆風なし。金利差とC/Rで判断",
        "rate_dn": "株安＋円安は長続きしにくい。円高への急転（巻き戻し）に注意",
        "rate_up": "円高方向。キャリーには逆風",
    },
}


def _num(v):
    """数値（bool・NaN・無限大を除く）だけ float で返す。それ以外は None。"""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    return v if v == v and abs(v) != float("inf") else None


def _clamp(x, lo=-1.0, hi=1.0):
    return max(lo, min(hi, x))


def _r2(x):
    """小数2桁に丸める（JS側と同じ：四捨五入・0.5は正の方向）。"""
    if x is None:
        return None
    return math.floor(x * 100 + 0.5) / 100


def _f(v, d, sign=False):
    """表示用の固定小数（JS側と1文字も違わないよう、丸めてから整形する）。"""
    k = 10 ** d
    neg = v < 0
    v = math.floor(abs(v) * k + 0.5) / k
    return ("-" if neg else "+" if sign else "") + ("%.*f" % (d, v))


def _dir(v):
    if v is None:
        return 0
    return 1 if v >= TH else -1 if v <= -TH else 0


def parse_ms(s):
    """'2026-11-15 10:55 JST' / '2026.11.15 10:43' / '2026-11-15 10:48'（すべてJST）→ UNIXミリ秒。"""
    if not isinstance(s, str):
        return None
    m = re.match(r"^(\d{4})[-.](\d{2})[-.](\d{2})[ T](\d{2}):(\d{2})", s)
    if not m:
        return None
    try:
        y, mo, d, h, mi = (int(x) for x in m.groups())
        dt = datetime.datetime(y, mo, d, h, mi, tzinfo=datetime.timezone(datetime.timedelta(hours=9)))
        return int(dt.timestamp()) * 1000
    except Exception:
        return None


def _fresh(ts, key, now_ms):
    ms = parse_ms(ts)
    if ms is None:
        return False
    return (now_ms - ms) <= STALE_MIN[key] * 60000


def normalize(kd, gp, fxs, cs, now_ms):
    """4アプリの公開JSON → 判定用の入力。古い・欠けている項目は None。"""
    kd = kd if isinstance(kd, dict) else None
    gp = gp if isinstance(gp, dict) else None
    fxs = fxs if isinstance(fxs, dict) else None
    cs = cs if isinstance(cs, dict) else None
    inp = {"jp": None, "us": None, "fx": None, "yen": None}

    rg = (kd or {}).get("regime")
    if isinstance(rg, dict) and isinstance(rg.get("label"), str) and rg["label"] in JP_LABEL and _fresh(kd.get("generated"), "kabu", now_ms):
        inp["jp"] = {"label": rg["label"], "chg1": _num(rg.get("chg1")), "chg20": _num(rg.get("chg20")),
                     "price": _num(rg.get("price"))}

    idx = (gp or {}).get("idx")
    if isinstance(idx, dict) and _fresh(gp.get("asof"), "globe", now_ms):
        def g(k, f):
            d = idx.get(k)
            return _num(d.get(f)) if isinstance(d, dict) else None
        inp["us"] = {"sp": g("SP500", "chg"), "nq": g("NASDAQ", "chg"), "dow": g("DOW", "chg"),
                     "sox": g("SOX", "chg"), "us10y": g("US10Y", "price"), "us10y_chg": g("US10Y", "chg"),
                     "usdjpy": g("USDJPY", "price"), "usdjpy_chg": g("USDJPY", "chg")}

    if fxs and isinstance(fxs.get("pairs"), list) and _fresh(fxs.get("generated_at"), "fx", now_ms):
        usd, cross, bid = None, [], None
        for p in fxs["pairs"]:
            if not isinstance(p, dict):
                continue
            sym = str(p.get("symbol") or "")
            if not sym.endswith("_JPY"):
                continue
            m = p.get("mtf") if isinstance(p.get("mtf"), dict) else {}
            h1, h4 = _num(m.get("1hour")), _num(m.get("4hour"))
            if h1 is None or h4 is None:
                continue
            v = _clamp(W_H4 * h4 + W_H1 * h1)
            if sym == "USD_JPY":
                usd, bid = v, _num(p.get("bid"))
            else:
                cross.append(v)
        if usd is not None or cross:
            inp["fx"] = {"usd": usd, "cross": (sum(cross) / len(cross)) if cross else None, "bid": bid}

    y = ((cs or {}).get("link") or {}).get("yen") if isinstance((cs or {}).get("link"), dict) else None
    if isinstance(y, dict) and _num(y.get("score")) is not None and _fresh(cs.get("generated_at"), "carry", now_ms):
        inp["yen"] = {"score": _num(y.get("score")), "level": y.get("level") if isinstance(y.get("level"), str) else None}
    return inp


def judge(inp):
    """株の地合い × 円の方向 → 相場の型。判定材料が1つも無ければ None。"""
    inp = inp or {}
    jp, us, fx, yen = inp.get("jp"), inp.get("us"), inp.get("fx"), inp.get("yen")

    # ── 株：日経の地合い（中期）と米国株の前日比（直近）の平均 ──
    jp_s = float(JP_LABEL[jp["label"]]) if jp else None
    us_m = None
    if us:
        s = t = 0.0
        for k, w in US_W:
            if us.get(k) is not None:
                s += us[k] * w
                t += w
        if t:
            us_m = s / t
    us_s = _clamp(us_m / US_FULL) if us_m is not None else None
    parts = [v for v in (jp_s, us_s) if v is not None]
    S = (sum(parts) / len(parts)) if parts else None

    # ── 円（+＝円安）：ドル円とクロス円の上位足 → 無ければ Carry の円の強さ → 無ければドル円の前日比 ──
    Y, ysrc = None, None
    if fx and (fx.get("usd") is not None or fx.get("cross") is not None):
        u, c = fx.get("usd"), fx.get("cross")
        Y = (0.5 * u + 0.5 * c) if (u is not None and c is not None) else (u if u is not None else c)
        ysrc = "fx"
    elif yen and yen.get("score") is not None:
        Y, ysrc = -yen["score"], "carry"
    elif us and us.get("usdjpy_chg") is not None:
        Y, ysrc = us["usdjpy_chg"] / FX_FULL, "globe"
    if Y is not None:
        Y = _clamp(Y)
    if S is None and Y is None:
        return None

    s, y = _dir(S), _dir(Y)
    # ── 金利（例外パターンの説明に使う）──
    rate = 0
    bp = None
    if us and us.get("us10y") is not None and us.get("us10y_chg") is not None and us["us10y_chg"] > -100:
        bp = us["us10y"] - us["us10y"] / (1 + us["us10y_chg"] / 100)
        rate = 1 if bp >= RATE_BP else -1 if bp <= -RATE_BP else 0

    if s > 0 and y > 0: code = "on"
    elif s < 0 and y < 0: code = "off"
    elif s < 0 and y > 0: code = "rate_dn"
    elif s > 0 and y < 0: code = "rate_up"
    elif s > 0: code = "on_soft"
    elif s < 0: code = "off_soft"
    elif y > 0: code = "yen_weak"
    elif y < 0: code = "yen_strong"
    else: code = "flat"
    lv, label, icon, desc = REGIMES[code]
    if (code == "rate_dn" and rate > 0) or (code == "rate_up" and rate < 0):
        desc = RATE_DESC[code]

    notes = []
    if fx and fx.get("usd") is not None and fx.get("cross") is not None:
        du, dc = _dir(fx["usd"]), _dir(fx["cross"])
        if du == 0 and dc < 0:
            notes.append("ドル円は横ばいでもクロス円は下落：ドルと円が同時に買われる形（リスク回避でよく出る）")
        elif du > 0 and dc < 0:
            notes.append("ドル円だけ上昇：円安ではなくドル高が主役")
        elif du < 0 and dc > 0:
            notes.append("ドル円だけ下落：円高ではなくドル安が主役")
    if yen and yen.get("level") == "warn":
        notes.append("Carry Navi の円の強さが警戒水準（%s）：円クロス全体で円が買われている" % _f(yen["score"], 2, True))
    if rate > 0:
        notes.append("米10年金利が上昇（%s%%）：日米金利差の拡大は円安方向、株には重荷" % _f(us["us10y"], 2))
    elif rate < 0:
        notes.append("米10年金利が低下（%s%%）：金利差の縮小は円高方向、株には支え" % _f(us["us10y"], 2))
    if us and us.get("sox") is not None:
        if us["sox"] >= SOX_TH:
            notes.append("半導体指数SOXが%s%%：日米の半導体関連に追い風" % _f(us["sox"], 1, True))
        elif us["sox"] <= -SOX_TH:
            notes.append("半導体指数SOXが%s%%：日米の半導体関連に逆風" % _f(us["sox"], 1, True))
    if jp and us_s is not None and jp_s * _dir(us_s) < 0:
        notes.append("日経の地合い（%s）と米国株の前日の動きが逆：日本固有の材料にも注意" % jp["label"])

    return {
        "code": code, "lv": lv, "label": label, "icon": icon, "desc": desc,
        "s": s, "y": y, "S": _r2(S), "Y": _r2(Y), "ysrc": ysrc, "rate": rate,
        "jp_s": _r2(jp_s), "us_s": _r2(us_s), "us_m": _r2(us_m),
        "stock_txt": "不明" if S is None else ("強い" if s > 0 else "弱い" if s < 0 else "方向感なし"),
        "yen_txt": "不明" if Y is None else ("円安" if y > 0 else "円高" if y < 0 else "横ばい"),
        "notes": notes,
        "advice": {a: ADVICE[a][code] for a in ("kabu", "globe", "fx", "carry")},
    }


def line(app, r):
    """通知に足す2行（判定できなければ空文字）。"""
    if not r or app not in ADVICE:
        return ""
    return ("🌐 株×FX地合い: %s %s（株:%s／円:%s）\n  → %s"
            % (r["icon"], r["label"], r["stock_txt"], r["yen_txt"], r["advice"][app]))


# ---------- 取得（通知を作る時だけ呼ぶ）----------
def _get_json(url):
    try:
        import requests
        r = requests.get(url, timeout=TIMEOUT, params={"t": int(datetime.datetime.now().timestamp())})
        if getattr(r, "status_code", 0) != 200:
            return None
        j = r.json()
        return j if isinstance(j, dict) else None
    except Exception as e:
        print("[xmarket] 取得失敗 %s: %s" % (url, e), file=sys.stderr)
        return None


def snapshot(own=None, now_ms=None):
    """4アプリの公開JSONを読んで判定する。own={"fx": 自分のstatus等} を渡すと、その分は取りに行かない。
       何が起きても例外は出さず、判定できなければ None。"""
    try:
        own = own or {}
        docs = {k: (own[k] if k in own else _get_json(u)) for k, u in URLS.items()}
        if now_ms is None:
            now_ms = int(datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000)
        return judge(normalize(docs["kabu"], docs["globe"], docs["fx"], docs["carry"], now_ms))
    except Exception as e:
        print("[xmarket] 判定失敗: %s" % e, file=sys.stderr)
        return None


def notify_line(app, own=None, now_ms=None):
    """通知の末尾に足す文（先頭に空行つき）。取れない・株か為替の片方しか分からない時は空文字。"""
    try:
        r = snapshot(own, now_ms)
        if not r or r["S"] is None or r["Y"] is None:
            return ""              # 株と為替の両方がそろった時だけ出す（片方だけなら各アプリの表示と同じ内容になる）
        t = line(app, r)
        return ("\n\n" + t) if t else ""
    except Exception:
        return ""


if __name__ == "__main__":      # 手元確認用: python xmarket.py [kabu|globe|fx|carry]
    print(notify_line(sys.argv[1] if len(sys.argv) > 1 else "fx") or "（判定できませんでした）")
