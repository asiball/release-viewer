# 規約とJSONスキーマ（提案 v0.1）

対象：コンポーネント単位でバージョンを持つFWモノレポ。
目的：「どの系列に何が入っているか」「hotfixがどこまで伝播したか」「依存制約を満たしているか」を、
git の履歴と少数の設定ファイルだけから**機械的に**判定できるようにする。

方針：
- 判定の根拠は必ず git 上の事実（コミット・タグ・ファイル内容）に置き、JSONには「なぜそう判定したか」（方式・根拠コミット）を残す。
- 設定ファイルはすべて TOML（Python 3.11+ の `tomllib` で読めるため、追加依存なし）。
- 曖昧なものは「合格」にせず、警告として表に出す。

---

## 1. リポジトリ構成

```
components/
  driver/   component.toml  ...
  hal/      component.toml  ...
  lib-comm/ component.toml  ...
  app/      component.toml  ...
.release/
  series.toml       # 系列の定義（追跡するブランチと親子関係）
  exclusions.toml   # 「この系列には適用しない」宣言
```

- コンポーネントは `components/<name>/` 直下に置く。`<name>` は `[a-z0-9][a-z0-9-]*`。
- コミットがどのコンポーネントに属するかは、変更パスが `components/<name>/` 配下かどうかで判定する。
- `.release/` の設定は **設定参照ref（既定 `main`）の HEAD から読む**。各系列ブランチ上のコピーは無視する（設定の分裂を防ぐため）。
  実リポジトリに設定をコミットしたくない場合は、コレクタの `--config-dir <path>` でローカルのディレクトリを渡せる。

## 2. ブランチ（系列）命名

| 種別 | ブランチ名 | 例 | 親 |
|---|---|---|---|
| mainline | `main` | `main` | なし |
| release | `release/<major>.<minor>` | `release/1.2` | `main` |
| customer | `customer/<customer>/<major>.<minor>` | `customer/acme/1.2` | `release/1.2` |

- 作業用ブランチ（`hotfix/*`, `feature/*`）は追跡しない。
- 実際に追跡するのは `series.toml` に書かれたブランチだけ（命名規則は推奨であり、判定には使わない）。

## 3. タグ命名

### 3.1 コンポーネントタグ（必須）
```
<component>/v<semver>          例: hal/v1.2.1
```
- タグは注釈付きタグ（`git tag -a`）を推奨。軽量タグも読むが警告を出す。
- タグ付けしたコミット上の `components/<component>/component.toml` の `version` と、タグのバージョンは一致しなければならない（不一致は警告 `tag_version_mismatch`）。
- **顧客ブランチ固有の変更**でコンポーネントを出し直す場合は semver のビルドメタデータを使う：`hal/v1.2.1+acme.1`。
  - semver の規定どおり、依存制約の比較ではビルドメタデータを無視する（`1.2.1+acme.1` は `>=1.2.1` を満たす）。
  - プレリリース（`-rc.1` 等）は通常の semver 順序で扱う。

### 3.2 製品リリースタグ（任意・GitHub Release に対応）
```
<tag_prefix>v<semver>          例: fw/v1.2.3, fw-acme/v1.2.3
```
- `tag_prefix` は系列ごとに `series.toml` で定義する。
- 「リリース詳細」画面の主役。タグ時点の全コンポーネントのバージョン（各 `component.toml` を読む）、前版からの差分、含まれる fix を表示する。
- コンポーネントタグもクリックで同様に詳細を出せる（その時点の全コンポーネント構成）。

## 4. 系列定義 `.release/series.toml`

```toml
[[series]]
branch     = "main"
kind       = "mainline"
status     = "active"        # active | maintenance | eol
tag_prefix = "fw/"

[[series]]
branch     = "release/1.2"
kind       = "release"
parent     = "main"
status     = "active"
tag_prefix = "fw/"

[[series]]
branch     = "customer/acme/1.2"
kind       = "customer"
parent     = "release/1.2"
customer   = "acme"
label      = "ACME 向け 1.2"   # 表示名（任意）
status     = "active"
tag_prefix = "fw-acme/"
```

- 系列IDはブランチ名そのもの。設定を読む ref はコレクタの `--config-ref`（既定 `main`）で指定する。
- `parent` は分岐元の系列。分岐点は「系列ブランチの first-parent を遡って、最初に親系列から到達可能になるコミット」とする
  （`git merge-base` は親系列をマージすると動いてしまうため使わない）。
- ブランチはローカル（`refs/heads/`）→ リモート追跡（`refs/remotes/<remote>/`、既定 `origin`）の順に探す。CI の clone でもそのまま動く。
- **hotfix伝播の必須対象は `status = "active"` の系列**。`maintenance` は判定するが未適用は警告止まり、`eol` は判定しない（表示のみ）。

## 5. hotfix の識別

### 5.1 Fix-ID トレーラー（必須規約）
hotfix の**元コミット**のメッセージ末尾に git トレーラーを付ける。

```
hal: I2C タイムアウト時にバスをリセットする

Fix-ID: FIX-123
```
- 書式：`Fix-ID: <PREFIX>-<number>`（正規表現 `^[A-Z][A-Z0-9]*-\d+$`）。1コミットに複数行可。
- 読み取りは `git log --format=%(trailers:key=Fix-ID,valueonly)` で行い、本文中の似た文字列は拾わない。
- 1つの fix が複数コミットにまたがる場合は、全コミットに同じ Fix-ID を付ける。系列に1つでもあれば「適用済み」とはせず、**全コミットの対応が取れた場合のみ適用済み**とする。

### 5.2 `cherry-pick -x` の扱い
- 伝播は **`git cherry-pick -x`** で行うことを規約とする。`-x` は元コミットのSHAを `(cherry picked from commit <sha>)` として残すので、監査上の追跡根拠になる。
- `-x` を付けてもトレーラーは元メッセージからそのままコピーされるため、通常はトレーラーで一致する。`-x` の記録は「メッセージを書き換えてトレーラーが消えた」場合の救済として使う。

### 5.3 fix の起点（origin）と単位（units）
- 同じ Fix-ID を持つコミットのうち、走査範囲内のコミットを指す `-x` 記録を持たないものを「原本」とする。
  最も古い原本を origin とし、origin と同じ系列にある原本すべてをその fix の**単位**とする（複数コミットの fix では単位が複数）。
- `-x` の記録は連鎖を辿る（例：acme → release/1.2 → main）。途中のコミットにトレーラーがなくても、起点の単位に繋がれば対応が取れる。
- fix の対象コンポーネントは単位の変更パスから推定する。

## 6. 伝播判定

各 fix × 各系列 × 各単位について、次の順で判定し、**最初に一致した方式**を記録する。
系列の方式は全単位のうち最も根拠の弱いもの。1つでも単位が欠ければ `missing`（`units_matched` / `units_total` で部分適用を示す）。

| 優先 | 方式 `method` | 条件 | 状態 `state` |
|---|---|---|---|
| 0 | `ancestry` | 単位のコミット自体が系列ブランチから到達可能（分岐前に入っていた、マージで入った等） | `applied` |
| 1 | `trailer` | 系列に同じ Fix-ID トレーラーを持つコミットがあり、`-x` でその単位に繋がる（単位が1つなら `-x` なしでも可） | `applied` |
| 2 | `cherry_pick_x` | 系列に `(cherry picked from commit <sha>)` の連鎖でその単位に繋がるコミットがある（トレーラーなし） | `applied` |
| 3 | `patch_id` | 系列に `git patch-id --stable` が単位と一致するコミットがある | `patch_id_only` |
| – | – | 除外宣言あり（§7） | `excluded` |
| – | – | 上記いずれもなし | `missing` |
| – | – | 系列が `eol`、または対象コンポーネントが系列に存在しない | `not_applicable` |

- 「系列にある」＝ 系列ブランチから到達可能なコミット（走査範囲内、§9）。
- `patch_id_only` は**適用済みとみなすが警告を出す**（追跡根拠が弱いため）。`--check` では失敗にせず、`--strict` のときだけ失敗にする。
- `missing` の重大度は系列が `active` なら error、`maintenance` なら warning。
- 除外宣言があるのに実際には適用されていた場合は `applied` とし、警告 `exclusion_but_applied` を出す。
- 衝突解消で差分が変わった cherry-pick は patch-id が一致しないので、トレーラーか `-x` が無いと `missing` になる。これは意図した挙動（規約違反を可視化する）。

## 7. 除外宣言 `.release/exclusions.toml`

```toml
[[exclude]]
fix     = "FIX-107"
series  = ["customer/beta/1.1"]
reason  = "beta 向けは該当機能（CANFD）を無効化しているため影響なし"
decided = "2026-09-01"
by      = "t.yamada"
```

- `fix`・`series`・`reason` は必須。理由のない除外はエラー（`--check` 失敗）。
- 設定参照ref（main）に集約する理由：除外の判断履歴が1ファイルの `git log` で追え、レビュー対象にしやすい。
- 存在しない Fix-ID・系列を指す宣言は警告 `stale_exclusion`。

## 8. コンポーネントメタデータ `components/<name>/component.toml`

```toml
[component]
name    = "app"
version = "2.1.0"

[dependencies]
hal      = ">=1.2.0, <2.0.0"
lib-comm = ">=1.4.1"
```

- `version` は semver 2.0.0。
- 制約式は**比較演算子のカンマ区切り（AND）のみ**：`>=`, `>`, `<=`, `<`, `==`, `!=`。
  `^` / `~` は解釈の揺れがあるため採用しない（正確さ優先）。
- 依存チェックは各系列の HEAD と、各製品リリースタグ時点で行う。その時点のツリーにある全 `component.toml` を読み、制約を満たさなければ `dependency_violation`（error）。依存先コンポーネントが存在しない場合も error。

## 9. 走査範囲（大規模リポジトリ対策）

- 読むのは `series.toml` のブランチから到達可能なコミットと、命名規約に合うタグ（`<prefix>/v<semver>`）だけ。`git log --all` は使わない。
- 履歴が長い場合は `--since-ref <ref>` でその ref の祖先を走査から外せる（例：最も古い保守系列の分岐点より前のタグ）。
  外した範囲にある fix・タグの fix 集計は対象外になる。
- `patch-id` は重いので、トレーラー／`-x` で決まらなかった組み合わせについて、**fix の対象コンポーネントのパスを変更したコミットだけ**をまとめて1回で計算する。
- 実装は `git` コマンドを subprocess で呼ぶ（Python 標準ライブラリのみ）。
  理由：`git patch-id --stable` と `%(trailers)` がそのまま使え、git 本体と同じ解釈になる。pygit2 は patch-id の互換実装がなくネイティブ依存も増える。GitPython は結局 git CLI を呼ぶラッパーで得るものが少ない。

## 10. コレクタ CLI（概要）

```
release-collect <repo> -o site/data.json [--config-ref main] [--config-dir DIR] [--remote origin]
                [--since-ref REF] [--name NAME] [--check [--strict]]
```
- 出力：`data.json` と、同内容を `window.RELEASE_DATA = …` で包んだ `data.js`
  （`file://` で開いた HTML でも fetch 不要で読めるようにするため。画面側はファイル選択での読み込みにも対応する）。
- `--check` の終了コード：`0` 違反なし／`1` error 級の違反あり（`fix_missing`, `dependency_violation` ほか）／`2` 設定・引数エラー。
  警告（`patch_id_only` 等）は `--strict` 指定時のみ `1` にする。

## 11. 出力JSONスキーマ（schema_version 1.0）

互換性ルール：
- `schema_version` は `MAJOR.MINOR`。フィールド追加は MINOR、削除・意味変更は MAJOR。
- 読み手は未知のフィールドを無視する。画面は MAJOR が一致しなければ読み込みを拒否する。
- ツール固有の追加情報はトップレベルの `extensions` に入れる。
- 列挙値（`state`, `method`, `kind` 等）は小文字スネークケースの文字列。
- 同じ入力なら同じ出力になるよう、配列はすべて決定的な順序でソートする（`generated_at` 以外は差分比較できる）。

```jsonc
{
  "schema_version": "1.0",
  "generated_at": "2026-09-28T00:00:00Z",          // SOURCE_DATE_EPOCH があればその時刻
  "generator": { "name": "release-collect", "version": "0.1.0" },
  "repository": {
    "name": "sample-fw",
    "config_ref": "main",                          // --config-dir 使用時は null
    "config_commit": "<sha>",
    "since_ref": null
  },

  "components": [ { "name": "hal", "path": "components/hal" } ],

  "series": [
    {
      "id": "release/1.2", "branch": "release/1.2", "ref": "refs/heads/release/1.2",
      "kind": "release", "parent": "main", "customer": null, "label": null,
      "status": "active", "tag_prefix": "fw/",
      "head": "<sha>", "fork_point": "<sha>",
      "head_snapshot": { "app": "1.2.1", "driver": "1.1.1", "hal": "1.2.1", "lib-comm": "1.1.1" }
    }
  ],

  // 画面に出す「注目コミット」だけ（タグ・fix・分岐点・HEAD・他系列からのマージ）。seq の昇順
  "commits": [
    {
      "sha": "<sha>", "seq": 41,                    // 走査範囲内の通し番号。祖先は必ず小さい
      "series": "release/1.2",                     // その系列の first-parent 上にあるコミット
      "subject": "hal: ...", "author": "...", "date": "2026-...",
      "components": ["hal"],
      "fix_ids": ["FIX-123"],                      // トレーラー・-x・patch-id のいずれかで対応した fix
      "cherry_picked_from": ["<sha>"],
      "parents": ["<sha>"],
      "tags": ["hal/v1.2.1"],
      "roles": ["fix", "fork_point", "head", "merge", "tag"]
    }
  ],

  // 注目コミット間を縮約したグラフ（系列ツリー用）
  "graph": {
    "edges": [
      { "from": "<sha>", "to": "<sha>", "hidden_commits": 12, "kind": "first_parent" }
      // kind: first_parent | fork | merge | cherry_pick
    ]
  },

  "tags": [
    {
      "name": "fw/v1.2.3", "kind": "release",          // release | component
      "component": null, "version": "1.2.3",
      "commit": "<sha>", "series": "release/1.2", "annotated": true,
      "date": "2026-...",
      "snapshot": { "driver": "1.0.4", "hal": "1.2.1", "lib-comm": "1.4.2", "app": "2.1.0" },
      "previous": "fw/v1.2.2",                     // 祖先で最も近い同 prefix のタグ（なければ任意のリリースタグ）
      "diff": [ { "component": "hal", "from": "1.2.0", "to": "1.2.1" } ],
      "fixes_added": ["FIX-123"],                  // previous からの差分で揃った fix
      "fixes_included": ["FIX-101", "FIX-123"]     // コンポーネントタグではそのコンポーネントの fix のみ
    }
  ],

  "fixes": [
    {
      "id": "FIX-123", "title": "hal: I2C タイムアウト時にバスをリセットする",
      "components": ["hal"],
      "origin": { "commit": "<sha>", "series": "main" },
      "units": ["<sha>"],                          // §5.3
      "status": {
        "release/1.2":       { "state": "applied", "method": "trailer", "commits": ["<sha>"] },
        "customer/acme/1.2": { "state": "patch_id_only", "method": "patch_id", "commits": ["<sha>"] },
        "customer/beta/1.1": { "state": "excluded", "reason": "...", "by": "...", "decided": "..." },
        "release/1.1":       { "state": "missing" },
        "customer/x/1.0":    { "state": "not_applicable", "reason": "系列が eol" }
        // 単位が複数の fix では units_total / units_matched が付く。missing でも一部一致すれば commits が付く
        // 除外宣言があるのに適用済みなら applied に "exclusion": {...} が付く
      }
    }
  ],

  "dependency_checks": [
    {
      "series": "customer/beta/1.1",
      "at": { "kind": "head", "ref": "customer/beta/1.1", "commit": "<sha>" },  // kind: head | tag
      "ok": false,
      "results": [
        { "component": "app", "dependency": "hal", "constraint": ">=1.2.0, <2.0.0",
          "actual": "1.1.0", "ok": false }         // 評価できない場合は "error" が付く
      ]
    }
  ],

  "violations": [
    {
      "kind": "fix_missing",
      // error:   fix_missing(active) | dependency_violation | exclusion_without_reason
      // warning: fix_missing(maintenance) | patch_id_only | exclusion_but_applied | stale_exclusion
      //          | tag_version_mismatch | lightweight_tag | invalid_tag | invalid_fix_id | invalid_component_meta
      "severity": "error",
      "message": "FIX-123 が release/1.1 に未適用",
      "refs": { "fix": "FIX-123", "series": "release/1.1" }   // fix / series / tag / component / ref / commit
    }
  ],

  "summary": { "errors": 1, "warnings": 0 },
  "extensions": {}
}
```

## 付録：サンプルモノレポで再現するケース

| 系列 | 内容 |
|---|---|
| `main` | driver / hal / lib-comm / app の開発線 |
| `release/1.1`, `release/1.2` | 製品リリース系列（`fw/v1.1.x`, `fw/v1.2.x`） |
| `customer/acme/1.2`, `customer/beta/1.1`, `customer/gamma/1.2` | 顧客系列 |

意図的に含めるケース（期待値は `tests/test_propagation.py` の `EXPECTED`）：
- **部分未伝播**：FIX-102 が `customer/gamma/1.2` にだけ入っていない → `missing`（error）
- **複数コミットの fix**：FIX-105（2コミット）が gamma には1コミットだけ → `missing`（1/2）
- **`-x` なし・トレーラー消失**：FIX-103 をメッセージを書き換えて `release/1.1` へ移植 → `patch_id_only`（warning）
- **`-x` のみ**：FIX-104 はトレーラーを消して `-x` 記録だけ残して `release/1.2` へ、さらにそこから acme へ → `cherry_pick_x`
- **除外**：FIX-102 を `release/1.1` と `customer/beta/1.1` で除外宣言（該当コードなし） → `excluded`
- **依存違反**：`customer/beta/1.1` で app だけ CAN FD 対応（`hal >=1.2.0` を要求）、hal は 1.1.0 のまま → `dependency_violation`
- **マージでの取り込み**：`customer/acme/1.2` は `release/1.2`（fw/v1.2.1）をマージ（FIX-101/102/105 はマージで入る）
- **顧客固有ビルド**：`hal/v1.2.0+acme.1`, `hal/v1.2.1+acme.1`, `app/v1.1.0+beta.1`

日時は固定の基準時刻から1コミットごとに一定間隔で進め、作者・コミッターは固定値を環境変数（`GIT_AUTHOR_DATE` 等）で与える。これにより再実行で同じコミットSHAになる。
