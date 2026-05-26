---
layout: home
title: Claude Code 源码剖析
titleTemplate: 深度解析 AI 编程助手的实现原理

hero:
  name: Claude Code 源码剖析
  text: 深度解析 AI 编程助手的实现原理
  tagline: 从 CLI 入口到安全机制，逐行剖析 51.5 万行 TypeScript 源码
  actions:
    - theme: brand
      text: 开始阅读
      link: /chapters/01-architecture/
    - theme: alt
      text: 阅读路线图
      link: /reading-roadmap
    - theme: alt
      text: GitHub
      link: https://github.com/FrozenYears/claude-code-analysis

features:
  - icon: 🏗️
    title: 四层架构
    details: 深入理解用户界面层、引擎层、工具层、基础设施层的完整架构设计
  - icon: ⚙️
    title: Agentic Loop
    details: 剖析 query() 函数中的无限循环，理解 LLM 与工具之间的多轮交互机制
  - icon: 🤖
    title: Agent 系统
    details: 探索子 Agent 创建、Fork 机制、多 Agent 协作的完整实现
  - icon: 🔧
    title: Tool 系统
    details: 分析 40+ 内置工具的注册、权限检查、执行流程的完整设计
  - icon: 📦
    title: Context 管理
    details: 理解四级上下文压缩策略：Snip、Microcompact、Context Collapse、Auto-compact
  - icon: 🔒
    title: 安全机制
    details: 揭示工具权限控制、BashTool 安全检查链、权限模式切换的完整安全架构
