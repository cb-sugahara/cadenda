# Phase 4: Architect / Reviewer / Router

作業Issue: `cadenda-1bq` / 2026-11-12

**全83テスト成功、独立コードレビューapproved。** 実機でCodex Architectの分解案生成と、Antigravity実装→固定チェック→別Codexレビューのパイプラインを確認した。
レビューAIはソースと実行証跡を照合したもので、テストを実行したという意味ではない。

## 使い方

```bash
.venv/bin/aiops run plan PROJECT ISSUE
.venv/bin/aiops run policy PROJECT ISSUE
.venv/bin/aiops run pipeline PROJECT ISSUE --max-fixes 2
.venv/bin/aiops run review RUN_ID
.venv/bin/aiops run show RUN_ID
```

クラウド利用は所有者管理の`config/projects.yaml`で`cloud_enabled: true`としたプロジェクトに限定する。
今回、ユーザーの明示回答により`cadenda`と隔離試験用`worker-poc`だけを有効化した。Issue・コード・テスト証跡を既存認証のCodex/Antigravity CLIへ送る範囲で、秘密情報は対象外。
他プロジェクトの既定値はfalse。fm/PCCへの自動fallbackはなく、秘密分類・staging/production・未知の役割はRouterで人間へ戻す。
`route()`は現在、固定の初期ルール。モデルや利用枠を根拠なく自動選択しない。

## 分解とプロジェクト方針

Architectは構造化したタスク分解案を出す。重複ID、未知の依存、循環、不正JSONは拒否する。
`run policy`は影響するIssueとADR/AGENTSの候補を含む、`status=proposed`の方針提案を保存する。
どちらも`human_approval_required: true`で止まり、BeadsやADRを自動変更しない。

正式な流れは、所有者が内容・ハッシュを確認→実際の人間判断をBeadsに記録→承認した内容だけをBeads登録/ADR・AGENTSへ反映→承認コメントIDとハッシュをreceiptに結合→影響Issueを再計画、となる。
登録・採用の操作はこの段階では所有者が手動で行う。AIの提案やreview approvedを、人間の承認として登録しない。

## 独立レビューと修正

- 全チェック前に候補Git treeを固定し、各チェック後にindex・作業木・未追跡ファイルの変化を検出する。
- 最終コミットのtree、全チェックのargvハッシュ、終了状態、stdout/stderrハッシュが一致する場合だけレビューへ進む。
- Reviewerは要件、累積差分、実行記録、独立テストを受け取る。応答は厳格な型とverdict/findingsの整合性で検証する。
- 応答後にIssue本文だけでなく、関連Issue/コメント・receipt・文書・設定の入力ハッシュを再照合する。変更があればNeedsHumanへ止める。
- 修正は最大2回。拒否されたコミットから新しいworktreeを作り、前回の不変な指摘を渡す。最初の実装基準を維持して累積差分をレビューする。
- 権限拒否、不正応答、テスト失敗などは自動で無制限修正せず停止する。レビュー修正上限ではhuman_neededイベントと結果を残す。

モデル承認後もRunはReviewで、人間確認が必要。Issue自動close、merge、承認権限の付与、GUI認証は実装していない。

## 自己利用と実機結果

CadendaのContextで着手し、ArchitectをCadenda自身のIssueで試験。最後の83テストも`aiops run check`で実行し、Recorderに保存した。
実機の試験Runは`run_3b87883623924931b1794c2586170a37`。コード差分と固定check.pyを照合したCodexレビューがapprovedとなり、人間確認待ちで止まっている。
修正ループは実Git worktreeを使った制御されたモデル応答で、前コミットの継承・指摘の引継ぎ・上限を検証した。実機の成功ケースでは修正は不要だった。

独立実装レビューでは、最終ソースに対するテスト結合、全要件の再検証、修正時の累積差分が不足していると指摘された。
チェックがソースを書き換えるケース、関連コメントのハッシュが変わるケース、前コミットを継承するケースを回帰テストに追加して修正し、再レビューで承認された。

- [テスト](evidence/phase4/tests.txt) / [JUnit](evidence/phase4/tests.xml)
- [実機パイプライン](evidence/phase4/final-pipeline-live.json)
- [Architectの人間確認待ち提案](evidence/phase4/plan-live.json)
- [最終コードレビュー](evidence/phase4/review-result.json) / [以前の指摘](evidence/phase4/review-attempt-1.json)
- [ソースハッシュ](evidence/phase4/review-source-hashes.json) / [証跡索引](evidence/phase4/index.json)

次はPhase 5のApple fm Local/PCC。PCCは前回の実行コンテキスト制約により保留中で、今回のクラウド許可にも含めない。
GUIはPhase 6、常駐運用はPhase 7。現在は所有者が明示的に呼ぶローカルCLIである。
