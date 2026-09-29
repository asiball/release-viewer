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
pip install pytest ruff
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
release-viewer check   <repo> [collect と同じオプション] [--strict]
```

`pip install .` で `release-viewer` コマンドが入ります。インストールせずに `python -m release_viewer …` でも同じです。

| サブコマンド | 説明 |
|---|---|
| `collect` | 走査して JSON を出力する。`-o` も `--site` もなければ標準出力に書く |
| `check` | `collect` と同じ走査をして、error 級の違反（active 系列の fix 未伝播、依存違反、理由のない除外）があれば終了コード 1。`-o` / `--site` を付ければ JSON も出力する |

| オプション | 説明 |
|---|---|
| `-o` | 出力先の `data.json`（同じ場所に `data.js` も書く） |
| `--site` | 指定したディレクトリに `data.json`・`data.js` と画面（`index.html`, `app.js`, `style.css`）を揃えて出力する |
| `--config-ref` | `.release/config.toml` と `.release/exclusions.toml` を読む ref（既定 `main`） |
| `--config-dir` | 設定をリポジトリに置かず、ローカルのディレクトリ（`config.toml`, `exclusions.toml`）から読む |
| `--remote` | ローカルブランチがないときに参照するリモート（既定 `origin`）。CI の clone でもそのまま動く |
| `--since-ref` | 指定した ref の祖先を走査しない（大規模リポジトリ向け） |
| `--strict` | `check` のみ。warning（patch-id のみ一致、maintenance 系列の未伝播など）も失敗扱いにする |

終了コードは、`0` が成功、`1` が `check` で違反あり、`2` が設定・引数・git のエラーです。想定外の例外（`git` が見つからない等）も traceback を標準エラーに出して `2` で終えます。

走査するのは `config.toml` に書いた系列のブランチから到達できるコミットと、`[repository].tag_pattern`（既定 `<prefix>/v<semver>`）に合うタグだけです（`--all` は使いません）。

## 実リポジトリに適用する

### 1. 規約を入れる（詳細は docs/conventions.md）

- 各コンポーネントに `components/<name>/component.toml` を置き、`version` と `[dependencies]` を書く
- コンポーネントのリリースタグは `<component>/v<semver>`（注釈付き）で付ける。製品リリースタグは `<tag_prefix>v<semver>` で付ける
- hotfix の元コミットに `Fix-ID: FIX-123` トレーラーを付け、各系列へは `git cherry-pick -x` で配る
- main に `.release/config.toml`（リポジトリ構成と追跡する系列）と `.release/exclusions.toml`（対象外の宣言、理由は必須）を置く

コンポーネントのディレクトリ・メタファイル名・タグ書式・Fix-ID トレーラーが既定と違う場合は、`config.toml` の `[repository]` で指定します（docs/conventions.md §4）。

### 2. FWリポジトリ側の GitHub Actions（CIゲート＋Pages）

```yaml
name: release-viewer
on:
  push:
    branches: [main, "release/**", "customer/**"]
  pull_request:
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

- **注意**：コレクタが評価するのは追跡ブランチの現状です。PR で実行しても「その PR をマージした後の状態」は評価しないため、PR 単位のゲートにはなりません（現状の違反を検出するだけ）。PR ゲートにするには、ブランチをマージ後のコミットに置き換えて評価する機能の追加が必要です。
- `actions/checkout` で `fetch-depth: 0` にすると、系列ブランチは `refs/remotes/origin/<branch>` として取得されます。コレクタはローカルブランチが見つからなければ、自動的にこちらを参照します。
- `check --site` は違反があってもサイトを出力してから終了コード 1 で終わります。違反があっても画面は公開したい場合は、上の例のように `if: always()` でアップロードしてください。
- 設定をFWリポジトリにコミットしたくない場合は、`--config-dir` で別の場所から渡せます。

## 既知の制約・注意

- 衝突を解消した cherry-pick は差分が変わるため、patch-id では一致しません。トレーラーか `-x` の記録がなければ「未適用」と判定されます（規約違反を見逃さないための意図した挙動です）。
- 1つの fix の単位（コミット）は「起点と同じ系列にある、`-x` 記録を持たない Fix-ID 付きコミット」で決まります。起点系列で後から同じ Fix-ID の追加修正を入れた場合は、それも単位に含まれます。
- 規模の大きいリポジトリでの性能は未計測です。系列ごとに到達可能なコミット集合をメモリに持つので、履歴が長い場合は `--since-ref` で走査範囲を絞ってください。
- 依存制約の書式は比較演算子のカンマ区切りだけです（`^` と `~` は使えません）。プレリリースも通常の semver の順序で比較します。
