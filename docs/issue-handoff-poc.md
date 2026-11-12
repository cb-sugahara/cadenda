# Issue引継ぎPoC 結果

2026-11-12（JST） / 対象: 実装計画書 Phase 0・Phase 1 / プロジェクト: `handoff-poc`

## 結果

**初回ゲート合格。** Beadsを入口に、旧会話なしの新規セッションが、最新の承認済みJST仕様・旧UTC仕様・現在の成果物・制約・次の作業を復元できた。
3子Issueはそれぞれ独立Nodeテストと、新しい読み取り専用Codexセッションのレビューで承認された。
最終HTTP版は **44テスト成功・0失敗**。cadenda本体の常駐Orchestrator・Context Builder・GUIは後続工程であり、今回の成果物はそれらの前提を確かめるPoC。

## 人間の確認とIssue

現チャットで [分解案](poc-task-proposal.md) を提示し、ユーザーの「この3子Issueで進める」を受領してから登録。
承認元と対象範囲はEpicコメントと `state/handoff-poc/mapping.json` に記録した。
UTC→JSTへの変更は、承認された**試験シナリオ**として記録し、AI自身が人間の承認者を名乗っていない。

| Issue | 内容 | 独立テスト | 最終コミット |
|---|---|---|---|
| `poc-eye` | Phase 1 Epic | 子Issue・証跡の整合確認 | 子Issueの履歴を参照 |
| `poc-eye.1` | UTC月曜00:00の基準実装 | 38成功 | `017153ee5e4bbc1f8efede6d7f9473b194d85040` |
| `poc-eye.2` | 新規セッション引継ぎとJSTへの変更 | 33成功 | `8518935a8bfddd1a8b842a7fe4250adafe50697a` |
| `poc-eye.3` | ローカルHTTP APIと最終レビュー | 44成功 | `2d0fd153581e7d2c57a41cf12dece2455e7dd135` |

テスト数は各版の件数。同じテストの継承を含むため合算しない。
Beads DBの正本は `state/projects/handoff-poc/.beads/`。`mapping.json`はID・worktreeの対応であり、Issue本文や状態の代替DBではない。

## 実証した引継ぎ

1. Antigravityが専用worktreeへUTC版とテストを書いた。テスト実行・Gitコミットは確定的なPython/Node/Git実行が担当。
2. UTC版のCLI実行を終了し、独立レビュー後にIssueを閉じた。
3. 承認済みの試験仕様変更「JST月曜00:00」をPOC-02へ追記。古いUTC版と履歴は保持した。
4. Beadsの対象・関連・親Issueとコメント、Gitコミットを取得し、元データ・ハッシュ付きの手動Contextを作成。
5. 新規Antigravityセッションは、そのContextだけから現行JST・旧UTC・制約・次の作業を説明した。`--continue`や会話再開引数は使用していない。
6. 別の新規セッションがJST版を実装。独立テストで日曜14:59:59 UTCと15:00:00 UTCの切替を検証。
7. Codexはソース・差分・テスト出力に加え、引継ぎ回答・起動引数・前後セッションの開始終了時刻を確認して承認。
8. API版の独立テストでは、実際のloopback HTTPリクエストで200・400・404とJST境界を確認した。

Context: [手動Markdown](evidence/poc/jst-context.md)、[元データとソースハッシュ](evidence/poc/jst-context.json)。
新規セッションの回答: [handoff結果](evidence/poc/jst-handoff-check-result.json)。
最終レビュー: [POC-01](evidence/poc/review1-result.json)、[POC-02](evidence/poc/review2-result.json)、[POC-03](evidence/poc/review3-result.json)。
実際の最終テスト出力: [44件の結果](evidence/poc/step3-tests.txt)。

## 観測した失敗と訂正

- **最初の実装が150秒でタイムアウト**：exit=3 / `finished_reason=print_timeout`。部分ファイルを保全し、失敗をIssueへ記録した。成功とは扱わず、新規セッションで残作業を完了した。
- **独立レビューの誤検知**：末尾改行を受理するとの指摘は、Node 26で再現せず、元コードでも拒否された。誤って「再現した」と記録したコメントは追記型の訂正イベントで明確に訂正した。追加した完全一致ガードは防御的な明確化であり、実在バグの修正とは報告しない。
- **引継ぎ証跡の不足**：JSTコードは合格でも、最初のレビュー資料に新規セッション出力・起動記録がなかったため承認されなかった。元の記録を追加して再レビューし、承認を得た。実装者の自己申告だけを証明にはしていない。
- Phase 0ではAntigravityの**exit=0 / status=SUCCESSでも書込み拒否**を観測。`denied_actions`、stderr、ファイル不在を照合した。

## 実行・確認方法

このワークスペースのルートで実行する。試験Issueとworktreeは保持している。

```bash
bd --directory state/projects/handoff-poc show poc-eye --json
bd --directory state/projects/handoff-poc comments poc-eye.2 --json

cd state/worktrees/handoff-poc/poc-eye.3
node --test
npm start
# 別ターミナルから（+09:00の + はURLエンコード）:
curl 'http://127.0.0.1:3000/week-start?at=2026-11-16T00%3A00%3A00%2B09%3A00'
```

期待するJSON: `{"week_start":"2026-11-15T15:00:00.000Z"}`。
試験後に常駐APIは起動していない。`npm start`で起動した場合はCtrl-Cで停止する。

再現用スクリプトは `scripts/poc_setup.py` → `poc_step1.py` → `poc_review.py 1` → `poc_step2.py` → `poc_review.py 2` → `poc_step3.py` → `poc_review.py 3` → `poc_finish.py`。
既存PoCを重複生成しないガードがある。失敗後はIssue・command manifest・差分を確認して再開し、作業DBや既存worktreeを削除して再実行しない。
当日のCLI・OS条件は [互換性表](compatibility-matrix.md)、制約は [risks.md](risks.md) を参照。

## 未検証と次工程

- Contextは手動組立。動的な関連Issue探索、循環参照・欠損検出、予算・分割制御、`aiops issue context`は未実装。
- 強制クラッシュ、残存子プロセス検出、lease、重複Run防止、OutboxはPhase 3。今回のCLI timeoutと正常なチェックポイント停止だけでは保証しない。
- GUI、本人認証付き承認、SSE、常駐、バックアップ復元、本番操作は未実装。
- PCCは実行コンテキスト制約で保留。Local/Codex/Antigravityによる次工程を止める理由にはしない。

次は **Phase 2: Project Registry / BeadsAdapter / Context Builder**。
このPoCの実Issueを回帰用データとして使い、Issue IDから同じ根拠付きContextを再生成できることを受け入れ条件にする。
重要な承認済み決定を通常コメントと分け、未対応CLI・参照欠損・予算超過を警告として返す。

証跡一覧とハッシュ: [evidence/index.json](evidence/index.json)。実行ログ・元DB・worktreeは `state/` に保存。
エクスポートしたJSONは検証時点の参照資料であり、Beads/Doltの完全バックアップではない。
