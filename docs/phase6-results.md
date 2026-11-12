# Phase 6: ローカルGUI

作業Issue: `cadenda-1rm` / 2026-11-13（JST）

FastAPIとReactによる所有者向けのGUIを追加した。Issueの正本はBeads、Runと監査証跡はSQLite Recorderを使う。GUI専用の代替Issue DBは作らない。

## 起動と接続

```bash
# 初回または依存更新時
.venv/bin/python -m pip install -e '.[dev]'
npm ci --prefix web
npm run build --prefix web

# 所有者管理のconfig/projects.yamlを使い、Mac内だけで提供
.venv/bin/aiops serve --port 8765
```

`http://127.0.0.1:8765`を開き、起動時に案内される`state/gui-pairing-code.txt`の一回限りのコードで接続する。このファイルは0600、stateディレクトリは0700。Macの通常パスワードや既存のCLI認証情報は収集しない。

所有者認証は「このOSアカウントだけが読めるランダムな起動コードを所持している」ことに基づく。Touch ID・共有OSアカウント内の個人識別ではない。コードは一回使用、セッションは8時間。ログアウト・サーバー再起動で失効する。再ペアリングはサーバー再起動で新コードを発行する。コード・Cookie・CSRF値をIssueやAIへ送らない。

外部ネットワークへ公開しない。スマートフォン検証は390pxのブラウザ表示であり、スマートフォン実機からMacへ接続する機能ではない。リモート接続・HTTPS・常駐化は後続工程。

## 画面と操作

- Dashboard: 登録プロジェクト、実行中、確認待ち、最近のRun。
- Board: Beadsと最新RunからReady / Running / Review / Blocked / Needs Human / Doneを表示。200件上限とプロジェクト切替・検索。
- Issue Detail: 要件・受け入れ条件の編集、関連Issue、型付きコメント、正式な方針決定、実行履歴。
- Context Inspector: 4モード、Markdownプレビュー、コピー・保存、警告・参照元のページング。HTMLを実行しない。
- Run Detail: 実行先・状態・イベント・ハッシュ照合済み成果物・ログ。実行中の停止要求と、安全確認後の明示的な再開。
- Approval Inbox: 現在の要件・方針と成果物をプレビューして承認/却下。承認によるmerge・deploy・Issue自動closeは行わない。
- Settings: 許可済みプロジェクト・Provider設定の表示。登録パスやクラウド権限の変更は所有者管理ファイルで行い、GUIへ任意パス入力を公開しない。健康状態表示は設定のみで、認証/推論の実動確認とは区別する。

Run開始は固定チェック、許可済みCloudパイプライン、許可済みLocal 4タスクから選ぶ。任意コマンド・任意パス・PCCはHTTPから受け付けない。再開は旧Runを保存して新Runを作る操作で、元のWorkerと子プロセスの終了確認が必要。起動時と「設定と接続 → 停止した実行を確認」で死亡Workerを照合し、NeedsHumanへ復旧する。再開前の設定/競合チェックが失敗した場合は旧Runを解放しない。プロセス起動失敗時は再試行可能な状態を記録して保持する。明示的な再開にも既存の排他と試行上限を適用する。

## 判断と承認の整合性

正式な方針決定は、内容・対象Issue・現在の要件ハッシュを5分間有効な確認に結合する。確認後、Beadsへ正確なコメントを作成し、所有者セッションのreceiptをSQLiteへ保存する。Context BuilderはCLI利用時もreceiptを読み、参照コメントのハッシュと要件を照合する。タグやAIの`approved`だけは正式判断に昇格しない。要件やコメントが変われば未検証扱いに止める。自動ADR/AGENTS編集は行わない。

Runの承認は、最新のReview依頼、Run ID、成果物ダイジェスト、現在のIssue・コメント・適用される検証済み方針を結合する。プレビューは5分、依頼は24時間で失効。旧形式のハッシュ、改変成果物、古いプレビュー、二重決定は拒否する。従来Phase 4等の古い依頼はそのまま承認できず、新しいレビューが必要。承認確定と同じSQLite書込みトランザクションで、Run状態・最新の承認依頼・レビューbindingを再照合する。

Issue編集はダイアログを開いた時点のfingerprintを保持し、更新後の画面情報で古い編集を正当化しない。GUI内の書込みを直列化し、Beads保存後も内容を再取得する。現Beads CLIには任意revisionのCAS更新フラグがないため、外部CLIと同時に同じIssueを編集する運用は対象外。外部編集を行った場合はGUIで再読込する。

## APIと実行境界

固定Host / Origin / loopback、データ・操作APIのセッション認証、書込みのCSRF、JSON限定、要求本文64KiB・読取り10秒・同時バッファ16件の制限を適用する。CookieはHttpOnly / SameSite Strict。秘密パターンを含む入力はBeadsや推論へ送る前に拒否する。検証エラーで入力本文を返さない。CSP・HTML無効のMarkdown・外部画像取得の抑止を適用した。

SSEは登録プロジェクトのイベントとマスク済みログ抜粋を送る。再接続のcursorを検証し、最初のRun状態とcursorを同じSQLite読取りスナップショットで取得する。コマンドログは終了後の保存時点で通知し、行ごとのストリーミングではない。5秒ごとの更新通知でBeads側の変更も再読込する。別プロジェクトの未登録Runを一覧やSSEへ露出しない。

Outbox送信は専用のプロセス間ロックで直列化し、Beads呼出中はSQLite書込みロックを保持しない。送信中にも停止・heartbeatが書けること、送信後クラッシュの再照合、競合マーカー拒否を検証した。元の長い一括DBロックはGUIの安全な介入を妨げるため修正した。Context参照元のプレーンテキストも改行を維持してページングする。

## 検証

CadendaのContext Builderで着手し、最終139テストを`aiops run check cadenda cadenda-1rm`で実施し成功。実ブラウザ2試験も通過した。

実ブラウザ試験は、クラウド無効の隔離した実Git/Beadsプロジェクトを使用。作成→編集→コメント→正式判断→Context反映→実CLIチェック→停止→プロセス終了確認→再開→GUI_CHECK_OK閲覧→成果物承認まで通した。実プロジェクトの方針や成果物の所有者承認を代理したものではない。

さらに制御された遅延応答で、閉じたRun画面の再表示防止、古い編集・実行開始確認の拒否、Issue切替後のジョブログ混入防止を確認した。デスクトップと390px幅で画像を保存し、JavaScriptエラー、HTML注入、ページ全体の横はみ出しを検証した。表・Board内部は横スクロールできる。

- [Python試験](evidence/phase6/tests.txt) / [JUnit](evidence/phase6/tests.xml) / [自己検証Run](evidence/phase6/self-run.json)
- [実ブラウザ試験](evidence/phase6/browser-tests.txt)
- [独立レビュー](evidence/phase6/review-result.json) / [初回指摘](evidence/phase6/attempt-1-review-result.json)
- [ソースハッシュ](evidence/phase6/review-source-hashes.json) / [証跡索引](evidence/phase6/index.json)
- [Desktop](evidence/phase6/dashboard-desktop.png) / [Mobile](evidence/phase6/dashboard-mobile.png)

最終独立レビューはapproved。指摘された確認対象の固定・秘密マスク・非同期応答・復旧と起動受付・承認競合を修正して回帰試験に追加した。独立レビューは提供したソースと証跡の照合であり、レビューAIがテストを実行したわけではない。FastAPIのTestClientにはhttpx互換性の非推奨警告が1件あるが、検証は通過する。移行は依存更新時に別途確認する。

次はPhase 7の常駐・監視・バックアップ・複数プロジェクト運用。PCCの利用許可・起動条件・実quota上限は未解決のまま維持する。

作業中に存在した未追跡の`_review/fix_instructions.md`は変更・コミットせず保持した。実装パイプラインは従来通り、対象リポジトリに未コミット変更があれば開始を拒否する。

実装の一次資料: [FastAPI Middleware](https://fastapi.tiangolo.com/advanced/middleware/)、[Starlette Responses](https://www.starlette.io/responses/)、[Vite Build](https://vite.dev/guide/build.html)。実際の版・挙動はこのリポジトリの実測を優先する。

GUIの起動受付もSQLiteに永続化し、Workerのclaimと同じトランザクションで対応Runを記録する。CLIがclaim前に終了した場合は旧Runを再試行可能に戻し、サーバー再起動後も状態を確認できる。Run・復旧イベントもSQLiteに保存する。受付直後の停止でWorker PIDが未記録でも、古い受付をDB内で失効させる。遅延Workerはclaim時に拒否されるため、不在を推測した強制停止に頼らず旧Runを再試行可能へ戻せる。ディスク障害と完全なキュー運用はPhase 7で別途検証する。生存している孤立プロセス群や再利用の疑いがあるPIDは強制解放しない。

正式判断の確認は現在の方針本文・receiptにも結合し、他の判断が確定した後は古い確認を失効させる。履歴の検証済みreceiptは過去の判断として保持し、現在の方針に自動昇格させない。

同一ワークスペースのGUIを二重起動しないプロセスロックを使用し、既存のペアリングコードを上書きしない。
