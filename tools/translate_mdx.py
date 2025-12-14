#!/usr/bin/env python3
"""
MDX翻訳・検証システム
ASTで構文解析し、翻訳・逆翻訳・比較を行う統合ツール
"""

import sys
import time
import json
import argparse
import difflib
import subprocess
import re
from pathlib import Path
from typing import List, Dict, Tuple, Any, Optional
from datetime import datetime
from deep_translator import GoogleTranslator

# 設定
DELAY_BETWEEN_TRANSLATIONS = 0.2
SIMILARITY_WARNING_THRESHOLD = 0.7  # 類似度70%未満で警告
NODE_TRANSLATOR_SCRIPT = Path("mdx_translator.mjs")
TERMS_FILE = Path("translation_terms.json")
MAX_RETRY_COUNT = 3  # 翻訳リトライ回数
PROPER_NOUN_MAX_CHARS = 50  # 固有名詞判定の最大文字数
PROPER_NOUN_MAX_WORDS = 5   # 固有名詞判定の最大単語数


class TranslationError(Exception):
    """翻訳エラー"""
    pass


class BackTranslationError(Exception):
    """逆翻訳エラー"""
    pass


class TermsManager:
    """翻訳用語管理"""
    
    def __init__(self, terms_file: Path):
        self.terms_file = terms_file
        self.proper_nouns = []
        self.tech_terms = {}
        self.do_not_translate = []
        self._load()
    
    def _load(self):
        """用語ファイルを読み込み"""
        if not self.terms_file.exists():
            print(f"⚠️  警告: {self.terms_file} が見つかりません。用語保護なしで続行します。")
            return
        
        try:
            with open(self.terms_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                self.proper_nouns = data.get('proper_nouns', [])
                self.tech_terms = data.get('tech_terms', {})
                self.do_not_translate = data.get('do_not_translate', [])
        except Exception as e:
            print(f"⚠️  警告: 用語ファイルの読み込みエラー: {e}")
    
    def is_proper_noun(self, text: str) -> bool:
        """固有名詞かどうか判定（短いテキストのみ対象）"""
        if not text or not text.strip():
            return False
        
        text = text.strip()
        
        # 完全一致チェック
        if text in self.proper_nouns:
            return True
        
        # 長い文章は翻訳対象（固有名詞判定をスキップ）
        words = text.replace('-', ' ').replace('.', ' ').split()
        if len(text) >= PROPER_NOUN_MAX_CHARS or len(words) >= PROPER_NOUN_MAX_WORDS:
            return False
        
        # 部分一致チェック（例: "Claude 3"）- 短いテキストのみ
        for noun in self.proper_nouns:
            if noun in text:
                return True
        
        # 全て大文字のアクロニム（2文字以上、GPT, LLM, RAG等）- 短いテキストのみ
        if any(word.isupper() and len(word) >= 2 for word in words):
            return True
        
        return False
    
    def get_tech_term_translation(self, text: str) -> Optional[str]:
        """技術用語の翻訳を取得（なければNone）"""
        return self.tech_terms.get(text)
    
    def should_not_translate(self, text: str) -> bool:
        """翻訳すべきでない用語かチェック"""
        text_stripped = text.strip()
        
        # 空または短すぎるテキストはスキップ
        if not text_stripped or len(text_stripped) <= 2:
            return True
        
        # 句読点・記号のみのテキストはスキップ
        if re.match(r'^[\s\W]+$', text_stripped):
            return True
        
        # 用語リストに含まれているかチェック
        if text in self.do_not_translate:
            return True
        
        # メールアドレスパターンをチェック
        email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
        if re.match(email_pattern, text_stripped):
            return True
        
        # 著者引用パターンをチェック（例: "Dai et al. (2022)", "Smith (2021)"）
        # "et al." を含む
        if re.search(r'\bet\s+al\.', text):
            return True
        # "[著者名 (年)]" または "(著者名, 年)" パターン
        if re.search(r'\([A-Z][a-z]+(?:\s+(?:et\s+al\.?|and|&)\s+[A-Z][a-z]+)*,?\s*\d{4}\)', text):
            return True
        # "[著者名 et al., 年]" パターン
        if re.search(r'[A-Z][a-z]+\s+et\s+al\.,?\s*\d{4}', text):
            return True
        
        return False


class ProgressManager:
    """進捗管理"""
    
    def __init__(self, progress_file: Path):
        self.progress_file = progress_file
        self.progress = self._load()
    
    def _load(self) -> Dict:
        """進捗ファイルを読み込み"""
        if self.progress_file.exists():
            with open(self.progress_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}
    
    def save(self):
        """進捗ファイルを保存"""
        with open(self.progress_file, 'w', encoding='utf-8') as f:
            json.dump(self.progress, f, ensure_ascii=False, indent=2)
    
    def get_status(self, file_path: str) -> str:
        """ファイルの状態を取得"""
        return self.progress.get(file_path, 'pending')
    
    def set_status(self, file_path: str, status: str):
        """ファイルの状態を設定"""
        self.progress[file_path] = status
        self.save()
    
    def is_translated(self, file_path: str) -> bool:
        """翻訳済みかチェック"""
        return self.get_status(file_path) in ['translated', 'validated', 'completed']
    
    def is_validated(self, file_path: str) -> bool:
        """検証済みかチェック"""
        return self.get_status(file_path) in ['validated', 'completed']
    
    def reset(self):
        """進捗をリセット"""
        self.progress = {}
        if self.progress_file.exists():
            self.progress_file.unlink()


class MDXParser:
    """MDX構文解析器（ASTベース）"""
    
    def __init__(self):
        self.parser_script = NODE_TRANSLATOR_SCRIPT
        if not self.parser_script.exists():
            raise FileNotFoundError(f"Parser script not found: {self.parser_script}")
    
    def parse_file(self, file_path: Path) -> Dict:
        """MDXファイルをASTに変換"""
        try:
            result = subprocess.run(
                ['node', str(self.parser_script), str(file_path)],
                capture_output=True,
                text=True,
                check=True
            )
            return json.loads(result.stdout)
        except subprocess.CalledProcessError as e:
            print(f"AST解析エラー: {e.stderr}")
            raise
        except json.JSONDecodeError as e:
            print(f"JSON解析エラー: {e}")
            raise


class Translator:
    """翻訳エンジン"""
    
    def __init__(self):
        self.en_to_ja = GoogleTranslator(source='en', target='ja')
        self.ja_to_en = GoogleTranslator(source='ja', target='en')
        self.translation_count = 0
    
    def translate(self, text: str, direction: str = 'en_to_ja') -> str:
        """テキストを翻訳（リトライ付き、失敗時は例外スロー）"""
        if not text or not text.strip():
            return text
        
        last_error = None
        
        for attempt in range(1, MAX_RETRY_COUNT + 1):
            try:
                # 翻訳
                if direction == 'en_to_ja':
                    translated = self.en_to_ja.translate(text)
                else:
                    translated = self.ja_to_en.translate(text)
                
                # Noneが返された場合はエラー
                if translated is None:
                    raise TranslationError(f"翻訳APIがNoneを返しました: {text[:50]}...")
                
                self.translation_count += 1
                time.sleep(DELAY_BETWEEN_TRANSLATIONS)
                
                return translated
            
            except TranslationError:
                # TranslationErrorはリトライせずに即座にスロー
                raise
            
            except Exception as e:
                last_error = e
                if attempt < MAX_RETRY_COUNT:
                    print(f"    翻訳エラー（リトライ {attempt}/{MAX_RETRY_COUNT}）: {e}")
                    time.sleep(DELAY_BETWEEN_TRANSLATIONS * 2)  # リトライ前に少し待つ
                else:
                    break
        
        # 全てのリトライが失敗
        raise TranslationError(f"翻訳に失敗しました（{MAX_RETRY_COUNT}回リトライ後）: {last_error}")


class MDXTranslationSystem:
    """統合翻訳システム（AST解析ベース）"""
    
    def __init__(self, terms_manager: TermsManager):
        self.parser = MDXParser()
        self.translator = Translator()
        self.terms = terms_manager
    
    def translate_mdx_file(self, src_path: Path, dst_path: Path) -> Dict:
        """MDXファイルを翻訳（Node.js MDXヘルパー使用）"""
        print(f"  翻訳中: {src_path.name}")
        
        # 1. Node.jsでテキストノードを抽出
        result = subprocess.run(
            ['node', str(NODE_TRANSLATOR_SCRIPT), 'extract-text', str(src_path)],
            capture_output=True,
            text=True,
            check=True
        )
        text_nodes = json.loads(result.stdout)
        
        # 2. Pythonで翻訳（エラー時は例外がスローされる）
        translations = []
        for node in text_nodes:
            original = node['text']
            
            # 翻訳すべきでないテキストはそのまま
            if self.terms.should_not_translate(original):
                translated = original
            # 固有名詞の場合もそのまま
            elif self.terms.is_proper_noun(original):
                translated = original
            # 技術用語の翻訳があればそれを使用
            elif self.terms.get_tech_term_translation(original):
                translated = self.terms.get_tech_term_translation(original)
            else:
                # 翻訳失敗時はTranslationErrorがスローされる
                translated = self.translator.translate(original, 'en_to_ja')
            
            translations.append({
                'id': node['id'],
                'path': node['path'],
                'original': original,
                'translated': translated
            })
        
        # 3. Node.jsで翻訳を適用してMDX生成
        translations_json = json.dumps(translations)
        result = subprocess.run(
            ['node', str(NODE_TRANSLATOR_SCRIPT), 'apply-translations', str(src_path), translations_json],
            capture_output=True,
            text=True,
            check=True
        )
        
        # Node.js側でエラーがあれば検出
        if result.stderr:
            error_lines = [line for line in result.stderr.strip().split('\n') if line.startswith('ERROR:')]
            if error_lines:
                raise TranslationError(f"翻訳適用エラー: {'; '.join(error_lines)}")
        
        translated_content = result.stdout
        
        # 保存（ここまで来たら全て成功）
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        with open(dst_path, 'w', encoding='utf-8') as f:
            f.write(translated_content)
        
        # 行数をカウント
        with open(src_path, 'r', encoding='utf-8') as f:
            src_content = f.read()
            src_lines = len(src_content.splitlines())
        dst_lines = len(translated_content.splitlines())
        
        print(f"    ✓ {src_lines}行 → {dst_lines}行 ({len(translations)}テキストノード)")
        
        return {
            'src': src_path,
            'dst': dst_path,
            'src_lines': src_lines,
            'dst_lines': dst_lines,
            'status': 'success'
        }
    
    def translate_meta_file(self, src_path: Path, dst_path: Path) -> Dict:
        """メタファイル(JSON)を翻訳"""
        print(f"  翻訳中: {src_path.name}")
        
        with open(src_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # 再帰的に値を翻訳
        translated_data = self._translate_json_values(data)
        
        # 保存
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        with open(dst_path, 'w', encoding='utf-8') as f:
            json.dump(translated_data, f, ensure_ascii=False, indent=2)
        
        print(f"    ✓ 完了")
        
        return {
            'src': src_path,
            'dst': dst_path,
            'status': 'success'
        }
    
    def _translate_json_values(self, obj: Any) -> Any:
        """JSON内の値を再帰的に翻訳"""
        if isinstance(obj, dict):
            return {k: self._translate_json_values(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._translate_json_values(item) for item in obj]
        elif isinstance(obj, str):
            # URL、キー名は翻訳しない
            if obj.startswith(('http://', 'https://', '/', '.')):
                return obj
            
            # 翻訳禁止リストをチェック
            if self.terms.should_not_translate(obj):
                return obj
            
            # 技術用語の翻訳があればそれを使用
            tech_translation = self.terms.get_tech_term_translation(obj)
            if tech_translation:
                return tech_translation
            
            # 固有名詞を保護（翻訳しない）
            if self.terms.is_proper_noun(obj):
                return obj
            
            # テキストを翻訳
            return self.translator.translate(obj, 'en_to_ja').strip()
        else:
            return obj
    
    def back_translate_file(self, jp_path: Path) -> str:
        """日本語ファイルを英語に逆翻訳（エラー時は例外スロー）"""
        # 1. Node.jsでテキストノードを抽出
        result = subprocess.run(
            ['node', str(NODE_TRANSLATOR_SCRIPT), 'extract-text', str(jp_path)],
            capture_output=True,
            text=True,
            check=True
        )
        text_nodes = json.loads(result.stdout)

        # 2. Pythonで逆翻訳（日→英）- エラー時は例外がスローされる
        translations = []
        for node in text_nodes:
            original = node['text']
            
            # 翻訳不要なテキストはそのまま
            if self.terms.should_not_translate(original):
                back_translated = original
            else:
                back_translated = self.translator.translate(original, 'ja_to_en')
            
            translations.append({
                'id': node['id'],
                'path': node['path'],
                'original': original,
                'translated': back_translated
            })

        # 3. Node.jsで逆翻訳を適用してMDX生成
        translations_json = json.dumps(translations)
        result = subprocess.run(
            ['node', str(NODE_TRANSLATOR_SCRIPT), 'apply-translations', str(jp_path), translations_json],
            capture_output=True,
            text=True,
            check=True
        )
        
        # Node.js側でエラーがあれば検出
        if result.stderr:
            error_lines = [line for line in result.stderr.strip().split('\n') if line.startswith('ERROR:')]
            if error_lines:
                raise BackTranslationError(f"逆翻訳適用エラー: {'; '.join(error_lines)}")
        
        return result.stdout
    
    def calculate_similarity(self, text1: str, text2: str) -> float:
        """テキストの類似度を計算"""
        matcher = difflib.SequenceMatcher(None, text1.lower(), text2.lower())
        return matcher.ratio()


class ReportGenerator:
    """HTMLレポート生成"""
    
    def generate_validation_report(self, validation_results: List[Dict], output_dir: Path):
        """検証結果のHTMLレポートを生成"""
        output_dir.mkdir(exist_ok=True)
        
        # 個別ファイルのレポート
        for result in validation_results:
            if result['status'] == 'success':
                self._generate_file_report(result, output_dir)
        
        # インデックスページ
        self._generate_index(validation_results, output_dir)
        
        print(f"\n📄 レポート生成完了: {output_dir}/index.html")
    
    def _generate_file_report(self, result: Dict, output_dir: Path):
        """個別ファイルの詳細レポート"""
        filename = result['file_name'].replace('.jp.mdx', '_report.html')
        filepath = output_dir / filename

        # 差分を生成
        differ = difflib.HtmlDiff(wrapcolumn=80)
        html_diff = differ.make_table(
            result['original_lines'],
            result['back_translated_lines'],
            fromdesc=f"元の英語: {result['en_file'].name}",
            todesc=f"逆翻訳: {result['jp_file'].name} → 英語",
            context=True,
            numlines=3
        )

        # 警告バッジ
        warning = ''
        if result['similarity'] < SIMILARITY_WARNING_THRESHOLD:
            warning = f'''
            <div class="warning-badge">
                ⚠️ 警告: 類似度が低いです ({result['similarity']:.1%})
                <br>手動での確認を推奨します
            </div>
            '''

        # パスを正しく計算（相対パスまたは絶対パス）
        try:
            # pages_dirからの相対パスを取得
            pages_dir = Path(__file__).parent.parent / 'pages'
            relative_path = result['jp_file'].resolve().relative_to(pages_dir.resolve())
        except ValueError:
            # 相対パス計算に失敗した場合はファイル名のみ表示
            relative_path = result['jp_file'].name

        html = f'''<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <title>{result['file_name']} - 翻訳検証</title>
    <style>
        body {{ font-family: -apple-system, sans-serif; padding: 20px; background: #f5f5f5; }}
        .header {{ background: white; padding: 20px; border-radius: 8px; margin-bottom: 20px; }}
        .stats {{ display: flex; gap: 20px; margin: 20px 0; }}
        .stat {{ background: #e3f2fd; padding: 15px; border-radius: 4px; flex: 1; text-align: center; }}
        .stat-value {{ font-size: 24px; font-weight: bold; color: #1976d2; }}
        .stat-label {{ font-size: 12px; color: #666; margin-top: 5px; }}
        .warning-badge {{ background: #fff3cd; border-left: 4px solid #ff9800; padding: 15px; margin: 20px 0; }}
        table.diff {{ width: 100%; background: white; border-radius: 8px; overflow: hidden; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>📝 {result['file_name']}</h1>
        <p>パス: {relative_path}</p>
    </div>
    
    {warning}
    
    <div class="stats">
        <div class="stat">
            <div class="stat-value">{result['similarity']:.1%}</div>
            <div class="stat-label">類似度</div>
        </div>
        <div class="stat">
            <div class="stat-value">{result['src_lines']}</div>
            <div class="stat-label">元の行数</div>
        </div>
        <div class="stat">
            <div class="stat-value">{result['dst_lines']}</div>
            <div class="stat-label">翻訳後の行数</div>
        </div>
    </div>
    
    {html_diff}
    
    <p style="text-align: center; margin-top: 20px;">
        <a href="index.html">← インデックスに戻る</a>
    </p>
</body>
</html>
'''
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(html)
    
    def _generate_index(self, results: List[Dict], output_dir: Path):
        """インデックスページを生成"""
        # 類似度でソート
        sorted_results = sorted(results, key=lambda x: x.get('similarity', 1.0))
        
        # 統計
        total = len(results)
        avg_similarity = sum(r.get('similarity', 0) for r in results) / total if total > 0 else 0
        warnings = sum(1 for r in results if r.get('similarity', 1) < SIMILARITY_WARNING_THRESHOLD)
        
        # テーブル行を生成
        table_rows = ''
        for result in sorted_results:
            if result['status'] != 'success':
                continue
            
            similarity = result['similarity']
            filename = result['file_name'].replace('.jp.mdx', '_report.html')
            
            # 評価アイコン
            if similarity >= 0.8:
                icon = '✅'
                class_name = 'good'
            elif similarity >= SIMILARITY_WARNING_THRESHOLD:
                icon = '⚠️'
                class_name = 'warning'
            else:
                icon = '❌'
                class_name = 'error'
            
            table_rows += f'''
            <tr class="{class_name}">
                <td>{icon}</td>
                <td><a href="{filename}">{result['file_name']}</a></td>
                <td>{similarity:.1%}</td>
                <td>{result['src_lines']}</td>
                <td>{result['dst_lines']}</td>
            </tr>
            '''
        
        html = f'''<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <title>翻訳検証レポート</title>
    <style>
        body {{ font-family: -apple-system, sans-serif; padding: 20px; background: #f5f5f5; }}
        .header {{ background: white; padding: 30px; border-radius: 8px; margin-bottom: 20px; text-align: center; }}
        .stats {{ display: flex; gap: 20px; margin: 20px 0; }}
        .stat {{ background: white; padding: 20px; border-radius: 8px; flex: 1; text-align: center; }}
        .stat-value {{ font-size: 36px; font-weight: bold; color: #1976d2; }}
        .stat-label {{ font-size: 14px; color: #666; margin-top: 5px; }}
        table {{ width: 100%; background: white; border-collapse: collapse; border-radius: 8px; overflow: hidden; }}
        th {{ background: #1976d2; color: white; padding: 15px; text-align: left; }}
        td {{ padding: 12px 15px; border-bottom: 1px solid #e0e0e0; }}
        tr:hover {{ background: #f5f5f5; }}
        tr.error {{ background: #ffebee; }}
        tr.warning {{ background: #fff3e0; }}
        tr.good {{ background: #e8f5e9; }}
        a {{ color: #1976d2; text-decoration: none; }}
        a:hover {{ text-decoration: underline; }}
        .legend {{ margin: 20px 0; padding: 15px; background: white; border-radius: 8px; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>🌐 MDX翻訳検証レポート（ASTベース）</h1>
        <p>生成日時: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
    </div>
    
    <div class="stats">
        <div class="stat">
            <div class="stat-value">{total}</div>
            <div class="stat-label">翻訳ファイル数</div>
        </div>
        <div class="stat">
            <div class="stat-value">{avg_similarity:.1%}</div>
            <div class="stat-label">平均類似度</div>
        </div>
        <div class="stat">
            <div class="stat-value">{warnings}</div>
            <div class="stat-label">⚠️ 要確認</div>
        </div>
    </div>
    
    <div class="legend">
        <strong>凡例:</strong>
        ✅ 優秀 (80%以上) | ⚠️ 要確認 (70-80%) | ❌ 要修正 (70%未満)
    </div>
    
    <table>
        <thead>
            <tr>
                <th width="50"></th>
                <th>ファイル名</th>
                <th width="120">類似度</th>
                <th width="100">元の行数</th>
                <th width="100">翻訳後</th>
            </tr>
        </thead>
        <tbody>
            {table_rows}
        </tbody>
    </table>
</body>
</html>
'''
        
        index_path = output_dir / 'index.html'
        with open(index_path, 'w', encoding='utf-8') as f:
            f.write(html)


def detect_orphan_files(pages_dir: Path) -> Tuple[List[Path], List[Path]]:
    """孤立ファイルを検出（英語ファイルがない日本語ファイル、または日本語ファイルがない英語ファイル）"""
    orphan_jp_files = []
    missing_jp_files = []

    # 日本語ファイルで対応する英語ファイルがないものを検出
    jp_mdx_files = list(pages_dir.rglob("*.jp.mdx"))
    jp_meta_files = list(pages_dir.rglob("*_meta.jp.json"))

    for jp_file in jp_mdx_files + jp_meta_files:
        if '.jp.mdx' in str(jp_file):
            en_file = Path(str(jp_file).replace('.jp.mdx', '.en.mdx'))
        else:
            en_file = Path(str(jp_file).replace('.jp.json', '.en.json'))

        if not en_file.exists():
            orphan_jp_files.append(jp_file)

    # 英語ファイルで対応する日本語ファイルがないものを検出
    en_mdx_files = list(pages_dir.rglob("*.en.mdx"))
    en_meta_files = list(pages_dir.rglob("*_meta.en.json"))

    for en_file in en_mdx_files + en_meta_files:
        if '.en.mdx' in str(en_file):
            jp_file = Path(str(en_file).replace('.en.mdx', '.jp.mdx'))
        else:
            jp_file = Path(str(en_file).replace('.en.json', '.jp.json'))

        if not jp_file.exists():
            missing_jp_files.append(en_file)

    return orphan_jp_files, missing_jp_files


def clean_japanese_files(pages_dir: Path):
    """既存の日本語ファイルを削除"""
    print("\n🗑️  既存の日本語ファイルを削除します...")

    # 孤立ファイルを検出
    orphan_jp_files, _ = detect_orphan_files(pages_dir)

    if orphan_jp_files:
        print(f"\n⚠️  警告: 対応する英語ファイルがない日本語ファイルが{len(orphan_jp_files)}件見つかりました:")
        # プロジェクトルートからの相対パスで表示
        project_root = pages_dir.parent
        for f in orphan_jp_files[:10]:  # 最初の10件のみ表示
            try:
                rel_path = f.relative_to(project_root)
                print(f"    - {rel_path}")
            except ValueError:
                # フォールバック: pages/からの相対パス
                try:
                    rel_path = f.relative_to(pages_dir)
                    print(f"    - pages/{rel_path}")
                except ValueError:
                    print(f"    - {f.name}")
        if len(orphan_jp_files) > 10:
            print(f"    ... 他 {len(orphan_jp_files) - 10}件")

    jp_files = list(pages_dir.rglob("*.jp.mdx")) + list(pages_dir.rglob("*_meta.jp.json"))

    if not jp_files:
        print("  削除対象のファイルが見つかりませんでした")
        return

    print(f"\n  削除対象: {len(jp_files)}件")

    # ファイルリストを表示（最大20件、それ以上は省略）
    print("\n  削除されるファイル:")
    project_root = pages_dir.parent
    for i, f in enumerate(jp_files[:20], 1):
        try:
            rel_path = f.relative_to(project_root)
            print(f"    {i}. {rel_path}")
        except ValueError:
            try:
                rel_path = f.relative_to(pages_dir)
                print(f"    {i}. pages/{rel_path}")
            except ValueError:
                print(f"    {i}. {f.name}")

    if len(jp_files) > 20:
        print(f"    ... 他 {len(jp_files) - 20}件")

    print()
    response = input("  本当に削除しますか？ (yes/no): ").strip().lower()

    if response not in ['yes', 'y']:
        print("  キャンセルしました")
        sys.exit(0)

    for f in jp_files:
        f.unlink()

    print(f"  ✓ {len(jp_files)}件削除しました")


def find_all_english_files(pages_dir: Path) -> Tuple[List[Tuple[Path, Path]], List[Tuple[Path, Path]]]:
    """全ての英語ファイルを検索"""
    mdx_files = []
    meta_files = []
    
    for en_file in sorted(pages_dir.rglob("*.en.mdx")):
        jp_file = Path(str(en_file).replace('.en.mdx', '.jp.mdx'))
        mdx_files.append((en_file, jp_file))
    
    for en_meta in sorted(pages_dir.rglob("_meta.en.json")):
        jp_meta = Path(str(en_meta).replace('.en.json', '.jp.json'))
        meta_files.append((en_meta, jp_meta))
    
    return mdx_files, meta_files


def main():
    parser = argparse.ArgumentParser(description='MDX翻訳・検証システム（ASTベース）')
    parser.add_argument('--limit', type=int, help='処理するファイル数の上限')
    parser.add_argument('--skip-validation', action='store_true', help='逆翻訳検証をスキップ')
    parser.add_argument('--validate-only', action='store_true', help='翻訳をスキップして既存ファイルの検証のみ実行')
    parser.add_argument('--clean', action='store_true', help='既存の日本語ファイルを全削除してゼロから開始')
    parser.add_argument('--reset-progress', action='store_true', help='進捗をリセット（翻訳ファイルは残す）')
    parser.add_argument('--retry-errors', action='store_true', help='エラーになったファイルのみ再翻訳')
    args = parser.parse_args()
    
    pages_dir = Path("../pages")
    report_dir = Path("report")
    progress_file = Path("report/translation_progress.json")
    
    print("=" * 70)
    print("MDX翻訳・検証システム（ASTベース - Node.js MDXパーサー使用）")
    print("=" * 70)
    
    # Node.js翻訳ヘルパーの確認
    if not NODE_TRANSLATOR_SCRIPT.exists():
        print(f"❌ エラー: {NODE_TRANSLATOR_SCRIPT} が見つかりません")
        sys.exit(1)
    
    # 用語管理の初期化
    terms = TermsManager(TERMS_FILE)
    
    # 進捗管理の初期化
    progress = ProgressManager(progress_file)
    
    # オプション処理
    if args.reset_progress:
        print("\n🔄 進捗をリセット中...")
        progress.reset()
        print("  ✓ リセット完了")
    
    if args.retry_errors:
        print("\n🔄 エラーファイルを再翻訳対象にします...")
        error_files = [f for f, status in progress.progress.items() if status == 'error']
        for f in error_files:
            # エラーステータスをクリア
            del progress.progress[f]
            # 対応する日本語ファイルを削除（存在すれば）
            jp_path = pages_dir / f
            if jp_path.exists():
                jp_path.unlink()
                print(f"  削除: {f}")
        progress.save()
        print(f"  ✓ {len(error_files)}件のエラーファイルを再翻訳対象にしました")
    
    # ステップ1: クリーンアップ（オプション）
    if args.clean:
        clean_japanese_files(pages_dir)
        progress.reset()
    else:
        print("\n♻️  再開モード: 既存の翻訳ファイルをスキップします")

    # ステップ1.5: 孤立ファイルの検出と報告
    print("\n🔍 ファイルの整合性をチェック中...")
    orphan_jp_files, missing_jp_files = detect_orphan_files(pages_dir)

    if orphan_jp_files:
        print(f"\n⚠️  警告: 対応する英語ファイルがない日本語ファイルが{len(orphan_jp_files)}件見つかりました:")
        # プロジェクトルートからの相対パスで表示（削除コマンドで使いやすいように）
        project_root = pages_dir.parent
        for f in orphan_jp_files[:5]:
            try:
                rel_path = f.relative_to(project_root)
                print(f"    - {rel_path}")
            except ValueError:
                # フォールバック: pages/からの相対パス
                try:
                    rel_path = f.relative_to(pages_dir)
                    print(f"    - pages/{rel_path}")
                except ValueError:
                    print(f"    - {f.name}")
        if len(orphan_jp_files) > 5:
            print(f"    ... 他 {len(orphan_jp_files) - 5}件")
        print("  これらのファイルは英語ファイルが存在しないため、翻訳で再作成されません。")
        print("  必要に応じて手動で削除してください。")
        print(f"\n  削除例: rm {' '.join(str(f.relative_to(project_root)) for f in orphan_jp_files[:3])}")

    # ステップ2: ファイルを検索
    print("\n📂 英語ファイルを検索中...")
    mdx_files, meta_files = find_all_english_files(pages_dir)
    all_files = mdx_files + meta_files

    print(f"  MDXファイル: {len(mdx_files)}件")
    print(f"  メタファイル: {len(meta_files)}件")
    print(f"  合計: {len(all_files)}件")

    if missing_jp_files:
        print(f"  翻訳が必要なファイル: {len(missing_jp_files)}件")

    if args.limit:
        all_files = all_files[:args.limit]
        print(f"  制限: {args.limit}件のみ処理")

    system = MDXTranslationSystem(terms)
    translation_results = []
    skipped_count = 0

    # ステップ3: 翻訳実行（--validate-onlyの場合はスキップ）
    if args.validate_only:
        print("\n" + "=" * 70)
        print("⏭️  翻訳スキップ（検証のみモード）")
        print("=" * 70)

        # 既存の翻訳済みファイルを収集
        for src, dst in all_files:
            if dst.exists() and src.suffix == '.mdx':
                with open(src, 'r', encoding='utf-8') as f:
                    src_lines = f.read().count('\n') + 1
                with open(dst, 'r', encoding='utf-8') as f:
                    dst_lines = f.read().count('\n') + 1
                translation_results.append({
                    'src': src,
                    'dst': dst,
                    'src_lines': src_lines,
                    'dst_lines': dst_lines,
                    'status': 'success'
                })

        print(f"  検証対象: {len(translation_results)}件のMDXファイル")
    else:
        print("\n" + "=" * 70)
        print("📝 翻訳開始（英語 → 日本語）")
        print("=" * 70)

        for i, (src, dst) in enumerate(all_files, 1):
            # 既に翻訳済みかチェック（ファイルの存在を最優先）
            dst_rel = str(dst.relative_to(pages_dir))

            if dst.exists():
                # ファイルが存在すれば、進捗ファイルに記録されていなくてもスキップ
                if not progress.is_translated(dst_rel):
                    # 進捗ファイルに記録がない場合は追加
                    progress.set_status(dst_rel, 'translated')

                print(f"[{i}/{len(all_files)}] ⏭️  スキップ: {src.name} (既に翻訳済み)")
                skipped_count += 1
                # 結果リストには追加（検証のため）
                if src.suffix == '.mdx':
                    with open(src, 'r', encoding='utf-8') as f:
                        src_lines = f.read().count('\n') + 1
                    with open(dst, 'r', encoding='utf-8') as f:
                        dst_lines = f.read().count('\n') + 1
                    translation_results.append({
                        'src': src,
                        'dst': dst,
                        'src_lines': src_lines,
                        'dst_lines': dst_lines,
                        'status': 'success'
                    })
                continue

            print(f"\n[{i}/{len(all_files)}]")
            try:
                if src.suffix == '.mdx':
                    result = system.translate_mdx_file(src, dst)
                else:
                    result = system.translate_meta_file(src, dst)
                
                # statusを確認してから進捗を保存
                if result.get('status') == 'success':
                    translation_results.append(result)
                    progress.set_status(dst_rel, 'translated')
                else:
                    # 想定外のstatus（現在の実装では発生しないはずだが安全のため）
                    print(f"  ⚠️ 予期しないstatus: {result.get('status')}")
                    progress.set_status(dst_rel, 'error')

            except Exception as e:
                error_type = "翻訳" if isinstance(e, (TranslationError, subprocess.CalledProcessError)) else "予期しない"
                print(f"  ✗ {error_type}エラー: {e}")
                # 不完全なファイルが作成されていたら削除
                if dst.exists():
                    dst.unlink()
                translation_results.append({
                    'src': src,
                    'dst': dst,
                    'status': 'error',
                    'error': str(e)
                })
                progress.set_status(dst_rel, 'error')

        if skipped_count > 0:
            print(f"\n  ⏭️  スキップ: {skipped_count}件")
            print(f"  ✅ 新規翻訳: {len(all_files) - skipped_count}件")
    
    # ステップ4: 検証（逆翻訳）
    if not args.skip_validation:
        print("\n" + "=" * 70)
        print("🔄 検証開始（日本語 → 英語 逆翻訳）")
        print("=" * 70)
        
        validation_results = []
        validated_count = 0
        
        for i, result in enumerate(translation_results, 1):
            if result['status'] != 'success' or result['dst'].suffix != '.mdx':
                continue

            # 既に検証済みかチェック（--validate-onlyモードでは強制実行）
            dst_rel = str(result['dst'].relative_to(pages_dir))

            if not args.validate_only and progress.is_validated(dst_rel):
                print(f"[{i}/{len(translation_results)}] ⏭️  スキップ: {result['dst'].name} (既に検証済み)")
                validated_count += 1
                continue
            
            print(f"\n[{i}/{len(translation_results)}] 検証中: {result['dst'].name}")
            
            try:
                # 逆翻訳
                back_translated = system.back_translate_file(result['dst'])
                
                # 元のファイルを読み込み
                with open(result['src'], 'r', encoding='utf-8') as f:
                    original = f.read()
                
                # 類似度を計算
                similarity = system.calculate_similarity(original, back_translated)
                
                print(f"  類似度: {similarity:.1%}")
                
                validation_results.append({
                    'file_name': result['dst'].name,
                    'en_file': result['src'],
                    'jp_file': result['dst'],
                    'similarity': similarity,
                    'src_lines': result['src_lines'],
                    'dst_lines': result['dst_lines'],
                    'original_lines': original.split('\n'),
                    'back_translated_lines': back_translated.split('\n'),
                    'status': 'success'
                })
                
                # 進捗を保存
                progress.set_status(dst_rel, 'validated')
                
            except Exception as e:
                print(f"  ✗ 検証エラー: {e}")
                progress.set_status(dst_rel, 'error')
        
        if validated_count > 0:
            print(f"\n  ⏭️  スキップ: {validated_count}件（既に検証済み）")
            print(f"  ✅ 新規検証: {len(validation_results)}件")
        
        # ステップ5: レポート生成
        if validation_results:
            print("\n" + "=" * 70)
            print("📊 レポート生成中...")
            print("=" * 70)
            
            report_gen = ReportGenerator()
            report_gen.generate_validation_report(validation_results, report_dir)
        else:
            print("\n  ℹ️  新規検証ファイルがないため、レポート生成をスキップ")
    
    # 完了
    print("\n" + "=" * 70)
    print("✅ 完了！")
    print("=" * 70)
    print(f"  処理ファイル: {len(all_files)}件")
    if skipped_count > 0:
        print(f"  スキップ: {skipped_count}件（既存）")
        print(f"  新規翻訳: {len(all_files) - skipped_count}件")
    print(f"  翻訳テキスト数: {system.translator.translation_count}件")
    
    if not args.skip_validation and validation_results:
        print(f"\n  📄 レポート確認:")
        print(f"    open {report_dir}/index.html")
    
    print(f"\n  💾 進捗保存: {progress_file}")
    print("  中断した場合、再実行で自動再開します")
    print("=" * 70)


if __name__ == "__main__":
    main()
