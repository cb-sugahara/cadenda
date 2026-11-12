# 未解決制約と対応

更新: 2026-11-12 / 初期の実測結果

| 制約 | 対応・後続ゲート |
|---|---|
| PCCはCodex起動コンテキストから使用不可 | Phase 5保留。Terminal/GUIセッションで本人が条件を確認する。fm serveで回避できるとは仮定しない。別クラウドへ自動代替しない。 |
| Beads Embeddedの全体doctor未対応 | 対応する3個別チェックとIssue/依存の実操作で確認。全体診断は未検証のまま明示し、必要時だけServerモードを再評価。 |
| AIの終了0でも未実行になり得る | Antigravityのdenied_actions、stderr、成果物、独立テストを確認。Beads doctorもunsupportedを判定する。 |
| Antigravity autoモードは現アカウントで不可 | Workerはdefaultと個別permissions.allowを使用。グローバル設定は変更しない。 |
| CLIは通常の認証領域書込みやローカルIPCを必要とする | 許可された実行コンテキストで検証。launchdのログイン条件・認証継続はPhase 7で別途実測する。 |
| CLI版更新による契約変更 | 互換性表に対象版を記録。更新後に同じ合成入力で再検証し、アダプタの前提を更新する。 |
| 手動PoCと常駐基盤は別 | PoC成功だけではクラッシュ復旧・排他・Outbox・承認認証は証明されない。Phase 3以降の受け入れ条件で検証する。 |
| Issueと成果物の参照切れ | PoCはローカルstate配下を保持。Phase 2で欠損警告、Phase 7で整合バックアップ・別場所復元を実装する。 |
| 承認イベントの本人確認 | 今回はユーザーのチャット回答を根拠として記録する。イベントJSONやactor名だけを認証と扱わない。GUIの本人認証はPhase 6。 |
| Phase 2のreceiptとトークン見積り | receiptは所有者管理の設定であり認証APIではない。token見積りはUTF-8バイト数の保守的近似。実Provider上限は測定してからプロファイルへ設定する。 |
| 実行管理の運用境界 | SQLite Recorder・排他・Outbox・停止/復旧はPhase 3で検証済み。チェックは所有者が信頼するコードとし、OSサンドボックスや本番ネットワーク隔離を保証しない。常駐運用・本人認証は後続工程。 |
| テストIssueに対するclose試験 | CLI fixtureのみ直接closeを検証。本PoCの子Issueは独立テスト・Codexレビュー・引継ぎ記録を確認してから閉じる。 |
