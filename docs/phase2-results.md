# Phase 2 実装・検証結果

2026-11-12 / 作業Issue: `cadenda-3l6` / branch: `implementation/phase2-context`

**Phase 2の受け入れゲートを通過。** 前回の実Issue `handoff-poc / poc-eye.3`だけを指定して、承認済みJST仕様と根拠を、実Beads・Git・既存Run/Artifactから再生成できた。
テストは **41成功・0失敗**。別の読み取り専用Codexセッションの最終レビューは`approved`、指摘0件。
レビューはソースと実行証跡の照合であり、レビューAIがテストを実行したという意味ではない。

## 成果物

- Pythonパッケージ `packages/cadenda/`、`aiops`コマンド、`pyproject.toml`と実機依存版の`requirements.lock`
- Pydantic設定・Project Registry・明示的なパス許可リスト
- 読み取り専用BeadsAdapterと安定したエラー分類
- `standard / debug / review / full`、関連Issue・親子・依存の取得と循環/上限の検出
- コメントID・ハッシュ・承認根拠に結び付けた、所有者管理の正式決定receipt
- Git、プロジェクト文書、既存Run、テスト/レビュー/ログの参照結合
- 予算内の出力、正式決定を落とさない停止条件、プロファイル別上限、ソースのページ取得
- 時刻・版・入力設定・ソースハッシュを含むSnapshot、保存済みContextの再読込と破損検知
- 型から出力した設定・Context・Evidence CatalogのJSON Schema

使い方と設計上の境界は [context-builder.md](context-builder.md) を参照。
Issueの正本は引き続きBeads。Snapshotは時点記録であり、Issue情報を管理する新しいDBは作っていない。

## 実行した確認

```bash
.venv/bin/pytest tests --poc-config=config/projects.yaml --junitxml=state/phase2/tests.xml
.venv/bin/python -m compileall -q packages/cadenda
.venv/bin/python -m pip wheel --no-deps . --wheel-dir state/phase2/wheels
```

| 対象 | 確認結果 |
|---|---|
| 実機4モード | 全モードでJSTの正式決定を確認し、ソースのID・ハッシュ・参照を保持 |
| standard | 最新引継ぎと最終44件成功のテスト要約を予算内に表示 |
| debug | 過去の150秒timeoutの失敗/中断記録を優先して表示 |
| review | 現行要件、差分、記録されたテスト/レビューを取得 |
| full | 詳細をSnapshotへ保持し、任意のページから復元可能 |
| 新プロセスreplay | PATHからBeads/Gitを外しても、保存Snapshotを再表示できた |
| 整合性 | Snapshot・Artifactの欠損/改変、移動したHEAD、未解決参照を拒否または警告 |
| パス・プロセス | 許可外パス・中間symlink・認証ファイルを拒否。出力超過/timeoutでCLIのプロセスグループを停止 |
| 正式決定 | 偽装タグを昇格せず、最新receiptが不一致でも古い仕様へ戻らない |
| 機密情報 | 合成キー/値/参照のマスク、キー衝突時の値保持、端末制御文字の除去を確認 |

取得予算24,000は実tokenizerでのtoken数ではなく、UTF-8バイト数による保守的な見積り。
`BUDGET_TRUNCATED`や`SOURCE_EXCERPT`は詳細をページ取得に回したことを示し、データ欠損やテスト失敗とは区別している。
実機Snapshot ID、各モードの大きさ・警告・ソース数は [verification.json](evidence/phase2/verification.json) に記録。

## レビューで検出して修正したもの

1. **重複コメントIDによる承認文の取り違え**：数値/文字列で正規化した重複を拒否し、照合したコメントと表示ソースの一致を検証。
2. **存在しないIssueによる探索上限回避**：成功数ではなく取得試行数を数え、失敗とreceipt参照にも共通上限を適用。正式決定を先に取得。
3. **JSONキー中の秘密文字列**：キーも値と同じくマスクし、衝突したキーを安全なハッシュ付き名称で区別して値を保持。参照や診断情報も処理。
4. **不正なstatus型でのTypeError**：配列・オブジェクトを含む応答を型確認後に判定し、`SCHEMA_MISMATCH`として返す。

修正前の再現では、最初の指摘群で5ケース、追加のstatus型で2ケースが失敗することを確認した。
その後の全体テストで41件成功。2回の変更要求と最終承認を削除せず保存している。
そのほか、壊れたmanifestの分類、予算上限、プロンプト注入、別プロセスreplayなども検証した。

## 証跡と再開

- [テスト出力](evidence/phase2/tests.txt) / [JUnit](evidence/phase2/tests.xml)
- [最終レビュー](evidence/phase2/review-result.json) / [レビューしたソースのハッシュ](evidence/phase2/review-source-hashes.json)
- [生成したstandard Context](evidence/phase2/context-standard.md)
- [証跡のハッシュ索引](evidence/phase2/index.json)

元の全ログ、Review入力、Context本体はGit対象外の`state/phase2/`と`state/contexts/`に保持。
ソースのコミットIDはBeadsの完了コメントと`state/phase2/commit.txt`に記録する。
このMacのローカル設定は`config/projects.yaml`。他環境は`config/projects.example.yaml`から登録する。

## 次の工程

Phase 3はSQLiteのRun/Event/Artifact/Approval/Outbox、worktreeとProcess Controller、独立Test Executor、排他・中断・再起動復旧。
現在のEvidence CatalogはPhase 1の既存証跡を読み取る接続層であり、本番のExecution Recorderではない。
最初にRecorderの型とSQLite migrationを設け、このContext Builderへ読み取り境界を接続する。
lease切れだけで再実行せず、途中差分とプロセス生存を確認し、二重実行しないことを受け入れ条件にする。
PCCは前回どおり保留、GUI・常駐・デプロイは未実装。
