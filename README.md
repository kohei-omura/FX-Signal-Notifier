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
| `worker.js` | （任意）リアルタイム価格用のCloudflare Worker |
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

**反映について（改良済み）**：画面は `positions.json` を直接読むため、登録をcommitすれば（Action完了を待たず）**すぐ画面に反映**されます。さらに `positions.json` を編集すると**自動でGitHub Actionが起動**し、ATR推奨レベルの確定とLINE/メール通知を行います。
※画面の損益に使う価格は、Worker未設定時は `status.json`（約5分間隔）基準です。秒単位にしたい場合は下記のリアルタイム設定を行ってください。
※LINE/メール通知・ATR自動設定はGitHub Action側で動くため、Actionが動いていることが前提です（止まる場合はActionsタブでエラー確認）。

---

## 推奨値(ATR)・損益の計算
- SL = ATR×1.5、TP = ATR×2.0（`ATR_SL_MULT`/`ATR_TP_MULT`で調整）
- 足が5分のためATRは小さめ＝スキャル向け。広げたい場合は倍率増 or `INTERVAL`を`1hour`等へ
- ロング損益=bid−建値 / ショート損益=建値−ask、pips=差÷0.01、円=差×lot

## 注意
- `cron`最短5分・遅延あり（真のリアルタイム不可）。LINE無料枠は月200通。
- `positions.json`と`status.json`はアプリが自動コミット（`contents: write`・設定済み）。
- 価格取得元: GMOコイン外国為替FX Public API。


---

## リアルタイム表示について（重要）
- 標準では画面は `status.json`（GitHub Actionsが**約5分間隔**で更新）を30秒ごとに読み込みます。
  つまり**金額は約5分ごとに変化**し、ティック単位の完全リアルタイムではありません。
- もし「更新が止まる」場合：Actionsタブで失敗(赤)ランを確認。本ワークフローはpushを
  rebase＆3回リトライする堅牢版にしてあります。`*/5`のcronはGitHub高負荷時に遅延/間引きされる仕様です。

### 数秒ごとに金額を動かす（任意・推奨）
GMOのAPIは画面から直接呼べない（CORS不可）ため、無料の**Cloudflare Worker**で中継します。
1. https://dash.cloudflare.com → Workers & Pages → Create → Worker を作成
2. コードを `worker.js` の内容に差し替えて Deploy
3. 発行されたURL（例 `https://fx-navi.xxxx.workers.dev`）をコピー
4. `index.html` の `const LIVE_PRICE_URL = "";` にそのURLを貼って commit

設定すると、画面が**約9秒ごと**にライブ価格を取得し、保有ポジションの含み損益(円/pips)を
即時に再計算、ヘッダに「● LIVE hh:mm:ss」が表示されます（空欄なら従来どおり5分更新）。
※シグナル判定・TP/SL到達の通知は引き続きGitHub Actions側で行います。


---

## アプリ内ポジション管理（JSON手打ち不要）
ダッシュボードの「保有ポジション」見出しの **＋追加 / ⚙** から操作できます。GitHubトークン経由で `positions.json` に直接読み書きします。

### 初回設定（⚙）
1. GitHub → Settings → Developer settings → **Fine-grained tokens** → Generate new token
2. Repository access: **Only select repositories** → このFXリポジトリだけを選択
3. Permissions → Repository permissions → **Contents: Read and write**
4. 生成したトークンを、アプリの ⚙ に貼る（オーナー/リポジトリは自動入力）。
   ※トークンは端末内(ブラウザ)にのみ保存され、リポジトリには書き込まれません。必ず上記の最小権限で発行してください。

### 使い方
- **＋追加**：通貨ペア・売買・建値・数量・(ATR自動 or pips指定)を入れて追加。
- **決済**：各ポジションの「決済」→ **実際の約定価格**を入力（買いなら売値／売りなら買値）。
  実損益(円/pips)を計算し「決済履歴」に残します。**アプリは自動決済しません**＝あなたの実約定が正です。
- **削除**：誤登録や不要な履歴を削除。

### 通知の挙動（変更点）
TP/SL価格に到達すると **「利確/損切りライン到達」通知**（LINE/メール）が届きます（1回のみ）。
**自動では決済しません**ので、GMO等で決済した後にアプリの「決済」へ実際の価格を入力してください。
（GMO公開値とご自身の約定価格・スプレッドの差による不一致を防ぐためです）


---

## シグナルエンジン（スキャル/デイトレ・加重スコア）
判定は **テクニカル90% + ファンダ10%** の合成スコア（-1〜+1）で行います。
- スコア ≥ しきい値 → **買い**、≤ -しきい値 → **売り**、それ以外は様子見
- 各ペアのカードに スコア／テク／ファンダ／RSI／ADX を表示

### テクニカル90%（内訳）
EMAクロス(0.35) ＋ MACDヒスト(0.25) ＋ RSI(0.20) ＋ ボリンジャー位置(0.20)。
さらに **ADX** でトレンド強度を加味（弱トレンド時はEMA/MACD寄与を減衰）。

### ファンダ10%
金利差キャリーの方向バイアス（円ペアは円が低金利→買い寄り）。
`fx_signal.py` の `FUND_BIAS` で各ペアを調整可（現在の政策金利差に合わせる）。
重要指標の前後を避けたい場合は `NEWS_BLACKOUT`（JST日時）に追加するとその前後はシグナル抑制。

### TP/SLの決め方（Pattern 1：ATR基準）
- **SL（損切り）= エントリー時のATR × 1.0**（ATRをそのままpips換算）
- **TP（利確）= SL × 1.5**（リスクリワード 1:1.5）
- 例：ATRが2.0pipsなら → SL=2.0pips / TP=3.0pips
- 倍率は `fx_signal.py` の `SL_ATR_MULT` / `TP_SL_RATIO` で調整可（TPを2倍にしたいなら`TP_SL_RATIO=2.0`）
- ATRは足に依存（scalp=1分足ATR→狭い、day=5分足ATR→広い）

### モード切替（scalp / day）
| モード | 足 | EMA | RSI | TP/SL(×ATR) | しきい値 |
|---|---|---|---|---|---|
| scalp | 1分 | 5/13 | 7 | ATR基準(下記) | 0.35 |
| day | 5分 | 9/21 | 14 | ATR基準(下記) | 0.40 |

切替方法：GitHubの **Settings → Secrets and variables → Actions → Variables** に `MODE` を作り `scalp` か `day`。
（または `fx_signal.py` 冒頭の `MODE` を直接編集）

### 注意（スキャルの限界）
GitHub Actionsは最短5分間隔のため、**シグナルの再計算・通知は約5分ごと**が上限です。
秒単位のスキャルには足りないので、画面の数値はその周期で更新されます
（Cloudflare Workerでの**ライブ価格**は数秒ごとに含み損益へ反映されますが、シグナル自体の再計算はAction側です）。


---

## エントリー有効条件（通知後 いつ・いくらまで入ってよいか）
通知が来てから実際に発注するまでに価格が動くため、「**まだ入ってよいか**」を判定します。
シグナル通知と画面の各カードに、次を表示します。

- **時間の有効期限**：通知から `VALID_BARS`本ぶん（scalp=3分 / day=15分）。`HH:MMまで`。
- **追いかけ上限/下限**：通知価格から **SL × 0.5（=ATR×0.5）** まで。
  - 買い：現在値が上限以下なら可。上限超え＝高く動きすぎ→**見送り**
  - 売り：現在値が下限以上なら可。下限割れ＝安く動きすぎ→**見送り**
  - （追いかけるほどリスクリワードが悪化するため）

### 画面の判定（ライブ）
シグナルが出ているペアに判定バッジが出ます：
- ✅ **エントリー可（残り約○分）**
- ⛔ **高く/安く動きすぎ・見送り**（価格が追いかけ上限/下限を超過）
- ⌛ **期限切れ・見送り**（有効時間を経過）

Worker設定済みなら現在値が数秒ごとに更新され、判定もリアルタイムに切り替わります。

### 調整
`fx_signal.py` 冒頭：`VALID_BARS`（有効足数）/ `MAX_CHASE_RATIO`（追いかけ許容＝SL比）。
厳しめにするなら `MAX_CHASE_RATIO=0.3` など。


---

## ライブ計算（任意）— シグナルを数十秒ごとに再計算
GitHub Actionは約5分間隔のため、スキャルではシグナルが古くなりがちです。
Cloudflare Workerでklinesも取得し、**画面側でスコア・シグナル・エントリー判定を再計算**します（計算式はPython版と一致を検証済み）。

### 有効化（3ステップ）
1. **worker.js を新版に差し替えてDeploy**（klines中継 `?path=` と間引き `?last=` に対応した版）
2. **index.html を新版に差し替え**（ライブ計算を内蔵）
3. `index.html` の `LIVE_PRICE_URL` に Worker のURLを設定（ticker用と同じURLでOK）

### 動作
- **価格・含み損益・エントリー判定**：約9秒ごとに更新
- **スコア・シグナル・推奨TP/SL・エントリー有効帯**：約45秒ごとに再計算（scalp=1分足 / day=5分足）
- ヘッダに「● LIVE計算 時刻」を表示

### 通信量
klinesはWorker側で末尾400本に間引き（`?last=400`）して軽量化。モバイル通信が気になる場合は
`index.html` 末尾の `setInterval(liveSignals,45000)` の値を `90000`（90秒）等に伸ばしてください。
LIVE_PRICE_URL が空ならライブ計算は動かず、従来どおりstatus.json（約5分）で更新します。


---

## ライブLINE通知（画面のシグナルもLINEに飛ばす）
GitHub Actionの通知は約5分間隔のため、45秒ごとのライブ計算で出たシグナルが通知されないことがあります。
そこで **Worker側にLINEトークンを置き、画面がシグナル検知時にWorkerへ依頼→WorkerがLINE送信** します。
トークンはブラウザに出さないので安全です。

### 設定
1. **worker.js を最新版に差し替えてDeploy**（POST `?action=notify` でLINE送信に対応）
2. Cloudflareの Worker → **Settings → Variables and Secrets** に追加：
   | 種別 | 名前 | 値 |
   |---|---|---|
   | Secret(暗号化) | `LINE_TOKEN` | LINEチャネルアクセストークン（GitHubのと同じ）|
   | （任意）Text | `NOTIFY_KEY` | 任意の合言葉（簡易の不正送信防止）|
3. `NOTIFY_KEY` を設定したら、`index.html` の `const NOTIFY_KEY = "";` に同じ値を入れる
4. `index.html` を最新版に差し替え

### 動作・連投防止
- ダッシュボードが**新規シグナル**を検知した時だけ送信（同じ通貨は**3分間は再送しない**）
- 文面の先頭は「⚡ライブ」（Action発の通常通知と区別）
- **画面（PWA）を開いている間のみ**動作。閉じている時はAction(5分)の通知が担当します
- これによりライブのシグナルは**約1分以内**にLINEへ届きます

※Action通知とライブ通知の両方が来る場合があります（同じシグナルを二重に拾った時）。気になる場合はどちらかに寄せられます。
テスト
