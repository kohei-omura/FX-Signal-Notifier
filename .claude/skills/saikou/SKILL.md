---
name: saikou
description: 「最高‼︎」「最高!!」「最高」と送られたら使う。FX-Signal-Notifierの不具合・欠陥の洗い出しと修正、アプリ(index.html/tools.html)の見た目を綺麗で格好良く高品質に磨くこと、現時点で最高性能・最高機能になる追加を、検証つきで一気に行う。
---

# 最高‼︎（FX-Signal-Notifier 総合改善）

「最高‼︎」と送られたら、下の3本柱を順に実行する。むらちゃんの共通ルール（日本語・簡潔・本番に影響する処理はモック確認・変更は小さく分ける）に従う。

## 0. 開始時
- 返答冒頭に「推奨: Opus 5.5 × high / 理由: 横断・本番影響」を1行出す（今のモデルが低ければ「切替推奨」を添え、聞いて待つ）。
- `git fetch origin main` → main の最新に合わせる。`python3 -m unittest tests.test_fx_signal` が通る状態を起点にする（通らなければまずそれが最優先の不具合）。
- `data/status.json`・`data/entry_log.json`・`data/backtest.json`・直近の Actions ログ（`mcp__github__actions_list` / `get_job_logs`）を読み、実運用の事実から始める。

## 1. 不具合・欠陥の改善（最優先）
調べる観点（毎回すべて）:
| 観点 | 見る場所・やり方 |
|---|---|
| 通知の取りこぼし | LINE 429(月200通)・pending再送・quota警告、メール失敗時の再送、🔒利益ロック/想定保有2倍超(メールのみ)の発火条件 |
| 判定のズレ | swing=`judge:"tech"`、`EXIT_RULE`、`lock_stop_r`、同足TP+SLはSL優先、売りはask判定 |
| 時刻・市場 | FX休場(NY金17時〜日17時)の扱い、JST/UTC混在、週末またぎ |
| 状態保存 | status.json `open_positions` の `lock_r`/`overstay`、ワークフローのgit-add一覧に新ファイルがあるか |
| UIと計算の不一致 | index.html のJSパラメータと engine/fx_signal.py の PARAMS が同じか（`tests` の同期テストを活用） |
| 実損益との乖離 | `engine/review_trades.py` の答え合わせ、月・モード・時間帯・売買別の成績 |
修正ルール: 原因を再現するテストを先に書く → 修正 → 全テスト → 小さくコミット。過去の誤り（古い売り無し期間のデータで結論を出す等）を繰り返さない。

## 2. 見た目を綺麗で格好良く高品質に
- 世界観は「濃紺×金」。`index.html` と `tools.html` の「仕上げ層」CSS（`:root` トークン、カード、ヘッダー、fab）を土台にし、両ページを必ず揃える。
- 判定・計算・通知ロジックには触れない（見た目だけ）。数字は `tabular-nums`、押下反応、フォーカス、`prefers-reduced-motion`、スマホ幅(375px)で崩れないこと。
- 変更したら `sw.js` の VERSION を上げる。Playwright(プリインストール)でスマホ幅・PCの両方をスクリーンショットし（`deviceScaleFactor:2`）、目で確認してから確定する。重なり・はみ出し・コントラスト不足を探す。
- 既存のUIテスト（AppPolishTest / ToolsPolishTest）を壊さない。新しい要素にもテストを足す。

## 3. 現時点で最高性能・最高機能の追加
- 追加は「検証できるものだけ」。GMO APIはサンドボックスから届かないため、バックテストは GitHub Actions の `backtest.yml`（workflow_dispatch、`modes` 例: "swing,day,mtf" / "review"）で回し、`git fetch origin main` を見て `backtest.json` の更新を待つ。
- 候補は事前に宣言し、前半/後半の分割・95%信頼区間・R倍数で評価する。数を試して良いものを拾う（過学習）ことはしない。改善が有意でなければ入れない、とはっきり報告する。
- 実運用の穴（損切り後の再エントリー、指標前後、スプレッド、ロット/リスク、保有時間）から、効果が確かめられる順に1〜3個だけ選ぶ。
- 通知を増やす時はLINE枠(月200通)を考え、重要度の低いものはメール専用にする。

## 4. 仕上げ
- `python3 -m unittest tests.test_fx_signal` 全通過を確認（新規ファイルを data_path に足したら RunTestCase の sandbox へ追加）。
- コミットは小さく分け、末尾に Co-Authored-By / Claude-Session 行。`git fetch origin main` → merge（`data/*.json` の衝突は `--theirs`）→ `main` と作業ブランチへ通常 push（force は使わない）。PRは頼まれた時だけ。
- 最後に日本語の表で報告: 直した不具合 / 見た目の変更 / 追加機能 / 検証結果（数字と限界）/ 未解決。強すぎる言い切りはしない。
