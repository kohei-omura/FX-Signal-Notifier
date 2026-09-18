# -*- coding: utf-8 -*-
"""記録簿(entry_log.json)のモードを、記録されたTP/SL幅から割り出し直す。

なぜ必要か
    2026-09-11 より前、stamp_new_entries は建玉自身のモードではなく
    【その時の運用モード】をそのまま書いていた。運用mtfのまま画面をデイに
    切り替えて入った取引は「mtf」と記録される。この札のままモード別に
    集計すると、比較が黙って壊れる。

何を手がかりにするか
    TP/SL幅はモード固有の係数だけで決まる。手入力ではなく、
    入ったその瞬間に画面が出していた値がそのまま記録されている。
        sl_pips = slm * ATR(そのモードの足) / pip
        tp_pips = sl_pips * tsr
      scalp slm1.00 tsr1.5 ／ day   slm1.30 tsr1.6
      swing slm1.80 tsr1.8 ／ mtf   slm1.95 tsr1.6
    TP/SL比だけで scalp(1.5) と swing(1.8) は確定する。
    day と mtf は比が同じ 1.6 なので、同時刻の status.json が持つ
    15分足ATRと突き合わせ、係数が 1.30 か 1.95 かを見る。

どれくらい信用できるか
    2026-09-11 以降の「建玉自身のモード」が分かっている17件を、
    この方法だけで 17件とも言い当てる（tests参照）。
"""
import bisect
import datetime
import json
import os
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SLM = {"scalp": 1.00, "day": 1.30, "swing": 1.80, "mtf": 1.95}
TSR = {"scalp": 1.5, "day": 1.6, "swing": 1.8, "mtf": 1.6}
RATIO_TOL = 0.06     # TP/SL比の許容。1.5/1.6/1.8 の間隔0.1に対して余裕を持たせない
SLM_TOL = 0.08       # 係数の許容。1.30と1.95は50%離れているので8%で十分絞れる
# mtf は 2026-09-02 14:53 JST の実装が初出。それ以前に mtf の建玉は存在しない。
MTF_BORN = datetime.datetime(2026, 9, 2, 14, 53)


def mode_from_levels(tp_pips, sl_pips, logged_at=None, atr15_pips=None):
    """TP/SL幅からモードを割り出す。分からなければ (None, 理由) を返す。

    atr15_pips は day と mtf を分ける時だけ使う。
    """
    if not tp_pips or not sl_pips:
        return None, "TP/SLが記録されていない"
    r = tp_pips / sl_pips
    pick = min(TSR, key=lambda m: abs(TSR[m] - r))
    if abs(TSR[pick] - r) > RATIO_TOL:
        return None, "TP/SL比 %.3f がどのモードの形とも合わない" % r
    if TSR[pick] != 1.6:
        return pick, "TP/SL比 %.3f" % r
    # ここから day と mtf の切り分け
    if logged_at is not None and logged_at < MTF_BORN:
        return "day", "TP/SL比 %.3f・mtf実装前なのでデイ" % r
    if not atr15_pips:
        return None, "TP/SL比 %.3f（デイかmtf）だが当時の15分ATRが無い" % r
    m = min(("day", "mtf"), key=lambda k: abs(SLM[k] * atr15_pips - sl_pips))
    err = abs(SLM[m] * atr15_pips - sl_pips) / sl_pips
    why = "SL%.1f ÷ 15分ATR%.1f = %.2f（%s の係数 %.2f・誤差%.0f%%）" % (
        sl_pips, atr15_pips, sl_pips / atr15_pips, m, SLM[m], err * 100)
    return (m if err <= SLM_TOL else None), why


class StatusHistory:
    """コミットに残っている status.json を時刻で引けるようにする。"""

    def __init__(self, root=ROOT):
        self.root = root
        out = self._git("log", "--format=%H %ct", "--all", "--", "*status.json")
        seen = set()
        for ln in out.split("\n"):
            if ln.strip():
                h, t = ln.split()
                seen.add((int(t), h))
        self.commits = sorted(seen)
        self.times = [c[0] for c in self.commits]
        self._cache = {}

    def _git(self, *a):
        return subprocess.run(("git",) + a, cwd=self.root,
                              capture_output=True, text=True).stdout

    def _snap(self, h):
        if h not in self._cache:
            try:
                raw = self._git("show", h + ":data/status.json") or \
                      self._git("show", h + ":status.json")
                d = json.loads(raw)
                self._cache[h] = (d.get("mode"),
                                  {p["symbol"]: p.get("atr") for p in d.get("pairs", [])})
            except Exception:
                self._cache[h] = (None, {})
        return self._cache[h]

    def atr15_pips(self, ts_utc, symbol, pip=0.01, spans=(600, 1800, 7200)):
        """その時刻に一番近い、15分足モード(day/mtf)のスナップショットのATR。"""
        i = bisect.bisect_left(self.times, ts_utc)
        for span in spans:
            for d in range(len(self.commits)):
                hit = False
                for j in (i - d, i + d):
                    if not (0 <= j < len(self.commits)):
                        continue
                    if abs(self.times[j] - ts_utc) > span:
                        continue
                    hit = True
                    mode, atr = self._snap(self.commits[j][1])
                    if mode in ("day", "mtf") and atr.get(symbol):
                        return atr[symbol] / pip
                if not hit and d:
                    break
        return None


def recover(path=None, write=True, hist=None):
    path = path or os.path.join(ROOT, "data", "entry_log.json")
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    rows = doc["entries"] if isinstance(doc, dict) else doc
    hist = hist if hist is not None else StatusHistory()
    report, changed = [], 0
    for e in rows:
        try:
            dt = datetime.datetime.strptime(e.get("logged_at", ""), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            dt = None
        atr = None
        if dt is not None and hist is not False:
            ts = int((dt - datetime.timedelta(hours=9))
                     .replace(tzinfo=datetime.timezone.utc).timestamp())
            atr = hist.atr15_pips(ts, e.get("symbol"))
        got, why = mode_from_levels(e.get("tp_pips"), e.get("sl_pips"), dt, atr)
        report.append({"logged_at": e.get("logged_at"), "symbol": e.get("symbol"),
                       "was": e.get("mode"), "was_src": e.get("mode_src"),
                       "got": got, "why": why})
        if got and e.get("mode_src") != "position":
            if e.get("mode") != got or e.get("mode_src") != "recovered":
                changed += 1
            e["mode"] = got
            e["mode_src"] = "recovered"
    if write and changed:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=1)
            f.write("\n")
    return changed, report


if __name__ == "__main__":
    import collections
    import sys
    n, rep = recover(write="--dry-run" not in sys.argv)
    print("書き換え:", n, "件 /", len(rep), "件中")
    print(collections.Counter((r["was"], r["was_src"], r["got"]) for r in rep))
    for r in rep:
        if r["got"] is None:
            print("  割り出せず:", r["logged_at"], r["symbol"], r["was"], "-", r["why"])
        elif r["was"] != r["got"]:
            print("  訂正:", r["logged_at"], r["symbol"], r["was"], "->", r["got"], "-", r["why"])
