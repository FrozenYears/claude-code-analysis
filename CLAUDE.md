# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

Claude Code 源码剖析 — 一个深度逆向分析 Claude Code CLI 源码的文档站点，使用 VitePress 构建，内容为中文。同时提供独立 HTML 电子书。

分析对象：`777genius/claude-code-source-code-full`（约 1,894 个 TS/TSX 文件，~515k 行）

## 常用命令

```bash
npm install          # 安装依赖（Node 22）
npm run dev          # 启动 VitePress 开发服务器
npm run build        # 构建生产站点
npm run preview      # 预览构建结果

python3 build_ebook.py   # 构建独立 HTML 电子书 → ebook/index.html
```

无测试、无 lint、无类型检查。

## 架构要点

### 双内容结构

项目有两套平行的内容表示：

1. **根目录章节**（`00-阅读路线/` ~ `14-安全与权限/`，各含 `README.md`）— 原始内容，也是电子书构建源
2. **VitePress 章节**（`docs/chapters/01-architecture/` ~ `docs/chapters/14-security/`，各含 `index.md`）— 站点展示用

编辑内容时需注意两边保持同步，或明确当前只改哪一边。

### 章节模板

每章遵循 11 段结构（定义于 `规划.md`）：章节目标 → 前置知识 → 宏观概览 → 源码入口 → 调用链分析 → 核心源码分析 → 架构设计思想 → 工程实践细节 → 常见错误 → 本章小结 → 延伸思考

### 关键文件

| 文件 | 用途 |
|---|---|
| `docs/.vitepress/config.ts` | VitePress 配置（导航、侧边栏、Mermaid、搜索） |
| `docs/.vitepress/theme/custom.css` | Anthropic Docs 风格设计系统（~734 行，含暗色模式、响应式断点） |
| `docs/.vitepress/theme/index.ts` | 主题入口，扩展 DefaultTheme |
| `build_ebook.py` | 将根目录章节合并为单一 HTML 电子书 |
| `scripts/rewrite.cjs` | CSS 重写工具，覆盖 custom.css |
| `规划.md` | 写作规划和章节依赖图 |
| `.github/workflows/deploy.yml` | CI：push 到 main 触发构建并部署到 gh-pages |

### 技术栈

- VitePress 1.6.3 + Mermaid 11.4.1（通过 vitepress-plugin-mermaid）
- 纯 ES Modules，无 TypeScript 编译
- 部署：GitHub Pages（gh-pages 分支）

## 注意事项

- 站点语言为 `zh-CN`，导航和侧边栏均为中文
- Mermaid 图表在内容中广泛使用，修改 Markdown 时注意不破坏图表语法
- `custom.css` 是完整的设计系统，改动需兼顾亮色/暗色模式和 768px/1024px/1440px 三个响应式断点
- 当前无 lint/format 工具，保持现有代码风格一致性
