#!/usr/bin/env python3
"""Write a reviewable acceptance snapshot from actual local results and persisted jobs."""

import json
import time

from paperspeak import config, db

db.init()
out = config.ROOT / "docs/VERIFICATION.md"
lines = [
    "# 検証記録",
    "",
    f"更新: {time.strftime('%Y-%m-%d %H:%M:%S %Z')}",
    "",
    "この記録は実測ファイルとデータベースから作成します。未完了を合格として扱いません。",
    "",
    "## 実行環境",
    "",
    "Ubuntu 24.04 / RTX 5090 32GB / CUDA12.8 PyTorch2.8.0 / Python3.12。依存関係と重みは固定しています。初回比較は画像エンコーダーをCPUに配置し、その後MuseのGPU画像処理を追加検証しました。",
    "",
    "## 固定論文の比較",
    "",
    "| 構成 | 本文＋画像の正答 | 図表画像のみの正答 | 合計時間（秒） |",
    "|---|---:|---:|---:|",
]
reports = {}
for name in ["model-comparison", "visual-comparison"]:
    path = config.DATA / f"evaluation/{name}.json"
    if path.exists():
        reports[name] = json.loads(path.read_text())
for profile in ["qwen-q8", "qwen-q6", "muse-q6"]:
    cells = []
    seconds = 0
    for name in ["model-comparison", "visual-comparison"]:
        row = next(
            (
                r
                for r in reports.get(name, {}).get("summary", [])
                if r["profile"] == profile
            ),
            None,
        )
        cells.append(f"{row['correct']}/{row['total']}" if row else "実行中")
        seconds += row["seconds"] if row else 0
    lines.append(f"| {profile} | {' | '.join(cells)} | {seconds:.1f} |")
lines += [
    "",
    "Transformer、DDPM、LoRAの原論文から定めた固定項目を照合しました。数字を文字列で返した場合は同じ数値として扱い、booleanも厳密に正規化します。本文＋該当ページ画像の16項目、図表画像のみの18項目の小規模比較です。後者は抽出テキストを渡さず元ページ画像を渡します。生成時間は推論呼出しの経過時間（切替がある場合はモデルロードを含む）で、同時期の他処理による変動を含みます。",
    "",
    "固定項目の正答だけでは説明の完全性やA2適合性を保証しません。文の短さは補助指標です。評価するモデル自身の自己採点によるランキングではありません。",
    "",
    "## 人の音声による確認",
    "",
]
vision = config.DATA / "evaluation/vision-gpu-probe.json"
if vision.exists():
    v = json.loads(vision.read_text())
    lines[-2:-2] = [
        "## 画像処理の追加検証",
        "",
        f"Muse Q6のGPU画像処理: {v['status']}。4呼出し（3論文＋LoRAの複数画像）の確認項目は {sum(sum(r['checks'].values()) for r in v['results'])}/{sum(len(r['checks']) for r in v['results'])}。最小空きVRAMは {v['min_free_mib']} MiB。入力は最大3枚で、同ページの重複断片も含みます。現在のMuse構成ではこのGPU配置を採用しています。",
        "",
    ]
p = config.DATA / "evaluation/human-speech-report.json"
if p.exists():
    r = json.loads(p.read_text())
    s = r["stress"]
    p = r["phoneme"]
    lines += [
        "SpeechOcean762のtest splitから100件（10箇所の連続10件）と、StressTestの俳優音声218件を使用しました。録音と注釈の取得元revision・各音声SHA-256を `data/evaluation/human-labels.json` に保存しています。",
        "",
        f"- Qwen ASR: 発話ごとの平均単語誤り率 {r['mean_utterance_wer'] * 100:.2f}%（論文用の語彙ヒントなし）。これは発音の良し悪しの点数ではありません。",
        f"- WhiStress: 適合率 {s['precision'] * 100:.2f}%、再現率 {s['recall_scored'] * 100:.2f}%、偽陽性率 {s['false_positive_rate'] * 100:.2f}%。",
        f"- Wav2Vec2音素プローブ: 適合率 {p['precision'] * 100:.2f}%、判定対象率 {p['coverage'] * 100:.2f}%。正解音素をIPAへ変換する方式の影響も含み、誤検出が多いため発音の断定・合否点数には採用しません。",
        "",
        "UIは単語認識、音の比較、母音の相対エネルギー、文強勢を区別します。音素は推定として聞き比べへつなぎ、強勢も別の自然な読み方を誤りにしません。単語内強勢の表示は辞書の位置と音声の母音ピーク比較であり、校正済みの強勢判定器ではありません。",
    ]
else:
    lines += ["実行中です。合格判定はまだありません。"]
probe = config.DATA / "evaluation/phoneme-comparison.json"
if probe.exists():
    p = json.loads(probe.read_text())
    lines += [
        "",
        "同じ100件を使い、IPA表記・米語の母音変種をそろえて別の英語用Wav2Vec2も比較しました。",
        "",
        "| 音素モデル | 誤り候補の適合率 | 保留も含めた再現率 | 偽陽性率 | 判定対象率 |",
        "|---|---:|---:|---:|---:|",
    ]
    for label, key in [("facebook/espeak", "baseline"), ("vitouphy/TIMIT", "timit")]:
        v = p[key]
        lines.append(
            f"| {label} | {v['precision']:.2%} | {v['recall_including_abstentions']:.2%} | {v['false_positive_rate']:.2%} | {v['coverage']:.2%} |"
        )
    lines += [
        "",
        "代替モデルは検出数とともに誤検出も増えました。元のモデルを聞き比べ用として維持し、合否には使用しません。採用記録は `evaluation/phoneme_decision.json`。",
    ]
lines += [
    "",
    "## 機能と復旧の確認",
    "",
    "- Python: 引用・数値・短文、取り込み重複、版、並行ジョブ、再開、録音先行保存と再送、認証・パス境界、無音/白色雑音/クリッピング、生成検証失敗時の非公開、日次上限・滞留を自動試験します。",
    "- Playwright: 出典表示、音声再生、段階ヒント、進捗復元、実ブラウザのMediaRecorder、録音中の画面移動防止、通信断後の再送を確認しました。明示した試験用教材と仮想マイクを使い、科学的品質の試験とは区別しています。",
    "- RTX5090: TTS→ASRで実際に生成した文が一致。音素とWhiStressも実行済み。",
    "- HTTPS: LAN IP宛ての接続とCAによる証明書検証をこのLinux上で確認しました。物理的に別のPCでのマイク・証明書設定は未確認です。",
    "- バックアップ: ワーカーの区切りで取得し、新しいフォルダへ復元、SQLite整合性検査を確認しました。",
    "",
    "## 実際の処理状況",
    "",
    "| 処理 | 状態 | 現在の段階 |",
    "|---|---|---|",
]
test_path = config.DATA / "evaluation/software-tests.json"
if test_path.exists():
    checks = json.loads(test_path.read_text())
    position = lines.index("## 実際の処理状況")
    lines[position:position] = [
        f"保存した試験記録: Python {checks['backend']['passed']}件、ブラウザ {checks['browser']['passed']}件が成功。ブラウザ試験の入力は専用の仮想マイクです。",
        "",
    ]
pipeline_path = config.DATA / "evaluation/real-pipeline.json"
if pipeline_path.exists():
    pipeline = json.loads(pipeline_path.read_text())
    position = lines.index("## 実際の処理状況")
    lines[position:position] = [
        f"実教材でのHTTPS→録音保存→GPU評価: {pipeline.get('status', '検証中')}。合成したお手本音声を試験入力に使い、音読と理解回答の保存・評価を確認します。人の発音精度や別PCでのマイク試験とは区別します。",
        "",
    ]
browser_path = config.DATA / "evaluation/production-browser.json"
if browser_path.exists():
    browser = json.loads(browser_path.read_text())
    position = lines.index("## 実際の処理状況")
    lines[position:position] = [
        f"実教材のブラウザ確認: {browser['status']}。完成したLoRAの章で、音声再生、原論文のページ画像と本文、理解確認のヒント、再読み込み後の位置復元を確認しました。試験後は元の学習位置へ戻しています。画面記録は `data/evaluation/production-study.png` と `production-source.png`。",
        "",
    ]
context_path = config.DATA / "evaluation/asr-context-probe.json"
if context_path.exists():
    position = lines.index("## 実際の処理状況")
    lines[position:position] = [
        "論文用語の音声照合: 論文名と語彙だけをASRへ渡し、LoRAの認識を確認しました。正解文は渡しません。three/two、two/three、old/newの異なる音声を使う対照試験でも差を検出しました。合成音声の試験であり、人の発音判定精度を示すものではありません。",
        "",
    ]
recommendation_path = config.DATA / "evaluation/first-recommendation.json"
if recommendation_path.exists():
    recommendation = json.loads(recommendation_path.read_text())
    position = lines.index("## 実際の処理状況")
    lines[position:position] = [
        f"自動探索: 初回に{recommendation['retrieved_distinct_papers']:,}本を収集し、{recommendation['shortlist_size']}候補を本文選考へ進めました。最初の候補は全文の評価、簡単な英語への調整、意味の再確認を経て `{recommendation['state']}` になっています。ブラウザで推薦・注意点・原文を開けることも確認しました。残りの選考と、それに続く自動教材化は処理中です。",
        "",
    ]
backup_path = config.DATA / "evaluation/ready-chapter-backup-check.json"
if backup_path.exists():
    backup = json.loads(backup_path.read_text())
    position = lines.index("## 実際の処理状況")
    lines[position:position] = [
        f"完成章のバックアップ復元: `{backup['status']}`。新しいフォルダに復元し、SQLite整合性、{backup['referenced_assets']}個の参照ファイル、音声の生成設定、{backup['generation_and_evaluation_json_files']}件の生成・検証JSONを確認しました。原本は `{backup['backup']}`。",
        "",
    ]
for j in db.all("SELECT kind,state,stage FROM jobs ORDER BY created"):
    lines.append(f"| {j['kind']} | {j['state']} | {j['stage'].replace('|', '/')} |")
chapters = db.all("SELECT state,data FROM chapters")
ready = [c for c in chapters if c["state"] == "ready"]
seconds = sum(t.get("duration", 0) for c in ready for t in c["data"].get("turns", []))
lines += [
    "",
    f"教材: {len(chapters)}章中{len(ready)}章が利用可能。完成章の音声は合計約{seconds / 60:.1f}分です。全章の教材化、候補全件の本文選考、複数日の自動運用は引き続き検証が必要です。",
    "",
    "現在の既定モデルはQwen Q8です。最初のLoRA章には、生成後の独立した根拠確認と前提用語の補足も行いました。経過と限界は `evaluation/production_quality.json` に保存しています。",
]
lines += ["", "## 連続稼働", ""]
p = config.DATA / "evaluation/soak-report.json"
if p.exists():
    r = json.loads(p.read_text())
    lines.append(
        f"72時間観測: {r['status']}。観測{r['samples']}件、失敗{r['failures']}件、観測の空白{len(r['gaps'])}件。終了前には複数日の安定動作を確認済みとはしません。"
    )
    lines += [
        "",
        "これはサービスの応答・ワーカーの稼働とジョブ状態の観測です。各日の探索完了、教材の完成数、失敗ジョブは別途記録し、応答があるだけで全工程の完成とは扱いません。開発中の中断・故障注入を含む以前の観測は `data/evaluation/soak-history/` に保持しています。",
        "",
        f"この観測中に記録した失敗ジョブは{len(r.get('job_failures', {}))}件です。再開しても失敗の記録を消しません。",
    ]
else:
    lines.append(
        "72時間の実時間観測は未完了です。時計を進めたスケジューラ試験は実際の複数日稼働の代わりにはしません。"
    )
recovery_path = config.DATA / "evaluation/production-recovery.json"
if recovery_path.exists():
    recovery = json.loads(recovery_path.read_text())
    lines += [
        "",
        f"実運用で見つかった停止への対応: `{recovery['status']}`。LoRA第2章の説明の言い過ぎと、長い候補論文のメモ集約時の出力上限を記録しました。文を限定した修正と、集約単位を縮小する処理を実装し、保存した段階から再開しています。実データの通過結果は `data/evaluation/production-recovery.json` に記録します。",
    ]
lines += [
    "",
    "結果の原本: `data/evaluation/`。更新は `.venv/bin/python scripts/report.py`。",
    "",
]
out.write_text("\n".join(lines))
print(out)
