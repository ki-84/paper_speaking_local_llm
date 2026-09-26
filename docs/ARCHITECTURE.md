# 実装構造

```
LAN browser ── HTTPS :8443 Caddy ── 127.0.0.1:8190 FastAPI
                                     │ SQLite WAL + files
                               persistent priority worker
                                     │ one owned model process
                    llama.cpp :8191 / TTS actor / speech actor
```

- `backend/paperspeak/papers.py`: arXiv ID/版、取得間隔・キャッシュ・再試行、HTML節・数式・図表の抽出、PDF全ページの画像と本文。
- `planning.py`: 根拠ノートを概念ごとの学習目標へ割り当てます。同一条件の重複だけをまとめ、各主張と教材の対応を保存します。
- `lessons.py`: 根拠ノート→概念構成→分割対話→原文再検査→英語の調整→再検査→質問→TTS→ASR照合→日本語訳。段階ごと・音声一文ごとに保存。
- `translation.py`: ローカルモデルで会話・問題・用語を区切って翻訳し、数値を照合します。旧版の完成章も起動時に翻訳待ちへ登録します。
- `voices.py`/`revoice.py`: Aidenと米国英語の女性音声Mayaの設定、旧Ryan音声の段階的な再生成とASR照合。新音声が検証を通るまで旧音声を使い、置換後も履歴に元ファイルを保持します。
- `quality.py`: 引用IDと数値の機械的検査、短文チェック、真の編集距離による単語照合。
- `discovery.py`: 30日/差分の収集、分野分散・好みを考慮した最大10候補、全文の分割読解、根拠を持つ推薦、日次上限・滞留抑制。
- `practice.py`: 録音品質、内容認識、音素比較、文強勢、母音の相対エネルギー、理解/英語を分ける返答、間隔反復。
- `runtime.py`/`speech_actor.py`: 排他的なモデル切替、VRAM空き確認、完全ローカルの固定モデル、所有する子プロセスだけを停止。CustomVoiceとVoiceDesignを必要に応じて入れ替えます。
- `worker.py`/`db.py`: SQLiteの即時トランザクションでジョブ重複を防ぎ、優先順に処理。heartbeat90秒で中断ジョブを回収。
- `api.py`: 個人用認証、REST、SSE、録音先行保存、私的ファイルの境界。任意URL取得は許さずarXivへ限定。
- `frontend/src/`: ライブラリ・推薦・学習・復習・ジョブ・設定。学習時は英語と日本語訳を併記し、録音再送用にIndexedDBを使用。

## データ

論文の `(source_id,version)` を一意に保存します。教材は論文とは別IDの版です。完成した版は上書きせず、新しい版を作ります。出典IDは元のHTML断片またはPDFページ/断片に対応します。生成・評価時のモデルmanifestを教材/録音に記録します。

`papers`, `sources`, `lessons`, `chapters`, `attempts`, `reviews`, `recommendations`, `jobs`, `events`, `settings`, `sessions`, `cursors` が主要テーブルです。音声/ページ画像/録音のファイルはDBに相対パスを保存します。

原文にある文字列を引用した場合は照合し、一致しない引用文を引用として保存しません。要約の主張は別のレビュー段階で原文に照らします。完成判定は単にJSONを返したことではなく、検証エラーがないこと、質問が整うこと、全音声が照合済みであること、日本語訳の全項目がそろうことです。

音声認識には論文名と専門語の一覧を補助として渡し、正解の文や回答は渡しません。略語の文字間隔や文字名の同音表記をそろえつつ、数字・専門語・変数の違いは厳しく確認します。音声の再生成でも照合に通らない文は、回数を制限して言い換え、原文との内容確認から再実行します。前の文と音声は履歴として保存します。

推薦の詳しい科学的評価と、画面に出す短い説明は別々に保持します。やさしい英語への書き換え後に意味を再確認し、条件の省略による言い過ぎや不明な専門語が残る説明は公開しません。本文を読めない候補は自動教材化しません。

内容確認で文を特定できる場合は、その文と直前・直後だけを修正対象にします。対象外の文と音声を保持し、変更後は章全体を再確認します。修正前の内容も履歴に保存します。主張の説明が章全体で不足している場合は、構成を含む修正を行います。

長い論文の選考では、原本の読解メモを保持したまま、少数のメモを順にまとめます。モデルの上限に達した場合は同じ入力を繰り返さず、まとめる個数を減らします。それでも全文の評価を完了できない候補は理由を記録して見送り、途中までの読解で推薦しません。

固定したllama.cppのMuse専用パーサーは `response_format` のJSON制約を適用しないため、Museではネイティブの推論/返答形式に対応した `muse_json.gbnf` を渡します。Qwenは空でないJSON Schemaを使います。Museの推論強度は論文でhigh、短い練習フィードバックでlowです。Qwen用の `enable_thinking` をMuseの切替として扱いません。最終返答だけを保存し、形式エラーの返答も診断用に残します。生成記録には設定とgrammarのSHA-256を記録します。

Museの推論部分は通常2048トークン、短い分類・フィードバックは512トークンを上限とし、専用の開始・終了タグをllama.cppに渡します。Museの画像エンコーダーは追加の実機試験を経てGPUへ配置しています。入力の長さは実際のtokenizerで測り、画像と返答の余裕を確保します。

## 主なAPI

パスワード設定が有効な場合、health/login以外のAPIにはCookieまたはローカル管理用Bearer認証が必要です。`./studio auth-off` のLANモードでは認証なしで開けます。詳細な型はFastAPIのOpenAPIに対応します。

| Method | Path | 内容 |
|---|---|---|
| GET | `/api/health`, `/api/status` | 稼働状態 |
| POST | `/api/login`, `/api/logout` | 個人用ログイン |
| GET | `/api/papers`, `/api/lessons` | 保存一覧 |
| POST | `/api/papers/import`, `/api/papers/upload` | arXiv・PDF登録 |
| POST | `/api/papers/{id}/lessons` | 教材の作成/新しい版 |
| GET | `/api/lessons/{id}` | 章・録音・進捗 |
| POST | `/api/chapters/{id}/translation` | 旧版完成章の訳を手動再投入 |
| PUT | `/api/lessons/{id}/progress` | 再開位置 |
| GET | `/api/sources/{id}`, `/api/files/{path}` | 原文・音声 |
| POST | `/api/attempts` | 録音、client_idによる安全な再送 |
| GET/DELETE | `/api/attempts/{id}` | 評価・録音削除 |
| GET | `/api/reviews`, `/api/recommendations` | 復習・推薦 |
| POST | `/api/discover` | 探索開始 |
| POST | `/api/recommendations/{id}/feedback` | 興味・既読 |
| GET | `/api/jobs` | 保存済みの処理状態 |
| POST | `/api/jobs/{id}/{pause,resume,retry,cancel}` | 処理制御 |
| GET/PUT | `/api/settings` | 設定 |
| GET | `/api/events` | SSE、Last-Event-IDで再接続 |

設定・出典・アップロードは厳密な許可値とパス境界を使います。論文本文はモデルへの命令として扱わないよう各呼出しで指示します。単一利用者/信頼できるLANを想定したアプリです。
