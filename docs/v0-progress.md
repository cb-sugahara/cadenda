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
