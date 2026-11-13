# cadenda

Macで動く、BeadsをIssueの正本とするAI開発オーケストレーション基盤。
設計は [実装計画書](docs/macmini_ai_development_implementation_plan.md) に従う。
現在はv2.1計画に沿い、1プロジェクト・1タスクを最後まで実行できるv0を優先する。次の作業順とレビュー資料の採否は計画書にまとめた。

Phase 0（実機・CLI互換性）とPhase 1（Issue引継ぎPoC）を完了。
Phase 2のContext Builderに加え、Phase 3の明示的な単一Run実行・Recorder・停止復旧を実装。Phase 6でローカルWeb GUIを追加。Phase 7の監視・バックアップ復元・限定stagingを実装し、実機常駐化を検証中。

- [実機互換性と再検証コマンド](docs/compatibility-matrix.md)
- [Context Builderの導入・使い方・データ境界](docs/context-builder.md)
- [Phase 2の検証結果と次工程](docs/phase2-results.md)
- [Runの実行・停止・復旧](docs/execution.md)
- [Phase 3と自己利用の検証結果](docs/phase3-results.md)
- [Phase 4の分解・独立レビュー・修正パイプライン](docs/phase4-results.md)
- [Phase 5のfm Localと実機比較・PCC保留](docs/phase5-results.md)
- [Phase 6のGUI・接続方法・所有者認証](docs/phase6-results.md)
- [Phase 7の運用機能・復元手順・残るゲート](docs/phase7-results.md)
- [Issue引継ぎPoCの結果・確認方法・次工程](docs/issue-handoff-poc.md)
- [未解決制約](docs/risks.md)
- [承認されたPoCの課題分解](docs/poc-task-proposal.md)
- [手動引継ぎテンプレート](templates/handoff.md)
- [公開可能な合成入力の検証証跡](docs/evidence/index.json)

CLI検証スクリプトはPython標準ライブラリのみで実行する。実行ログ、試験用Git/Beads DB、worktreeは `state/` に隔離し、Gitへ含めない。
独立Nodeテスト・レビュー結果・Issueコメントを照合し、AIの成功申告だけで完了判定しない。

```bash
# Phase 2（導入手順はContext Builder文書を参照）
.venv/bin/aiops issue context handoff-poc poc-eye.3 --mode standard

# 実機情報・各CLIヘルプ・利用可能モデルを収集（推論なし）
python3 scripts/probe_environment.py

# 合成入力による非対話・構造化出力とLocal推論の検証
python3 scripts/probe_environment.py --agents --fm-local
```

PCCは現在の実行コンテキストでは利用できないため保留。グローバル認証・権限設定は変更せず、実装試験は個別許可とsandboxを利用する。
詳細は互換性表を参照。スクリプトの終了0はProviderや実装の合格と同義ではない。

GUIは `.venv/bin/aiops serve` で起動し、`http://127.0.0.1:8765`を開く。接続コードは起動時に案内されるローカルファイルで確認する。
