# cadenda 作業規約

- 設計の基準は `docs/macmini_ai_development_implementation_plan.md`。工程ごとの完了条件を検証し、未合格のまま後工程を大量実装しない。
- Phase 0・Phase 1は `docs/issue-handoff-poc.md`、Phase 2は `docs/phase2-results.md`、Phase 3は `docs/phase3-results.md` を参照。次工程はPhase 4（Codex Architect/Reviewer / Router）。実測と推測、未検証を明確に区別する。
- Cadenda自身の開発にも `aiops issue context`、`aiops run check`、Recorder/Outboxを利用し、成果物の動作確認と実装を同時に進める。
- Issueの正本はBeads。実行ログ・成果物は `state/` に保存し、公開可能な検証結果のみ `docs/` に記録する。代替Issue DBを作らない。
- PoCのソース編集は試験リポジトリのworktree内に限定する。Control Planeの検証スクリプト・スキーマ・文書はこのリポジトリで管理する。
- CLIは実機の版とヘルプを確認する。Shell文字列で外部入力を実行せず、引数配列・タイムアウトを使用する。
- 終了コード0だけでAI作業を成功と判定しない。構造化出力、stderr、差分、独立テストを確認する。
- 認証情報・秘密情報を読まない、Issueやログへ転載しない。認証や利用規約への同意は人間が行う。
- 権限チェックを無効化するフラグを使わない。本番操作・公開デプロイは実施しない。PCCは初期無効、別クラウドへの自動代替は禁止。
- 作業終了時に互換性表・リスク・引継ぎ文書を更新し、未完了のゲートと再開手順を残す。
