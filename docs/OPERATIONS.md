# 運用

アプリのフォルダで実行します。

```bash
./studio start       # HTTPS・API・ワーカーを起動
./studio status      # GPU・ジョブ・稼働状態
./studio doctor     # 診断情報
./studio access     # パスワードとCA証明書の場所
./studio stop       # 停止。作業は保存した区切りから再開
```

画面の **In the making** でジョブごとに停止・再開・再試行できます。「Pause after this step」は現在の推論終了後に止まります。録音は優先度0、日本語入力による論文検索は7、候補情報の収集は8、推薦の日本語化と旧Ryan音声の更新は9、手動の教材は10、自動の教材は20、候補本文の選考は30です。進行中の推論呼出しは切り捨てず、その区切りで優先順位を再評価します。録音解析が始まるまで数分待つ場合があります。

長い手動教材が論文探しを止め続けないよう、自動教材や本文選考が15分以上待った場合は、一つの保存区切りだけ手動教材より先に進めます。録音の優先順位は維持します。

GPUの空きが不足する場合はジョブを待機させます。他アプリの推論を終了して空きを作ると自動再開します。モデルは同時常駐させません。推論プロセスはワーカーが所有し、ワーカーが異常終了した場合もLinuxの親プロセス監視により終了します。

## 自動起動

```bash
./studio stop
./studio install-service
systemctl --user start paperspeak
systemctl --user status paperspeak
```

このPCではサービスを有効化し、ログアウト後も継続するlingerも設定済みです。新規インストールでは、ユーザーログイン時に起動します。ログアウト後も動かす場合はLinux側で `loginctl enable-linger "$USER"` を設定します。これはOSのユーザーサービス設定です。サービス使用中の再起動は `systemctl --user restart paperspeak`、停止は `systemctl --user stop paperspeak` です。

ユーザーサービスの環境変数は `systemctl --user edit paperspeak` の `[Service]` に `Environment=PAPERSPEAK_LAN_HOST=192.168.10.112` などを追加します。

## バックアップと復元

```bash
./studio backup --output /保存先/paperspeak-backup.tar.gz
.venv/bin/python scripts/restore.py /保存先/paperspeak-backup.tar.gz /新しいフォルダ/restore
```

バックアップはワーカーが現在の区切りを保存するまで待ち、SQLiteのオンラインバックアップと論文・音声・録音ファイルをまとめます。モデル重みは含めません。復元先は**存在しない新しいフォルダ**を指定します。既存データは上書きしません。

生成時の実際の設定と検証結果のJSONも含めます。ベンチマーク用に取得した音声データセットや、過去のバックアップファイルは含めません。

復元後は現在のスタジオを停止し、`PAPERSPEAK_DATA=/新しいフォルダ/restore/data ./studio start` で起動します。復元したデータベースはintegrity_check済みです。ログインセッションを破棄し、新しいパスワードとCA証明書を作ります。別PCでは新しいCAを信頼してください。

録音には自分の声が含まれます。バックアップは自分だけがアクセスできる場所に置きます。

## 調査するファイル

- `data/logs/supervisor.log`: 起動・子プロセス監視
- `data/logs/api.log`, `worker.log`, `caddy.log`: API・処理・HTTPS
- `data/logs/llama.log`, `tts.log`, `asr.log`: 推論
- `data/evaluation/model-comparison.json`: 同条件のモデル比較
- `data/evaluation/human-speech-report.json`: 人の注釈付き音声の評価
- `data/paperspeak.sqlite3`: 全履歴・ジョブ・進捗

初回の30日分の収集や本文確認は時間がかかります。arXivへの取得間隔は3.1秒以上とし、成功したレスポンスをキャッシュします。教材待ちが上限に達していると、新規の自動教材化を見送ります。

初回は投稿日の範囲で探し、以後は更新日の降順で前回の成功時刻まで読みます。これにより古い論文の改訂も候補に含め、既存の版は保持します。APIの取得は500件ずつです。[arXivのページング仕様](https://info.arxiv.org/help/api/user-manual.html#3112-start-and-max_results-paging)に従います。

失敗ジョブには理由を表示し、無条件に完成扱いにはしません。出典の不一致や音声の照合失敗では、教材を公開せず停止します。修正後に **Try again** で保存した段階から再試行します。

同じ日に手動で始めた探索が失敗している場合、日次スケジューラは別の探索で置き換えません。保存した途中結果を確認し、そのジョブを再試行します。長い論文のメモ集約では、出力上限を検出した場合に処理単位を縮小します。

## 連続運転の観測

このPCでは `paperspeak-soak.service` が72時間の観測を記録しています。状態は `data/evaluation/soak-report.json`、各回の結果は `soak-samples.jsonl` です。停止は `systemctl --user stop paperspeak-soak`。観測中の停止・欠測は隠さず記録します。

サービスの応答とワーカーの稼働に加えて、探索が完了した日、教材の完成数、失敗ジョブを記録します。応答の成功だけでは自動教材化の全工程を合格にしません。開発中の故障注入・再起動を含む記録は `data/evaluation/soak-history/` に残し、リリース後の観測と分けます。

モデル全ファイルの再検査は `.venv/bin/python scripts/verify_assets.py`。比較の根拠は `evaluation/model_decision.json` に保存しています。
