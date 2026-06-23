#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FX Carry Navi — 金利差（キャリー）特化ナビ / スイング・ポジション向け
---------------------------------------------------------------------
元のFX Navi(USD/EUR/GBP/AUD円)では扱わない、金利差が大きいクロス円7種を対象。
  TRY/JPY  ZAR/JPY  MXN/JPY  HUF/JPY  NZD/JPY  CAD/JPY  SEK/JPY
判定 = トレンド(テクニカル) × キャリー(金利差/スワップ方向) を、ボラティリティで調整した
「キャリー対リスク(carry-to-risk)」でランキング。4時間足ベースのスイング設計。
GMOコイン外国為替FX Public API + GitHub Actions + LINE/メール + ダッシュボード(carry_status.json)。

⚠️ 金利・スワップは変動します。キャリー取引は急落(アンワインド)・新興国通貨下落の尾部リスクが大。
   特にTRYは超高金利＝高インフレ・減価リスク。表示は判断補助で、利益も最適値も保証しません。自己責任で。
"""
import os, sys, json, math, smtplib, datetime
from email.mime.text import MIMEText
from email.utils import formatdate
from zoneinfo import ZoneInfo
import requests

JST = ZoneInfo("Asia/Tokyo")
BASE = "https://forex-api.coin.z.com/public/v1"
PRICE_TYPE = "BID"
SYMBOLS = ["TRY_JPY","ZAR_JPY","MXN_JPY","HUF_JPY","NZD_JPY","CAD_JPY","SEK_JPY"]

# ===== 政策金利(%)  as of 2026-06-15（要確認・編集可）=====
# 出典: BoJ/CBRT/SARB/Banxico/MNB/RBNZ/BoC/Riksbank 各発表・各種金利表(2026-06)
POLICY_RATES = {"JPY":0.75, "TRY":40.0, "ZAR":7.00, "MXN":6.75, "HUF":6.25, "NZD":2.25, "CAD":2.25, "SEK":1.75}
RATE_ASOF = "2026-06-15"

# ===== スイング設定（4時間足）=====
P = {"interval":"4hour","ema_f":21,"ema_s":55,"rsi":14,"macd":(12,26,9),"bb":(20,2.0),"adx":14,"atr":14}
TREND_TH   = 0.25     # トレンドスコアのしきい値
SL_ATR     = 2.0      # SL = entry - 2.0×ATR(4h)
TP_ATR     = 3.0      # TP = entry + 3.0×ATR  → リスクリワード 1:1.5
HIGH_VOL_PCT = 18.0   # 年率ボラがこれ以上なら「高ボラ」警告
PERIODS_PER_YEAR = 6*252   # 4時間足の年間本数の目安
W_EMA,W_MACD,W_RSI,W_BB = 0.40,0.30,0.15,0.15
CHART_POINTS = 60
STATUS_FILE = "carry_status.json"
_OHLC = {}

# ===== 想定保有・勝率の検証設定 =====
STATS_WINDOW = 500    # 直近何本(4h)のシグナルを検証対象にするか（≒83日）
STATS_LOOKBACK = 220  # 各バーで指標を再計算する際の遡り本数
BAR_HOURS = 4         # 4時間足


# ---------- データ取得（4時間足は date=年 で一括）----------
def get_ohlc(symbol):
    if symbol in _OHLC: return _OHLC[symbol]
    yr = datetime.datetime.now(JST).year
    rows = {}
    for y in (yr, yr-1):
        try:
            j = requests.get(f"{BASE}/klines", timeout=20, params={
                "symbol":symbol,"priceType":PRICE_TYPE,"interval":P["interval"],"date":str(y)}).json()
            if j.get("status")==0:
                for k in j.get("data",[]):
                    rows[int(k["openTime"])] = (float(k["high"]),float(k["low"]),float(k["close"]))
        except Exception as e:
            print(f"[WARN] {symbol} klines失敗: {e}", file=sys.stderr)
        if len(rows) >= 200: break
    out = [rows[t] for t in sorted(rows)]
    _OHLC[symbol] = out
    return out

def fetch_ticker():
    out={}
    try:
        for d in requests.get(f"{BASE}/ticker",timeout=10).json().get("data",[]):
            out[d["symbol"]]={"bid":float(d["bid"]),"ask":float(d["ask"])}
    except Exception as e:
        print(f"[WARN] ticker失敗: {e}",file=sys.stderr)
    return out

def market_is_open():
    try:
        return requests.get(f"{BASE}/status",timeout=10).json().get("data",{}).get("status")=="OPEN"
    except Exception:
        return True


# ---------- 指標 ----------
def clamp(x,lo=-1.0,hi=1.0): return max(lo,min(hi,x))
def ema_series(v,p):
    if len(v)<p: return [None]*len(v)
    k=2/(p+1); out=[None]*len(v); e=sum(v[:p])/p; out[p-1]=e
    for i in range(p,len(v)): e=v[i]*k+e*(1-k); out[i]=e
    return out
def ema(v,p):
    s=ema_series(v,p); return s[-1] if s else None
def rsi(v,p):
    if len(v)<p+1: return None
    d=[v[i]-v[i-1] for i in range(1,len(v))]
    g=[max(x,0.0) for x in d]; l=[max(-x,0.0) for x in d]
    ag,al=sum(g[:p])/p,sum(l[:p])/p
    for i in range(p,len(d)): ag=(ag*(p-1)+g[i])/p; al=(al*(p-1)+l[i])/p
    return 100.0 if al==0 else 100.0-100.0/(1.0+ag/al)
def macd(v,f,s,sig):
    ef,es=ema_series(v,f),ema_series(v,s)
    ml=[(ef[i]-es[i]) if ef[i] is not None and es[i] is not None else None for i in range(len(v))]
    vals=[m for m in ml if m is not None]
    if len(vals)<sig+1: return None
    ss=ema_series(vals,sig)
    if ss[-1] is None or ss[-2] is None: return None
    return vals[-1],ss[-1],vals[-1]-ss[-1],vals[-2]-ss[-2]
def bollinger(v,p,k):
    if len(v)<p: return None
    w=v[-p:]; mid=sum(w)/p; sd=(sum((x-mid)**2 for x in w)/p)**0.5
    return mid,mid+k*sd,mid-k*sd,sd
def atr(o,p):
    if len(o)<p+1: return None
    tr=[]
    for i in range(1,len(o)):
        h,l,_=o[i]; pc=o[i-1][2]; tr.append(max(h-l,abs(h-pc),abs(l-pc)))
    a=sum(tr[:p])/p
    for i in range(p,len(tr)): a=(a*(p-1)+tr[i])/p
    return a
def adx(o,p):
    if len(o)<2*p+1: return None
    pdm,mdm,tr=[],[],[]
    for i in range(1,len(o)):
        h,l,c=o[i]; ph,pl,pc=o[i-1]; up,dn=h-ph,pl-l
        pdm.append(up if (up>dn and up>0) else 0.0); mdm.append(dn if (dn>up and dn>0) else 0.0)
        tr.append(max(h-l,abs(h-pc),abs(l-pc)))
    def w(a):
        s=sum(a[:p]); o2=[s]
        for i in range(p,len(a)): s=s-s/p+a[i]; o2.append(s)
        return o2
    at,pd,md=w(tr),w(pdm),w(mdm)
    pdi=[100*pd[i]/at[i] if at[i] else 0 for i in range(len(at))]
    mdi=[100*md[i]/at[i] if at[i] else 0 for i in range(len(at))]
    dx=[100*abs(pdi[i]-mdi[i])/((pdi[i]+mdi[i]) or 1) for i in range(len(pdi))]
    if len(dx)<p: av=sum(dx)/len(dx)
    else:
        av=sum(dx[:p])/p
        for i in range(p,len(dx)): av=(av*(p-1)+dx[i])/p
    return av,pdi[-1],mdi[-1]
def annual_vol_pct(closes, n=120):
    c=closes[-(n+1):]
    if len(c)<20: return None
    rets=[math.log(c[i]/c[i-1]) for i in range(1,len(c)) if c[i-1]>0]
    if len(rets)<10: return None
    m=sum(rets)/len(rets); sd=(sum((r-m)**2 for r in rets)/(len(rets)-1))**0.5
    return sd*math.sqrt(PERIODS_PER_YEAR)*100.0


# ---------- キャリー＋トレンド評価 ----------
def evaluate(symbol, o):
    closes=[r[2] for r in o]
    if len(closes) < max(P["ema_s"],P["macd"][1],P["adx"]*2)+5: return None
    price=closes[-1]
    ef,es=ema(closes,P["ema_f"]),ema(closes,P["ema_s"])
    rv=rsi(closes,P["rsi"]); md=macd(closes,*P["macd"]); bb=bollinger(closes,P["bb"][0],P["bb"][1])
    a=atr(o,P["atr"]); ax=adx(o,P["adx"]); vol=annual_vol_pct(closes)
    if None in (ef,es,rv,a,vol) or md is None or bb is None or ax is None: return None
    adx_val,_,_=ax
    adxf=max(clamp(adx_val/40,0,1),0.25)
    ema_sig=clamp((ef-es)/(a if a else 1e-9))
    macd_sig=clamp(md[2]/(0.6*a if a else 1e-9))
    if md[2]>md[3]: macd_sig=clamp(macd_sig+0.1)
    elif md[2]<md[3]: macd_sig=clamp(macd_sig-0.1)
    rsi_sig=clamp((rv-50)/50.0)
    bb_sig=clamp((price-bb[0])/(P["bb"][1]*bb[3] if bb[3] else 1e-9))
    trend=clamp(W_EMA*ema_sig*adxf+W_MACD*macd_sig*adxf+W_RSI*rsi_sig+W_BB*bb_sig)

    foreign=symbol.split("_")[0]
    diff=POLICY_RATES.get(foreign,0.0)-POLICY_RATES.get("JPY",0.0)  # 金利差(年率%)
    carry_dir="買い" if diff>=0 else "売り"                          # 高金利通貨ロング
    ctr=diff/max(vol,1e-9)                                          # キャリー対リスク

    # 総合判定（キャリー方向=買い前提でトレンドが追随しているか）
    if diff<=0:
        verdict, vcls = "対象外", "none"
    elif trend>=TREND_TH:
        verdict, vcls = "買い（順張り）", "buy"
    elif trend<=-TREND_TH:
        verdict, vcls = "様子見（下落中）", "wait"
    else:
        verdict, vcls = "買い寄り（押し目検討）", "soft"
    if diff<=0: vcls="none"

    # TP/SL（価格・%。スケール差を吸収するためpipsでなく価格/％で表現）
    sl_price=price - SL_ATR*a; tp_price=price + TP_ATR*a
    atr_pct=a/price*100
    sl_pct=(sl_price/price-1)*100; tp_pct=(tp_price/price-1)*100
    risk = vol>=HIGH_VOL_PCT
    em = symbol in ("TRY_JPY","ZAR_JPY","MXN_JPY","HUF_JPY")
    if symbol=="TRY_JPY": risk_note="超高金利＝急落・減価リスク特大。少額・長期前提で"
    elif em: risk_note="新興国通貨・尾部(急落)リスクあり"
    elif risk: risk_note="高ボラ"
    else: risk_note=""
    risk = risk or em

    reasons=[]
    reasons.append(f"金利差{diff:+.2f}%")
    if abs(ema_sig)>0.2: reasons.append(f"EMA{'上' if ema_sig>0 else '下'}({P['ema_f']}/{P['ema_s']})")
    if abs(macd_sig)>0.2: reasons.append(f"MACD{'+' if md[2]>0 else '-'}")
    reasons.append(f"RSI{rv:.0f}"); reasons.append(f"ADX{adx_val:.0f}")
    return {"symbol":symbol,"price":price,"ef":ef,"es":es,"rsi":round(rv,1),"adx":round(adx_val,1),
            "atr":round(a,4),"atr_pct":round(atr_pct,2),"vol":round(vol,1),
            "diff":round(diff,2),"foreign_rate":POLICY_RATES.get(foreign),"carry_dir":carry_dir,
            "swap_sign":("+" if diff>=0 else "-"),"ctr":round(ctr,3),
            "trend":round(trend,3),"verdict":verdict,"vcls":vcls,"risk":risk,"risk_note":risk_note,
            "tp_price":round(tp_price,3),"sl_price":round(sl_price,3),
            "tp_pct":round(tp_pct,2),"sl_pct":round(sl_pct,2),"reasons":reasons,
            "closes":[round(c,3) for c in closes[-CHART_POINTS:]],
            "ema_f_series":[round(x,3) if x is not None else None for x in ema_series(closes,P["ema_f"])[-CHART_POINTS:]],
            "ema_s_series":[round(x,3) if x is not None else None for x in ema_series(closes,P["ema_s"])[-CHART_POINTS:]]}


# ---------- 想定保有・TP勝率（過去シグナルをATRのTP/SLで検証）----------
def _median(a):
    if not a: return None
    s=sorted(a); n=len(s); m=n//2
    return s[m] if n%2 else (s[m-1]+s[m])/2.0

def _trend_at(o_slice):
    """与えた4h足スライスの最終バー時点のトレンドスコアとATRを返す（evaluateと同式）"""
    closes=[r[2] for r in o_slice]
    if len(closes) < max(P["ema_s"],P["macd"][1],P["adx"]*2)+5: return None
    ef,es=ema(closes,P["ema_f"]),ema(closes,P["ema_s"])
    rv=rsi(closes,P["rsi"]); md=macd(closes,*P["macd"]); bb=bollinger(closes,P["bb"][0],P["bb"][1])
    a=atr(o_slice,P["atr"]); ax=adx(o_slice,P["adx"])
    if None in (ef,es,rv,a) or md is None or bb is None or ax is None: return None
    adxf=max(clamp(ax[0]/40,0,1),0.25)
    ema_sig=clamp((ef-es)/(a if a else 1e-9))
    macd_sig=clamp(md[2]/(0.6*a if a else 1e-9))
    if md[2]>md[3]: macd_sig=clamp(macd_sig+0.1)
    elif md[2]<md[3]: macd_sig=clamp(macd_sig-0.1)
    rsi_sig=clamp((rv-50)/50.0)
    bb_sig=clamp((closes[-1]-bb[0])/(P["bb"][1]*bb[3] if bb[3] else 1e-9))
    trend=clamp(W_EMA*ema_sig*adxf+W_MACD*macd_sig*adxf+W_RSI*rsi_sig+W_BB*bb_sig)
    return trend, a

def compute_carry_stats(o, diff):
    """過去の『買い（順張り）』シグナル時点でロング→ATRのTP(+3)/SL(-2)のどちらに先に当たったかを集計。
       TP勝率と、TP/SL到達までの中央値(本)を時間に換算して返す。"""
    if diff is None or diff<=0 or len(o)<120: return None
    n=len(o); start=max(120, n-STATS_WINDOW)
    tp_bars=[]; sl_bars=[]
    for i in range(start, n-1):
        tr=_trend_at(o[max(0,i-STATS_LOOKBACK):i+1])
        if not tr: continue
        trend,a=tr
        if trend < TREND_TH or not a: continue          # 「買い（順張り）」成立時のみ検証
        entry=o[i][2]; tp=entry+TP_ATR*a; sl=entry-SL_ATR*a
        for j in range(i+1, n):
            hi,lo,_=o[j]
            if lo<=sl: sl_bars.append(j-i); break        # 同足はSL優先（保守的）
            if hi>=tp: tp_bars.append(j-i); break
    tot=len(tp_bars)+len(sl_bars)
    if tot<5: return None                                # サンプル不足は表示しない
    mt,ms=_median(tp_bars),_median(sl_bars)
    return {"tp_winrate":round(100*len(tp_bars)/tot),
            "hold_tp_h":round(mt*BAR_HOURS) if mt is not None else None,
            "hold_sl_h":round(ms*BAR_HOURS) if ms is not None else None,
            "n":tot}

def fmt_dur(h):
    if h is None: return "—"
    return f"約{h}時間" if h<24 else f"約{round(h/24,1)}日"


# ---------- スワップ（金利）収益の理論概算 ----------
REF_UNITS = 10000   # 概算の基準数量（1万通貨）

def swap_estimate(price, diff, hold_tp_h, tp_price):
    """金利差ベースの理論スワップ概算（1万通貨）。
       ⚠ 実際のスワップ付与額は業者・日々で変動し、スプレッド/税は別。あくまで目安。"""
    if price is None or diff is None or diff <= 0: return None
    notional = price * REF_UNITS
    day_yen = notional * (diff/100.0) / 365.0     # 1日あたり概算スワップ(円)
    day_pct = diff / 365.0                          # 1日あたり概算(%)
    out = {"swap_day_yen": round(day_yen), "swap_day_pct": round(day_pct, 3)}
    if hold_tp_h:
        days = hold_tp_h / 24.0
        swap_yen = day_yen * days
        price_yen = (tp_price - price) * REF_UNITS if tp_price is not None else 0.0
        out.update({
            "swap_tp_yen": round(swap_yen),
            "swap_tp_pct": round(day_pct*days, 2),
            "combo_tp_yen": round(price_yen + swap_yen),
            "combo_tp_pct": round(((tp_price/price-1)*100 if tp_price else 0.0) + day_pct*days, 2),
        })
    return out


def build_status(ticker, market_open):
    pairs=[]; notify=[]
    for sym in SYMBOLS:
        ev=evaluate(sym, get_ohlc(sym))
        if not ev: continue
        ev["bid"]=ticker.get(sym,{}).get("bid"); ev["ask"]=ticker.get(sym,{}).get("ask")
        st=compute_carry_stats(get_ohlc(sym), ev["diff"])
        if st:
            ev["tp_winrate"]=st["tp_winrate"]; ev["hold_tp_h"]=st["hold_tp_h"]
            ev["hold_sl_h"]=st["hold_sl_h"]; ev["stats_n"]=st["n"]
        sw=swap_estimate(ev["price"], ev["diff"], ev.get("hold_tp_h"), ev["tp_price"])
        if sw: ev.update(sw)
        pairs.append(ev)
        if market_open and ev["vcls"]=="buy":
            stxt=(f"\n  ⏱想定保有: 利確まで{fmt_dur(ev.get('hold_tp_h'))} / 損切りまで{fmt_dur(ev.get('hold_sl_h'))}"
                  f" / 📊TP勝率 {ev.get('tp_winrate')}%（直近{ev.get('stats_n')}回）") if ev.get("stats_n") else ""
            swtxt=""
            if ev.get("swap_day_yen") is not None:
                swtxt=f"\n  💰スワップ概算(1万通貨): {ev['swap_day_yen']:+,}円/日"
                if ev.get("combo_tp_yen") is not None:
                    swtxt+=(f"・利確までスワップ約{ev['swap_tp_yen']:+,}円"
                            f"\n  → 価格+スワップ 合算めやす 約{ev['combo_tp_yen']:+,}円（{ev['combo_tp_pct']:+.1f}%）")
            notify.append(f"🟢 {sym} {ev['verdict']}（キャリー）\n"
                          f"  金利差 {ev['diff']:+.2f}%（{ev['foreign_rate']}% vs JPY {POLICY_RATES['JPY']}%）/ スワップ方向: 買いで{ev['swap_sign']}\n"
                          f"  トレンド{ev['trend']:+.2f} / 年率ボラ{ev['vol']:.1f}% / キャリー対リスク{ev['ctr']:.2f}{' ⚠高ボラ' if ev['risk'] else ''}\n"
                          f"  現在{ev['price']} / TP {ev['tp_price']}({ev['tp_pct']:+.1f}%) / SL {ev['sl_price']}({ev['sl_pct']:+.1f}%)"
                          + stxt + swtxt)
    # ランキング（キャリー対リスク降順）
    rank=sorted([p for p in pairs if p["diff"]>0], key=lambda x:x["ctr"], reverse=True)
    ranking=[{"symbol":p["symbol"],"diff":p["diff"],"ctr":p["ctr"],"vol":p["vol"],
              "trend":p["trend"],"verdict":p["verdict"],"vcls":p["vcls"],"risk":p["risk"]} for p in rank]
    status={"generated_at":datetime.datetime.now(JST).strftime("%Y-%m-%d %H:%M JST"),
            "market_open":market_open,"mode":"swing-4H","rate_asof":RATE_ASOF,
            "jpy_rate":POLICY_RATES["JPY"],"pairs":pairs,"ranking":ranking}
    json.dump(status, open(STATUS_FILE,"w",encoding="utf-8"), ensure_ascii=False, indent=2)
    return notify


# ---------- 通知 ----------
def notify_line(text):
    tok=os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
    if not tok: print("[INFO] LINE未設定"); return
    try:
        r=requests.post("https://api.line.me/v2/bot/message/broadcast",
            headers={"Authorization":f"Bearer {tok}","Content-Type":"application/json"},
            json={"messages":[{"type":"text","text":text}]},timeout=15)
        print(f"[INFO] LINE {r.status_code}")
    except Exception as e: print(f"[WARN] LINE失敗 {e}",file=sys.stderr)
def notify_mail(sub,body):
    a=os.environ.get("GMAIL_ADDRESS"); pw=os.environ.get("GMAIL_APP_PASSWORD"); to=os.environ.get("MAIL_TO") or a
    if not(a and pw): print("[INFO] Gmail未設定"); return
    try:
        m=MIMEText(body,"plain","utf-8"); m["Subject"],m["From"],m["To"]=sub,a,to; m["Date"]=formatdate(localtime=True)
        with smtplib.SMTP_SSL("smtp.gmail.com",465,timeout=20) as s: s.login(a,pw); s.send_message(m)
        print("[INFO] mail sent")
    except Exception as e: print(f"[WARN] mail失敗 {e}",file=sys.stderr)


def main():
    now=datetime.datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")
    if os.environ.get("TEST_NOTIFY","").lower()=="true":
        m=f"✅ Carry Naviテスト通知\n{now}"; print(m); notify_line(m); notify_mail("【Carry】テスト",m); return
    market_open=market_is_open(); ticker=fetch_ticker()
    notify=build_status(ticker,market_open)
    if not market_open: print(f"[INFO] {now} 市場クローズ")
    if not notify: print(f"[INFO] {now} 通知なし"); return
    body=(f"💱 FX Carry Navi 通知\n時刻: {now}\n金利基準: {RATE_ASOF}\n\n"+"\n\n".join(notify)
          +"\n\n※金利差・スワップは変動。キャリーは急落リスク大。自己責任で。")
    print(body); notify_line(body); notify_mail("【Carry】買いシグナル",body)

if __name__=="__main__":
    main()
