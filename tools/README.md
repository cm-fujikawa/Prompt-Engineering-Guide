# 🌐 MDX翻訳ツール

Prompt Engineering GuideのMDXファイルを日本語に翻訳する自動化ツールです。

## 🎯 特徴

- 🔧 **Makefile対応**: `make`コマンドで簡単操作
- ✅ **Node.js + Python連携**: MDX構文処理はNode.js、翻訳はPythonで担当
- ✅ **構造を完全保持**: import文、JSXタグ名、属性、コードブロックを完全保護
- ✅ **外部用語管理**: `translation_terms.json`で用語を管理（コード変更不要）
- ✅ **中断・再開機能**: 処理中断時も進捗を保存、自動再開
- ✅ **品質レポート**: 逆翻訳による品質確認HTMLレポート生成
- ✅ **無料**: Google Translate APIを使用（deep-translator経由）

## 🚀 クイックスタート

```bash
# ヘルプを表示
make help

# セットアップ
make setup

# 翻訳実行
make translate
```

### 1. セットアップ

```bash
# Makefileを使用（推奨）
make setup
```

### 2. 翻訳実行

**注意**: プロジェクトルートから実行してください。

```bash
# 全ファイルを翻訳（再開モード - 既存ファイルはスキップ）
make translate

# テスト（1ファイルのみ）
make translate-test

# 最初からやり直す（全削除してゼロから）※確認プロンプトが表示されます
make clean-translations

# 検証スキップ（翻訳のみ）
make translate-skip-validation

# 進捗リセット（翻訳ファイルは残す）
make reset-progress
```

## 📊 翻訳対象

### ✅ 翻訳されるもの

- Markdownテキスト
- 見出し
- リスト
- 引用
- 画像のaltテキスト
- JSXコンポーネント内のテキスト

### ❌ 翻訳されないもの（保護されるもの）

- Import文
- JSXタグ名（`<Screenshot>` など）
- JSX属性（`src={...}` など）
- コードブロック
- インラインコード（`` `code` ``）
- URL
- 技術用語（辞書で定義された場合）

## 🔧 翻訳用語管理

`translation_terms.json`で管理：

```json
{
  "proper_nouns": [
    "ChatGPT", "GPT-4", "Claude", "Gemini", ...
  ],
  "tech_terms": {
    "Prompt Engineering": "プロンプトエンジニアリング",
    "AI Agents": "AIエージェント",
    "LLM Research Findings": "LLM研究成果"
  },
  "do_not_translate": [
    "page", "menu", "separator"
  ]
}
```

### 用語の追加・編集

1. `translation_terms.json`を開く
2. 必要な用語を追加
3. 保存して翻訳を実行（コード変更不要）

## ⚠️ 注意事項

### レート制限

- リクエスト間に0.2秒の遅延
- （参考）全154ファイル（132 MDX + 22メタファイル）の翻訳に約30-40分

### 翻訳品質

- 自動翻訳のため、手動レビューを推奨
- 技術用語が文脈により適切に翻訳されない場合がある

## 📊 翻訳品質の確認方法

### 1. 品質レポートで数値的に確認

翻訳時に自動生成される品質レポートで、逆翻訳による類似度を確認できます：

```bash
# 方法1: 新規翻訳時に自動でレポートが生成される
make translate

# 方法2: 既存の翻訳ファイルを検証してレポートを生成
# （全ファイルを逆翻訳して検証するため、時間がかかります）
make validate

# レポートを開く
make report
# または
open tools/report/index.html
```

**レポートの見方**：

- 🟢 **類似度 80%以上**: 高品質な翻訳
- 🟡 **類似度 60-80%**: 要確認（ニュアンスの違いがある可能性）
- 🔴 **類似度 60%未満**: 要修正（誤訳の可能性が高い）

各ファイルの詳細ページで、原文・翻訳文・逆翻訳文を並べて比較できます。

### 2. 実際のページで確認

翻訳されたページを確認：

```bash
# 起動
make dev

# ブラウザで確認
# - トップページ: http://localhost:3000/
# - または英語ページから右上の言語セレクター（🌐）で「日本語」を選択
# - レイアウト崩れやコードブロックの確認
```

### 3. 手動レビューのポイント

特に以下の点を確認することを推奨：

- ✅ 専門用語の訳語が適切か
- ✅ コードブロックが保護されているか
- ✅ JSXコンポーネントが正常に動作するか
- ✅ リンクやURL参照が壊れていないか
- ✅ 数式（LaTeX）が正しく表示されるか

## 🐛 トラブルシューティング

### エラー: Python依存関係のインストールに失敗

```bash
# Poetry環境をクリーン
cd tools && poetry env remove python && poetry install && cd ..

# または最初からセットアップし直す
make setup
```

### エラー: AST解析エラー

```bash
# MDXファイルの構文エラーをチェック
pnpm dev
# 該当ページで構文エラーがないか確認
```

### 翻訳が途中で止まる

- ネットワーク接続を確認
- しばらく待ってから再実行（進捗は自動保存されます）
- Google Translate APIのレート制限により0.2秒の遅延が入ります
