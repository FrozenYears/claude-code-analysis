---
title: Claude Code 源码学习路线图
---

# Claude Code 源码学习路线图

## 项目概况

- **源码仓库**: 777genius/claude-code-source-code-full
- **技术栈**: TypeScript + React/Ink (终端UI) + Bun runtime
- **源码规模**: 1894 个 .ts/.tsx 文件，约 51.5 万行代码
- **核心文件**: main.tsx (4684行)、QueryEngine.ts (1297行)、query.ts (1730行)

## 学习阶段划分

### 🟢 第一阶段：入口与骨架（先读懂程序怎么跑起来的）

| 序号 | 章节 | 核心文件 | 优先级 | 预计时间 |
|------|------|----------|--------|----------|
| 1 | CLI入口与启动流程 | `entrypoints/cli.tsx` → `main.tsx` | ⭐⭐⭐⭐⭐ | 2-3天 |
| 2 | QueryEngine核心循环 | `QueryEngine.ts` → `query.ts` | ⭐⭐⭐⭐⭐ | 3-4天 |
| 3 | REPL交互循环 | `screens/REPL.tsx` → `ink.ts` | ⭐⭐⭐⭐ | 2天 |

**目标**：理解程序从 `claude` 命令到开始对话的完整流程。

### 🟡 第二阶段：核心机制（理解 Agent 怎么工作的）

| 序号 | 章节 | 核心文件 | 优先级 | 预计时间 |
|------|------|----------|--------|----------|
| 4 | Tool系统 | `Tool.ts` → `tools.ts` → `tools/` | ⭐⭐⭐⭐⭐ | 3-4天 |
| 5 | Prompt系统 | `context.ts` → `context/` → `utils/systemPrompt*.ts` | ⭐⭐⭐⭐⭐ | 2-3天 |
| 6 | Context管理 | `context/` → `utils/attachments.ts` | ⭐⭐⭐⭐ | 2天 |
| 7 | Agent系统 | `tools/AgentTool/` → `tasks/` | ⭐⭐⭐⭐⭐ | 3-4天 |

**目标**：理解 Agent 如何接收指令、选择工具、执行任务。

### 🔴 第三阶段：高级机制（理解多Agent、通信、状态管理）

| 序号 | 章节 | 核心文件 | 优先级 | 预计时间 |
|------|------|----------|--------|----------|
| 8 | 多Agent协作 | `coordinator/` → `tasks/LocalAgentTask/` | ⭐⭐⭐⭐⭐ | 3-4天 |
| 9 | Session与状态管理 | `state/` → `utils/sessionStorage.ts` | ⭐⭐⭐⭐ | 2天 |
| 10 | Streaming与API通信 | `services/api/` → `query.ts` | ⭐⭐⭐⭐ | 2-3天 |
| 11 | 命令系统 | `commands/` → `commands.ts` | ⭐⭐⭐ | 2天 |

**目标**：理解多Agent如何并行、通信、状态如何持久化。

### ⚫ 第四阶段：支撑系统（理解工程化细节）

| 序号 | 章节 | 核心文件 | 优先级 | 预计时间 |
|------|------|----------|--------|----------|
| 12 | UI组件系统 | `components/` → `screens/` | ⭐⭐⭐ | 2天 |
| 13 | 插件与扩展 | `plugins/` → `skills/` → `services/mcp/` | ⭐⭐⭐ | 2天 |
| 14 | 安全与权限 | `hooks/toolPermission/` → `utils/permissions/` | ⭐⭐⭐⭐ | 2天 |

**目标**：理解终端UI渲染、插件机制、权限控制。

---

## 推荐阅读顺序

```
entrypoints/cli.tsx
    ↓
main.tsx (入口，最核心)
    ↓
setup.ts (初始化)
    ↓
QueryEngine.ts (LLM调用)
    ↓
query.ts (agentic loop)
    ↓
Tool.ts + tools.ts (工具系统)
    ↓
tools/AgentTool/ (子Agent)
    ↓
context.ts + context/ (上下文)
    ↓
coordinator/ (多Agent)
    ↓
tasks/LocalAgentTask/ (任务管理)
    ↓
services/api/claude.ts (API通信)
    ↓
state/ (状态管理)
```

## 关键概念速查

| 概念 | 位置 | 说明 |
|------|------|------|
| Agentic Loop | `query.ts` | 核心循环：发请求→执行工具→再发请求 |
| Tool | `Tool.ts` | 工具的类型定义和接口 |
| AgentTool | `tools/AgentTool/` | 子Agent的创建和管理 |
| Coordinator | `coordinator/` | 多Agent协调者模式 |
| Fork Subagent | `tools/AgentTool/forkSubagent.ts` | 缓存优化的分身机制 |
| Context | `context/` | 系统提示词和用户上下文 |
| Session | `utils/sessionStorage.ts` | 对话历史持久化 |
| Permission | `hooks/toolPermission/` | 工具权限控制 |
| MCP | `services/mcp/` | Model Context Protocol |
| Task | `tasks/LocalAgentTask/` | 后台任务管理 |
| Streaming | `services/api/claude.ts` | API流式响应处理 |

## 核心文件速览

| 文件 | 行数 | 职责 |
|------|------|------|
| `main.tsx` | 4684 | CLI入口，参数解析，启动流程 |
| `query.ts` | 1730 | Agentic loop 核心实现 |
| `QueryEngine.ts` | 1297 | LLM API 调用封装 |
| `Tool.ts` | 794 | 工具类型系统定义 |
| `commands.ts` | 754 | 命令注册中心 |
| `setup.ts` | 478 | 环境初始化 |
| `context.ts` | 190 | 上下文收集 |
| `tasks.ts` | 40 | 任务系统入口 |
| `Task.ts` | 127 | 任务类型定义 |

## 学习建议

1. **先跑通主流程**：从 `cli.tsx` → `main.tsx` → `query.ts` 走一遍，理解程序怎么启动、怎么进入对话循环
2. **再理解工具系统**：`Tool.ts` + `tools/` 是整个 Agent 的核心，理解工具怎么注册、怎么调用
3. **然后看Agent系统**：`tools/AgentTool/` + `tasks/` 理解子Agent怎么创建、怎么通信
4. **最后看高级特性**：Coordinator、Fork、Session、Memory 等

## 每章阅读方法

1. **先看类型定义**：理解数据结构
2. **再看主函数**：理解执行流程
3. **追踪调用链**：理解模块间关系
4. **看边界处理**：理解错误处理、边界情况
5. **看设计模式**：理解工程思想
