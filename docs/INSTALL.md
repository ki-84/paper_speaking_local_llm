# インストールの再現

検証機: Ubuntu 24.04、Python 3.12、RTX 5090 32GB。モデル取得を含め約200GBの空き容量を用意します。NVIDIAドライバーとCUDA対応のllama.cppが必要です。PyTorchはCUDA12.8の2.8.0を固定し、TTSとASRのTransformers依存関係を分離します。

```bash
python3 scripts/setup.py --llama-bin /path/to/llama-server --models
./studio start
./studio access
```

Node・Caddy・Python依存関係を固定ハッシュで取得します。Node24.21.0、Caddy2.11.4、uv0.12.18です。`npm ci` はpackage-lockを使用します。既存の環境は再利用します。追加比較モデルは以下で取得します。

図のPNG出力には、package-lockで固定したPlaywrightが指定するChromiumをsetupが取得します。日本語にはLinux内の `Noto Sans CJK JP` を使用します。Ubuntuでは `sudo apt install fonts-noto-cjk` で事前に用意してください。Chromiumの共有ライブラリが不足する環境では `.tools/node/bin/node frontend/node_modules/playwright/cli.js install-deps chromium` も必要です。取得後の図の描画はネットワーク要求を遮断して実行し、外部フォントや画像生成APIを使いません。

```bash
.venv/bin/python scripts/prepare_assets.py qwen-q6 muse-q6
```

llama.cppは `9d57ce456c94d241dde672b2db9cf18879766568` を使用します。別PCでビルドする場合:

```bash
git clone https://github.com/ggml-org/llama.cpp vendor/llama.cpp
git -C vendor/llama.cpp checkout 9d57ce456c94d241dde672b2db9cf18879766568
cmake -S vendor/llama.cpp -B vendor/llama.cpp/build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=120
cmake --build vendor/llama.cpp/build --config Release -j 8 --target llama-server
python3 scripts/setup.py --llama-bin "$PWD/vendor/llama.cpp/build/bin/llama-server" --models
```

CUDA12.8以降のSM120対応ツールチェーンとCMake/C++コンパイラを用意してください。コンパイラやCUDAが変わるとバイナリハッシュも変わるため、`models.lock.json` のruntime欄は取得時に実際の配置を記録します。モデルのrevision・重みハッシュは固定です。既存のQ8ファイルはハッシュが一致する場合だけシンボリックリンクで利用します。

## テストと評価

```bash
.venv/bin/python -m pytest -q
PATH="$PWD/.tools/node/bin:$PATH" npm --prefix frontend run build
.venv/bin/python scripts/prepare_evaluation.py
.venv/bin/python scripts/evaluate.py models
.venv/bin/python scripts/evaluate.py speech
```

評価は通常の永続ジョブとして動き、録音の練習が優先されます。結果は `data/evaluation/` に残ります。SpeechOcean762（Apache2.0）とStressTest（CC-BY-NC4.0）の人の音声を、公開された研究評価の範囲で利用します。利用者の録音を外部に送信する処理はありません。

主要モデルの公式資料:
[Qwen3.8](https://huggingface.co/Qwen/Qwen3.8-27B)、[Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS)、[Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR)、[WhiStress](https://github.com/slp-rl/WhiStress)、[arXiv APIの取得条件](https://info.arxiv.org/help/api/tou.html)。モデル・データセットのライセンスは各配布元に従います。
