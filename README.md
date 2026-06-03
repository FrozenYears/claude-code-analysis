# Claude Code 源码剖析

[![Deploy VitePress site to Pages](https://github.com/FrozenYears/claude-code-analysis/actions/workflows/deploy.yml/badge.svg)](https://github.com/FrozenYears/claude-code-analysis/actions/workflows/deploy.yml)

Deep reverse-engineering and architectural analysis of Claude Code, rewritten as a structured interactive documentation site.

## 概述

本项目是对 Claude Code 源码的深度逆向工程分析，以结构化、交互式的文档站点形式呈现。涵盖从 CLI 入口到安全机制的完整架构剖析。

- **源码仓库**: 777genius/claude-code-source-code-full
- **技术栈**: TypeScript + React/Ink (终端UI) + Bun runtime
- **源码规模**: 1894 个 .ts/.tsx 文件，约 51.5 万行代码

## 章节结构

| 章节 | 主题 | 核心文件 | 深度 |
|------|------|----------|------|
| 01 | 整体架构 | 四层架构、数据流、模块依赖 | ★★★★★ |
| 02 | CLI 入口与启动流程 | cli.tsx → main.tsx → init() | ★★★★ |
| 03 | QueryEngine 核心 | Agentic Loop、AsyncGenerator | ★★★★★ |
| 04 | Agent 系统 | 子 Agent、Fork、工具过滤 | ★★★★★ |
| 05 | Tool 系统 | Tool 接口、权限检查、BashTool | ★★★★ |
| 06 | Prompt 系统 | 系统提示词构建、缓存优化 | ★★★ |
| 07 | Context 系统 | 上下文管理、四级压缩 | ★★★★ |
| 08 | Session 与状态管理 | AppState、持久化、Task 生命周期 | ★★★★ |
| 09 | Streaming 与 API 通信 | 流式响应、错误恢复、重试机制 | ★★★★ |
| 10 | 多 Agent 协作 | Coordinator、Worker、通信机制 | ★★★★ |
| 11 | 命令系统 | 斜杠命令、参数解析、Help 系统 | ★★★★ |
| 12 | UI 组件系统 | Ink 渲染、REPL、终端 UI | ★★★★★ |
| 13 | 插件与扩展 | MCP、Skills、插件系统 | ★★★ |
| 14 | 安全与权限 | 权限模型、安全检查链 | ★★★★ |

## 本地运行

```bash
# 安装依赖
npm install

# 启动开发服务器
npm run dev

# 构建生产版本
npm run build

# 预览生产版本
npm run preview
```

## 技术栈

- **文档框架**: [VitePress](https://vitepress.dev/)
- **图表**: [Mermaid](https://mermaid.js.org/)
- **部署**: GitHub Pages
- **CI/CD**: GitHub Actions

## 项目结构

```
claude-code-analysis/
├── docs/
│   ├── .vitepress/
│   │   ├── config.ts          # VitePress 配置
│   │   └── theme/
│   │       ├── index.ts       # 主题入口
│   │       └── custom.css     # 自定义样式
│   ├── chapters/              # 15 章内容
│   ├── public/                # 静态资源
│   ├── index.md               # 首页
│   ├── reading-roadmap.md     # 阅读路线图
│   └── planning.md            # 写作规划
├── scripts/
│   └── rewrite.cjs            # CSS 重写工具
├── .github/
│   └── workflows/
│       └── deploy.yml         # GitHub Pages 部署
├── build_ebook.py             # 构建独立 HTML 电子书
├── package.json
└── README.md
```

## 许可

本项目内容基于 Claude Code 源码的逆向工程分析，仅供学习和研究使用。

## 相关链接

- [Claude Code 官方文档](https://docs.anthropic.com/en/docs/claude-code)
- [Claude Code 源码仓库](https://github.com/777genius/claude-code-source-code-full)
