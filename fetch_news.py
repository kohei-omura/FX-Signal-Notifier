#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
経済指標カレンダー取得 — Forex Factory 週次フィードから高インパクト指標を抽出し、
通貨別ブラックアウト用の news_blackout.json を生成する。
※フィードは頻繁アクセス禁止。このスクリプトは「1日1回」だけ実行する想定（news-calendar.yml）。
"""
import json, datetime, sys, time
from zoneinfo import ZoneInfo
import requests

JST = ZoneInfo("Asia/Tokyo")
FEED = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

# 採用するインパクト。まずは High のみ（誤発動が少ない）。Medium も避けたいなら {"High","Medium"} に。
IMPACTS = {"High"}
# 監視する国/通貨。クロス円なので JPY は常に対象。AUD は最大輸出先の中国(CNY)指標も効く。
WATCH = {"USD", "EUR", "GBP", "AUD", "JPY", "CNY"}
OUT = "news_blackout.json"


RETRIES = 4          # 1日1回しか走らないので、ここで落ちると丸1日カレンダーが古いままになる


def fetch_feed():
    """フィードを取りに行く。失敗したら間隔を空けて数回だけ再試行する。
       日次実行なので取りこぼすと24時間ブラックアウト判定が古いデータのままになる。"""
    last = None
    for n in range(1, RETRIES+1):
        try:
            r = requests.get(FEED, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
            if r.status_code == 429 or r.status_code >= 500:
                last = f"HTTP {r.status_code}"
            else:
                data = r.json()
                if isinstance(data, list):
                    return data
                last = f"想定外の形式: {str(data)[:120]}"
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
        print(f"[WARN] フィード取得失敗({n}/{RETRIES}): {last}", file=sys.stderr)
        if n < RETRIES:
            time.sleep(min(5 * 2 ** (n-1), 40))
    return None


def main():
    data = fetch_feed()
    if data is None:
        print("[ERROR] フィードを取得できませんでした（既存の news_blackout.json は残します）",
              file=sys.stderr)
        sys.exit(1)

    events = []
    for e in data:
        if e.get("impact") not in IMPACTS:
            continue
        c = e.get("country")
        if c not in WATCH:
            continue
        try:
            dt = datetime.datetime.fromisoformat(e["date"]).astimezone(JST)
        except Exception:
            continue  # 終日/未定イベント等は時刻が無いのでスキップ
        events.append({
            "country": c,
            "time": dt.strftime("%Y-%m-%d %H:%M"),
            "title": e.get("title", ""),
        })

    if not events:
        # 0件で上書きすると「重要指標なし」と誤認して回避が効かなくなる。前回分を残す。
        print("[ERROR] 対象イベントが0件でした。既存の news_blackout.json を維持します",
              file=sys.stderr)
        sys.exit(1)

    events.sort(key=lambda x: x["time"])
    out = {
        "generated_at": datetime.datetime.now(JST).strftime("%Y-%m-%d %H:%M JST"),
        "source": "forexfactory(faireconomy) thisweek",
        "impacts": sorted(IMPACTS),
        "events": events,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"[OK] 高インパクト指標 {len(events)}件 を {OUT} に保存")
    for ev in events:
        print(f"  {ev['time']} JST [{ev['country']}] {ev['title']}")


if __name__ == "__main__":
    main()
