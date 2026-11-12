# Phase 5: Apple fm Local / PCC保留

作業Issue: `cadenda-6dh` / 2026-11-12

fm systemの限定4タスクを実装し、同じBeads IssueにRecorderで実行履歴・入力・スキーマ・応答・検証結果を残した。PCCはこの起動コンテキストでは利用できず、別途の利用許可もないため保留する。別クラウドへの自動代替は行わない。

## 利用方法と境界

所有者管理の`config/projects.yaml`で対象プロジェクトだけ`fm_local_enabled: true`にする。既定はfalse。今回有効化したのはcadendaのみ。

```bash
.venv/bin/aiops run local cadenda ISSUE classify 'The application crashes on Save.'
.venv/bin/aiops run local cadenda ISSUE extract 'issue_id=bd-demo status=open'
.venv/bin/aiops run local cadenda ISSUE names 'A weekly ranking start function'
.venv/bin/aiops run local cadenda ISSUE clamp 'Generate the bounded clamp function.'
.venv/bin/aiops run show RUN_ID
.venv/bin/aiops run flush
```

- classify: bug / feature / questionの分類候補。
- extract: 原文中に存在するIssue IDと状態の短い抽出。`issue_id=ID status=STATE`という単一のラベル付きレコードだけを受け付け、複数レコードは拒否する。
- names: 1〜3個のASCII snake_case命名案。意味の適切さは人間が判断する。
- clamp: 3引数の限定Python関数案。構文木を制限してcompileし、純粋なAST評価器で156組の決定的な整数ベクトルを照合する。モデルのコードはexec/evalせず、プロジェクトへ自動適用しない。任意コードの安全性や全数値型の正しさを保証する機能ではない。

public/internal分類だけを許可し、secret、既知の認証情報パターン・制御文字、4000 UTF-8バイト超の入力は推論前に拒否する。パターン検出は完全な秘密情報検出ではなく、呼出者の正しい分類が必要。CLI参照文は`--`の後へ配置し、`--model=pcc`などを実行オプションとして解釈させない。

/usr/bin/fmとmodel=systemを固定し、45秒・総出力1MiB以内・不正な結果だけ最大1回再試行する。終了異常、非空stderr、quota、利用不可、タイムアウト、停止は再試行や他Providerへの切替をせずNeedsHumanへ止め、理由を記録する。安全にプロセス終了を確認してから所有者が`run release RUN_ID`する。成功時もReviewで、返値は`suggestion_only: true`。Issueの状態や承認を自動変更しない。

## 実機比較

AFM 3 Core Advanced / Antigravity default・effort lowを使用。同一合成入力を各タスク1件ずつ、同じ出力契約・抽出照合・コード検証で比較した。Antigravityは別の明示的な比較試験で、フォールバックではない。ツール使用を禁止し、既存のcadenda向けクラウド許可の範囲で実施した。

| タスク | Local秒 | Antigravity秒 | 検証結果 |
|---|---:|---:|---|
| 分類 | 1.177 | 11.589 | 両者bug |
| 抽出 | 0.950 | 10.818 | 両者bd-demo / open |
| 命名 | 1.186 | 7.951 | 両者形式適合の3案 |
| clamp | 1.200 | 11.549 | 両者compile・AST・整数ベクトル通過 |

両者4/4、リトライ0。Localの時間はCLI起動・Beads照会・Recorderを含む。Antigravityは事前にclaim/スキーマ準備後のプロセス実行・検証時間。測定境界は同一ではなく、初期化・ウォームアップ・大入力・複雑な仕様・品質を一般化できる標本数でもない。命名は形式適合を成功条件とし、主観的品質は採点していない。

## PCCと残るゲート

`fm models`はsystem利用可、PCCは「Terminal appを使用」のエラー。`fm quota-usage`もPCC利用不可で終了1、systemにはquota非適用と応答した。PCC推論は開始していない。`--model pcc`はAdapterでPCC_NOT_AUTHORIZEDとして停止する。
quota超過エラーの分類・無再試行は制御された応答で検証したが、実際のPCC上限到達・リセット時刻・GUI/Terminal/launchd起動条件は未検証。本人による別許可と起動条件確認後に別Issueで再開する。PCCを使えない環境で安全に保留する範囲が今回の到達点である。

## 検証と自己利用

Context Builderで着手し、4タスクと比較試験を同じIssueのRecorderへ記録。最終110テストを`aiops run check cadenda cadenda-6dh`で実行した。モデル切替時も履歴は同じIssueで再取得できる。プロセス停止・出力上限・排他は既存Workerを共有し、PCC/秘密拒否、不正応答上限、quota/利用不可、引数注入、危険なコード案の拒否を回帰テストに追加した。

- [自己検証Run](evidence/phase5/self-run.json) / [110テスト](evidence/phase5/tests.txt) / [JUnit](evidence/phase5/tests.xml)
- [Local比較](evidence/phase5/benchmark-local.json) / [Antigravity比較](evidence/phase5/benchmark-agy.json)
- [モデル確認](evidence/phase5/models.txt) / [quota確認](evidence/phase5/quota.txt)
- [独立レビュー](evidence/phase5/review-result.json) / [レビュー対象ソースハッシュ](evidence/phase5/review-source-hashes.json)
- [Run履歴](evidence/phase5/run-history.json) / [証跡索引](evidence/phase5/index.json)

最終独立レビューはapproved。独立レビュー初回の4指摘（逆転境界・stderr・レコード対応・承認対象の結合）を修正し、回帰テストを追加した。承認対象はRun IDとレビュー時点の成果物ハッシュに結合し、復旧時も同じスナップショットを維持する。レビューAIは提供したソース・試験証跡を照合し、テスト自体を実行したわけではない。次工程はPhase 6のGUI。現在は明示実行のCLIであり、GUI本人認証・常駐運用は後続工程。
