
# AI Diet Coach – LINE Bot (Full Features)

本ボットは以下をサポートします：
- オンボーディング（進捗バーつき）
- プラン作成（BMR/TDEE、安全な日次kcal、P/F/C算出）
- 体重ログ（`log 65.2`）と直後の**動的サジェスト**
- 履歴サマリ（7日/30日、平均・変化・傾向）
- プロフィール表示/変更（`profile show` / `profile set key value`）
- モード別食事ガイド（`guide`）
- `help`/`reset`

## セットアップ
1. **LINE Developers** でMessaging APIチャネルを作成し、チャネルシークレット/アクセストークンを取得
2. `.env` を用意して環境変数を設定（下記雛形）
3. 依存をインストールして起動

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn line_bot.app:app --host 0.0.0.0 --port 3000
# ngrok/Cloudflare Tunnel等で https://<your-domain>/callback を公開→LINEコンソールのWebhookに設定→Verify
```

### .env.example
```
LINE_CHANNEL_SECRET=YOUR_CHANNEL_SECRET
LINE_CHANNEL_ACCESS_TOKEN=YOUR_CHANNEL_ACCESS_TOKEN
```

## 使い方（メッセージ）
- `start` … 設定開始（進捗バー表示）
- `plan` … 現在のプロフィールでプラン再計算
- `log 65.2` … 体重ログ
- `history` … 7日/30日サマリ表示
- `profile show` / `profile set activity active`
- `guide` … モード別の食事ガイド
- `reset` / `help`


---

## Render デプロイ手順（推奨）
1. このフォルダをGitHubリポジトリにプッシュ
2. Renderで「New +」→「Web Service」→リポジトリ選択
3. Environment: **Python**, Build Command: `pip install -r requirements.txt`（デフォルトでOK）
4. Start Command: `uvicorn line_bot.app:app --host 0.0.0.0 --port $PORT`
5. **Disks**: 1〜5GB 追加（mountPath は `render.yaml` と同じ `/opt/render/project/src/data` を推奨）
6. **Environment Variables**: `LINE_CHANNEL_SECRET`, `LINE_CHANNEL_ACCESS_TOKEN` をセット
7. デプロイURLに `/callback` を付けてLINEのWebhookへ登録→Verify

> 備考: 本アプリは相対パス `./data` を利用しています。`render.yaml` の `mountPath` はこのパスに一致するように設定済みです。
