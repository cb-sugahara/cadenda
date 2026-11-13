# Phase 7: 運用・復元・限定staging

作業Issue: `cadenda-a2s`。実装と隔離環境での検証を進めた。**実機launchdからのAPI応答確認が未合格のため、Phase 7全体は未完了**。

## 実装した機能

`aiops ops`にhealth、backup / verify / restore、supervise / reset / status、launch-plan / install、ci、stage-planを追加した。Settingsから監視状態を取得できる。Issueは引き続きBeads、Run・承認・受付はSQLiteを正本とする。

supervise稼働中の監視は5分間隔でディスク、死亡・停滞Run、コマンド失敗、成果物の破損、Beads接続、24時間を超えたバックアップを確認する。`health --probe`は推論を行わずCodexのログイン状態とfmのモデル一覧を確認する。Antigravityの非推論認証確認コマンドは利用できず、保存済みの認証失敗・quota・ネットワーク失敗を検出する。認証ファイルの中身は収集しない。実アカウントの認証失効やquota消費を意図的には起こしていない。

superviseはAPIを子プロセスとして起動する。600秒以内の開始を最大3回に制限し、最後の開始時点で上限を永続化する。上限到達後は時間経過やsupervisor再起動だけでは解除せず、明示的なresetを待つ。resetと起動受付は専用ロックで直列化する。AI Workerの再実行はしない。停止Runは既存の安全な復旧・所有者による再開手順を使う。

LaunchAgentはユーザーのGUIログイン領域、RunAtLoad=true、KeepAlive=false。API再起動はsupervisorが担当する。supervisor自体の停止は人間が確認して再起動する。Mac全体の再起動・ログアウト・スリープを実際には実施していない。

## バックアップと復元

実行中Runと未解決の起動受付がないことを確認し、maintenanceの排他ロックを取得する。Recorder書込み、GUIの更新操作、Context保存は共有ロックを取得する。古いGUIプロセスが稼働している場合は、対応版への再起動を要求して止まる。外部CLIによる同時変更は運用上停止する必要があり、Beadsは前後のIssue・コメント・関係ハッシュを照合する。

保存対象はBeadsのDoltネイティブ履歴、Gitの全refと記録済みcommit/base_commit、所有者の設定、SQLiteの整合スナップショット、登録成果物、検証済みContext、登録されたGUIジョブログ。旧カタログの未参照コミットもbundleへ含め、復元時は正確なcommitのworktreeを作る。未追跡ソース、CLI認証、ペアリングコード、HOME全体は含めない。バックアップはローカルの0700ディレクトリ・0600ファイルに保存する。別ディスク保管・暗号化・保存世代の自動削除は未設定。

バックアップ先は`state/backups/backup_*`。native作業先は`state/native-backups/PROJECT`。既存のBeads backup URLが異なる場合は上書きしない。リンク・特殊ファイル・改変・不足/追加ファイルを拒否する。ハッシュmanifestは偶発破損の検査であり、manifestごと改ざんする所有者に対する署名ではない。

復元は**存在しない新規の絶対パス**に限定し、既存DBを上書きしない。Git内の`.beads` scaffoldは別に保存し、信頼して初期化に流用しない。Beads initの`-C`は未初期化リポジトリを拒否し、祖先DBを探索することがあるため、祖先に本プロジェクトがない一時Gitリポジトリで初期化する。既存DBへの強制初期化は使わない。ネイティブrestoreのforceは新規の復元DBだけに適用する。元バックアップは私有コピーから復元して変更しない。

復元設定はchecks空・cloud/fm/staging無効。歴史的なイベントとContext内の元パス・ハッシュは改変せず、参照済み成果物・worktree・旧カタログは復元先へ移す。所有者が構成と内容を確認するまでコードを実行しない。

```bash
.venv/bin/aiops ops health
.venv/bin/aiops ops backup
.venv/bin/aiops ops verify /absolute/path/to/backup_ID
.venv/bin/aiops ops restore /absolute/path/to/backup_ID /absolute/new/restore-directory
```

毎日、静止時間帯にbackupとverifyを実施し、少なくとも設定やスキーマ変更後・月次に新規ディレクトリへの復元を照合する。5分監視が24時間を超えたバックアップを通知する。**自動定時バックアップと定期復元ジョブはまだ登録していない**。バックアップが取得できない場合は既存パケットを削除せず、busy・tracked source差分・保存先競合・容量を確認する。

## 複数プロジェクトとBeads

既存Registryの許可パス・project ID分離を利用し、全Beads呼出しに明示したBEADS_DIRを渡す。redirect、未初期化、server mode、プロジェクト外DBを拒否し、通常の読取り/更新でもembeddedの実データパスを確認する。複数プロジェクトの隔離復元でIssue・コメント・依存関係を照合した。現在の構成でDolt Serverへの移行を必要とする条件は観測しておらず、移行しない。多人数・別ホストからの同時接続が必要になった場合に再評価する。

## CIとローカルstaging

`aiops ops ci PROJECT ISSUE`は登録済みの固定チェックを実行し、Recorderへ証跡を保存する。外部CIへの鍵設定やリモートworkflow実行は行っていない。

stagingは所有者設定の`staging_enabled`が必要で、初期値false。候補はAntigravity executorのcommit、成功した固定チェック、不変の検証tree、独立レビュー、現在の関連要件ハッシュ、成果物への所有者の承認を満たす必要がある。その後、GUIでstaging内容を確認して**別の所有者確認**を行う。既存のReview承認はこの操作の許可を兼ねない。

出力は`state/staging/PROJECT/RUN-COMMIT`に限定する。Git commitのblobとarchive内容を照合してコピーし、リンク・submodule・特殊ファイル・秘密/実行時ディレクトリを拒否する。公開直前にReview・承認・所有者の最新設定を再検証する。レビューartifactの作成イベント順で最新を選び、Beadsの更新と最終確認・配置を同じプロジェクトロックで直列化する。配置先の改変は拒否し、上書きしない。**配置時にソースを実行せず、外部公開も行わない**。隔離試験のmodel reviewとownerセッションは合成fixtureであり、実モデルの承認や実ユーザーの承認として扱わない。

本番フローは未有効化。将来の承認は、対象環境・現在のproduction revision・候補commit・差分・テストと独立レビュー・期限・rollback手順を一つの確認に結合し、stagingとは別の所有者確認を必須とする。環境ごとの最小権限credential、直前revisionの比較、監査証跡、失敗時rollbackを別リスク評価するまでproduction操作を提供しない。

## 証跡と残るゲート

[テスト](evidence/phase7/tests.txt)、[JUnit](evidence/phase7/tests.xml)、[自己検証Run](evidence/phase7/self-run.json)、[実ブラウザ](evidence/phase7/browser-live.json)、[運用画面](evidence/phase7/operations-desktop.png)、[隔離復元](evidence/phase7/final-restore.json)、[再起動上限](evidence/phase7/supervisor-live.json)、[staging fixture](evidence/phase7/staging-live.json)、[独立レビュー](evidence/phase7/review-result.json)を参照。レビューは提供したソース/証跡の照合であり、レビューAIがテストを実行したものではない。

実機launchdの一時登録は成功したが、観測時間中にAPI応答を確認できなかった。初期のAPI観測にはsandboxのネットワーク制限が混ざっていたため、許可された実機コンテキストでも再測定した。画面確認時にはMacがロックされており、手動解除が必要との結果だった。ロックがlaunchd不達の原因であるとはまだ断定できない。一時登録は解除した。通常GUIは新コードへ安全に再起動し、localhost応答とペアリングファイルの0600を確認した（コード本文は読んでいない）。正式なLaunchAgentの常用登録、メイン3プロジェクトの新方式バックアップ、物理的な再起動・ログイン後の認証継続は未合格。実機解除後、launchd status・ログとAPI応答を照合してから切り替える。PCCは従来どおり保留。

一次資料: [SQLite Backup API](https://www.sqlite.org/backup.html)、[Apple launchd.plist](https://github.com/apple-oss-distributions/launchd/blob/main/man/launchd.plist.5)、[Beads Dolt](https://github.com/gastownhall/beads/blob/main/docs/architecture/dolt.md)。挙動は実機ヘルプと試験結果を優先する。
