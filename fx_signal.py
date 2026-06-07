#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FX Signal Notifier  (FXシグナル通知)
-----------------------------------
GMOコイン 外国為替FX Public API の5分足から、テクニカルシグナルを判定し、
LINE Messaging API と Gmail にリアルタイム（数分おき）で通知する。

⚠️ 重要: これは「売買タイミングの補助情報」を出すだけのツールです。
   未来の値動きを保証する予測ではありません。投資判断・結果はすべて自己責任です。

シグナルロジック（5分足の最新「確定足」で判定）:
  ・買い: ゴールデンクロス(短期SMA>長期SMA) または RSIが30を下から上抜け（売られすぎ脱出）
  ・売り: デッドクロス(短期SMA<長期SMA)   または RSIが70を上から下抜け（買われすぎ脱出）

クロスが起きた足でのみ発火するため、同じシグナルの連投は自然に抑制されます。
"""

import os
import sys
import smtplib
import datetime
from email.mime.text import MIMEText
from email.utils import formatdate
from zoneinfo import ZoneInfo

import requests

# ===================== 設定 =====================
JST = ZoneInfo("Asia/Tokyo")

# GMOが提供する主要な円ペア（ティッカーで存在確認済み）
SYMBOLS = ["USD_JPY", "EUR_JPY", "GBP_JPY", "AUD_JPY"]

INTERVAL   = "5min"   # 足の種類
PRICE_TYPE = "BID"    # BID（売値）基準
SMA_SHORT  = 5        # 短期移動平均（5本=25分）
SMA_LONG   = 20       # 長期移動平均（20本=100分）
RSI_PERIOD = 14
RSI_LOW    = 30       # 売られすぎ
RSI_HIGH   = 70       # 買われすぎ

BASE = "https://forex-api.coin.z.com/public/v1"
# ================================================


# --------------- データ取得 ---------------
def fetch_klines(symbol: str) -> list[float]:
    """昨日+今日(JST)の5分足を結合し、終値リストを時系列順で返す。"""
    today = datetime.datetime.now(JST).date()
    closes: dict[int, float] = {}  # openTime(ms) -> close（重複除去）
    for d in (today - datetime.timedelta(days=1), today):
        url = f"{BASE}/klines"
        params = {"symbol": symbol, "priceType": PRICE_TYPE,
                  "interval": INTERVAL, "date": d.strftime("%Y%m%d")}
        try:
            r = requests.get(url, params=params, timeout=15)
            j = r.json()
            if j.get("status") != 0:
                continue
            for k in j.get("data", []):
                closes[int(k["openTime"])] = float(k["close"])
        except Exception as e:
            print(f"[WARN] {symbol} 取得失敗: {e}", file=sys.stderr)
    return [closes[t] for t in sorted(closes)]


def market_is_open() -> bool:
    try:
        j = requests.get(f"{BASE}/status", timeout=10).json()
        return j.get("data", {}).get("status") == "OPEN"
    except Exception:
        return True  # 取れない時は処理継続


def latest_price(symbol: str) -> str:
    try:
        j = requests.get(f"{BASE}/ticker", timeout=10).json()
        for d in j.get("data", []):
            if d["symbol"] == symbol:
                return d["bid"]
    except Exception:
        pass
    return "-"


# --------------- テクニカル指標 ---------------
def sma(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def rsi(values: list[float], period: int) -> float | None:
    """Wilder方式のRSI（最新値）。"""
    if len(values) < period + 1:
        return None
    deltas = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains  = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


# --------------- シグナル判定 ---------------
def detect_signal(closes: list[float]) -> tuple[str, list[str], float | None] | None:
    """最新確定足でのシグナルを判定。(side, reasons, rsi_now) or None"""
    need = max(SMA_LONG, RSI_PERIOD) + 2
    if len(closes) < need:
        return None

    prev, now = closes[:-1], closes  # 1本前 / 最新
    s_prev, s_now = sma(prev, SMA_SHORT), sma(now, SMA_SHORT)
    l_prev, l_now = sma(prev, SMA_LONG),  sma(now, SMA_LONG)
    r_prev, r_now = rsi(prev, RSI_PERIOD), rsi(now, RSI_PERIOD)
    if None in (s_prev, s_now, l_prev, l_now, r_prev, r_now):
        return None

    buy_reasons, sell_reasons = [], []
    if s_prev <= l_prev and s_now > l_now:
        buy_reasons.append(f"ゴールデンクロス(SMA{SMA_SHORT}↑SMA{SMA_LONG})")
    if s_prev >= l_prev and s_now < l_now:
        sell_reasons.append(f"デッドクロス(SMA{SMA_SHORT}↓SMA{SMA_LONG})")
    if r_prev < RSI_LOW <= r_now:
        buy_reasons.append(f"RSI売られすぎ脱出({RSI_LOW}↑)")
    if r_prev > RSI_HIGH >= r_now:
        sell_reasons.append(f"RSI買われすぎ脱出({RSI_HIGH}↓)")

    if buy_reasons and not sell_reasons:
        return ("買い", buy_reasons, r_now)
    if sell_reasons and not buy_reasons:
        return ("売り", sell_reasons, r_now)
    return None  # シグナルなし / 両方向矛盾はスルー


# --------------- 通知 ---------------
def notify_line(text: str) -> None:
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
    if not token:
        print("[INFO] LINEトークン未設定。LINE通知スキップ")
        return
    try:
        r = requests.post(
            "https://api.line.me/v2/bot/message/broadcast",
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"},
            json={"messages": [{"type": "text", "text": text}]},
            timeout=15,
        )
        print(f"[INFO] LINE送信 status={r.status_code} {r.text[:120]}")
    except Exception as e:
        print(f"[WARN] LINE送信失敗: {e}", file=sys.stderr)


def notify_mail(subject: str, body: str) -> None:
    addr = os.environ.get("GMAIL_ADDRESS")
    pw   = os.environ.get("GMAIL_APP_PASSWORD")
    to   = os.environ.get("MAIL_TO") or addr
    if not (addr and pw):
        print("[INFO] Gmail設定なし。メール通知スキップ")
        return
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = addr
        msg["To"] = to
        msg["Date"] = formatdate(localtime=True)
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as s:
            s.login(addr, pw)
            s.send_message(msg)
        print("[INFO] メール送信完了")
    except Exception as e:
        print(f"[WARN] メール送信失敗: {e}", file=sys.stderr)


# --------------- メイン ---------------
def main() -> None:
    now_str = datetime.datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")

    if not market_is_open():
        print(f"[INFO] {now_str} 市場クローズ。終了。")
        return

    blocks = []
    for sym in SYMBOLS:
        closes = fetch_klines(sym)
        sig = detect_signal(closes)
        if not sig:
            continue
        side, reasons, r_now = sig
        mark = "🟢" if side == "買い" else "🔴"
        price = latest_price(sym)
        reason_txt = "\n".join(f"  ・{x}" for x in reasons)
        blocks.append(
            f"{mark} {sym} {side}シグナル\n{reason_txt}\n"
            f"  現在値:{price} / RSI:{r_now:.1f}"
        )

    if not blocks:
        print(f"[INFO] {now_str} シグナルなし。")
        return

    body = (
        f"📊 FXシグナル通知 (5分足)\n時刻: {now_str}\n\n"
        + "\n\n".join(blocks)
        + "\n\n※自動判定の補助情報です。売買は必ずご自身の判断・責任で。"
    )
    print(body)
    notify_line(body)
    notify_mail("【FXシグナル】売買タイミング通知", body)


if __name__ == "__main__":
    main()
