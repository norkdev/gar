# plan.md — MCP サーバー実装と AWS 移行の計画

> このドキュメントは v1.0.0 以降の拡張計画。Claude Code での実装セッションは
> このファイルを正とする。README / spec.md と矛盾が生じた場合はこのファイルを
> 更新してから実装すること。

---

## 0. ゴールと優先順位

1. **Phase 1(最優先): ローカル MCP サーバー** — Claude Code / Claude Desktop
   から GAR を操作できるようにする。stdio トランスポート。`v1.1.0` タグ。
2. **Phase 2: AWS 移行** — v1 に組み込み済みのスケールシームを実リソースに
   差し替える。`v1.2.0` タグ。
3. **Phase 3(スコープ外・記録のみ): リモート MCP** — AWS 上の
   streamable HTTP MCP + OAuth。本計画では実装しない。

Phase 1 と Phase 2 が直交するように設計する(後述の D-102)。
Phase 1 完了 → README 更新 → Phase 2 着手、の順。並行作業はしない。

---

## 1. 設計判断(Decisions)

### D-101: MCP は内部ツールではなく HITL ゲートを公開する

**判断**: MCP ツール面は GAR の内部機構(`search_arxiv` 等の低レベルツール)
ではなく、ラン管理と 3 つの HITL ゲートの形で切る。GAR は MCP クライアント
から見て「ガバナンス付きサブエージェント」になる。

**理由**: 低レベルツールを公開すると、MCP クライアント側の LLM がエージェント
ループを代替でき、grounding 検証・HITL ゲート・監査ログをバイパスした
サーベイが成立してしまう。「すべてのステップが監査され、人間がゲートする」
という本プロジェクトの中心主張が MCP 境界で破れる。ゲートを公開境界に
すれば、ガバナンス層はプロトコル境界を越えても不変。

**例外(当初案)**: `search_arxiv` は公開情報のみを扱うため単体公開してよい
(補助ツール扱い)。private 側(ideas 検索)は D-103 に従う。

**改訂(2026-06-14, v1.1 実装時)**: `search_arxiv` は v1.1 では**実装しない**。
理由は 2 点。(1) この補助ツールは低レベル検索の公開であり、D-101 本文の
「MCP 面は統治されたゲートとして切る」という主張をわずかに薄める。7 つの
ゲート/ラン管理ツールだけでサーベイは完結し、補助検索は無くても機能要件を
満たす。(2) 単体検索エンドポイント(`GET /sources/arxiv/search`)を新設すると
route パスに `arxiv` が現れ、「`arxiv` は `sources/arxiv.py`・そのテスト・
`deps.py` の 1 行にのみ出現する」という generic-source 原則(CLAUDE.md / spec §4)
と緊張する。将来 MCP に公開検索を足すなら、generic な
`GET /sources/public/search` + generic 名のツールとして入れる(Future Work)。
よって v1.1 の公開ツールは **7 個**（補助 0）。

### D-102: MCP サーバーは既存 HTTP API の薄いクライアントとして実装する(案B)

**判断**: MCP サーバーは `gar_backend` を直接 import せず、`httpx` で
既存 REST API(`POST /runs`、`POST /runs/{id}/gates/*`、`GET /runs/{id}` 等)
を呼ぶ。base URL は環境変数 `GAR_API_URL`(デフォルト
`http://localhost:8000`)。認証ヘッダは `GAR_API_KEY`(未設定なら付与しない。
v1.1 のローカルバックエンドは pass-through なので不要)。

**理由**: シーム #2(「UI は AWS を直接呼ばない。データプレーンはバックエンド
の居場所から 1 ホップ」)を MCP にも適用する。これにより AWS 移行(Phase 2)
後も **base URL と認証ヘッダの差し替えだけ**で同じ MCP サーバーが動き、
MCP 実装と AWS 移行が直交する。「Two clients」は「Three clients」になり、
エージェントループとガバナンス層の共有という既存の構図が延長される。

**却下した代替案**: インプロセス型(CLI と同様に import して駆動)。
ローカルでは手軽だが AWS 移行後に使えず、クライアント実装が分岐する。

### D-103: private ツールは MCP スキーマからデフォルトで構造的に不在とする

**判断**: MCP サーバーは起動時にロール(env `GAR_MCP_ROLE`、デフォルト
`public`)を取り、既存の `governance/rbac.py` のレジストリ分離と同じ原則を
MCP 境界で再現する。`public` ロールでは ideas(private)に触れるツールは
**拒否されるのではなくスキーマに現れない**。`owner` ロールを明示設定した
場合のみ全ツールを公開する。

**理由**: rbac.py の中心主張(「private ツールは呼び出し時に拒否されるの
ではなく、スキーマから構造的に不在」)が MCP というもう一つのツール公開
境界でも成立することを、実装とテストで示す。

**補足**: `start_survey` は notes の内容そのものを引数で受けるため
(D-105)、ideas 検索ツールの公開とは別問題。ノート内容を MCP クライアント
が渡すのはユーザー自身の操作であり、RBAC の管轄外。

### D-104: v1.1 のツール呼び出しは同期、スキーマは最初からポーリング前提で切る

**判断**: v1.1 では既存 API と同じく同期呼び出しを受け入れる(エージェント
フェーズがツール呼び出し内で完走する。ローカルでは実用上問題ない)。
ただしツールスキーマは `run_id` を返し `get_run_status` でポーリングする
形を最初から定義しておき、Phase 2 で API が 202 + ポーリングに変わっても
**MCP ツールの形は変えない**。

**理由**: AWS 移行時に Lambda の実行時間制約から非同期化が必要になる。
スキーマを先に非同期対応の形にしておけば、移行時の変更はサーバー実装の
内側に閉じる。

### D-105: MCP 経由のノート入力は content-upload パスを使う

**判断**: MCP の `start_survey` は `notes`(`{path, content}` の配列)を
受け、API の `notes_content` パス(`InMemoryIdeasSource`)に乗せる。
`vault_path` パスは MCP では公開しない。

**理由**: MCP サーバーとバックエンドが同一マシンにある保証は Phase 2 以降
なくなる。vault_path はバックエンドのファイルシステム前提であり、リモート
バックエンドに対して意味を持たない。content-upload パスなら local / AWS の
どちらのバックエンドでも同一の挙動になる(Web UI と同じ理由)。
ローカル vault のファイル読み出しは MCP クライアント側(Claude Code は
ファイルを読める)の責務とする。
レポートの保存も同様に、`get_report` が本文を返し、書き戻しはクライアント
側の責務(Web UI と同じ)。

### D-106: MCP 操作も監査する

**判断**: バックエンドの監査ログに、リクエスト元クライアントの識別子を
記録する。実装は API リクエストヘッダ `X-GAR-Client`(値: `web` / `cli` /
`mcp`)を受けて audit レコードに `client` フィールドを追加する。
`schema_version` を `1.1` に上げる(後方互換: フィールド追加のみ)。

**理由**: 「ラン中に何が起きたかは audit.jsonl を読めば分かる。影の経路は
ない」という主張を維持するため、新しいクライアント面も監査対象に含める。

### D-107: SDK とパッケージ構成

**判断**: 公式 Python SDK(`mcp` パッケージ)の FastMCP を使う。
配置は `backend/src/gar_backend/mcp_server/`(モジュール名は `mcp` パッケージ
との衝突を避けるため `mcp_server`)。エントリポイントは
`uv run --package gar-backend gar-mcp` (pyproject の scripts に追加)。

**理由**: 公式 SDK が Claude Code / Claude Desktop との互換で最も安全。
同一パッケージ内に置くことで Pydantic モデル(ツール入出力)を API スキーマ
と共有でき、乖離を防げる。

### D-108: `get_run_status` は候補を構造化リストで、アブスト込み・多めに返す

**判断(2026-06-14, v1.1 スモーク後に追加)**: `get_run_status` の候補提示を
プロセ文字列の要約(先頭20件)から、**構造化リスト** `candidates: [{id, title,
abstract?, authors, published, url}]` + `candidate_count`(総数)に変更する。
件数上限は引数 `max_candidates`(既定 100、env `GAR_MCP_MAX_CANDIDATES` で
既定変更可)。アブストは引数 `include_abstracts`(**既定 on**)で、トークンを
気にする呼び出し側がオプトアウトする。`activity_summary` は短い見出しに戻す。

**理由**: ゲート2(採用選択)は本来の人間の意思決定点。スモークでは 89 件
ヒットに対し 20 件しか見えず、しかも title のみだったため、クライアント側
(Claude Code)の関連度グルーピングが「タイトル推測」止まりだった。アブストは
arXiv 取得時に既に SearchResult.snippet として state にあり、エージェントの
関連度評価にも使われている既存データなので、MCP 面に通すのは追加 API コール
無しでトークンコストのみ。「クライアントが要約・整理する」(D-101 の分担)を
活かすには、整理の材料=アブストを既定で渡すのが理にかなう。件数100は id+title
で約3k、アブスト込みで約20–25k tokens。重くなりすぎたら将来
`list_candidates(limit, offset, include_abstracts)` に分離する(今は過剰設計を
避け get_run_status 拡張で足りる)。

**限界(明記)**: 上限拡大は対症療法。ヒットが上限を超えれば依然こぼれ、arXiv の
返却順は関連度順とは限らない。本質的な緩和は rerank(retrieve 技法、後フェーズ)。

---

## 2. Phase 1 — MCP サーバー(stdio)

### 2.1 公開ツール(7 個。補助 `search_arxiv` は D-101 改訂で v1.1 から除外)

すべて入出力を Pydantic モデルで定義し、API スキーマと共有する。

| ツール | 入力 | 出力 | 対応 API |
|---|---|---|---|
| `start_survey` | `notes: list[{path, content}]` | `run_id, status` | `POST /runs` (notes_content) |
| `list_runs` | なし | `runs: list[{run_id, status, updated_at}]` | `GET /runs` |
| `get_run_status` | `run_id` | `status, current_gate?, activity_summary` | `GET /runs/{id}` |
| `review_concept` | `run_id, action: approve\|edit, edited_concept?` | `status` | `POST /runs/{id}/gates/concept` |
| `select_sources` | `run_id, adopted_ids: list[str]` | `status` | `POST /runs/{id}/gates/sources` |
| `approve_report` | `run_id, action: approve\|reject, feedback?` | `status` | `POST /runs/{id}/gates/report` |
| `get_report` | `run_id` | `markdown, citations_valid, warnings` | `GET /runs/{id}`(report 部分) |

注意:
- `list_runs` の時刻フィールドは、バックエンドの `serialize_state` が持つのが
  `updated_at`(`created_at` は未保持)なので `updated_at` を返す。
- `approve_report` の `action: reject` はバックエンドに棄却遷移が無いため、
  v1.1 では呼ぶと「未対応」エラーを返す(スキーマ形は D-104 のため維持)。
- `get_report` の `citations_valid` / `warnings` は、report ゲートの
  `pending_payload` に grounding 検証サマリ(`report_validation`)を載せる
  小改修(hitl.py + loop.py)で供給する。採用エビデンスが無いランでは
  `citations_valid = null`(検証対象なし)。同じフィールドは web UI も将来
  利用できる。
- `get_run_status` の `current_gate` は、MCP クライアント(Claude)が
  「次に人間に何を確認すべきか」を判断できる程度の情報を含めること
  (例: gate=sources のとき候補一覧の要約)。
- ツール description には「ゲートでは必ず人間の確認を取ってから呼ぶこと」
  を明記する。ガバナンスの最後の 1 マイルは MCP クライアント側の挙動に
  依存するため、description はその指示を運ぶ場所として扱う。
- `search_arxiv` 用の素のエンドポイントが現状 API にない場合は
  `GET /sources/arxiv/search` を新設する(レート制御は既存 arxiv.py を通す)。

### 2.2 実装タスク

1. `mcp_server/` パッケージ新設: FastMCP サーバー、ツール定義、
   `GarApiClient`(httpx ラッパー、`GAR_API_URL` / `GAR_API_KEY` /
   `X-GAR-Client: mcp` ヘッダ)。
2. D-103 のロール実装: `GAR_MCP_ROLE=public|owner`。v1.1 では ideas 系
   ツールが存在しないため実質 no-op だが、ツール登録をレジストリ経由に
   して、ロールでスキーマが変わるテストを書く(将来 ideas 検索を MCP に
   足すときの受け皿)。
3. D-106 の audit 拡張: `client` フィールド、`schema_version: "1.1"`、
   既存ログとの後方互換テスト。
4. pyproject scripts に `gar-mcp` 追加。
5. `.mcp.json`(リポジトリルート、Claude Code 用)と Claude Desktop の
   設定例を `docs/mcp.md` または README に記載。
6. スモークテスト: Claude Desktop から 1 ラン完走(start → concept →
   sources → report → get_report)。そのときの audit.jsonl 断片を README に
   貼る(既存の「実ログを見せる」流儀)。

### 2.3 テスト方針

- 既存の流儀を踏襲: オフライン、`httpx.MockTransport` でバックエンド API を
  モック。実 API キー不要。
- 必須ケース:
  - 各ツール → 正しいエンドポイント・ペイロードへの変換
  - `GAR_MCP_ROLE` によるツールスキーマの差(構造的不在の検証)
  - バックエンド未起動 / 4xx / 5xx 時のエラーメッセージ(MCP クライアントの
    LLM が読んで次の行動を決められる文面にする)
  - audit `client` フィールドと schema_version 1.1
- ゲート遷移の状態異常(例: concept 未承認で select_sources)はバックエンド
  側の既存責務。MCP 側はエラーをそのまま透過することをテストする。

### 2.4 ドキュメント / リリース

- README: 「Two clients」→「Three clients」に表を拡張(CLI / Web UI / MCP)。
  MCP セクション新設(D-101 / D-102 の理由を 1 段落ずつ)。
- spec.md に MCP 章を追記。
- `v1.1.0` タグ。

---

## 3. Phase 2 — AWS 移行

方針: 既存の 7 つのスケールシームを上から順に現実化する。
**API の外形(パスとスキーマ)は変えない**。変えるのは応答の同期性のみ
(下記 3.2)。フロントエンドと MCP サーバーは base URL 差し替えで動く
ことを移行の完了条件とする。

### 3.1 タスク(優先順)

1. **状態の外部化**: `DynamoDbRunStore`(RunStore Protocol の実装追加、
   1 クラススワップ)。レポート保存を S3 に(`reports/` の保存先抽象を
   確認し、必要なら `ReportStore` を切る)。
2. **Lambda 化**: 既存の Mangum フックを使い API Gateway + Lambda で
   現行 API を載せる。
3. **非同期化**: エージェントフェーズを伴う POST(/runs、各 gate)を
   202 Accepted + `get_run_status` ポーリングに変更。フェーズ実行は
   まず「受付 Lambda が非同期で自己 invoke」の最小構成とし、
   Step Functions wait-for-callback への発展は future work のまま据え置く。
   ※ MCP ツールの形は D-104 により不変。Web UI はポーリング対応の修正が必要
   (SSE は v1.2 では CloudWatch ベースに置き換えず、ポーリングに簡素化して
   よい — 判断は実装時に)。
4. **監査ログ**: ラン単位で S3 オブジェクト(JSONL)に書く。ローカルは
   従来通りファイル。`AuditSink` 抽象を切って 2 実装。
5. **BedrockLLM**: `LLMClient` Protocol の実装追加(シーム #5 の現実化)。
   env でプロバイダ選択。クロスリージョン推論プロファイルの利用は実装時に
   リージョン事情を確認して決める。
6. **認証**: `api/auth.py` の pass-through を API キー検証(API Gateway の
   API key または独自ヘッダ)に差し替え。**Cognito は v1.2 ではやらない**。
7. **CDK**: スカフォールド済みスタックに実リソース定義
   (DynamoDB / S3 / Lambda / API Gateway / IAM)。`cdk synth` が CI で
   通ることを維持。
8. **ノートの扱いの明文化**: AWS バックエンドは content-upload パスのみ
   サポート。vault_path はローカルバックエンド専用と README に明記
   (未公開ノートの所在はこのプロジェクトのプライバシー設計の核心なので、
   設計判断として 1 段落書く)。

### 3.2 やらないこと(v1.2 スコープ外)

- Cognito / OAuth、マルチテナントの実体化(シームは維持)
- リモート MCP(streamable HTTP)
- LLM トークンストリーミング
- PDF ingestion、追加 public source

### 3.3 リリース

- README: Architecture 図を v1.2 構成(local / AWS の 2 通り)に更新。
  「AWS infra: scaffolded」の記述を実態に合わせて更新。
- `v1.2.0` タグ。

---

## 4. 進め方のメモ(Claude Code セッション向け)

- 作業はローカルコピー上の feature ブランチで行う
  (`feature/mcp-server`、`feature/aws-backend`)。main 直 push はしない。
- 1 PR = 1 関心事。Phase 1 は (a) audit 拡張、(b) mcp_server 本体、
  (c) docs、の 3 PR 程度に分割する。
- 新規コードも既存の構造規約(governance は 1 関心 1 ファイル、
  Protocol によるスワップ点、frozen dataclass の純関数)に従う。
- テストは `backend/tests/` のミラー構造に追加。全テストはオフラインを維持。
- このファイルの Decisions に反する実装が必要になったら、先にこのファイルを
  更新し、理由を 1 段落書いてから実装する。

---

## 5. 検索 recall 改善トラック(Phase 2 とは独立)

MCP スモークで判明:arXiv 検索の取りこぼし(関連の核心文献が候補順の後方に
埋もれる/そもそも検索語に乗らない)が、GAR の本来目的(新規性・進歩性の予備
調査)に直接効く。**目的に照らすと precision より recall が支配的**——先行研究
の見逃し(FN)は「偽の新規」を生む致命的誤りで、余分な候補(FP)は人間/
クライアントが弾けばよい(D-108 でアブスト提示済み)。指標は **recall@K**(人間
が読む上位 K 件で決定的先行研究を拾えるか)+ **citation precision = 1.0**(grounding)。
F1(等重み)は目的に合わないため使わない。

レバー(影響大→小):

- **B. breadth 検索(実装済み, feature/recall)**: `SEARCH_SYSTEM` を recall 優先に
  書き換え(ファセット分解・同義語/別表記・並列クエリ・過剰 prune 禁止、「5-20件で
  停止」を撤廃)。`max_search_iterations` 4→6、検索ツール `max_results` 既定 10→15。
- **A. 原文フレーズ注入(実装済み, feature/recall)**: 検索フェーズに元ノート原文
  (上限 8000 字)を注入し、要約で落ちた技術語句を literature クエリに使わせる
  (spec §5 の未実現を実装)。privacy:生の私案を web search に流さない指示は維持
  (arXiv 等の文献ソースには distill した技術語のみ)。
- **D. rerank(実装済み, feature/recall)**: `retrieval/rerank.py` に `Reranker`
  Protocol(spec §5 のスワップ点)+ 依存なしの `BM25Reranker`。`phase_search` で
  dedup 後・ソースゲート前にコンセプトで並べ替え → MCP の上限は rerank 後に切れる
  (低関連の裾だけ落ちる)。安定ソートで無シグナル時は no-op。embedding/LLM rerank は
  同 Protocol で後から差し替え可能。
- **計器(実装済み, feature/recall)**: `retrieval/recall.py` に `recall_at_k` /
  `rank_of` / `known_item_recall`(純関数)。オフラインテストで「決定的先行研究を
  プールの末尾に仕込み → rerank で上位 K に引き上がる(recall@5: 0.0→1.0)」を実証。
  実 arXiv に対する live 評価ハーネス(seed 概念＋ハンドラベル)は今後の作業。

### 実地検証(v1.1 スモーク, 2026-06-15)

同一ノートで B+A 適用前後を比較:候補 **94→294**(3.1×)、arXiv 検索 **12→23**、
原文注入で private_ideas 検索も発火。前回採用の核心6件中5件を再発見＋着想により近い
新規文献(One Chatbot Per Person 等)が多数浮上。知見:(イ)breadth 化は厳密な上位
集合ではない(クエリ語彙の変動で出入りあり)→ rerank + recall@K 計器で制御/計測する
動機。(ロ)recall-max 検索は重く、同期 gate POST が接続タイムアウト → run は durable
で完走しポーリング復帰(D-104 の実証、Phase 2 非同期化の動機)。
