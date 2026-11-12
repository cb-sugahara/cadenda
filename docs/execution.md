# Phase 3: 実行管理と運用

IssueはBeads、実行状態はSQLite `state/orchestrator.sqlite3`を正本とする。
この段階は所有者がローカルCLIから明示的に実行する構成。GUI・常駐スケジューラ・本番デプロイ・Issue自動closeは提供しない。

## 実行

所有者管理の`config/projects.yaml`でプロジェクトに固定チェックを設定する。

```yaml
recorder_enabled: true
checks:
  - check_id: unit
    argv: ["/absolute/path/to/python", "-m", "pytest", "tests"]
    timeout_seconds: 300
worktree_roots: ["/absolute/path/to/dedicated-worktrees"]
artifact_roots: ["/absolute/path/to/state/artifacts"]
```

`worktree_roots[0]`を実装Runに使用。ソースのrootそのものは指定できない。
checksは登録済み設定から取得し、Issue・AI応答から任意のコマンドを実行しない。Shell文字列結合は使わない。
テスト用環境は必要最小限の環境変数と専用HOMEを使い、Pythonはworktreeのpackages/srcを優先する。
チェック自体は所有者が信頼するコード・コマンドを前提とし、OSサンドボックスや本番ネットワーク隔離を保証するものではない。本番秘密情報を持つチェックは登録しない。
Antigravityは既存認証を使い、sandboxとworktree限定の編集許可、コマンド・外部ツール拒否で起動する。

```bash
# 実装Run: open、受け入れ条件あり、依存解消、ソースがcleanの場合のみ
.venv/bin/aiops run start PROJECT ISSUE
# 同じworktree手順で、AIを呼ばず固定チェックだけを実行
.venv/bin/aiops run start PROJECT ISSUE --checks-only
# 現在の登録済み作業ディレクトリで、開発中のコードを診断（dirtyでも可）
.venv/bin/aiops run check cadenda cadenda-h3o

.venv/bin/aiops run show run_...
.venv/bin/aiops issue context cadenda cadenda-h3o --mode debug --budget-tokens 60000
```

`run check`は独立した明示的な診断。完了済み診断の再実行はできるが、実装Runを暗黙に再開しない。
`run show`は状態、イベント、成果物のハッシュ、承認待ちを表示する。内部のWorker所有権トークンは表示しない。
Context BuilderもSQLiteを読み取り専用で参照する。旧Evidence Catalogは引き続き過去のPhase 1/2証跡に使用できる。
互換Run表現では`outcome=unknown`の進行中記録の時刻は最終更新時刻として扱い、正確な工程・実行中判定は`run show`のSQLite状態で確認する。

## 状態と排他

Ready判定の後、SQLiteの排他トランザクションでClaimedを取得し、Running → Verifying → Reviewへ進む。
同じIssueだけでなくホスト全体でアクティブな実装/診断Runを1件に制限する。NeedsHuman/Interruptedも解消までは枠を保持する。
失敗した実装の明示的な再試行は初回+2回まで。権限拒否・不正応答・不明な失敗はNeedsHumanへ止める。
テスト失敗はReviewに進めず、exit=0でもAntigravityのdenied_actions・stderr・構造化出力・差分を照合する。
Reviewは「独立チェックが成功し、次のレビューを待つ」状態で、Issueの完了や承認済みを意味しない。
Review状態と対象コミットに結び付いた承認待ちは同じSQLiteトランザクションで作成する。承認を付与するAPIはまだない。

## 停止・復旧

```bash
.venv/bin/aiops run cancel run_...
.venv/bin/aiops run recover
# NeedsHumanの元Worker・プロセスグループが存在しないことを確認した後のみ
.venv/bin/aiops run release run_...
```

キャンセルはSQLiteに要求し、生きているWorkerが子のプロセスグループを停止する。
子は信頼済みのPythonゲートを`-I -S`で起動し、親がPGIDを永続記録してから実処理へexecする。
ゲート許可前に親が死ねば子はEOFで終了する。Gate後の親クラッシュでは記録したPGIDとworktreeを保持してNeedsHumanにする。
テスト・AIだけでなく、変更を伴うworktree作成・stage・commitにも同じゲートを使う。
timeout・出力上限超過・キャンセルではグループを停止し、取得できたstdout/stderrを機密パターン除去後に保存する。

lease切れだけでWorkerを奪ったり、別プロセスかもしれないPIDを殺したり、同じIssueを再実行したりしない。
`recover`は元Workerが消えた記録をNeedsHumanへ移し、旧試験の不足した承認待ちも冪等に補完する。
`release`は元Worker/グループが残る間は拒否する。解放してもworktree・ログは削除せず、次のRun開始も別途明示する。
途中差分・未追跡ファイルは専用worktreeを残して保全する。自動削除は無効。最終レビュー・マージ・成果物照合後の保持/削除は後続工程で扱う。

## イベントとOutbox

```bash
# Run終了後、所有者が明示的に同期
.venv/bin/aiops run flush
```

イベントとOutboxは同じトランザクションで保存する。Issueへは要点とイベントIDを送信し、大きなログはファイル参照にする。
OutboxはSQLiteの単一ライターを保持して送信し、再送前にBeadsのコメントを照合する。
送信後・ack前に落ちても、同一イベントID・本文・Issue IDが完全一致すれば重複送信しない。衝突する内容はOUTBOX_CONFLICTで止める。
別の書込み窓口を同時運用しない。flush中はDBの書込みが待機するため、まずRunを終了させてから同期する。

## 今回の自己利用

- Phase 2のContext Builderで`cadenda-h3o`の引継ぎを復元して着手。
- 実Antigravityの小課題を新Workerで実装し、独立チェック、worktreeコミット、Review停止を実機確認。
- Cadenda自身のテストを`run check`で実行し、結果・ハッシュをRecorderへ保存。
- その結果をContext Builderから読み直し、OutboxでBeadsへ参照可能にした。
- 別のCodexセッションで実装をレビューし、検出したクラッシュ窓を回帰テスト付きで修正。

詳細なRun ID・テスト結果・レビューは`docs/phase3-results.md`を参照。
