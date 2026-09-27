# 図付き教材からYouTube用動画を作る

画面の「教材とMP4を作る」ボタンを押すと、`paper-visual-2` 教材の生成を開始し、その完成章を検証済みの原図・補助図、英語音声、日本語訳から自動的に動画にします。英語と日本語の字幕は映像に直接描き込みます。章ごとのMP4を先に保存し、教材の全章が完成したときだけ、全章を含む一本のMP4を結合します。公開前の未検証章は動画に入れません。画面の「Video for YouTube」から最初の完成MP4と、後に完成する全章MP4をダウンロードできます。ダウンロード欄には「論文名 — 第1章」または「論文名 — 全章」の推奨タイトルとコピーボタンを表示します。

MP4は1920×1080/30fps、H.264 High/yuv420p、AAC-LC/48kHzステレオです。静止図が中心なので、H.264のCRF 21、mediumプリセット、最大4秒のキーフレーム間隔、AAC 128 kbpsで容量を抑えます。画質は図の細かい文字と字幕を目視で確認します。MP4のファイル名は画面の推奨タイトルと同じです。OSで使えない記号はハイフンへ置き換え、版は別フォルダに保存します。各章動画の冒頭には論文名、英日併記の章名を大きく表示するタイトルカードを置き、ローカルTTSの英語音声で論文名を読み上げます。全章版では章の切り替わりごとにこのカードが入ります。読み上げた題名はローカルASRで照合し、合わなければ再生成します。別の `.en.srt` と `.ja.srt` もダウンロードできますが、字幕ファイルをYouTubeに追加しなくても動画内に二言語が常に見えます。図は文ごとの参照に同期し、原図と説明用の補助図を映像中でも区別します。音声・翻訳・図のチェックが終わった章だけ使用します。

動画描画にはPythonロック内の `imageio-ffmpeg==0.6.0` に含まれるFFmpeg、フロントエンドの固定Playwright Chromium、Linux内のNoto Sans CJK JPを使用します。動画の生成にクラウドAIや有料APIは使いません。途中のスライドとジョブは保存され、再起動後に続きから処理します。録音評価が待っているときは動画エンコードを中断し、録音後に再開します。

## YouTubeへの自動アップロード

公開する場合は、完成した全章MP4をダウンロードし、[YouTube Studio](https://studio.youtube.com/) のブラウザ画面から手動でアップロードして「公開」を選べます。この方法では、このアプリのGoogle接続は不要です。自動アップロードは非公開で投稿したい場合だけ利用します。

Googleアカウントとの接続は一度だけ必要です。接続後、**全章のMP4が完成した時だけ**、YouTubeへ**非公開**で自動アップロードします。章ごとの動画は自動アップロードしません。画面から自動アップロードを停止・再開できます。アップロード中断時はGoogleの再開用セッションを保存し、受信済み位置を問い合わせて続けます。セッションが期限切れになり、アップロードの成否が不明な場合は重複投稿を避けて停止します。

1. [Google Cloud Console](https://console.cloud.google.com/apis/library/youtube.googleapis.com)でYouTube Data API v3を有効にし、OAuth同意画面と**デスクトップアプリ**のOAuthクライアントを作ります。テスト状態なら使うGoogleアカウントをテストユーザーに追加します。クライアントJSONをダウンロードします。
2. JSONをLinuxのこのアプリの `data/` にコピーし、Linuxで `cd /home/kuwabara/devwork/paper_speaking_linux && .venv/bin/python scripts/youtube_connect.py data/client_secret.json` を実行します。MacにJSONがある場合の転送例は `scp ~/Downloads/client_secret*.json kuwabara@192.168.10.112:/home/kuwabara/devwork/paper_speaking_linux/data/client_secret.json` です。
3. コマンドが表示するGoogleのURLをChromeで開き、アップロード権限を許可します。MacのChromeから開く場合は、表示されたポート番号を使って**別のMacターミナル**で `ssh -L ポート:127.0.0.1:ポート kuwabara@192.168.10.112` を先に実行します。Googleの戻り先 `127.0.0.1` をLinuxの一時的な認証窓口へ転送するためです。

OAuthクライアントと更新トークンは `data/youtube/` にモード600で保存し、Gitには含めません。アップロード権限は `youtube.upload` のみです。YouTubeアカウントの認証は動画生成のローカルLLMとは別です。

Google CloudのOAuth同意画面が「外部・テスト中」の場合、[更新トークンは7日で失効します](https://developers.google.com/identity/protocols/oauth2)。全章生成には時間がかかるので、接続は動画完成に近い時期に行うか、Google Cloud側の公開設定を確認してください。失効後は同じ接続コマンドで再認証できます。

[YouTubeのAPI仕様](https://developers.google.com/youtube/v3/docs/videos/insert)によると、未審査の新しいAPIプロジェクトから投稿した動画は非公開に制限されます。公開したい場合はGoogle側のAPI審査が必要です。また[15分を超える動画](https://support.google.com/youtube/answer/71673?co=GENIE.Platform%3DDesktop&hl=ja)にはYouTubeアカウントの確認が必要で、上限は12時間または256GBです。接続前でも完成動画のMP4をダウンロードして手動でアップロードできます。

## 確認

```bash
.venv/bin/python -m pytest -q tests/test_video.py
./studio status
```

`data/videos/` にMP4とSRT、SQLiteの `video_exports` に元教材のダイジェスト、長さ、ハッシュ、使用エンコーダとジョブ状態を保存します。バックアップにも完成動画を含めます。
