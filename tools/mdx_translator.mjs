#!/usr/bin/env node
/**
 * MDX翻訳ヘルパー
 * MDX ↔ AST変換、テキスト抽出、翻訳適用をNode.js側で完結
 */

import { readFileSync } from 'fs'
import { fromMarkdown } from 'mdast-util-from-markdown'
import { toMarkdown } from 'mdast-util-to-markdown'
import { mdxFromMarkdown, mdxToMarkdown } from 'mdast-util-mdx'
import { mdxjs } from 'micromark-extension-mdxjs'

const command = process.argv[2]
const filePath = process.argv[3]

if (!command || !filePath) {
  console.error('Usage:')
  console.error('  node mdx_translator.mjs extract-text <file-path>')
  console.error('  node mdx_translator.mjs apply-translations <file-path> <translations-json>')
  console.error('  node mdx_translator.mjs apply-translations <file-path> - # read from stdin')
  process.exit(1)
}

/**
 * テキストノードを再帰的に抽出
 */
function extractTextNodes(node, nodes = [], path = []) {
  if (node.type === 'text') {
    const text = node.value || ''
    if (text.trim()) {
      nodes.push({
        id: nodes.length,
        text: text,
        path: [...path]
      })
    }
  }
  
  if (node.children) {
    node.children.forEach((child, index) => {
      extractTextNodes(child, nodes, [...path, index])
    })
  }
  
  return nodes
}

/**
 * パスを辿ってノードを取得
 */
function getNodeByPath(root, path) {
  let node = root
  for (const index of path) {
    if (!node.children || !node.children[index]) {
      return null
    }
    node = node.children[index]
  }
  return node
}

/**
 * 翻訳を適用（エラーがあればstderrに出力して終了）
 */
function applyTranslations(ast, translations) {
  const errors = []
  
  for (const item of translations) {
    const node = getNodeByPath(ast, item.path)
    if (!node) {
      errors.push(`ERROR: ノードが見つかりません (id=${item.id}, path=${JSON.stringify(item.path)})`)
      continue
    }
    if (node.type !== 'text') {
      errors.push(`ERROR: ノードの型が不正です (id=${item.id}, expected=text, actual=${node.type})`)
      continue
    }
    node.value = item.translated
  }
  
  // エラーがあればstderrに出力して終了
  if (errors.length > 0) {
    console.error(errors.join('\n'))
    process.exit(1)
  }
  
  return ast
}

/**
 * MDX → AST
 */
function parseMarkdown(content) {
  return fromMarkdown(content, {
    extensions: [mdxjs()],
    mdastExtensions: [mdxFromMarkdown()]
  })
}

/**
 * AST → MDX
 */
function stringifyMarkdown(ast) {
  return toMarkdown(ast, {
    extensions: [mdxToMarkdown()],
    bullet: '-',  // 箇条書きを常に '-' に統一
    bulletOther: '*'  // 入れ子のリストは '*' を使用（必須）
  })
}

/**
 * 元のファイルからフォーマット情報を抽出
 */
function extractFormatInfo(content) {
  const lines = content.split('\n')
  const formatInfo = {
    calloutFormat: null
  }

  // Calloutの形式を検出（type= "info" のようなスペースの有無）
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    if (line.includes('<Callout')) {
      // type= "info" または type="info" のパターンを検出
      const hasSpaceAfterEquals = /type=\s+"/.test(line)
      formatInfo.calloutFormat = {
        hasSpaceAfterEquals,
        lineNumber: i,
        originalLine: line
      }
      break
    }
  }

  return formatInfo
}

/**
 * フォーマット情報を適用して出力を調整
 */
function applyFormatInfo(mdxOutput, formatInfo) {
  const lines = mdxOutput.split('\n')
  let inCallout = false

  // Calloutの形式を元のファイルに合わせる
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]

    // Calloutの開始タグ
    if (line.includes('<Callout')) {
      inCallout = true

      // type= "info" のようなスペースを保持
      if (formatInfo.calloutFormat && formatInfo.calloutFormat.hasSpaceAfterEquals) {
        lines[i] = line.replace(/type="([^"]+)"/, 'type= "$1"')
      }
    }
    // Calloutの終了タグ
    else if (line.includes('</Callout>')) {
      inCallout = false
    }
    // Callout内のコンテンツからインデントを削除
    else if (inCallout && line.startsWith('  ')) {
      lines[i] = line.substring(2)  // 先頭の2スペースを削除
    }
  }

  return lines.join('\n')
}

try {
  const content = readFileSync(filePath, 'utf8')
  const ast = parseMarkdown(content)

  if (command === 'extract-text') {
    // テキストノードを抽出してJSON出力
    const textNodes = extractTextNodes(ast)
    console.log(JSON.stringify(textNodes, null, 2))

  } else if (command === 'apply-translations') {
    // 翻訳を適用してMDX出力
    const translationsJson = process.argv[4]
    let translations

    if (!translationsJson || translationsJson === '-') {
      // 標準入力から読み込む（同期的に）
      const stdin = readFileSync(0, 'utf-8')  // 0 = stdin file descriptor
      translations = JSON.parse(stdin)
    } else {
      translations = JSON.parse(translationsJson)
    }

    applyTranslations(ast, translations)

    // 元のファイルのフォーマット情報を抽出
    const formatInfo = extractFormatInfo(content)

    // MDXに変換
    let mdx = stringifyMarkdown(ast)

    // フォーマット情報を適用（Calloutのスペースなど）
    mdx = applyFormatInfo(mdx, formatInfo)

    // 元のファイルの末尾の改行を保持
    // trimEnd()で余分な改行を削除し、元のファイルと同じ形式に
    mdx = mdx.trimEnd()

    // 元のファイルが改行で終わっている場合のみ改行を追加
    if (content.endsWith('\n')) {
      mdx += '\n'
    }

    // console.log() は自動的に改行を追加するため、process.stdout.write() を使用
    process.stdout.write(mdx)

  } else {
    console.error(`Unknown command: ${command}`)
    process.exit(1)
  }

} catch (error) {
  console.error('Error:', error.message)
  process.exit(1)
}

