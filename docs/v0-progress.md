# Cadenda v0 進捗

2026-11-13 / 計画v2.1。N0完了、N1実装開始。

N0では旧バックアップ改修4ファイルを`state/archives/n0-20261113T062805Z/changes.patch`と元ファイルへ保存した。逆適用前の照合と、確定済み版へ戻した後のパッチ再適用チェックが成功した。計画文書等の別差分は維持した。

旧builder 0.2.0の18件は`state/contexts_legacy/20261113T062805Z/`へ退避した。元ディレクトリを移動し、全ファイルのSHA256が移動前と一致することを確認。元パス・退避先・各ハッシュは同archive内の`manifest.json`。正常な新しいContextは移動していない。専用の旧形式ローダーや世代間保存処理は追加していない。

この退避は同じSSD内の保管であり、別媒体のバックアップではない。既存の通常backupにはlegacyディレクトリを含めない。必要時はlegacyディレクトリとarchiveのmanifestを所有者が別の場所へ手動コピーする。メイン3プロジェクトの完全バックアップ・旧18件の再生は未完了であり、N1/N2の前提にしない。

メインLaunchAgentのAPI応答・正常停止後の再起動は確認済み。物理Mac再起動は実施していない。ローカル証跡は`state/phase7/main-launchd-live.json`、`main-launchd-restart.json`。
