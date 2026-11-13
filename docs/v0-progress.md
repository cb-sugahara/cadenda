# Cadenda v0 進捗

2026-11-13 / 計画v2.1。N0完了、N1実装開始。

N0では旧バックアップ改修4ファイルを`state/archives/n0-20261113T062805Z/changes.patch`と元ファイルへ保存した。逆適用前の照合と、確定済み版へ戻した後のパッチ再適用チェックが成功した。計画文書等の別差分は維持した。

旧builder 0.2.0の18件は`state/contexts_legacy/20261113T062805Z/`へ退避した。元ディレクトリを移動し、全ファイルのSHA256が移動前と一致することを確認。元パス・退避先・各ハッシュは同archive内の`manifest.json`。正常な新しいContextは移動していない。専用の旧形式ローダーや世代間保存処理は追加していない。

この退避は同じSSD内の保管であり、別媒体のバックアップではない。既存の通常backupにはlegacyディレクトリを含めない。必要時はlegacyディレクトリとarchiveのmanifestを所有者が別の場所へ手動コピーする。メイン3プロジェクトの完全バックアップ・旧18件の再生は未完了であり、N1/N2の前提にしない。

メインLaunchAgentのAPI応答・正常停止後の再起動は確認済み。物理Mac再起動は実施していない。ローカル証跡は`state/phase7/main-launchd-live.json`、`main-launchd-restart.json`。

## N1

汎用captureの既定環境をPATH / HOME / locale / TMPDIR等の許可リストへ変更した。明示envは内部の呼出元が与え、Beadsの名前空間固定とWorkerの環境制限を維持する。

Workerのstdout/stderr合計上限は既定10MiB、Projectの`max_output_bytes`で64KiB〜64MiBの範囲に設定可能。構造化応答は上限内では完全なデータを返し、保存ログは各streamの先頭・末尾を合計1MiBまで残す。中間省略と観測出力量を記録し、上限超過時はPGIDを停止してOUTPUT_LIMITにする。停止後に出なかった末尾を保存できるとは扱わない。

全161試験（実Beads Contextの再生含む）が通過。2MB応答が完全に返ること、保存ログの先頭・末尾、明示上限超過、汎用captureでのダミー秘密環境変数の除外を確認した。候補tree照合・PGID停止・単一Run排他の試験も維持。

## N2

`.venv/bin/aiops open`で既存GUIのブラウザを開く。コードファイルを手で探さず、OS所有者が作った0600の単回ticketと私有HTML経由で接続する。ticketは60秒で失効し、実プロセスのロック・サーバーinstanceに結合する。秘密値をopenのプロセス引数に渡さず、URL fragmentはフロントエンドで直ちに消す。既存のHost / Origin / CSRFと手動ペアリングのフォールバックは維持。

DashboardとRun詳細は「実装→テスト→レビュー→人間確認」の1本のステッパーを使う。固定チェックだけなら実装・AIレビューは対象外。実行中Runのactiveスロットを優先表示し、レビュー完了にはコマンド成功とレビュー後のReview遷移を要する。却下と完了は区別する。stagingボタンは通常画面から除外した。

170件のPython試験、10件のフロントエンド状態シナリオ、Chromeの実ブラウザ試験に成功。単回接続、再起動した別instanceへのticket拒否、外部Origin・CSRFなしの変更拒否、不正UTF-8出力の保存後サイズ・成果物閲覧・承認プレビュー、CLIチェックの大小設定上限を確認。独立Codexレビューapproved。実ブラウザはクラウド無効の隔離GUIでIssueを作成し、固定チェックの停止と再開を実行した。

ブラウザの認証操作は新規の試験セッションで行い、既存OS/CLI認証情報と手動ペアリングファイルの中身は収集していない。物理再起動・外部公開・PCCは未実施。

## N3: 実AIによる1件の完走

メインGUIから許可済みworker-pocの`worker-7bc`を実行した。実Antigravityが`answer.txt`だけを追加し、その内容が`CADENDA_OK`と末尾改行に一致することをGitのcommitから検証した。既存`check.py`は変更されず終了0、実Codexの独立レビューもapproved。Runは`run_7eec05d4b7e246828b229386f3104176`、commitは`7ed26256a231865adfc0ce0f3be64d021dc30846`。

同じRunの結果確認画面まで実Chromeで確認した。成果物の採用はpendingのままとし、merge・deploy・Issueの自動closeはしていない。試験中に見つかった中間Reviewの古い承認依頼の重複表示を修正し、inactiveなReview Runの最新依頼だけを表示する。独立レビューapproved、最終171テスト通過。

Cadenda自身のN1/N2/N3チェックは`aiops issue context`と`aiops run check`で実行し、Recorderへ保存した。実行中の仕事がないことを確認してLaunchAgentを正常再起動し、最終GUIを反映した。隔離GUIでは停止→再開を実行し、元のRunと新Runの証跡を保持している。

本体を対象にした追加のAI編集試験は、作業中に存在した未追跡`howto.md`によりclean-repository条件を満たさないため未実施。ファイルを勝手に無視・移動・コミットして条件を迂回していない。したがってN3全体は引き続き進行中として、この残件と実ユーザーによる採用判断を区別する。

## 現在の使い方

常駐GUIを開くには、リポジトリで`.venv/bin/aiops open`を実行する。接続コードの手動検索は不要。停止中は別ターミナルで`.venv/bin/aiops serve`を起動してからopenする。URLは`http://127.0.0.1:8765`。

試験成果物はworker-pocの`worker-7bc`から、または承認インボックスの同Issueから確認できる。通常の結果確認は自動merge・deployを起こさない。

N0・N1・N2は完了。N3は上記の実AI完走・UI確認が成功し、本体への追加AI編集が残る。N4の4エージェント監視は追加しない。N5の自動Beads通知2種類への限定は未実施で、既存Outboxを削除していない。

証跡: [最終試験](evidence/v0/final-tests.txt)、[実AI結果](evidence/v0/real-pipeline-result.json)、[最終差分レビュー](evidence/v0/final-review-result.json)、[結果確認画面](evidence/v0/real-result-confirmation.png)。

## TOPページ不具合の修正

Beadsの`list --brief`が返す依存関係は`issue_id / depends_on_id / type`を持つedge形式だった。GUIの一覧APIは`id / dependency_type`形式だけを想定し、ValidationError→HTTP 500になっていた。画面がその本文`Internal Server Error`をJSONとして解析していたため、`Unexpected token 'I'`が表示された。

Issueモデルで一覧のedgeと詳細のbrief linkを正規化し、依存先と被依存元の方向を区別するよう修正。未対応の不正形式はSCHEMA_MISMATCHのJSON応答とし、画面側も非JSONのエラー応答をHTTPステータス付きの案内へ変換する。

174 Python試験、5件のフロントエンド応答試験が通過。実ChromeでメインTOP、登録3プロジェクトの一覧HTTP 200、意図的なプレーンテキスト500時の案内、再読込後の復旧を確認した。修正済みGUIをLaunchAgentへ反映した。ユーザーの`howto.md`と`_review/`は変更していない。
