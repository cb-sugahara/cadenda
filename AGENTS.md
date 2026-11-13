# cadenda 作業規約

- 設計の基準は `docs/macmini_ai_development_implementation_plan.md`。工程ごとの完了条件を検証し、未合格のまま後工程を大量実装しない。
- 今後の優先順位は計画v2.1のN0〜N5。単一プロジェクト・単一Runのv0を先に実用化し、旧Phase 7の全項目完了を通常画面の改善の前提にしない。N0/N1/N2は完了（docs/v0-progress.md）。N3はworker-pocの実AI実装・固定テスト・実Codexレビュー・確認画面が成功。本体の追加AI編集は未追跡howto.mdによるclean条件未達で未実施。ユーザーの未追跡ファイルを勝手に無視・移動・コミットしない。N5のBeads通知2種類化は未実施。
- 過去の検証は `docs/issue-handoff-poc.md` と `docs/phase2-results.md`〜`docs/phase7-results.md`。Phase 7初回記録以降、実機launchdの起動・再起動は確認済み。旧Context 18件と隔離保存改修4ファイルはstate/archivesとstate/contexts_legacyへ保全済み。元バイトを確認し4ファイルは確定済みの版へ戻した。メイン完全バックアップは未完了だがv0の先行条件にしない。PCCは別許可・条件確認まで保留。実測・推測・未検証を区別する。
- Cadenda自身の開発にも `aiops issue context`、`aiops run check`、Recorder/Outboxを利用し、成果物の動作確認と実装を同時に進める。
- Issueの正本はBeads。実行ログ・成果物は `state/` に保存し、公開可能な検証結果のみ `docs/` に記録する。代替Issue DBを作らない。
- PoCのソース編集は試験リポジトリのworktree内に限定する。Control Planeの検証スクリプト・スキーマ・文書はこのリポジトリで管理する。
- CLIは実機の版とヘルプを確認する。Shell文字列で外部入力を実行せず、引数配列・タイムアウトを使用する。
- 終了コード0だけでAI作業を成功と判定しない。構造化出力、stderr、差分、独立テストを確認する。
- 認証情報・秘密情報を読まない、Issueやログへ転載しない。認証や利用規約への同意は人間が行う。
- 権限チェックを無効化するフラグを使わない。本番操作・公開デプロイは実施しない。PCCは初期無効、別クラウドへの自動代替は禁止。
- 作業終了時に互換性表・リスク・引継ぎ文書を更新し、未完了のゲートと再開手順を残す。

- 簡素化でもOrigin / Host / CSRF、候補treeのwrite-tree照合、管理対象PGID全体の停止、one_active_workerは維持する。staging.pyはv0経路から除外し、Gitのstage操作とは区別する。
