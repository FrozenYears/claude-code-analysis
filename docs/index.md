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
    title: 四层架构全景
    details: 从用户界面层、引擎层、工具层到基础设施层，完整呈现 Claude Code 的分层架构设计与模块依赖关系
  - icon: ⚙️
    title: Agentic Loop 核心
    details: 逐行解析 query() 中的无限循环机制，揭示 LLM 与 40+ 工具之间的多轮交互核心逻辑
  - icon: 🤖
    title: 多 Agent 协作
    details: 探索子 Agent 创建、Fork 分身机制、Coordinator/Task 两种协调模式的完整实现
  - icon: 🔧
    title: 工具系统全解
    details: 覆盖 BashTool、ReadTool、WriteTool、AgentTool 等全部核心工具的注册、权限检查与执行流程
  - icon: 📦
    title: 上下文管理
    details: 理解 Snip → Microcompact → Context Collapse → Auto-compact 四级渐进式压缩策略
  - icon: 🔒
    title: 安全权限模型
    details: 揭示工具权限控制、BashTool 安全检查链、4 种权限模式的完整架构与实现细节
---
