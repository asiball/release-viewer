# release-viewer

コンポーネント単位でバージョンを持つ組み込みFWモノレポについて、次の3点を一望する静的Webアプリとコレクタ（サンプル実装）です。

- どの系列（リリースブランチ・顧客ブランチ）に、どのコンポーネントのどのバージョンが入っているか
- hotfix がどの系列まで伝播しているか、漏れはないか
- コンポーネント間の依存制約を各系列が満たしているか

規約（タグ・ブランチ命名、Fix-ID トレーラー、除外宣言、系列定義、JSONスキーマ）は [docs/conventions.md](docs/conventions.md) にまとめています。

## 構成

| パス | 内容 |
|---|---|
| `sample/generate_sample_repo.py` | 架空のFWモノレポ（driver / hal / lib-comm / app、6系列）を生成する。再実行しても同じSHAになる |
| `sample/repobuilder.py` | 固定の作者・日時で git リポジトリを組み立てる `Repo`（サンプル生成器とテストで共用） |
| `release_viewer/` | コレクタ CLI（Python 3.11+、標準ライブラリと `git` コマンドのみ） |
| `release_viewer/web/` | 画面（静的 HTML/JS/CSS、ビルド不要）。`--site` で data.json と一緒に出力される |
| `tests/` | pytest（生成したサンプルリポジトリを入力に、伝播判定・依存チェックなどを検証）。`tests/golden/data.json` は出力のゴールデン |
| `.github/workflows/pages.yml` | テスト（Ubuntu / Windows）→ サンプル生成 → 収集 → GitHub Pages へのデプロイ |

git の操作は `git` コマンドを subprocess で呼んでいます。`git patch-id --stable` とトレーラー解析（`%(trailers)`）を git 本体と同じ解釈で使えるためです。pygit2 には patch-id の互換実装がないうえネイティブ依存が増え、GitPython は内部で git CLI を呼ぶだけなので得るものが少ないと判断しました。

## ローカルで試す

```sh
python sample/generate_sample_repo.py build/sample-fw --force
python -m release_viewer collect build/sample-fw --site build/site
# build/site/index.html をブラウザで直接開く（サーバー不要）
```

`--site` は `data.json`・`data.js` と画面（`index.html` ほか）を同じディレクトリに揃えます。コレクタは `data.json` と一緒に `data.js`（`window.RELEASE_DATA = …`）も出力します。そのため `file://` で開いても読み込めます。`data.js` がない場合は、画面から `data.json` を選択するかドロップしてください。

テスト：

```sh
pip install pytest ruff==0.16.9
python -m pytest -q
ruff check .
```

`tests/golden/data.json` はサンプルリポジトリの出力（`SOURCE_DATE_EPOCH=1790000000`、`--name sample-fw`）と完全一致を確認します。出力を意図して変えたときは `UPDATE_GOLDEN=1 python -m pytest tests/test_golden.py` で更新し、差分をレビューしてください。

## 画面

URL のハッシュで画面と対象を指定できるので、リンクで共有できます（例：`#/matrix/FIX-102`、`#/release/fw-acme%2Fv1.2.1`）。

1. **系列ツリー**（`#/tree`）：タグ・hotfix・分岐点・マージ・HEAD のコミットだけを、系列ごとのレーンに表示します。省略したコミット数は `…n` で示します。未伝播の fix は、起点のコミットを赤丸で囲み、未伝播の系列のレーンに赤い × を付けます。patch-id のみで一致した fix は黄色の点線で囲みます。
2. **hotfix マトリクス**（`#/matrix`）：fix × 系列の表です。セルは ✔ 適用済み ／ ≈ patch-idのみ一致 ／ ✖ 未適用 ／ − 対象外 ／ n/a で表示し、クリックすると根拠（方式・コミット・除外理由）を表示します。未適用のある fix だけに絞り込めます。
3. **リリース詳細**（`#/release/<tag>`）：タグ時点の全コンポーネントのバージョン、前版との差分、追加された fix と含まれる fix、その系列で未適用の fix、その時点の依存チェックを表示します。
4. **依存チェック**（`#/deps`）：系列 × コンポーネントのバージョン表と、系列ごとの HEAD および各リリースタグ時点での制約の充足状況を表示します。
5. **検索**（`#/search/<q>`）：コンポーネント名・バージョン・Fix-ID・系列で絞り込みます。空白で区切ると AND 検索になり、入力に合わせて結果が更新されます。例えば `hal 1.2.1` で、hal 1.2.1 が入っている系列とタグを探せます。

画面上部の「違反 n ／ 警告 n」は、コレクタが検出した問題の一覧です。

## コレクタ CLI

```
release-viewer collect <repo> [-o data.json] [--site DIR] [--config-ref main] [--config-dir DIR]
                              [--remote origin] [--since-ref REF] [--name NAME]
                              [--override SERIES=REF ...]
release-viewer check   <repo> [collect と同じオプション] [--strict] [--series ID ...]
release-viewer lint-pr <repo> --series ID [--head HEAD] [--strict] [--config-ref main] [--config-dir DIR]
                              [--remote origin]
```

`pip install .` で `release-viewer` コマンドが入ります。インストールせずに `python -m release_viewer …` でも同じです。

| サブコマンド | 説明 |
|---|---|
| `collect` | 走査して JSON を出力する。`-o` も `--site` もなければ標準出力に書く |
| `check` | `collect` と同じ走査をして、error 級の違反（active 系列の fix 未伝播、依存違反、理由のない除外）があれば終了コード 1。`-o` / `--site` を付ければ JSON も出力する |
| `lint-pr` | PR のコミット（`<系列のブランチ>..<--head>`、マージコミットを除く）の規約チェック。Fix-ID の書式・位置と、mainline 以外の系列への移植に `cherry-pick -x` の記録があるかを見る（下表）。error があれば終了コード 1（`--strict` なら warning も）。JSON は出力しない |

| オプション | 説明 |
|---|---|
| `-o` | 出力先の `data.json`（同じ場所に `data.js` も書く） |
| `--site` | 指定したディレクトリに `data.json`・`data.js` と画面（`index.html`, `app.js`, `style.css`）を揃えて出力する |
| `--config-ref` | `.release/config.toml` と `.release/exclusions.toml` を読む ref（既定 `main`） |
| `--config-dir` | 設定をリポジトリに置かず、ローカルのディレクトリ（`config.toml`, `exclusions.toml`）から読む |
| `--remote` | ローカルブランチがないときに参照するリモート（既定 `origin`）。CI の clone でもそのまま動く |
| `--since-ref` | 指定した ref の祖先を走査しない（大規模リポジトリ向け） |
| `--override` | `系列=ref` の形で、系列の HEAD をブランチではなく ref（`HEAD` や SHA も可）にして走査する。繰り返し可。PR をマージした後の状態を評価するのに使う（CI の `pull_request` では `refs/pull/N/merge` が HEAD になる）。設定（`.release/`）は引き続き `--config-ref` から読むので、PR が `config.toml` や `exclusions.toml` 自体を変える場合は `--config-ref HEAD` も併せて渡す |
| `--strict` | `check` のみ。warning（patch-id のみ一致、maintenance 系列の未伝播など）も失敗扱いにする |
| `--series` | `check` のみ。終了コードの判定（と標準エラーの一覧）を、指定した系列に紐づく違反に絞る。繰り返し可。系列に紐づかない違反（設定エラー、タグ・コミット単位の警告など）は常に対象。JSON の `violations` / `summary` は絞り込まない |

`lint-pr` が検査する内容：

| kind | 重大度 | 条件 |
|---|---|---|
| `invalid_fix_id` | error | Fix-ID の値が `fix_id_pattern` に合わない |
| `fix_id_not_trailer` | warning | Fix-ID がトレーラーの位置にない（§5.1） |
| `cherry_pick_without_x` | error | 宛先が mainline 以外で、Fix-ID を持つのに `(cherry picked from commit <sha>)` がない |
| `cherry_pick_source_missing` | error | `-x` の記録にある sha がリポジトリに存在しない |

Fix-ID も `-x` の記録もないコミット（バージョン更新など）は対象外です。mainline 宛の PR は squash merge されるので `-x` を求めません。

終了コードは、`0` が成功、`1` が `check`・`lint-pr` で違反あり、`2` が設定・引数・git のエラーです。想定外の例外（`git` が見つからない等）も traceback を標準エラーに出して `2` で終えます。

走査するのは `config.toml` に書いた系列のブランチから到達できるコミットと、`[repository].tag_pattern`（既定 `<prefix>/v<semver>`）に合うタグだけです（`--all` は使いません）。

## 実リポジトリに適用する

### 1. 規約を入れる（詳細は docs/conventions.md）

- 各コンポーネントに `components/<name>/component.toml` を置き、`version` と `[dependencies]` を書く
- コンポーネントのリリースタグは `<component>/v<semver>`（注釈付き）で付ける。製品リリースタグは `<tag_prefix>v<semver>` で付ける
- hotfix の元コミットに `Fix-ID: FIX-123` トレーラーを付け、各系列へは `git cherry-pick -x` で配る
- main への PR を squash merge する場合は、GitHub のリポジトリ設定（Settings → General → Pull Requests →
  Allow squash merging → Default commit message）を **"Pull request title and description"** にし、`Fix-ID:` は PR 本文の末尾に書く。
  "Default message" や "Pull request title and commit details" だと PR 本文がコミットメッセージに入らず、Fix-ID が失われる。
  PR テンプレートのチェックリスト等が後ろに付いてトレーラーの位置から外れた場合も Fix-ID は拾うが、警告 `fix_id_not_trailer` が出る
- main に `.release/config.toml`（リポジトリ構成と追跡する系列）と `.release/exclusions.toml`（対象外の宣言、理由は必須）を置く

コンポーネントのディレクトリ・メタファイル名・タグ書式・Fix-ID トレーラーが既定と違う場合は、`config.toml` の `[repository]` で指定します（docs/conventions.md §4）。

### 2. FWリポジトリ側の GitHub Actions（PR ゲート＋Pages）

#### PR ゲート

```yaml
name: release-viewer-pr
on:
  pull_request:
    branches: [main, "release/**", "customer/**"]   # 宛先が系列のブランチの PR だけ
jobs:
  gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0            # 全ブランチ・タグの履歴が必要。HEAD は refs/pull/N/merge（マージ後の状態）
          path: fw
      - uses: actions/checkout@v4
        with:
          repository: <owner>/poc-release-viewer
          path: viewer
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Lint PR commits
        working-directory: viewer
        run: python -m release_viewer lint-pr ../fw --series "${{ github.base_ref }}"
      - name: Gate (merged state of the target series)
        working-directory: viewer
        run: >-
          python -m release_viewer check ../fw
          --override "${{ github.base_ref }}=HEAD"
          --series "${{ github.base_ref }}"
```

- `lint-pr` は `origin/<宛先>..HEAD`（マージコミットを除く）、つまり PR のコミットだけを検査します。
- `pull_request` では `refs/pull/N/merge`（PR を宛先ブランチにマージした結果）が detached HEAD として checkout されます。`--override <宛先>=HEAD` で、宛先の系列をその状態に置き換えて評価します。
- `--series <宛先>` で、終了コードの判定をその系列に関する違反（マージ後ツリーの依存違反など）に絞ります。系列に紐づかない違反（設定エラー等）は常に判定に含みます。
- 宛先が `config.toml` に定義された系列でないと `--override` / `--series` が終了コード 2 になるので、`branches:` は追跡している系列に合わせて限定してください。
- PR が `.release/config.toml` や `exclusions.toml` 自体を変更する場合は、`--config-ref HEAD` も渡してマージ後の設定で評価してください（既定は `main` の設定を読みます）。

#### main への push で Pages 公開＋全体チェック

```yaml
name: release-viewer
on:
  push:
    branches: [main, "release/**", "customer/**"]
jobs:
  collect:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0            # 全ブランチ・タグの履歴が必要
          path: fw
      - uses: actions/checkout@v4
        with:
          repository: <owner>/poc-release-viewer
          path: viewer
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Collect & gate
        working-directory: viewer
        run: python -m release_viewer check ../fw --site ../_site
      - uses: actions/upload-pages-artifact@v3
        if: always() && github.ref == 'refs/heads/main'
        with:
          path: _site
  # deploy ジョブは本リポジトリの .github/workflows/pages.yml と同じ（actions/deploy-pages）
```

- **PR ゲートが見るのは、依存違反とその系列に関する違反です。** hotfix の未伝播は、起点の系列ではなく伝播先の系列の違反（`fix_missing`）として出ます。このため起点への PR では検出されません。伝播先の系列をリリースする前に `check --series <その系列>` を通してください（リリースゲート）。
- `actions/checkout` で `fetch-depth: 0` にすると、系列ブランチは `refs/remotes/origin/<branch>` として取得されます。コレクタはローカルブランチが見つからなければ、自動的にこちらを参照します。
- `check --site` は違反があってもサイトを出力してから終了コード 1 で終わります。違反があっても画面は公開したい場合は、上の例のように `if: always()` でアップロードしてください。
- 設定をFWリポジトリにコミットしたくない場合は、`--config-dir` で別の場所から渡せます。

## 既知の制約・注意

- `-x` を付けずに cherry-pick しても、Fix-ID トレーラーが残っていれば「適用済み（trailer）」と判定します。複数コミットからなる fix では、どのコミットに当たるかを patch-id で対応付けます。
- 衝突を解消した cherry-pick は差分が変わるため、patch-id では一致しません。`-x` の記録がない場合、複数コミットからなる fix ではそのコミットを対応付けられず「未適用」と判定し、警告 `trailer_unmatched` を出します（規約違反を見逃さないための意図した挙動です）。トレーラーも `-x` もない場合は、fix の大きさによらず「未適用」です。
- 1つの fix の単位（コミット）は「起点と同じ系列にある、`-x` 記録を持たない Fix-ID 付きコミット」で決まります。起点系列で後から同じ Fix-ID の追加修正を入れた場合は、それも単位に含まれます。
- 規模の大きいリポジトリでの性能は未計測です。系列ごとに到達可能なコミット集合をメモリに持つので、履歴が長い場合は `--since-ref` で走査範囲を絞ってください。
- 依存制約の書式は比較演算子のカンマ区切りだけです（`^` と `~` は使えません）。プレリリースも通常の semver の順序で比較します。
