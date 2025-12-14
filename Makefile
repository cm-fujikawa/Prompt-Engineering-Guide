.PHONY: setup translate translate-test validate clean-translations reset-progress help

help: ## ヘルプを表示
	@echo "利用可能なコマンド:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

setup: ## 開発環境をセットアップ
	@echo "📦 Node.js依存関係をインストール中..."
	pnpm add mdast-util-from-markdown mdast-util-to-markdown mdast-util-mdx micromark-extension-mdxjs
	@echo "📦 Python依存関係をインストール中..."
	cd tools && poetry install
	@echo "✅ セットアップ完了！"

translate: ## 全ファイルを翻訳（再開モード）
	cd tools && poetry run python translate_mdx.py

translate-test: ## テスト翻訳（1ファイルのみ）
	cd tools && poetry run python translate_mdx.py --limit 1

translate-skip-validation: ## 検証スキップで翻訳
	cd tools && poetry run python translate_mdx.py --skip-validation

validate: ## 既存の翻訳ファイルを検証してレポート生成
	cd tools && poetry run python translate_mdx.py --validate-only

clean-translations: ## 既存の翻訳ファイルを削除（確認プロンプトあり）
	cd tools && poetry run python translate_mdx.py --clean

reset-progress: ## 進捗をリセット（翻訳ファイルは残す）
	cd tools && poetry run python translate_mdx.py --reset-progress

dev: ## 開発サーバーを起動
	pnpm dev

report: ## 翻訳レポートを開く
	open tools/report/index.html

