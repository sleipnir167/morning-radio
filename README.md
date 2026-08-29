# 朝の散歩ラジオ

毎朝、天気と最新ニュースを取り込んだラジオ番組の原稿とMP3を自動生成し、
スマホのポッドキャストアプリに配信するシステム。

原稿は **①目次生成 → ②章ごとの個別執筆 → ③結合** の3ステップで作るため、
一発生成にありがちな「後半の失速」が起きにくくなっている。

## 構成

| 役割 | 使うもの | 費用 |
|---|---|---|
| 実行基盤 | GitHub Actions（cron + 手動実行） | 無料（Publicリポジトリなら無制限） |
| 原稿生成 | OpenRouter経由 DeepSeek V4 Pro（既定）/ Gemini API に切替可 | 約5円/回。Geminiなら無料 |
| 天気 | Open-Meteo | 無料・APIキー不要 |
| ニュース | Google News RSS | 無料・APIキー不要 |
| 音声合成 | Edge-TTS | 無料・APIキー不要 |
| 配信 | GitHub Releases + Pages（Podcast RSS） / Google Drive | 無料 |
| 履歴管理 | `history/topics.jsonl`（リポジトリにコミット） | 無料 |

```
起動（05:30 JST / スマホから手動）
  └ 天気・ニュース取得
      └ ① 目次生成（履歴を渡して重複回避・連載判定）
          └ ② 各章を1章ずつ執筆（前章の末尾を渡して接続を担保）
              └ ③ オープニング／つなぎ／エンディング生成 → 結合
                  └ Edge-TTSでMP3化（【SE: 間】を実際の無音に変換）
                      └ Releasesへ公開 → RSS更新 → Driveへアップ → 履歴コミット
```

## セットアップ

### 1. リポジトリを作る

```bash
git init && git add . && git commit -m "初期構成"
gh repo create morning-radio --public --source=. --push
```

Publicにすると Actions の実行時間が無制限になる。原稿を人に見せたくない場合は
Privateでもよい（月2,000分の無料枠内で十分収まる）。

### 2. Gemini APIキーを取得

[Google AI Studio](https://aistudio.google.com/apikey) で無料のAPIキーを発行する。

### 3. GitHub Secrets を登録

`Settings → Secrets and variables → Actions → New repository secret`

| 名前 | 必須 | 内容 |
|---|---|---|
| `OPENROUTER_API_KEY` | ○ | 既定の DeepSeek V4 Pro を使う場合 |
| `GEMINI_API_KEY` | | `llm.provider: gemini` に切り替える場合 |
| `GDRIVE_SERVICE_ACCOUNT_JSON` | | サービスアカウントのJSONを丸ごと貼る |
| `GDRIVE_FOLDER_ID` | | 配信先フォルダのID |

### 4. GitHub Pages を有効化

`Settings → Pages → Source: Deploy from a branch → main / docs`

初回実行後に `https://<ユーザー名>.github.io/morning-radio/` が公開される。

### 5. Google Drive 配信の設定（任意）

1. [Google Cloud Console](https://console.cloud.google.com/) でプロジェクトを作り、Drive API を有効化
2. サービスアカウントを作成し、JSONキーをダウンロード → `GDRIVE_SERVICE_ACCOUNT_JSON` に登録
3. Driveで配信先フォルダを作り、**サービスアカウントのメールアドレスに「編集者」で共有**
4. フォルダURLの `folders/` 以降のIDを `GDRIVE_FOLDER_ID` に登録

> サービスアカウント自体は保存容量を持たない。必ず自分のフォルダを共有して、そこへ書き込ませること。

### 6. 設定を自分用に変える

`config/config.yaml` の以下を編集する。

- `location` … 自分の住んでいる場所の緯度経度（初期値は東京）
- `genres` … 曜日ごとのジャンル（0=月曜〜6=日曜）
- `script.chapters` / `chars_per_chapter` … 章数と1章あたりの文字数
- `tts.voice` … `ja-JP-NanamiNeural`（女性）/ `ja-JP-KeitaNeural`（男性）など

### 7. Pixel 6a で購読

[AntennaPod](https://antennapod.org/)（無料・オープンソース）を入れ、
`https://<ユーザー名>.github.io/morning-radio/feed.xml` をURL指定で追加する。
自動ダウンロードをONにすれば、朝起きた時点で端末に音声が落ちている。

## 使い方

### 毎朝の自動実行

`.github/workflows/daily-radio.yml` の cron が JST 05:30 に起動する。
時刻を変えるならUTCで書く（JST = UTC+9）。

### スマホから手動実行

GitHubモバイルアプリ、またはブラウザで
`Actions → 朝の散歩ラジオ → Run workflow`。
その場でジャンル・章数・文字数・モデル・音声を指定できる。

```
genre:             海外の話題・異文化
chapters:          6
chars_per_chapter: 900
provider:          openrouter
model:             anthropic/claude-sonnet-4.5
```

### モデルの切り替え

`config/config.yaml` の `llm.provider` を変えるか、実行時に指定する。

| provider | model の例 | 1回あたりの目安 |
|---|---|---|
| `openrouter` | `deepseek/deepseek-v4-pro`（既定） | 約5円 |
| `openrouter` | `deepseek/deepseek-v3.2` | 約2円 |
| `openrouter` | `anthropic/claude-sonnet-4.5` | 約20〜40円 |
| `gemini` | `gemini-2.5-flash` / `gemini-2.5-pro` | 無料枠 |

### ローカル実行（Windows）

```powershell
python -m pip install -r requirements.txt
copy .env.example .env    # OPENROUTER_API_KEY を書く
python -m src.main
```

音声を作らず原稿だけ確認したいときは `$env:SKIP_TTS="1"` を付ける。
ffmpeg が無い環境でも動くが、`【SE: 間】` の無音挿入はスキップされる。

## 生成物

| パス | 内容 |
|---|---|
| `out/YYYY-MM-DD/script.md` | 完成原稿 |
| `out/YYYY-MM-DD/outline.json` | 目次（デバッグ用） |
| `out/YYYY-MM-DD/radio-YYYY-MM-DD.mp3` | 音声 |
| `docs/feed.xml` | Podcast RSS |
| `docs/index.html` | 一覧ページ（原稿とプレーヤー） |
| `history/topics.jsonl` | 過去回のトピック履歴 |

## 重複防止と連載のしくみ

放送のたびに `history/topics.jsonl` へ、その回のトピックと章タイトルを1行追記して
リポジトリにコミットする。翌日の目次生成では直近30日分（`history.lookback_days`）を
プロンプトに渡し、同じ話題を避けさせる。

ただし「続報が出た」「前回は入口しか触れられなかった」話題が1つだけある場合は、
目次生成が `series` フィールドを返して1章だけ連載回にする。
その章の冒頭では前回の要点を15秒だけ振り返ってから本題に入る。

## プロンプト

3つのシステムプロンプトは `prompts/` にMarkdownで置いてある。編集すれば作風が変わる。

| ファイル | 役割 |
|---|---|
| `system_outline.md` | ①構成案をJSONで設計。重複回避と連載判定もここ |
| `system_chapter.md` | ②1章分の本文を執筆。口語体・尺・密度のルール |
| `system_assemble.md` | ③オープニング／つなぎ／ポイント3選を含むエンディング |

`{{変数}}` は実行時に置換される（`program_title` / `genre` / `target_chars` など）。

## つまずきやすい点

- **DeepSeek がまれに文字化けする**: 日本語生成中に半角カナや全角ラテン文字が語中へ混入することがある
  （「とにかｗ目立つ」「経剱消費」など）。実測で5章中1章に発生した。
  `pipeline.garbled_chars()` が検知して章単位で自動的に書き直すが、
  頻発するようなら `gemini-2.5-flash` に切り替えるのが確実。
- **文字数が足りない**: 章の文字数が目標の75%未満なら自動で書き直しを1回かける。
  それでも足りない場合は `chars_per_chapter` を下げるか、上位モデルに切り替える。
- **JSONのパースに失敗する**: モデルが不正なエスケープを出すことがあるため、
  修復を試みたうえで `llm.retries` 回まで生成し直す。それでも落ちる場合は章数を減らす。
- **Gemini のレート制限**: 無料枠は分あたりのリクエスト数に上限がある。
  章数を増やしすぎると429になるため、7章以上にするなら `llm.retries` を増やす。
- **Actions が push できない**: `Settings → Actions → General → Workflow permissions` を
  `Read and write permissions` にする。
- **60日以上前の音声が消えている**: Releasesは残るが `docs/episodes.json` は
  `max_episodes` 件で打ち切られる。増やしたければ `config/config.yaml` で変更する。
