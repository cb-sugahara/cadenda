# 実機・CLI互換性確認

実測日: 2026-11-12（JST）。Phase 0の合成入力と隔離DBで検証。
公開可能な証跡は [evidence/index.json](evidence/index.json)、元ログはGit対象外の `state/` に保存。

| 対象 | 実機・版 | 結果・制約 |
|---|---|---|
| macOS / CPU / RAM | 27.2.1 / arm64 / 51,539,607,552 bytes（48 GiB） | 確認済み。Codex実行sandbox内のsysctlは拒否され、通常権限で再確認。 |
| Xcode CLI Tools | `/Library/Developer/CommandLineTools` | パス確認済み。GUI版Xcodeの動作は未検証。 |
| Python / Node / Git | 3.14.7 / v26.8.1 / 2.50.1 (Apple Git-158) | 計画の最低版を満たす。 |
| Homebrew | 6.0.22 | Beads/Dolt導入成功。ネットワーク制限されたsandbox内ではDNS取得が失敗。 |
| Beads | 1.3.4 (Homebrew) | Embedded Doltでinit・Epic/子Issue作成・コメント・取得・依存・ready・closeを実測合格。 |
| Dolt | 2.7.3 | Embeddedモード確認。外部SQLサーバーは起動していない。 |
| `bd doctor --json` | Embedded | **未対応**。stderrへ`unsupported: true`を返すが終了コード0。全体診断の合格とは扱わない。 |
| `bd doctor --check=...` | artifacts / conventions / pollution | 3種類の個別チェックは実行成功。全体診断やバックアップ復元の代わりにはしない。 |
| Codex | 0.167.0 | 非対話exec、JSONL、JSON Schema、最終JSONファイル出力を確認。既存認証を使用。 |
| Antigravity | 1.5.6 | sandbox付きheadless、JSON envelope、structured_output、利用量を確認。 |
| Antigravity編集権限 | 一時設定の`permissions.allow` / `ask` | 指定テキスト作成は成功。Ask対象は作成されず、exit=0 / status=SUCCESSでも`denied_actions`とstderrの拒否通知が存在。 |
| Antigravity mode auto | 現在のアカウント | **利用不可**との警告後、defaultモードへ継続。Workerはautoを前提にしない。個別許可を使う。 |
| Antigravity timeout | `--print-timeout 1ms` | exit=3、status=ERROR、finished_reason=print_timeout。外側にもプロセスグループ単位のタイムアウトを設定。 |
| fm Local | `/usr/bin/fm` / AFM 3 Core Advanced | 通常権限の非対話`respond --model system --no-stream`で`CADENDA_OK`取得。独立のCLI版番号は未取得（OS付属）。 |
| fm PCC | 同CLI | **保留**。通常権限でも「Private Cloud Compute is not available in this context. Please use the Terminal app.」。sandbox内ではiCloudアカウント必須という別エラー。実際のサインイン状態は未判定。 |
| fm quota | `quota-usage` | Localは対象外。PCCは実行コンテキスト制約で取得不可。数値や復旧時刻を仮定しない。 |
| fm serve | 一時Unixソケット | `/health`と`/v1/models`が200、試験後プロセス停止済み。サーバー経由の推論とPCC回避は未検証。 |

## 実行条件と再検証

Phase 2の追加検証: Pydantic **2.14.3**、PyYAML **6.0.3**、pytest **9.2.0**をプロジェクト内`.venv`で使用。
Beads 1.3.4の`--readonly --sandbox`、`show --brief-deps --include-dependents`と`comments --json`を実データで確認した。

CLIの認証領域への通常ログ書込み、ローカルIPC、AppleモデルサービスはCodexの制限sandbox内では失敗した。
認証情報ファイルは収集せず、許可された通常実行で比較した。エージェント側のsandboxフラグは有効に保った。
この確認はlaunchdによるバックグラウンド常駐、再起動後の認証継続、利用規約適合を保証しない。

```bash
python3 scripts/probe_environment.py
python3 scripts/probe_environment.py --agents --fm-local
python3 scripts/beads_smoke.py
python3 scripts/probe_agent_permissions.py
python3 scripts/probe_fm_serve.py
```

PCCの追加試験は明示的な`--pcc`でのみ実行する。各試験は新しい証跡ディレクトリを作成し、既存結果を上書きしない。
`probe_environment.py`の終了0は証跡収集の完了を示す。Provider合格は各manifest・構造化応答・実際の成果物で判定する。

## 参照した一次資料

- [Beads README](https://github.com/gastownhall/beads) — 導入・Issue中心のワークフロー。対象版の実機ヘルプで照合。
- [Codex非対話モード](https://learn.chatgpt.com/docs/non-interactive-mode?translationFallback=ja-JP) — execの構造化出力。
- [Antigravity Headless](https://antigravity.google/docs/cli/headless/) と [権限設定](https://antigravity.google/docs/permissions?tab=cli) — headless権限と結果形式。
- fmの仕様は実機`respond --help`、`serve --help`と各コマンド出力を証跡保存。

## Phase 5追加実測（2026-11-12）

AFM 3 Core Advancedのsystemで構造化出力4タスクを確認。`fm respond --schema ... --instructions ... -- INPUT`を使用し、スキーマをRecorderへ不変保存する。PCCは引き続きTerminal起動コンテキスト制約で利用不可。`fm quota-usage`はsystem非適用/PCC unavailableで終了1。実PCC quota到達は未検証。比較・境界・再開ゲートは[Phase 5記録](phase5-results.md)を参照。

## Phase 6追加実測（2026-11-13 JST）

FastAPI 0.162.5 / Uvicorn 0.54.0 / httpx 0.28.1、React/Viteの確定版は`web/package-lock.json`を参照。HTTPは127.0.0.1限定。実Beads作成・更新・コメント、実CLI check停止/再開と承認をPlaywrightで確認。TestClientのhttpx非推奨警告は1件。localhost HTTPのためCookieはHttpOnly/SameSite Strict、Secureは付与しない。本人認証の境界・再開方法は[Phase 6記録](phase6-results.md)を参照。

## Phase 7追加実測（2026-11-13 JST）

Dolt native backup init/sync/restoreと2プロジェクトの別場所復元を確認。bd initは--jsonを無視し、未初期化repoの-Cを拒否するため、cwdを指定した別の一時repoで初期化する。Beadsの祖先探索を避けるため全通常呼出しはBEADS_DIRとembedded実パスを検証する。Git bundleはHEAD・Recorderのcommit/base_commit・旧カタログのcommit/base_commitを明示保存する。158件のPythonテスト（実Beads Context再生を含む）、API停止3回と明示reset、合成所有者の別承認によるローカルstagingを確認。実launchd登録は成功したがAPI応答未確認、物理再起動・ログイン後のCLI認証継続は未確認。詳細は[Phase 7記録](phase7-results.md)。

## v0 N1/N2追加実測（2026-11-13）

環境許可リスト、既定10MiB出力と保存ログの先頭/末尾、単回ローカル接続、1本の進行表示を実装。171 Python試験・Node状態試験・Chromeの停止/再開と実AIパイプラインに成功。OS所有者の起動ticketは60秒・server instance結合、Host/Origin/CSRFは維持。CLI依存の追加はない。詳細は[v0進捗](v0-progress.md)。

Beads一覧のfull edge（issue_id / depends_on_id / type）と詳細のbrief link（id / dependency_type）の両形式を確認した。2026-11-13のTOP不具合修正で両者を正規化し、174試験と登録3プロジェクトの実ブラウザ一覧HTTP 200を確認した。
