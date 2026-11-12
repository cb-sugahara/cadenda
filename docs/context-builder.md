# Phase 2: Project Registry / BeadsAdapter / Context Builder

実装: Python 3.12+ / Pydantic 2 / PyYAML / `aiops` CLI。Beadsの読み取りとローカルContext保存を担当する。
Issueの更新、ジョブ起動、LLM呼出し、公開サービスはこのCLIには含まれない。

## セットアップと使い方

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --no-deps -e .
# 簡単に導入する場合は pip install -e '.[dev]' でも可（範囲指定の最新版を解決）

# このMacに残したPhase 1 PoCは既に登録済み。
# 別環境ではconfig/projects.example.yamlをコピーし、絶対パスを設定する。
.venv/bin/aiops projects
.venv/bin/aiops issue context handoff-poc poc-eye.3 --mode standard --format markdown
.venv/bin/aiops issue context handoff-poc poc-eye.3 --mode debug --format json
.venv/bin/aiops issue context handoff-poc poc-eye.3 --mode review --budget-tokens 40000
.venv/bin/aiops issue context handoff-poc poc-eye.3 --mode full
```

`--config /absolute/path/projects.yaml`は`aiops`直後に指定する。stdoutはContext、stderrは保存したSnapshot IDと診断情報。
エラーはstderrのJSONと終了コード2で返す。警告付きContextの終了0はIssueの完了や実行許可を意味しない。

```bash
# 生成時に表示されたctx_...を指定。元のBeads/Gitへ再問い合わせしない。
.venv/bin/aiops context show ctx_... --format markdown
.venv/bin/aiops context sources ctx_...
.venv/bin/aiops context source ctx_... src_... --page 0 --page-chars 4000
```

ページは0始まり。JSONの`next_page`がnullになるまで取得して結合すると、保存されたサニタイズ済みソースを復元できる。
元ログが長くても、LLMへすべてを一括投入する必要はない。JSON出力は予算を空白で消費しないようcompact形式。

## 情報の境界

| データ | 正本・取得方法 |
|---|---|
| Issue・依存・親子・コメント | `bd --readonly --sandbox ... show/comments --json`。ライブのIssue DBは作らない。 |
| 登録プロジェクトと許可パス | 所有者が管理する`config/projects.yaml`。Git対象外。正規化済み絶対パスを指定。 |
| 正式決定 | 本文はBeadsコメント。ローカル設定はコメントID・SHA256・適用Issue・承認根拠への参照のみ。 |
| Run・Artifact | Phase 1では既存の実行記録を読み取るEvidence Catalog。SQLite RecorderはPhase 3で追加する。 |
| Git | 登録済みworktreeまたはリポジトリのHEAD・状態・差分。外部diff/textconv・fsmonitor・フックは無効にする。 |
| プロジェクト文書 | 設定に明示したMarkdown/textの相対ファイルのみ。base repoにない場合、登録済みの最新Run worktreeで読む。 |
| Context Snapshot | 生成時点の監査用コピー。Issueの現在状態の正本にはしない。 |

Phase 1の手動Contextを読んで書き直す仕組みではない。現在のBeads、登録された実行証跡、Git、文書から独立に組み立てる。
既存PoCの一度限りの登録は`import_poc_evidence.py`が行った。既存設定は上書きせず、過去の具体的な承認と既知のコメント本文が一致する場合だけreceiptを作る。

## 正式決定と未信頼データ

`[approved]`や`[decision_approved]`という文字列、投稿者名だけでは正式決定へ昇格させない。
所有者が設定したreceiptの適用Issue、コメントID、本文ハッシュを照合する。同じtopicでは最新receiptを使い、本文変更や取得失敗があっても古い仕様へ自動的に戻らない。
その場合は`DECISION_UNVERIFIED`と「古い前提で実行しない」旨を出力する。
正式決定は共通の取得試行上限内で、通常の関連Issueより先に検索深度の外でも取得する。本文を省略せず、上限で取得できない場合も未検証と明示する。
重複したコメントID（数値/文字列の同一IDを含む）は拒否し、ハッシュを確認した実体と表示するソースが一致することも確認する。

この設定ファイルを書き換えられる所有者は権限を持つ。receiptは本人認証や暗号署名の代替ではなく、AIから呼べる承認APIも設けていない。
本人認証付きの承認イベント・承認対象ハッシュの強制は後続工程で実装する。
Issue、ログ、文書の内容は実行ポリシーには昇格させず、この読取CLIがそれらの指示を実行することもない。

## 4モードと予算

| mode | 優先する追加情報 |
|---|---|
| standard | 現行要件・受け入れ条件・状態、最新引継ぎ、Run、テスト・レビューの短い事実 |
| debug | 失敗/中断Runとstderrを先にし、失敗履歴や試した対処を参照 |
| review | 差分、記録されたテスト/レビュー、要件との照合材料 |
| full | 履歴・根拠の索引と詳細の取得経路。全文はページで展開 |

深度・Issue取得の試行数・ソースごとのバイト数に上限を置く。失敗した取得や正式決定の参照も同じ試行上限を消費する。親子の往復参照を依存循環とは扱わず、依存と親階層の循環を別々に検出する。
上限外・未解決参照・欠損・破損・変更されたHEADは警告として残す。

現段階のtoken見積りは**UTF-8バイト数を用いた保守的なヒューリスティック**で、Providerのtokenizerによる実測ではない。
Markdownと実際のcompact JSON出力の大きい方を計測し、設定予算以下に収める。
所有者は`context_profiles: {profile_name: 予算}`を測定後に登録できる。`--profile profile_name`の上限は、要求の`--budget-tokens`で引き上げられない。
Providerの実際の最大コンテキスト長やPCC枠は仮定せず、プロファイルも初期は空。

必須の正式決定・要件・受け入れ条件・プロジェクト文書・根拠メタデータが収まらない場合は`BUDGET_REQUIRED`で停止する。
任意の詳細を省いた場合は`BUDGET_TRUNCATED`を出す。選別前のサニタイズ済みソースはSnapshotへ保存し、ページ取得できる。
ソース自体が`max_source_bytes`を超える場合は明示的なエラー/警告にする。任意サイズのファイルを無制限に保存する機能ではない。

## Snapshotと機密情報

`state/contexts/ctx_.../`へ以下をまとめて書き、完成したディレクトリをatomic renameで公開する。

- `context.md` / `context.json`: 実際の返却内容、生成日時、builder版、内容ハッシュ
- `sources/src_....json`: ソース本文、元データとサニタイズ後のハッシュ
- `manifest.json`: 各ファイルのサイズとSHA256

生成時刻・Snapshot IDを除いた内容ハッシュは、同じソースと設定なら同じになる。
許可リスト・receipt・取得上限などの入力設定も、ソースとしてスナップショットへ記録する。
構造化データの元ハッシュはcanonical JSON、ファイルの元ハッシュは読み取ったバイト列で計算する。
再読込時はすべての構成ファイルを照合し、欠損・変更・不正なパスがあれば`SNAPSHOT_CORRUPT`として拒否する。ハッシュは破損検知であり、所有者による悪意ある書換えを認証する署名ではない。
SnapshotにはIssueの取得時点のrevisionを残すが、Beads・Git・ログを跨ぐ一つのトランザクションを保証しない。

ファイル読取は各パス成分でsymlinkを辿らず、`..`や許可外パス、`.env`・`.aws`・`.ssh`などの認証領域を拒否する。
よくあるtoken/API key/password/Bearer/private key形式と端末制御文字は、表示・保存の前に除去する。
これは任意の秘密情報を完全に検出するDLPではない。元から認証情報をIssueや成果物へ置かない運用と併用する。
Artifactは期待ハッシュと一致する許可済みテキストだけを利用し、破損したテスト結果を成功根拠には使わない。

## 検証と後続工程

```bash
# Unit/security tests（実機PoCなしでも実行可能）
.venv/bin/pytest tests
# このMacの実Beadsを使う4モード・別プロセスreplay・ページ取得
.venv/bin/pytest tests --poc-config=config/projects.yaml --junitxml=state/phase2/tests.xml
```

結果・独立レビュー・生成したSnapshotの対応は`docs/phase2-results.md`へ記録する。
Phase 3ではSQLite Run/Event/Artifact/OutboxとProcess Controllerを追加し、Evidence Catalogの読取境界を実Recorderへ接続する。
ジョブ排他・クラッシュ復旧・GUI・常駐・本番承認はこのPhaseで実装したものとして扱わない。

実装時に参照した一次資料: [Pydantic Models](https://docs.pydantic.dev/latest/concepts/models/)、[PyYAML Documentation](https://pyyaml.org/wiki/PyYAMLDocumentation)。
Beadsの呼出し仕様は実機1.3.4の`show --help` / `comments --help`と実IssueのJSONで照合した。
