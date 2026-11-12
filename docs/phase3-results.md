# Phase 3 実装・検証結果

2026-11-12 / 作業Issue: `cadenda-h3o`

**全63テスト成功、最終の独立Codexレビューはapproved・指摘0件。**
SQLite Recorder、明示的な単一Run Worker、固定チェック、キャンセル/復旧、Outbox、Context BuilderからのRecorder読取を実装した。
テストは確定的なpytest実行で行い、レビューAIはソースと証跡を読み取った。レビューAIが独立にテストを実行したという意味ではない。

## Cadenda自身への活用

1. 既存`aiops issue context cadenda cadenda-h3o`で要件・前工程の引継ぎを取得して着手した。
2. 新しい`aiops run check`を使い、Cadenda自身のテストを診断Runとして実行した。改修中の55→57→61→62件のテスト結果をRecorderのArtifactへ保存した。
3. 最後のRun履歴表示テストを加えた全63件の実行結果も保存し、SQLite/成果物と独立レビューのソースハッシュを照合した。
4. SQLiteにある自己テストの履歴をContext Builderから読み取り、OutboxでBeadsへ実施内容を反映した。
5. 実Antigravityの小課題を新Workerで実行し、worktree編集→独立チェック→コミット→Review停止を確認した。

単なるJSONのモックではなく、実CLI・実Git worktree・実Beads・実SQLiteを使った。破壊的操作、本番デプロイ、外部公開は行っていない。

## 主な受け入れ条件

| 条件 | 実測・テスト |
|---|---|
| 重複実行防止 | 2接続からの競合取得で1件のみ成功。ホスト全体でactive Runを1件に制約 |
| 状態遷移 | 不正遷移を拒否。独立テスト失敗・exit=0の権限拒否でReviewへ進めない |
| 中断 | キャンセル要求・timeout・出力上限でプロセスグループを停止し、部分ログを保存 |
| クラッシュ | Controllerを実際に強制終了。残る子と途中ファイルを保持し、recover後のrelease/二重実行を拒否 |
| 起動窓 | PGID永続化前は子がGateで待機。親が死ねばEOF終了。`-I -S`によりsitecustomizeの先行実行も防止 |
| Git | 変更を伴うworktree/add/commitも同じGateを使用。失敗したworktreeは削除しない |
| Review/承認待ち | 同一トランザクション。承認INSERT失敗の注入でReview遷移もrollback。旧記録の不足は一度だけ補完 |
| Outbox | 外部送信直後のクラッシュでも重複送信せず、本文/Issueの不一致は衝突として停止 |
| テストの独立性 | AIにはコマンド実行を許可せず、登録済みargvでチェック。Pythonはworktreeソースを優先 |
| 機密情報 | 環境変数を絞り、テスト専用HOMEを使用。ログをマスクしてから保存 |

## 実機Run

- 実Antigravity最終smoke: `run_5d7ddb9f56674241a3f84a5d57fdd5a6`
  - `worker-poc`の隔離リポジトリでanswer.txtを生成し、独立check.pyが成功。
  - 元リポジトリは変更せず、専用worktreeのコミットとReview状態、承認待ちを保持。
- 最新のRecorder経由の自己テスト: [final-self-run-history.json](evidence/phase3/final-self-run-history.json)
- 最終全体テスト: [tests.txt](evidence/phase3/tests.txt) / [JUnit](evidence/phase3/tests.xml)
- 実機Runの状態・イベント・成果物・承認待ち: [final-live-result-history.json](evidence/phase3/final-live-result-history.json)
- 最終レビュー: [review-result.json](evidence/phase3/review-result.json)
- レビュー対象の正確なファイルハッシュ: [review-source-hashes.json](evidence/phase3/review-source-hashes.json)
- 証跡のハッシュ: [index.json](evidence/phase3/index.json)

Gateの追加強化後も、起動前の副作用・親クラッシュ・残存子・Git操作・状態rollbackを回帰テストで確認している。
実機smokeのIssueはWorkerが自動closeせず、Reviewの証跡として残している。

## レビュー修正

最初のレビューで、変更を伴うGit操作の起動窓、Reviewと承認待ちの別commit、Outboxの緩いmarker照合が指摘された。
次のレビューではPythonのsitecustomizeによるGate前の実行が指摘された。
各指摘を修正し、rollback注入・偽装marker・起動時のsentinelなどのテストを追加して最終承認を得た。
以前の変更要求も`docs/evidence/phase3/review-attempt-*.json`に残した。

## 運用境界と次工程

[execution.md](execution.md)にコマンドと復旧手順を記載した。
所有者が管理するローカルCLIが前提で、チェックは信頼するコード/設定に限定する。環境変数の除去はOSサンドボックスの代替ではない。
自動worktree削除、ジョブのバックグラウンド常駐、GUI、本人認証付き承認、本番操作は未実装。
ReviewはDoneではなく、承認待ちを作るだけで実行許可を付与しない。
次はPhase 4のCodex Architect/Reviewer、構造化応答、Router、修正ループとエスカレーション。
