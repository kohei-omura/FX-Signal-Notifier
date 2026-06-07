# FX Signal & Position Navigator 💱

GMOコイン外国為替FX Public API を使い、GitHub Actionsだけで動く、
**通知（LINE/メール）＋画面ダッシュボード**つきのFXシグナル＆利確ナビ。

- **(A) エントリー**: 主要円ペアの買い/売りシグナル＋ATR推奨TP/SLを通知
- **(B) エグジット**: 保有ポジションを監視し、利確/損切り到達で通知＋自動クローズ
- **(C) 推奨自動設定**: `"auto":true` のポジションはATRからTP/SLを自動算出
- **(D) ダッシュボード**: GitHub Pagesで、価格・シグナル・SMAクロス・含み損益を可視化

> ⚠️ ATR推奨は値動きに見合った目安で、未来の最適値や利益を保証する予測ではありません。売買・損益は自己責任です。

---

## ファイル
| ファイル | 役割 |
|---|---|
| `fx_signal.py` | シグナル判定／ポジション監視／status.json書き出し |
| `index.html` | ダッシュボード画面（GitHub Pages） |
| `manifest.webmanifest` / `sw.js` / `icon-*.png` | PWA（ホーム画面アプリ化）用 |
| `status.json` | 最新状態（アプリが毎回自動更新。画面が読み込む） |
| `positions.json` | 保有ポジション登録（あなたが編集） |
| `.github/workflows/fx-signal.yml` | 5分おき自動実行 |
| `requirements.txt` | 依存（requests） |

---

## セットアップ
### 1. push（Public推奨：Actions無制限）
一式をGitHubリポジトリへ。

### 2. 通知の設定
- LINE: 公式アカウント→チャネルアクセストークン(長期)→**自分で友だち追加**
- Gmail: 2段階認証ON→アプリパスワード(16桁)
- Secrets（Settings→Secrets and variables→Actions）:
  `LINE_CHANNEL_ACCESS_TOKEN` / `GMAIL_ADDRESS` / `GMAIL_APP_PASSWORD` / `MAIL_TO`

### 3. ダッシュボードを公開（GitHub Pages）
Settings → **Pages** → Source を「Deploy from a branch」、Branch を `main` / `/ (root)` で保存。
数十秒後、`https://<ユーザー名>.github.io/<リポジトリ名>/` で画面が開きます。
（画面は `status.json` を30秒ごとに読み込み、Actionの更新を反映）

### 4. ホーム画面アプリ化（iPhone）
Pages公開後、iPhoneの **Safari** で `https://<ユーザー名>.github.io/<リポジトリ名>/` を開き、
共有ボタン → **「ホーム画面に追加」**。アイコンが追加され、タップすると
アドレスバーなしの**全画面アプリ**として起動します（オフライン時も直近画面を表示）。
※必ずSafariで開くこと（Chrome等からは全画面PWAになりません）。

### 5. 起動
Actions → Run workflow（疎通確認はテストにチェック）。以降5分おきに自動実行。

---

## ポジション登録（`positions.json`）
### 例1: 自分でpips指定
```json
{ "positions": [
  { "id":"1","symbol":"USD_JPY","side":"long","entry":160.20,
    "lot":10000,"tp_pips":30,"sl_pips":20,"status":"open" }
]}
```
### 例2: ATRにおまかせ（推奨TP/SLを自動セット）
```json
{ "positions": [
  { "id":"2","symbol":"GBP_JPY","side":"short","entry":214.00,
    "lot":10000,"auto":true,"status":"open" }
]}
```
| 項目 | 意味 |
|---|---|
| `side` | `long`=買い / `short`=売り |
| `entry` | 建値 |
| `lot` | 数量(1万=10000、省略時10000) |
| `tp_pips`/`sl_pips` | 利確/損切りpips（円ペア1pips=0.01円） |
| `tp`/`sl` | 絶対価格で指定する場合 |
| `auto` | `true`でATR推奨を自動セット |
| `status` | `open`。到達でアプリが`closed`へ |

到達で通知＋`closed`記録され再通知なし。新規は新しい`id`で追記。

---

## 推奨値(ATR)・損益の計算
- SL = ATR×1.5、TP = ATR×2.0（`ATR_SL_MULT`/`ATR_TP_MULT`で調整）
- 足が5分のためATRは小さめ＝スキャル向け。広げたい場合は倍率増 or `INTERVAL`を`1hour`等へ
- ロング損益=bid−建値 / ショート損益=建値−ask、pips=差÷0.01、円=差×lot

## 注意
- `cron`最短5分・遅延あり（真のリアルタイム不可）。LINE無料枠は月200通。
- `positions.json`と`status.json`はアプリが自動コミット（`contents: write`・設定済み）。
- 価格取得元: GMOコイン外国為替FX Public API。
