---
title: 第01章：整体架构
---

# 第01章：整体架构

---

## 1. 本章目标

本章是整个 Claude Code 源码剖析系列的开篇，目标是帮助你建立对 Claude Code 系统的**全局认知**。读完本章，你应该能够：

1. **理解 Claude Code 的四层架构**——从用户终端到 LLM API 的完整数据流
2. **定位核心源码文件**——知道每个关键文件的职责、行数、以及它们之间的依赖关系
3. **追踪完整调用链**——从用户敲下回车键，到 LLM 返回响应，再到工具执行，最后回到用户屏幕
4. **理解关键设计模式**——Generator 模式、消息驱动架构、工具隔离、缓存友好设计
5. **掌握工程实践**——性能优化策略、错误处理机制、并发设计、配置管理

这不是一个简单的"文件列表"，而是一份**架构地图**——它将指引你在后续章节中深入每个子系统时不至于迷路。

---

## 2. 前置知识

在深入 Claude Code 架构之前，你需要具备以下基础知识：

### 2.1 TypeScript / JavaScript 基础
- **AsyncGenerator（异步生成器）**：Claude Code 的核心数据流大量使用 `async function*` 和 `yield*`，这是理解 `query.ts` 的关键
- **模块系统**：ESM（`import/export`）和 CommonJS（`require()`）混用，特别是 `feature()` 门控的条件加载
- **类型系统**：泛型、条件类型、`satisfies` 操作符

### 2.2 LLM 应用开发基础
- **Anthropic Messages API**：理解 `system`、`user`、`assistant` 消息角色，`tool_use` / `tool_result` 的交互模式
- **Agentic Loop（代理循环）**：LLM 调用 → 解析响应 → 执行工具 → 将结果反馈给 LLM → 继续循环
- **Prompt Caching**：Anthropic 的 `cache_control` 机制，理解为什么消息顺序和前缀一致性至关重要

### 2.3 React / Ink 框架
- **React 基础**：组件、Hooks、JSX
- **Ink**：一个将 React 渲染到终端的框架，Claude Code 的交互式 REPL 基于 Ink 构建

### 2.4 运行时知识
- **Bun**：Claude Code 使用 Bun 作为打包器和运行时，`bun:bundle` 的 `feature()` 函数用于编译时死代码消除
- **Node.js 子进程**：`child_process` 的 `execFile`、`spawn` 用于执行 shell 命令

---

## 3. 宏观概览

### 3.1 四层架构图

Claude Code 的架构可以抽象为四层：

```
┌─────────────────────────────────────────────────────────────┐
│                    Layer 4: 用户界面层                        │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌─────────────┐ │
│  │  REPL    │  │  -p 模式  │  │  SDK     │  │  MCP Server │ │
│  │ (Ink TUI)│  │ (Headless)│  │ (API)    │  │  (Protocol) │ │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └──────┬──────┘ │
│       │              │              │               │        │
├───────┴──────────────┴──────────────┴───────────────┴────────┤
│                    Layer 3: 引擎层                            │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │              QueryEngine (会话管理)                       │ │
│  │  ┌───────────────────────────────────────────────────┐  │ │
│  │  │         query() — Agentic Loop                    │  │ │
│  │  │  ┌─────────┐  ┌──────────┐  ┌─────────────────┐  │  │ │
│  │  │  │ API调用  │→│ 响应解析  │→│  工具执行/编排    │  │  │ │
│  │  │  └─────────┘  └──────────┘  └─────────────────┘  │  │ │
│  │  └───────────────────────────────────────────────────┘  │ │
│  └─────────────────────────────────────────────────────────┘ │
│                                                              │
├──────────────────────────────────────────────────────────────┤
│                    Layer 2: 工具层                            │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────────────┐  │
│  │BashTool  │ │FileRead  │ │AgentTool │ │  MCP Tools     │  │
│  │FileWrite │ │FileEdit  │ │SkillTool │ │  (外部服务器)   │  │
│  │GlobTool  │ │GrepTool  │ │WebFetch  │ │  ...40+ 工具   │  │
│  └──────────┘ └──────────┘ └──────────┘ └────────────────┘  │
│                                                              │
├──────────────────────────────────────────────────────────────┤
│                    Layer 1: 基础设施层                        │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐   │
│  │ 状态管理  │ │ 权限系统  │ │ 配置管理  │ │ 遥测/分析    │   │
│  │(AppState)│ │(Perms)   │ │(Settings)│ │(Analytics)   │   │
│  └──────────┘ └──────────┘ └──────────┘ └──────────────┘   │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐   │
│  │ 会话存储  │ │ 文件缓存  │ │ 模型管理  │ │ 插件/MCP     │   │
│  │(Session) │ │(FileState)│ │(Model)   │ │(Plugins)     │   │
│  └──────────┘ └──────────┘ └──────────┘ └──────────────┘   │
└──────────────────────────────────────────────────────────────┘
```

### 3.2 技术栈总览

| 类别 | 技术 | 用途 |
|------|------|------|
| 语言 | TypeScript 5.x | 整个项目 |
| 运行时 | Bun | 打包、运行时、feature() 编译时门控 |
| UI 框架 | React + Ink | 终端 TUI（REPL 模式） |
| CLI 框架 | Commander.js | 命令行参数解析 |
| LLM SDK | @anthropic-ai/sdk | Anthropic API 调用 |
| 状态管理 | 自研 Store | 简单的发布-订阅模式 |
| MCP | @modelcontextprotocol/sdk | 工具协议扩展 |
| 遥测 | OpenTelemetry + 自研 Analytics | 性能追踪和使用分析 |
| 测试 | Bun test / Vitest | 单元和集成测试 |

### 3.3 代码规模

根据源码统计，核心文件的代码量如下：

| 文件 | 行数 | 职责 |
|------|------|------|
| `main.tsx` | **4684** | 主入口：CLI 定义、REPL 启动、配置初始化 |
| `query.ts` | **1730** | Agentic Loop：消息循环、工具执行、错误恢复 |
| `QueryEngine.ts` | **1297** | 查询引擎：会话管理、消息处理、SDK 接口 |
| `Tool.ts` | **794** | 工具类型定义：接口、权限、渲染 |
| `tools.ts` | **390** | 工具注册：40+ 内置工具的组装和过滤 |
| `context.ts` | **190** | 上下文：Git 状态、CLAUDE.md、用户/系统上下文 |
| `cli.tsx` | **303** | 入口引导：快速路径分发 |

整个 `src/` 目录包含 **40+ 个工具实现**、**10+ 个子系统**（MCP、权限、遥测、插件等），是一个中大型 TypeScript 项目。

### 3.4 核心目录结构

```
src/
├── entrypoints/          # 入口点
│   ├── cli.tsx           # 真正的入口（引导层）
│   ├── init.ts           # 初始化逻辑
│   └── sdk/              # SDK 入口
├── main.tsx              # 主入口（Commander CLI + REPL 启动）
├── QueryEngine.ts        # 查询引擎（会话管理）
├── query.ts              # Agentic Loop（核心循环）
├── Tool.ts               # 工具类型定义
├── tools.ts              # 工具注册表
├── context.ts            # 上下文生成
├── tools/                # 40+ 工具实现
│   ├── BashTool/
│   ├── FileReadTool/
│   ├── AgentTool/
│   │   └── forkSubagent.ts  # Fork 子代理
│   └── ...
├── coordinator/          # Coordinator 模式
│   └── coordinatorMode.ts
├── state/                # 状态管理
│   ├── store.ts          # Store 实现
│   ├── AppStateStore.ts  # AppState 类型定义
│   └── onChangeAppState.ts
├── services/             # 服务层
│   ├── api/              # API 调用
│   ├── mcp/              # MCP 协议
│   ├── compact/          # 上下文压缩
│   └── analytics/        # 遥测分析
├── utils/                # 工具函数
│   ├── permissions/      # 权限系统
│   ├── model/            # 模型管理
│   ├── hooks/            # 生命周期钩子
│   └── ...
└── constants/            # 常量定义
```

### 3.5 数据流全景

理解 Claude Code 的架构，最重要的是理解**数据如何在系统中流动**。让我们从一个高层视角来看完整的数据流：

```
用户输入
  │
  ▼
┌─────────────────────────────────────────────────────────────┐
│ processUserInput()                                          │
│  ├─ 解析斜杠命令 (/clear, /compact, ...)                     │
│  ├─ 处理附件 (图片、文件)                                      │
│  ├─ 创建 UserMessage                                         │
│  └─ 决定是否需要查询 LLM (shouldQuery)                        │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│ QueryEngine.submitMessage()                                  │
│  ├─ fetchSystemPromptParts() → 构建系统提示                   │
│  │   ├─ getSystemPrompt() → 默认系统提示（工具描述、行为准则）  │
│  │   ├─ getUserContext() → CLAUDE.md + 日期                   │
│  │   └─ getSystemContext() → Git 状态                         │
│  ├─ recordTranscript() → 持久化到磁盘                         │
│  └─ yield* query() → 进入 Agentic Loop                       │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│ query() — Agentic Loop (无限循环)                            │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ [每次迭代]                                              │ │
│  │  1. 上下文压缩 (snip → microcompact → collapse → auto)  │ │
│  │  2. callModel() → Anthropic API 流式调用                 │ │
│  │  3. 解析响应 → 提取 text + tool_use 块                    │ │
│  │  4. 如果有 tool_use → 执行工具 → 生成 tool_result        │ │
│  │  5. 如果无 tool_use → 检查 stop hooks → return           │ │
│  │  6. continue → 下一次迭代（携带新的 messages）            │ │
│  └────────────────────────────────────────────────────────┘ │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│ 工具执行层                                                   │
│  ├─ StreamingToolExecutor (流式并行执行)                      │
│  │   ├─ 权限检查: canUseTool()                               │
│  │   ├─ 输入验证: tool.validateInput()                       │
│  │   ├─ 执行: tool.call()                                    │
│  │   └─ 结果映射: tool.mapToolResultToToolResultBlockParam()  │
│  └─ 或 runTools() (传统串行执行)                              │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│ 输出层                                                       │
│  ├─ REPL 模式: Ink 渲染到终端                                 │
│  ├─ SDK 模式: yield SDKMessage 给调用方                       │
│  └─ -p 模式: 直接输出到 stdout                                │
└─────────────────────────────────────────────────────────────┘
```

### 3.6 核心模块依赖关系

Claude Code 的模块依赖关系形成了一个清晰的层次结构。理解这个依赖图对于避免循环依赖和理解代码流向至关重要：

```
cli.tsx
  └── main.tsx
        ├── init.ts (初始化)
        ├── QueryEngine.ts (会话管理)
        │     ├── query.ts (Agentic Loop)
        │     │     ├── Tool.ts (工具接口)
        │     │     ├── services/api/claude.ts (API 调用)
        │     │     ├── services/compact/ (压缩系统)
        │     │     └── services/tools/ (工具编排)
        │     ├── context.ts (上下文)
        │     └── utils/processUserInput/ (输入处理)
        ├── tools.ts (工具注册)
        │     └── tools/* (40+ 工具实现)
        ├── state/ (状态管理)
        └── services/ (服务层)
              ├── mcp/ (MCP 协议)
              ├── analytics/ (遥测)
              └── policyLimits/ (策略)
```

关键的依赖规则：
- **向下依赖**：上层模块可以依赖下层模块
- **避免循环**：使用延迟加载（`require()`）打破循环依赖
- **接口隔离**：`Tool.ts` 定义接口，`tools/` 提供实现

---

## 4. 源码入口定位

Claude Code 的启动过程是一个精心设计的**快速路径分发**系统。让我们从真正的入口开始，逐层追踪。

### 4.1 第一层：cli.tsx — 引导入口

`cli.tsx` 是 Claude Code 的**真正入口**（303行）。它的设计哲学是：**尽可能少地加载模块，快速分发到对应子系统**。

```typescript
// src/entrypoints/cli.tsx (第 1-7 行)
import { feature } from 'bun:bundle';

// Bugfix for corepack auto-pinning, which adds yarnpkg to peoples' package.jsons
// eslint-disable-next-line custom-rules/no-top-level-side-effects
process.env.COREPACK_ENABLE_AUTO_PIN = '0';
```

入口函数 `main()` 的第一个动作是检查 `--version` 快速路径——**零模块加载**：

```typescript
// src/entrypoints/cli.tsx (第 49-55 行)
async function main(): Promise<void> {
  const args = process.argv.slice(2);

  // Fast-path for --version/-v: zero module loading needed
  if (args.length === 1 && (args[0] === '--version' || args[0] === '-v' || args[0] === '-V')) {
    console.log(`${MACRO.VERSION} (Claude Code)`);
    return;
  }
```

关键设计：`MACRO.VERSION` 是编译时内联的常量，连 `package.json` 都不需要读取。

接下来是一系列**快速路径检查**，每个路径只在匹配时才动态 `import()` 对应模块：

```typescript
// src/entrypoints/cli.tsx (第 57-60 行)
  // For all other paths, load the startup profiler
  const { profileCheckpoint } = await import('../utils/startupProfiler.js');
  profileCheckpoint('cli_entry');
```

快速路径包括（按出现顺序）：
1. `--dump-system-prompt` — 输出系统提示词并退出
2. `--claude-in-chrome-mcp` — Chrome MCP 服务器
3. `--daemon-worker` — 守护进程 Worker
4. `remote-control` / `bridge` — 远程控制桥接
5. `daemon` — 守护进程主管
6. `ps` / `logs` / `attach` / `kill` / `--bg` — 后台会话管理
7. `new` / `list` / `reply` — 模板任务
8. `environment-runner` — BYOC 无头运行器
9. `self-hosted-runner` — 自托管运行器
10. `--worktree --tmux` — tmux 工作树

当没有任何快速路径匹配时，进入**正常 CLI 路径**：

```typescript
// src/entrypoints/cli.tsx (第 266-275 行)
  // No special flags detected, load and run the full CLI
  const { startCapturingEarlyInput } = await import('../utils/earlyInput.js');
  startCapturingEarlyInput();
  profileCheckpoint('cli_before_main_import');
  const { main: cliMain } = await import('../main.js');
  profileCheckpoint('cli_after_main_import');
  await cliMain();
  profileCheckpoint('cli_after_main_complete');
```

注意 `startCapturingEarlyInput()` 的调用——它在 `main.js` 加载期间就开始捕获用户输入，避免用户在模块加载时敲的字符丢失。

### 4.2 第二层：main.tsx — 主入口

`main.tsx` 是整个项目的**巨石文件**（4684行），承担了三大职责：

**职责一：模块导入和副作用初始化**

文件开头的导入本身就包含重要的副作用：

```typescript
// src/main.tsx (第 1-11 行)
import { profileCheckpoint, profileReport } from './utils/startupProfiler.js';
profileCheckpoint('main_tsx_entry');
import { startMdmRawRead } from './utils/settings/mdm/rawRead.js';
startMdmRawRead();
import { ensureKeychainPrefetchCompleted, startKeychainPrefetch } from './utils/secureStorage/keychainPrefetch.js';
startKeychainPrefetch();
```

三个关键的启动时副作用：
- `profileCheckpoint('main_tsx_entry')` — 记录入口时间戳
- `startMdmRawRead()` — **并行**启动 MDM（Mobile Device Management）配置读取
- `startKeychainPrefetch()` — **并行**预取 macOS 钥匙串中的 OAuth 和 API Key

这些副作用被故意放在**模块顶层**（`import` 语句之间），因为它们需要在其他重量级模块加载期间就开始执行——这是经典的**启动时间优化**技巧。

**职责二：main() 函数 — CLI 入口**

```typescript
// src/main.tsx (第 501-520 行)
export async function main() {
  profileCheckpoint('main_function_start');
  process.env.NoDefaultCurrentDirectoryInExePath = '1';
  initializeWarningHandler();
  process.on('exit', () => { resetCursor(); });
  process.on('SIGINT', () => {
    if (process.argv.includes('-p') || process.argv.includes('--print')) {
      return; // print 模式有自己的 SIGINT 处理
    }
    process.exit(0);
  });
```

`main()` 函数做了以下关键工作：

1. **安全设置**：`NoDefaultCurrentDirectoryInExePath` 防止 Windows PATH 劫持攻击
2. **信号处理**：注册 SIGINT 处理器（print 模式有独立处理）
3. **运行时检测**：`isBeingDebugged()` 检查调试器，在非 ant 构建中阻止调试
4. **数据迁移**：`runMigrations()` 执行版本间的数据迁移
5. **设置加载**：`eagerLoadSettings()` 尽早加载 `--settings` 和 `--setting-sources`

**职责三：run() 函数 — Commander CLI 定义**

`run()` 函数（约3000行）使用 Commander.js 定义了完整的 CLI 接口，最终分发到两条路径：

- **交互模式**：调用 `launchRepl()` 启动 Ink TUI
- **无头模式**（`-p`/`--print`）：调用 `runHeadless()` 直接处理输入

### 4.3 第三层：init.ts — 初始化

`init.ts` 是一个 `memoize` 包装的异步函数，确保整个初始化过程只执行一次：

```typescript
// src/entrypoints/init.ts (第 70-78 行)
export const init = memoize(async (): Promise<void> => {
  const initStartTime = Date.now();
  logForDiagnosticsNoPII('info', 'init_started');
  profileCheckpoint('init_function_start');
  enableConfigs();
  applySafeConfigEnvironmentVariables();
  applyExtraCACertsFromConfig();
  // ...
```

初始化过程包括：
1. `enableConfigs()` — 启用配置系统
2. `applySafeConfigEnvironmentVariables()` — 应用安全的环境变量
3. `applyExtraCACertsFromConfig()` — 加载额外的 CA 证书（必须在首次 TLS 连接前）
4. 设置 Shell、代理、MTLS 等基础设施
5. 初始化遥测系统

### 4.4 第四层：REPL 启动

交互模式下，`launchRepl()` 创建 Ink 应用，渲染 `REPL` 组件。REPL 组件内部创建 `QueryEngine` 实例，开始监听用户输入。

### 4.5 完整入口链总结

```
用户输入 `claude`
  │
  ▼
cli.tsx::main()
  │ 检查 --version → 立即返回
  │ 检查快速路径（daemon, bridge, bg, ...）
  │ 无匹配 → import main.tsx
  ▼
main.tsx::main()
  │ 安全设置 + 信号处理
  │ 运行时检查 + 数据迁移
  │ eagerLoadSettings()
  ▼
main.tsx::run()
  │ Commander.js 定义 CLI 命令
  │ 解析参数、初始化认证
  ▼
init()  ← memoized, 只执行一次
  │ enableConfigs + 环境变量
  │ Shell/代理/TLS 配置
  ▼
┌─────────────┬──────────────────┐
│ 交互模式     │ 无头模式          │
│ launchRepl() │ runHeadless()    │
│ REPL 组件    │ print.ts         │
│ QueryEngine  │ QueryEngine      │
└──────┬──────┴────────┬─────────┘
       │               │
       ▼               ▼
    QueryEngine.submitMessage()
       │
       ▼
    query() — Agentic Loop
```

---

## 5. 调用链分析

让我们追踪一个完整的调用链：**用户输入一条消息 → LLM 响应 → 工具执行 → 结果返回**。

### 5.1 用户输入处理

在 REPL 模式下，用户输入通过 Ink 的 `useInput` Hook 捕获，然后调用 `processUserInput()`：

```typescript
// src/QueryEngine.ts (第 260-280 行)
const {
  messages: messagesFromUserInput,
  shouldQuery,
  allowedTools,
  model: modelFromUserInput,
  resultText,
} = await processUserInput({
  input: prompt,
  mode: 'prompt',
  setToolJSX: () => {},
  context: {
    ...processUserInputContext,
    messages: this.mutableMessages,
  },
  messages: this.mutableMessages,
  uuid: options?.uuid,
  isMeta: options?.isMeta,
  querySource: 'sdk',
});
```

`processUserInput()` 处理：
- 斜杠命令（`/clear`、`/compact` 等）
- 附件解析（图片、文件）
- 消息规范化

### 5.2 QueryEngine.submitMessage() — 提交消息

`QueryEngine` 是会话的**核心管理器**。每次 `submitMessage()` 调用代表一个**轮次（turn）**：

```typescript
// src/QueryEngine.ts (第 210-215 行)
export class QueryEngine {
  private config: QueryEngineConfig
  private mutableMessages: Message[]
  private abortController: AbortController
  private permissionDenials: SDKPermissionDenial[]
  private totalUsage: NonNullableUsage
```

`submitMessage()` 是一个 **AsyncGenerator**，这使得调用方可以逐步消费消息流：

```typescript
// src/QueryEngine.ts (第 218-222 行)
  async *submitMessage(
    prompt: string | ContentBlockParam[],
    options?: { uuid?: string; isMeta?: boolean },
  ): AsyncGenerator<SDKMessage, void, unknown> {
```

在进入 `query()` 之前，`submitMessage()` 做了关键的准备工作：

```typescript
// src/QueryEngine.ts (第 340-370 行)
    // 构建系统提示
    const { defaultSystemPrompt, userContext: baseUserContext, systemContext } =
      await fetchSystemPromptParts({
        tools,
        mainLoopModel: initialMainLoopModel,
        additionalWorkingDirectories: Array.from(
          initialAppState.toolPermissionContext.additionalWorkingDirectories.keys(),
        ),
        mcpClients,
        customSystemPrompt: customPrompt,
      });
```

系统提示由三部分拼接：
1. **默认系统提示**（`defaultSystemPrompt`）— 包含工具描述、行为准则
2. **用户上下文**（`userContext`）— CLAUDE.md 内容、日期、Coordinator 信息
3. **系统上下文**（`systemContext`）— Git 状态

### 5.3 query() — Agentic Loop 入口

`query()` 是整个系统的核心——一个无限循环的 AsyncGenerator：

```typescript
// src/query.ts (第 200-220 行)
export async function* query(
  params: QueryParams,
): AsyncGenerator<
  | StreamEvent
  | RequestStartEvent
  | Message
  | TombstoneMessage
  | ToolUseSummaryMessage,
  Terminal
> {
  const consumedCommandUuids: string[] = [];
  const terminal = yield* queryLoop(params, consumedCommandUuids);
  for (const uuid of consumedCommandUuids) {
    notifyCommandLifecycle(uuid, 'completed');
  }
  return terminal;
}
```

### 5.4 queryLoop() — 循环体

`queryLoop()` 是真正的 Agentic Loop。每次迭代代表一轮 LLM 交互：

```typescript
// src/query.ts (第 226-260 行)
async function* queryLoop(
  params: QueryParams,
  consumedCommandUuids: string[],
): AsyncGenerator<...> {
  const { systemPrompt, userContext, systemContext, canUseTool, fallbackModel,
          querySource, maxTurns, skipCacheWrite } = params;
  const deps = params.deps ?? productionDeps();

  let state: State = {
    messages: params.messages,
    toolUseContext: params.toolUseContext,
    maxOutputTokensOverride: params.maxOutputTokensOverride,
    autoCompactTracking: undefined,
    stopHookActive: undefined,
    maxOutputTokensRecoveryCount: 0,
    hasAttemptedReactiveCompact: false,
    turnCount: 1,
    pendingToolUseSummary: undefined,
    transition: undefined,
  }
```

每次迭代的关键步骤：

**步骤1：上下文压缩**（多种策略级联）

```typescript
// src/query.ts (第 310-360 行)
    // 1. Snip 压缩（裁剪历史消息）
    if (feature('HISTORY_SNIP')) {
      const snipResult = snipModule!.snipCompactIfNeeded(messagesForQuery);
      messagesForQuery = snipResult.messages;
    }

    // 2. Microcompact（工具结果预算裁剪）
    const microcompactResult = await deps.microcompact(
      messagesForQuery, toolUseContext, querySource
    );
    messagesForQuery = microcompactResult.messages;

    // 3. Context Collapse（上下文折叠）
    if (feature('CONTEXT_COLLAPSE') && contextCollapse) {
      const collapseResult = await contextCollapse.applyCollapsesIfNeeded(
        messagesForQuery, toolUseContext, querySource
      );
      messagesForQuery = collapseResult.messages;
    }

    // 4. Auto-compact（自动压缩）
    const { compactionResult, consecutiveFailures } = await deps.autocompact(
      messagesForQuery, toolUseContext, { systemPrompt, userContext, ... },
      querySource, tracking, snipTokensFreed
    );
```

**步骤2：调用 LLM**

```typescript
// src/query.ts (第 670-710 行)
      for await (const message of deps.callModel({
        messages: prependUserContext(messagesForQuery, userContext),
        systemPrompt: fullSystemPrompt,
        thinkingConfig: toolUseContext.options.thinkingConfig,
        tools: toolUseContext.options.tools,
        signal: toolUseContext.abortController.signal,
        options: {
          model: currentModel,
          fallbackModel,
          querySource,
          // ... 更多选项
        },
      })) {
```

**步骤3：流式处理响应**

响应是流式到达的，每个 `message` 都被检查：

```typescript
// src/query.ts (第 820-870 行)
            if (message.type === 'assistant') {
              assistantMessages.push(message);
              const msgToolUseBlocks = message.message.content.filter(
                content => content.type === 'tool_use',
              ) as ToolUseBlock[];
              if (msgToolUseBlocks.length > 0) {
                toolUseBlocks.push(...msgToolUseBlocks);
                needsFollowUp = true;
              }

              // 流式工具执行：在 API 流式传输期间就开始执行工具
              if (streamingToolExecutor && !toolUseContext.abortController.signal.aborted) {
                for (const toolBlock of msgToolUseBlocks) {
                  streamingToolExecutor.addTool(toolBlock, message);
                }
              }
            }
```

**步骤4：工具执行**

如果 LLM 请求了工具调用，进入工具执行阶段：

```typescript
// src/query.ts (第 1410-1440 行)
    const toolUpdates = streamingToolExecutor
      ? streamingToolExecutor.getRemainingResults()
      : runTools(toolUseBlocks, assistantMessages, canUseTool, toolUseContext);

    for await (const update of toolUpdates) {
      if (update.message) {
        yield update.message;
        toolResults.push(
          ...normalizeMessagesForAPI(
            [update.message],
            toolUseContext.options.tools,
          ).filter(_ => _.type === 'user'),
        );
      }
    }
```

**步骤5：决定是否继续循环**

```typescript
// src/query.ts (第 1355-1360 行)
      // 如果没有工具调用，检查是否需要停止钩子
      if (!needsFollowUp) {
        // 处理 stop hooks
        const stopHookResult = yield* handleStopHooks(...);
        if (stopHookResult.preventContinuation) {
          return { reason: 'stop_hook_prevented' };
        }
        return { reason: 'completed' };
      }
```

如果有工具调用，循环继续——将工具结果作为新的用户消息追加，然后回到步骤1。

### 5.5 完整调用链图

```
用户输入 "帮我写一个 Hello World"
  │
  ▼
QueryEngine.submitMessage("帮我写一个 Hello World")
  │
  ├─ processUserInput() → 解析输入
  ├─ fetchSystemPromptParts() → 构建系统提示
  ├─ recordTranscript() → 持久化消息
  │
  ▼
query({ messages, systemPrompt, ... })
  │
  ▼
queryLoop() — while(true) {
  │
  ├─ [1] 上下文压缩（snip → microcompact → collapse → autocompact）
  │
  ├─ [2] callModel({ messages, systemPrompt, tools }) → 流式 API 调用
  │      │
  │      ├─ message_start → 重置 usage
  │      ├─ content_block_delta → 累积文本/工具调用
  │      ├─ message_delta → 更新 stop_reason
  │      └─ 流式工具执行（StreamingToolExecutor）
  │
  ├─ [3] 检查响应：
  │      ├─ 有 tool_use → needsFollowUp = true
  │      ├─ 无 tool_use → 检查 stop hooks → return
  │      └─ 错误 → 恢复策略（reactive compact, fallback model）
  │
  ├─ [4] 工具执行：
  │      ├─ runTools() 或 streamingToolExecutor
  │      ├─ 每个工具 → canUseTool() 权限检查 → tool.call()
  │      └─ 生成 tool_result 消息
  │
  └─ [5] continue → 回到 [1]，携带新的 messages
}
```

### 5.6 详细追踪：一次完整的工具调用

让我们以一个具体的例子来追踪完整的调用链。假设用户输入 `"读取 src/main.tsx 的前10行"`，LLM 决定调用 `FileRead` 工具：

**第1步：用户输入处理**

```typescript
// src/QueryEngine.ts (第 260-280 行)
const { messages: messagesFromUserInput, shouldQuery, ... } = await processUserInput({
  input: "读取 src/main.tsx 的前10行",
  mode: 'prompt',
  context: processUserInputContext,
  messages: this.mutableMessages,
  querySource: 'sdk',
});
// messagesFromUserInput 包含一个 UserMessage：
// { type: 'user', message: { content: '读取 src/main.tsx 的前10行' } }
```

**第2步：进入 query() 循环**

```typescript
// src/query.ts (第 300-310 行)
// 第一次迭代开始
yield { type: 'stream_request_start' };
// 获取经过压缩的消息
let messagesForQuery = [...getMessagesAfterCompactBoundary(messages)];
```

**第3步：调用 LLM**

```typescript
// src/query.ts (第 670-710 行)
for await (const message of deps.callModel({
  messages: prependUserContext(messagesForQuery, userContext),
  systemPrompt: fullSystemPrompt,
  tools: toolUseContext.options.tools,
  signal: toolUseContext.abortController.signal,
  options: { model: currentModel },
})) {
```

LLM 流式返回一个 `AssistantMessage`，包含一个 `tool_use` 块：

```json
{
  "type": "assistant",
  "message": {
    "content": [
      {
        "type": "text",
        "text": "我来读取这个文件的前10行。"
      },
      {
        "type": "tool_use",
        "id": "toolu_01ABC123",
        "name": "FileRead",
        "input": {
          "file_path": "src/main.tsx",
          "offset": 0,
          "limit": 10
        }
      }
    ]
  }
}
```

**第4步：检测工具调用**

```typescript
// src/query.ts (第 830-845 行)
if (message.type === 'assistant') {
  assistantMessages.push(message);
  const msgToolUseBlocks = message.message.content.filter(
    content => content.type === 'tool_use',
  ) as ToolUseBlock[];
  if (msgToolUseBlocks.length > 0) {
    toolUseBlocks.push(...msgToolUseBlocks);
    needsFollowUp = true;  // 标记需要继续循环
  }
}
```

**第5步：工具执行**

```typescript
// src/query.ts (第 1410-1440 行)
const toolUpdates = streamingToolExecutor
  ? streamingToolExecutor.getRemainingResults()
  : runTools(toolUseBlocks, assistantMessages, canUseTool, toolUseContext);

for await (const update of toolUpdates) {
  if (update.message) {
    yield update.message;  // 产出工具结果消息
    toolResults.push(...);
  }
}
```

工具执行过程中，`FileRead` 工具的 `call()` 方法被调用：

```typescript
// tools/FileReadTool/FileReadTool.ts (简化)
async call(args, context, canUseTool, parentMessage, onProgress) {
  const { file_path, offset, limit } = args;
  const content = await readFile(file_path, { offset, limit });
  return {
    data: content,
    newMessages: [],
  };
}
```

**第6步：生成 tool_result**

工具执行完成后，系统生成一个 `UserMessage`，包含 `tool_result`：

```json
{
  "type": "user",
  "message": {
    "content": [
      {
        "type": "tool_result",
        "tool_use_id": "toolu_01ABC123",
        "content": "// These side-effects must run before all other imports:\nimport { profileCheckpoint..."
      }
    ]
  }
}
```

**第7步：继续循环**

```typescript
// toolUseBlocks.length > 0, needsFollowUp = true
// 循环继续，携带新的 messages（包含 tool_result）
// 回到步骤 1，再次调用 LLM
```

**第8步：LLM 最终响应**

LLM 看到工具结果后，返回最终的文本响应（不再包含 tool_use）：

```json
{
  "type": "assistant",
  "message": {
    "content": [
      {
        "type": "text",
        "text": "这是 src/main.tsx 的前10行：\n\n```typescript\nimport { profileCheckpoint..."
      }
    ],
    "stop_reason": "end_turn"
  }
}
```

**第9步：循环结束**

```typescript
// src/query.ts (第 1355-1370 行)
if (!needsFollowUp) {
  // 没有更多工具调用，检查 stop hooks
  const stopHookResult = yield* handleStopHooks(...);
  if (stopHookResult.preventContinuation) {
    return { reason: 'stop_hook_prevented' };
  }
  return { reason: 'completed' };
}
```

整个过程涉及 **2 次 LLM 调用**、**1 次工具执行**、**多条消息流转**。这就是 Agentic Loop 的本质——LLM 和工具之间的多轮交互。

---

## 6. 核心源码解析

### 6.1 QueryEngine — 会话管理器

`QueryEngine` 是连接 UI 层和 `query()` 循环的桥梁。它管理：

**状态定义：**

```typescript
// src/QueryEngine.ts (第 207-218 行)
export class QueryEngine {
  private config: QueryEngineConfig
  private mutableMessages: Message[]        // 消息历史
  private abortController: AbortController  // 中止控制器
  private permissionDenials: SDKPermissionDenial[]  // 权限拒绝记录
  private totalUsage: NonNullableUsage      // 累计 token 使用量
  private hasHandledOrphanedPermission = false
  private readFileState: FileStateCache     // 文件读取缓存
  private discoveredSkillNames = new Set<string>()   // 技能发现追踪
  private loadedNestedMemoryPaths = new Set<string>() // 嵌套内存路径
}
```

`QueryEngine` 的设计哲学是**一个会话一个引擎实例**。每次 `submitMessage()` 调用代表一个**轮次（turn）**，但状态（消息历史、token 使用量、权限拒绝记录等）在轮次之间持久化。

这与无状态的 `query()` 函数形成对比——`query()` 是纯粹的循环逻辑，不持有任何会话状态。`QueryEngine` 则是会话的**外壳**，负责管理状态的生命周期。

**配置类型：**

```typescript
// src/QueryEngine.ts (第 150-190 行)
export type QueryEngineConfig = {
  cwd: string                    // 工作目录
  tools: Tools                   // 可用工具集合
  commands: Command[]            // 斜杠命令
  mcpClients: MCPServerConnection[]  // MCP 服务器连接
  agents: AgentDefinition[]      // Agent 定义
  canUseTool: CanUseToolFn       // 权限检查函数
  getAppState: () => AppState    // 状态读取
  setAppState: (f: (prev: AppState) => AppState) => void  // 状态更新
  initialMessages?: Message[]    // 初始消息（恢复会话时）
  readFileCache: FileStateCache  // 文件缓存
  customSystemPrompt?: string    // 自定义系统提示
  appendSystemPrompt?: string    // 追加系统提示
  userSpecifiedModel?: string    // 用户指定模型
  fallbackModel?: string         // 备用模型
  thinkingConfig?: ThinkingConfig // 思考配置
  maxTurns?: number              // 最大轮次
  maxBudgetUsd?: number          // 最大预算
  taskBudget?: { total: number } // 任务预算
  jsonSchema?: Record<string, unknown>  // 结构化输出 schema
  verbose?: boolean              // 详细模式
  replayUserMessages?: boolean   // 重放用户消息
  includePartialMessages?: boolean // 包含部分消息
  snipReplay?: (...) => ...      // Snip 边界处理器
}
```

`QueryEngineConfig` 是一个**依赖注入容器**——它将所有外部依赖打包传入，使得 `QueryEngine` 本身不直接依赖全局状态，便于测试和复用。

`QueryEngine` 的核心方法 `submitMessage()` 是一个 **AsyncGenerator**，这使得调用方可以逐步消费消息流：

```typescript
// src/QueryEngine.ts (第 218-222 行)
  async *submitMessage(
    prompt: string | ContentBlockParam[],
    options?: { uuid?: string; isMeta?: boolean },
  ): AsyncGenerator<SDKMessage, void, unknown> {
```

在进入 `query()` 之前，`submitMessage()` 做了关键的准备工作：

```typescript
// src/QueryEngine.ts (第 340-370 行)
    // 构建系统提示
    const { defaultSystemPrompt, userContext: baseUserContext, systemContext } =
      await fetchSystemPromptParts({
        tools,
        mainLoopModel: initialMainLoopModel,
        additionalWorkingDirectories: Array.from(
          initialAppState.toolPermissionContext.additionalWorkingDirectories.keys(),
        ),
        mcpClients,
        customSystemPrompt: customPrompt,
      });
```

系统提示由三部分拼接：
1. **默认系统提示**（`defaultSystemPrompt`）— 包含工具描述、行为准则
2. **用户上下文**（`userContext`）— CLAUDE.md 内容、日期、Coordinator 信息
3. **系统上下文**（`systemContext`）— Git 状态

这三部分的拼接顺序是精心设计的——默认提示在最前面，确保 prompt cache 的前缀一致性。用户上下文和系统上下文在后面，因为它们可能因项目而异。

### 6.2 query() — Agentic Loop 的状态机

`query()` 的循环体使用一个显式的 `State` 类型来管理跨迭代的可变状态：

```typescript
// src/query.ts (第 38-51 行)
type State = {
  messages: Message[]
  toolUseContext: ToolUseContext
  autoCompactTracking: AutoCompactTrackingState | undefined
  maxOutputTokensRecoveryCount: number
  hasAttemptedReactiveCompact: boolean
  maxOutputTokensOverride: number | undefined
  pendingToolUseSummary: Promise<ToolUseSummaryMessage | null> | undefined
  stopHookActive: boolean | undefined
  turnCount: number
  transition: Continue | undefined
}
```

每个 `continue` 站点都通过创建新的 `State` 对象来更新状态，而不是直接修改：

```typescript
// src/query.ts (第 1120-1135 行)
          const next: State = {
            messages: drained.messages,
            toolUseContext,
            autoCompactTracking: tracking,
            maxOutputTokensRecoveryCount,
            hasAttemptedReactiveCompact,
            maxOutputTokensOverride: undefined,
            pendingToolUseSummary: undefined,
            stopHookActive: undefined,
            turnCount,
            transition: { reason: 'collapse_drain_retry', committed: drained.committed },
          }
          state = next
          continue
```

这种模式的好处：
- **可追踪性**：每个 `transition` 都记录了继续的原因
- **可测试性**：状态转换是纯函数
- **可调试性**：可以检查 `state.transition` 了解循环为何继续

### 6.3 上下文压缩系统

上下文压缩是 Claude Code 处理长对话的关键机制。它有**四级压缩策略**：

**第一级：Snip（裁剪）** — 基于规则的消息裁剪

```typescript
// src/query.ts (第 310-320 行)
    if (feature('HISTORY_SNIP')) {
      queryCheckpoint('query_snip_start');
      const snipResult = snipModule!.snipCompactIfNeeded(messagesForQuery);
      messagesForQuery = snipResult.messages;
      snipTokensFreed = snipResult.tokensFreed;
      if (snipResult.boundaryMessage) {
        yield snipResult.boundaryMessage;
      }
    }
```

**第二级：Microcompact（微压缩）** — 工具结果预算管理

```typescript
// src/query.ts (第 325-335 行)
    queryCheckpoint('query_microcompact_start');
    const microcompactResult = await deps.microcompact(
      messagesForQuery,
      toolUseContext,
      querySource,
    );
    messagesForQuery = microcompactResult.messages;
```

**第三级：Context Collapse（上下文折叠）** — 智能折叠旧上下文

```typescript
// src/query.ts (第 345-355 行)
    if (feature('CONTEXT_COLLAPSE') && contextCollapse) {
      const collapseResult = await contextCollapse.applyCollapsesIfNeeded(
        messagesForQuery,
        toolUseContext,
        querySource,
      );
      messagesForQuery = collapseResult.messages;
    }
```

**第四级：Auto-compact（自动压缩）** — LLM 驱动的摘要压缩

```typescript
// src/query.ts (第 360-375 行)
    const { compactionResult, consecutiveFailures } = await deps.autocompact(
      messagesForQuery,
      toolUseContext,
      {
        systemPrompt,
        userContext,
        systemContext,
        toolUseContext,
        forkContextMessages: messagesForQuery,
      },
      querySource,
      tracking,
      snipTokensFreed,
    );
```

### 6.4 Tool 系统 — 工具接口设计

`Tool` 接口是 Claude Code 最核心的抽象之一，定义在 `Tool.ts`（794行）中。它不仅定义了工具的执行逻辑，还定义了工具的**权限模型**、**UI 渲染**、**安全分类**等多个维度：

```typescript
// src/Tool.ts (第 310-340 行)
export type Tool<
  Input extends AnyObject = AnyObject,
  Output = unknown,
  P extends ToolProgressData = ToolProgressData,
> = {
  aliases?: string[]                    // 别名（重命名兼容）
  searchHint?: string                   // 工具搜索提示
  call(                                 // 核心：执行工具
    args: z.infer<Input>,
    context: ToolUseContext,
    canUseTool: CanUseToolFn,
    parentMessage: AssistantMessage,
    onProgress?: ToolCallProgress<P>,
  ): Promise<ToolResult<Output>>
  description(                          // 动态描述生成
    input: z.infer<Input>,
    options: { isNonInteractiveSession: boolean; ... },
  ): Promise<string>
  readonly inputSchema: Input           // Zod 输入 schema
  readonly inputJSONSchema?: ToolInputJSONSchema  // JSON Schema（MCP 工具）
  isConcurrencySafe(input): boolean     // 是否可并发执行
  isEnabled(): boolean                  // 是否启用
  isReadOnly(input): boolean            // 是否只读
  isDestructive?(input): boolean        // 是否破坏性操作
  interruptBehavior?(): 'cancel' | 'block'  // 中断行为
  // ... 更多方法
}
```

`Tool` 接口的设计体现了**关注点分离**：
- `call()` — 执行逻辑（"做什么"）
- `description()` — 提示词生成（"告诉 LLM 什么"）
- `checkPermissions()` — 权限检查（"是否允许"）
- `validateInput()` — 输入验证（"输入是否合法"）
- `renderToolUseMessage()` — UI 渲染（"怎么显示"）
- `isReadOnly()` / `isDestructive()` — 安全分类（"风险等级"）

这种分离使得每个维度都可以独立演化。例如，可以修改 `renderToolUseMessage()` 而不影响工具的执行逻辑，或者修改 `checkPermissions()` 而不影响 UI 渲染。

工具的 `description()` 方法特别值得注意——它是**动态的**，根据输入参数生成不同的描述：

```typescript
// 这意味着同一个工具在不同调用中可能有不同的描述
// 例如 BashTool 在执行 "ls -la" 和 "rm -rf /" 时的描述完全不同
async description(input, options) {
  return `Execute command: ${input.command}`;
}
```

这个动态描述注入到系统提示中，帮助 LLM 理解工具的当前状态。

### 6.5 buildTool() — 工具构建器

所有工具通过 `buildTool()` 函数构建，它提供了安全的默认值：

```typescript
// src/Tool.ts (第 760-790 行)
const TOOL_DEFAULTS = {
  isEnabled: () => true,
  isConcurrencySafe: (_input?: unknown) => false,
  isReadOnly: (_input?: unknown) => false,
  isDestructive: (_input?) => false,
  checkPermissions: (input, _ctx?) =>
    Promise.resolve({ behavior: 'allow', updatedInput: input }),
  toAutoClassifierInput: (_input?) => '',
  userFacingName: (_input?) => '',
}

export function buildTool<D extends AnyToolDef>(def: D): BuiltTool<D> {
  return {
    ...TOOL_DEFAULTS,
    userFacingName: () => def.name,
    ...def,
  } as BuiltTool<D>
}
```

默认值的设计是**fail-closed**（失败关闭）的：
- `isConcurrencySafe` 默认 `false` — 假设不安全，不并发执行
- `isReadOnly` 默认 `false` — 假设会写入，需要权限检查
- `checkPermissions` 默认允许 — 具体工具可以覆盖

### 6.6 tools.ts — 工具注册表

`tools.ts` 是工具的**注册中心**，`getAllBaseTools()` 返回所有内置工具：

```typescript
// src/tools.ts (第 130-180 行)
export function getAllBaseTools(): Tools {
  return [
    AgentTool,
    TaskOutputTool,
    BashTool,
    ...(hasEmbeddedSearchTools() ? [] : [GlobTool, GrepTool]),
    ExitPlanModeV2Tool,
    FileReadTool,
    FileEditTool,
    FileWriteTool,
    NotebookEditTool,
    WebFetchTool,
    TodoWriteTool,
    WebSearchTool,
    TaskStopTool,
    AskUserQuestionTool,
    SkillTool,
    EnterPlanModeTool,
    ...(process.env.USER_TYPE === 'ant' ? [ConfigTool] : []),
    ...(process.env.USER_TYPE === 'ant' ? [TungstenTool] : []),
    ...(WebBrowserTool ? [WebBrowserTool] : []),
    // ... 更多条件工具
    BriefTool,
    ListMcpResourcesTool,
    ReadMcpResourceTool,
    ...(isToolSearchEnabledOptimistic() ? [ToolSearchTool] : []),
  ]
}
```

工具注册的关键设计：
1. **条件注册**：通过 `feature()` 门控和环境变量控制工具可用性
2. **去重**：`assembleToolPool()` 使用 `uniqBy` 确保内置工具优先
3. **缓存友好**：内置工具和 MCP 工具分别排序，保持前缀一致性

```typescript
// src/tools.ts (第 290-310 行)
export function assembleToolPool(
  permissionContext: ToolPermissionContext,
  mcpTools: Tools,
): Tools {
  const builtInTools = getTools(permissionContext);
  const allowedMcpTools = filterToolsByDenyRules(mcpTools, permissionContext);

  // 分区排序：内置工具作为前缀，MCP 工具紧随其后
  // 这保证了 prompt cache 的前缀一致性
  const byName = (a: Tool, b: Tool) => a.name.localeCompare(b.name);
  return uniqBy(
    [...builtInTools].sort(byName).concat(allowedMcpTools.sort(byName)),
    'name',
  );
}
```

### 6.7 context.ts — 上下文生成

`context.ts` 负责生成注入到系统提示中的上下文信息：

```typescript
// src/context.ts (第 75-110 行)
export const getSystemContext = memoize(
  async (): Promise<{ [k: string]: string }> => {
    const gitStatus = isEnvTruthy(process.env.CLAUDE_CODE_REMOTE) ||
      !shouldIncludeGitInstructions() ? null : await getGitStatus();
    return {
      ...(gitStatus && { gitStatus }),
    };
  },
);
```

`getGitStatus()` 并行执行 5 个 Git 命令：

```typescript
// src/context.ts (第 45-60 行)
const [branch, mainBranch, status, log, userName] = await Promise.all([
  getBranch(),
  getDefaultBranch(),
  execFileNoThrow(gitExe(), ['--no-optional-locks', 'status', '--short'], ...)
    .then(({ stdout }) => stdout.trim()),
  execFileNoThrow(gitExe(), ['--no-optional-locks', 'log', '--oneline', '-n', '5'], ...)
    .then(({ stdout }) => stdout.trim()),
  execFileNoThrow(gitExe(), ['config', 'user.name'], ...)
    .then(({ stdout }) => stdout.trim()),
]);
```

注意 `--no-optional-locks` 标志——它防止 Git 获取不必要的文件锁，减少与其他 Git 进程的冲突。

### 6.8 Agent 系统 — Fork Subagent

Agent 系统是 Claude Code 实现**子任务委托**的关键。`forkSubagent.ts` 实现了一种特殊的 Agent 类型——**Fork**：

```typescript
// src/tools/AgentTool/forkSubagent.ts (第 25-35 行)
export function isForkSubagentEnabled(): boolean {
  if (feature('FORK_SUBAGENT')) {
    if (isCoordinatorMode()) return false;      // 与 Coordinator 互斥
    if (getIsNonInteractiveSession()) return false; // 仅交互模式
    return true;
  }
  return false;
}
```

Fork 的核心思想是**继承父 Agent 的完整对话上下文**：

```typescript
// src/tools/AgentTool/forkSubagent.ts (第 55-70 行)
export const FORK_AGENT = {
  agentType: FORK_SUBAGENT_TYPE,
  tools: ['*'],              // 继承父 Agent 的完整工具池
  maxTurns: 200,
  model: 'inherit',          // 继承父 Agent 的模型
  permissionMode: 'bubble',  // 权限提示冒泡到父终端
  source: 'built-in',
  getSystemPrompt: () => '', // 未使用，使用 override.systemPrompt
} satisfies BuiltInAgentDefinition
```

Fork 的关键优化是 **prompt cache 共享**：

```typescript
// src/tools/AgentTool/forkSubagent.ts (第 80-95 行)
/**
 * Build the forked conversation messages for the child agent.
 *
 * For prompt cache sharing, all fork children must produce byte-identical
 * API request prefixes. This function:
 * 1. Keeps the full parent assistant message (all tool_use blocks, thinking, text)
 * 2. Replaces all tool_result blocks with a constant placeholder
 * 3. Appends the fork directive as the final user message
 */
```

### 6.9 ToolUseContext — 工具执行上下文

`ToolUseContext` 是工具执行时的**完整上下文**，它包含了工具执行所需的一切信息：

```typescript
// src/Tool.ts (第 200-260 行)
export type ToolUseContext = {
  options: {
    commands: Command[]           // 可用的斜杠命令
    debug: boolean                // 调试模式
    mainLoopModel: string         // 当前使用的模型
    tools: Tools                  // 可用工具集合
    verbose: boolean              // 详细模式
    thinkingConfig: ThinkingConfig // 思考配置
    mcpClients: MCPServerConnection[]  // MCP 服务器连接
    mcpResources: Record<string, ServerResource[]>  // MCP 资源
    isNonInteractiveSession: boolean  // 是否非交互模式
    agentDefinitions: AgentDefinitionsResult  // Agent 定义
    maxBudgetUsd?: number         // 最大预算
    customSystemPrompt?: string   // 自定义系统提示
    appendSystemPrompt?: string   // 追加系统提示
    refreshTools?: () => Tools    // 刷新工具池的回调
  }
  abortController: AbortController  // 中止控制器
  readFileState: FileStateCache     // 文件读取缓存
  getAppState(): AppState           // 读取应用状态
  setAppState(f: (prev: AppState) => AppState): void  // 更新应用状态
  handleElicitation?: (...) => ...  // MCP 引导处理
  messages: Message[]               // 当前消息列表
  agentId?: AgentId                 // Agent ID（子代理场景）
  agentType?: string                // Agent 类型
  queryTracking?: QueryChainTracking // 查询链追踪
  contentReplacementState?: ContentReplacementState  // 内容替换状态
  renderedSystemPrompt?: SystemPrompt  // 预渲染的系统提示（用于 Fork 缓存共享）
  // ... 更多字段
}
```

`ToolUseContext` 的设计体现了**依赖注入**的核心思想——所有工具需要的外部依赖都通过这个上下文对象传入，而不是让工具直接访问全局状态。这带来了几个关键好处：

1. **可测试性**：可以构造模拟的 `ToolUseContext` 来测试工具
2. **隔离性**：每个子代理有自己的 `ToolUseContext`，互不干扰
3. **灵活性**：不同场景（REPL、SDK、子代理）可以提供不同的上下文配置

### 6.10 权限系统集成

权限系统是 Claude Code 安全模型的核心。它与工具系统紧密集成：

```typescript
// src/Tool.ts (第 160-180 行)
export type ToolPermissionContext = DeepImmutable<{
  mode: PermissionMode              // 权限模式：default, auto, plan, bypass_permissions
  additionalWorkingDirectories: Map<string, AdditionalWorkingDirectory>
  alwaysAllowRules: ToolPermissionRulesBySource    // 总是允许的规则
  alwaysDenyRules: ToolPermissionRulesBySource     // 总是拒绝的规则
  alwaysAskRules: ToolPermissionRulesBySource      // 总是询问的规则
  isBypassPermissionsModeAvailable: boolean        // 是否可用绕过权限模式
  isAutoModeAvailable?: boolean                     // 是否可用自动模式
  strippedDangerousRules?: ToolPermissionRulesBySource  // 被剥离的危险规则
  shouldAvoidPermissionPrompts?: boolean            // 是否避免权限提示（后台 Agent）
  awaitAutomatedChecksBeforeDialog?: boolean        // 是否在对话框前等待自动检查
  prePlanMode?: PermissionMode                      // 进入计划模式前的权限模式
}>
```

权限检查的完整流程：

```
工具调用请求
  │
  ▼
1. 工具级检查: tool.checkPermissions(input, context)
  │  → 工具特定的权限逻辑（如 BashTool 检查命令黑名单）
  │
  ▼
2. 全局权限检查: canUseTool(tool, input, context)
  │  → 检查 alwaysAllowRules / alwaysDenyRules
  │  → 检查权限模式（default, auto, plan, bypass）
  │
  ▼
3. 用户确认（如果需要）
  │  → REPL 模式：显示权限对话框
  │  → SDK 模式：通过 canUseTool 回调
  │  → 后台 Agent：自动拒绝（shouldAvoidPermissionPrompts）
  │
  ▼
4. 执行或拒绝
```

### 6.11 Coordinator 模式

Coordinator 模式是一种特殊的运行模式，Claude 作为**协调者**，将具体任务委托给 Worker Agent：

```typescript
// src/coordinator/coordinatorMode.ts (第 40-45 行)
export function isCoordinatorMode(): boolean {
  if (feature('COORDINATOR_MODE')) {
    return isEnvTruthy(process.env.CLAUDE_CODE_COORDINATOR_MODE);
  }
  return false;
}
```

Coordinator 的用户上下文注入了 Worker 的工具列表：

```typescript
// src/coordinator/coordinatorMode.ts (第 95-115 行)
export function getCoordinatorUserContext(
  mcpClients: ReadonlyArray<{ name: string }>,
  scratchpadDir?: string,
): { [k: string]: string } {
  if (!isCoordinatorMode()) return {};

  const workerTools = isEnvTruthy(process.env.CLAUDE_CODE_SIMPLE)
    ? [BASH_TOOL_NAME, FILE_READ_TOOL_NAME, FILE_EDIT_TOOL_NAME].sort().join(', ')
    : Array.from(ASYNC_AGENT_ALLOWED_TOOLS)
        .filter(name => !INTERNAL_WORKER_TOOLS.has(name))
        .sort()
        .join(', ');

  let content = `Workers spawned via the ${AGENT_TOOL_NAME} tool have access to these tools: ${workerTools}`;
```

### 6.10 状态管理 — Store

Claude Code 使用一个极简的自研 Store：

```typescript
// src/state/store.ts (第 1-25 行)
type Listener = () => void
type OnChange<T> = (args: { newState: T; oldState: T }) => void

export type Store<T> = {
  getState: () => T
  setState: (updater: (prev: T) => T) => void
  subscribe: (listener: Listener) => () => void
}

export function createStore<T>(initialState: T, onChange?: OnChange<T>): Store<T> {
  let state = initialState;
  const listeners = new Set<Listener>();
  return {
    getState: () => state,
    setState: (updater: (prev: T) => T) => {
      const prev = state;
      const next = updater(prev);
      if (Object.is(next, prev)) return;  // 引用相等则跳过
      state = next;
      onChange?.({ newState: next, oldState: prev });
      for (const listener of listeners) listener();
    },
    subscribe: (listener: Listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}
```

这个 Store 只有 25 行代码，但包含了关键的设计决策：
- **不可变更新**：`setState` 接受 updater 函数，鼓励不可变更新
- **引用相等检查**：`Object.is(next, prev)` 避免不必要的通知
- **onChange 回调**：用于副作用（如日志、遥测）
- **自动清理**：`subscribe` 返回取消订阅函数

`AppState` 是一个**深层不可变**的类型：

```typescript
// src/state/AppStateStore.ts (第 70-100 行)
export type AppState = DeepImmutable<{
  settings: SettingsJson
  verbose: boolean
  mainLoopModel: ModelSetting
  mainLoopModelForSession: ModelSetting
  statusLineText: string | undefined
  expandedView: 'none' | 'tasks' | 'teammates'
  isBriefOnly: boolean
  mcp: { clients: ...; tools: ...; ... }
  toolPermissionContext: ToolPermissionContext
  fastMode: FastModeState
  // ... 50+ 字段
}>
```

---

## 7. 架构设计思想

### 7.1 Generator 模式 — 流式数据流

Claude Code 最核心的架构决策是**全面使用 AsyncGenerator**。`query()`、`QueryEngine.submitMessage()`、甚至工具执行都是 AsyncGenerator。

为什么选择 Generator？

```typescript
// query() 的签名揭示了设计意图
export async function* query(
  params: QueryParams,
): AsyncGenerator<
  | StreamEvent          // 流式事件（message_start, content_block_delta...）
  | RequestStartEvent    // 请求开始
  | Message              // 完整消息（assistant, user, system...）
  | TombstoneMessage     // 墓碑消息（删除标记）
  | ToolUseSummaryMessage, // 工具使用摘要
  Terminal               // 最终返回值
>
```

Generator 的优势：
1. **背压控制**：消费者按自己的速度处理，生产者不会溢出
2. **取消语义**：调用 `.return()` 即可优雅终止整个链
3. **组合性**：`yield*` 可以无缝委托给子 Generator
4. **流式友好**：天然适合 LLM 的流式响应

### 7.2 消息驱动架构

整个系统围绕**消息（Message）**构建。所有交互都通过消息传递：

```typescript
// src/types/message.ts 中的核心类型
type Message =
  | UserMessage        // 用户输入
  | AssistantMessage   // LLM 响应
  | SystemMessage      // 系统消息
  | AttachmentMessage  // 附件
  | ProgressMessage    // 进度
  | TombstoneMessage   // 删除标记
  | ToolUseSummaryMessage  // 工具摘要
```

消息是**不可变的**——一旦创建，不会被修改。新的状态通过创建新消息来表达。这使得：
- **可序列化**：消息可以直接写入磁盘（会话恢复）
- **可回放**：从消息列表重建任何历史状态
- **可比较**：通过 UUID 和时间戳进行去重

### 7.3 工具隔离

每个工具都是**独立的模块**，通过统一的 `Tool` 接口与系统交互：

```typescript
// 工具的生命周期
1. 注册：getAllBaseTools() → tools.ts
2. 过滤：filterToolsByDenyRules() → 权限上下文
3. 组装：assembleToolPool() → 内置 + MCP
4. 描述：tool.description() → 注入系统提示
5. 验证：tool.validateInput() → 输入校验
6. 权限：tool.checkPermissions() → 工具级权限
7. 执行：tool.call() → 实际操作
8. 渲染：tool.renderToolResultMessage() → UI 展示
```

工具隔离的好处：
- **独立开发**：每个工具目录是自包含的
- **独立测试**：可以单独测试每个工具
- **动态加载**：MCP 工具在运行时发现和注册
- **权限控制**：每个工具有独立的权限检查

### 7.4 缓存友好设计

Prompt caching 是 Anthropic API 的重要优化。Claude Code 在多个层面保证缓存友好：

**工具列表排序一致性**：

```typescript
// src/tools.ts (第 295-310 行)
  // 分区排序：内置工具作为前缀，MCP 工具紧随其后
  const byName = (a: Tool, b: Tool) => a.name.localeCompare(b.name);
  return uniqBy(
    [...builtInTools].sort(byName).concat(allowedMcpTools.sort(byName)),
    'name',
  );
```

**系统提示缓存**：`context.ts` 使用 `memoize` 确保同一会话内系统上下文只生成一次：

```typescript
export const getSystemContext = memoize(async () => { ... });
export const getUserContext = memoize(async () => { ... });
```

**Fork 子 Agent 的字节级缓存共享**：

```typescript
// forkSubagent.ts 中所有 fork 子 Agent 使用相同的占位符文本
const FORK_PLACEHOLDER_RESULT = 'Fork started — processing in background';
// 这确保所有 fork 的 API 请求前缀是字节级相同的
```

### 7.5 编译时死代码消除

`feature()` 函数来自 `bun:bundle`，在编译时决定代码是否包含：

```typescript
// 编译时门控
if (feature('COORDINATOR_MODE')) {
  // 这段代码在非 ant 构建中会被完全移除
  const coordinatorModeModule = require('./coordinator/coordinatorMode.js');
}

// 运行时门控
if (isEnvTruthy(process.env.CLAUDE_CODE_COORDINATOR_MODE)) {
  // 这段代码始终存在，但只在环境变量设置时执行
}
```

这种双层门控（编译时 + 运行时）使得：
- **外部构建**不包含实验性功能的代码
- **内部构建**（ant）可以通过环境变量启用实验功能
- **包体积最小化**——未使用的功能不会增加包大小

Claude Code 中有大量使用 `feature()` 门控的功能模块：

```typescript
// src/tools.ts 中的条件加载示例
const SleepTool = feature('PROACTIVE') || feature('KAIROS')
  ? require('./tools/SleepTool/SleepTool.js').SleepTool : null;

const cronTools = feature('AGENT_TRIGGERS')
  ? [require('./tools/ScheduleCronTool/CronCreateTool.js').CronCreateTool,
     require('./tools/ScheduleCronTool/CronDeleteTool.js').CronDeleteTool,
     require('./tools/ScheduleCronTool/CronListTool.js').CronListTool] : [];

const WebBrowserTool = feature('WEB_BROWSER_TOOL')
  ? require('./tools/WebBrowserTool/WebBrowserTool.js').WebBrowserTool : null;

const coordinatorModeModule = feature('COORDINATOR_MODE')
  ? require('./coordinator/coordinatorMode.js') : null;
```

### 7.6 依赖注入与可测试性

Claude Code 大量使用依赖注入来提高可测试性。`QueryEngine` 的配置、`query()` 的依赖、工具的上下文都是通过参数传入的：

```typescript
// src/query.ts (第 170-185 行)
export type QueryParams = {
  messages: Message[]
  systemPrompt: SystemPrompt
  userContext: { [k: string]: string }
  systemContext: { [k: string]: string }
  canUseTool: CanUseToolFn
  toolUseContext: ToolUseContext
  fallbackModel?: string
  querySource: QuerySource
  maxTurns?: number
  skipCacheWrite?: boolean
  taskBudget?: { total: number }
  deps?: QueryDeps  // 可替换的依赖集合
}
```

`QueryDeps` 是一个特别精妙的设计——它将 `query()` 的所有外部依赖打包，测试时可以注入模拟实现：

```typescript
// src/query/deps.ts
export type QueryDeps = {
  uuid: () => string                    // UUID 生成器（测试时可用确定性值）
  callModel: typeof callModelWithStreaming  // API 调用（测试时可模拟）
  autocompact: typeof autoCompact       // 自动压缩（测试时可跳过）
  microcompact: typeof microcompact     // 微压缩（测试时可跳过）
}
```

### 7.7 不可变数据流

整个系统围绕**不可变消息**构建。消息一旦创建就不会被修改：

```typescript
// src/state/AppStateStore.ts
export type AppState = DeepImmutable<{
  settings: SettingsJson
  verbose: boolean
  mainLoopModel: ModelSetting
  // ...
}>
```

`DeepImmutable` 类型递归地将所有属性标记为 `readonly`，确保编译时捕获任何意外的修改。

新的状态通过**函数式更新**创建：

```typescript
// 正确的状态更新方式
setAppState(prev => ({
  ...prev,
  toolPermissionContext: {
    ...prev.toolPermissionContext,
    alwaysAllowRules: {
      ...prev.toolPermissionContext.alwaysAllowRules,
      command: allowedTools,
    },
  },
}));
```

这种模式的好处：
1. **时间旅行调试**：可以保存任意时刻的状态快照
2. **并发安全**：不可变数据天然是线程安全的
3. **变化检测**：通过引用相等（`Object.is`）快速判断是否变化
4. **会话恢复**：消息可以直接序列化到磁盘

### 7.8 错误边界设计

Claude Code 在多个层次设置了错误边界：

```
┌─────────────────────────────────────────────────┐
│ 层次 1: API 调用错误                               │
│  ├─ 网络错误 → 重试 + 指数退避                      │
│  ├─ Rate limit → 等待 + 重试                       │
│  ├─ 模型过载 → FallbackTriggeredError → 切换模型    │
│  └─ Prompt too long → 压缩 → 重试                 │
├─────────────────────────────────────────────────┤
│ 层次 2: 工具执行错误                               │
│  ├─ 权限拒绝 → 通知用户 → 跳过工具                  │
│  ├─ 输入验证失败 → 返回错误给 LLM → LLM 自行修正    │
│  └─ 执行异常 → 捕获 → 返回错误信息给 LLM            │
├─────────────────────────────────────────────────┤
│ 层次 3: 用户中断                                   │
│  ├─ Ctrl+C (流式传输中) → 中止 API → 清理           │
│  ├─ Ctrl+C (工具执行中) → 中止工具 → 清理           │
│  └─ 新消息到达 → interruptBehavior (cancel/block)  │
├─────────────────────────────────────────────────┤
│ 层次 4: 系统级错误                                 │
│  ├─ 内存不足 → gracefulShutdown                    │
│  ├─ 进程信号 → SIGINT/SIGTERM 处理                 │
│  └─ 未捕获异常 → 日志 + 优雅退出                    │
└─────────────────────────────────────────────────┘
```

---

## 8. 工程实践细节

### 8.1 性能优化 — 启动时间

Claude Code 的启动时间优化是**极致的**：

**并行预取**：

```typescript
// src/main.tsx (第 1-11 行)
// 在 import 语句之间插入副作用，利用模块加载的时间窗口
profileCheckpoint('main_tsx_entry');
import { startMdmRawRead } from './utils/settings/mdm/rawRead.js';
startMdmRawRead();  // 并行启动 MDM 读取
import { startKeychainPrefetch } from './utils/secureStorage/keychainPrefetch.js';
startKeychainPrefetch();  // 并行启动钥匙串预取
```

**延迟加载**：

```typescript
// src/main.tsx (第 85-90 行)
// 延迟加载避免循环依赖
const getTeammateUtils = () =>
  require('./utils/teammate.js') as typeof import('./utils/teammate.js');
```

**启动分析器**：

```typescript
// src/entrypoints/cli.tsx (第 57-60 行)
const { profileCheckpoint } = await import('../utils/startupProfiler.js');
profileCheckpoint('cli_entry');
// ... 每个关键路径都有 checkpoint
profileCheckpoint('cli_before_main_import');
profileCheckpoint('cli_after_main_import');
profileCheckpoint('cli_after_main_complete');
```

**延迟预取**：

```typescript
// src/main.tsx (第 310-350 行)
export function startDeferredPrefetches(): void {
  // 在 REPL 首次渲染后才执行这些预取
  if (isEnvTruthy(process.env.CLAUDE_CODE_EXIT_AFTER_FIRST_RENDER) || isBareMode()) {
    return;  // 性能测试模式下跳过所有预取
  }
  void initUser();
  void getUserContext();
  prefetchSystemContextIfSafe();
  // ...
}
```

### 8.2 错误处理 — 多层恢复

`query()` 实现了**多层错误恢复**策略：

**第一层：模型降级（Fallback）**

```typescript
// src/query.ts (第 900-940 行)
      } catch (innerError) {
        if (innerError instanceof FallbackTriggeredError && fallbackModel) {
          currentModel = fallbackModel;
          attemptWithFallback = true;
          // 清理上一次尝试的助手消息
          yield* yieldMissingToolResultBlocks(assistantMessages, 'Model fallback triggered');
          assistantMessages.length = 0;
          // 更新工具上下文
          toolUseContext.options.mainLoopModel = fallbackModel;
          // 剥离 thinking 签名（模型绑定的）
          if (process.env.USER_TYPE === 'ant') {
            messagesForQuery = stripSignatureBlocks(messagesForQuery);
          }
          continue;
        }
        throw innerError;
      }
```

**第二层：Prompt-too-long 恢复**

```typescript
// src/query.ts (第 1100-1150 行)
      const isWithheld413 = lastMessage?.type === 'assistant' &&
        lastMessage.isApiErrorMessage && isPromptTooLongMessage(lastMessage);

      if (isWithheld413) {
        // 先尝试 context collapse drain
        if (feature('CONTEXT_COLLAPSE') && contextCollapse &&
            state.transition?.reason !== 'collapse_drain_retry') {
          const drained = contextCollapse.recoverFromOverflow(messagesForQuery, querySource);
          if (drained.committed > 0) {
            // 成功恢复，继续循环
            state = { ...state, messages: drained.messages, transition: { reason: 'collapse_drain_retry' } };
            continue;
          }
        }
        // 再尝试 reactive compact
        if (reactiveCompact) {
          const compacted = await reactiveCompact.tryReactiveCompact({ ... });
          if (compacted) {
            // 成功恢复
            continue;
          }
        }
      }
```

**第三层：Max output tokens 恢复**

```typescript
// src/query.ts (第 1200-1250 行)
      if (isWithheldMaxOutputTokens(lastMessage)) {
        // 首先尝试升级 token 限制
        if (capEnabled && maxOutputTokensOverride === undefined) {
          state = { ...state, maxOutputTokensOverride: ESCALATED_MAX_TOKENS, transition: { reason: 'max_output_tokens_escalate' } };
          continue;
        }
        // 然后尝试多轮恢复（最多 3 次）
        if (maxOutputTokensRecoveryCount < MAX_OUTPUT_TOKENS_RECOVERY_LIMIT) {
          const recoveryMessage = createUserMessage({
            content: 'Output token limit hit. Resume directly — no apology, no recap...',
            isMeta: true,
          });
          state = { ...state, maxOutputTokensRecoveryCount: maxOutputTokensRecoveryCount + 1, transition: { reason: 'max_output_tokens_recovery' } };
          continue;
        }
      }
```

### 8.3 并发设计

Claude Code 在多个层面使用并发：

**流式工具执行**：

```typescript
// src/query.ts (第 695-705 行)
    // StreamingToolExecutor 在 API 流式传输期间就开始执行工具
    const useStreamingToolExecution = config.gates.streamingToolExecution;
    let streamingToolExecutor = useStreamingToolExecution
      ? new StreamingToolExecutor(
          toolUseContext.options.tools,
          canUseTool,
          toolUseContext,
        )
      : null;
```

**并行 Git 命令**：

```typescript
// src/context.ts (第 45-60 行)
const [branch, mainBranch, status, log, userName] = await Promise.all([
  getBranch(),
  getDefaultBranch(),
  execFileNoThrow(gitExe(), ['--no-optional-locks', 'status', '--short'], ...),
  execFileNoThrow(gitExe(), ['--no-optional-locks', 'log', '--oneline', '-n', '5'], ...),
  execFileNoThrow(gitExe(), ['config', 'user.name'], ...),
]);
```

**启动时并行预取**：

```typescript
// src/main.tsx
startMdmRawRead();        // 并行 1
startKeychainPrefetch();   // 并行 2
// 两者在模块加载期间就开始执行
```

### 8.4 配置管理

配置系统分层管理：

1. **环境变量**：`CLAUDE_CODE_*` 前缀
2. **全局配置**：`~/.claude/config.json`
3. **项目配置**：`.claude/settings.json`
4. **CLI 参数**：`--settings`、`--setting-sources`
5. **MDM 配置**：企业设备管理
6. **Remote Managed Settings**：远程管理配置
7. **Policy Limits**：组织策略

```typescript
// src/main.tsx (第 510-520 行)
function eagerLoadSettings(): void {
  const settingsFile = eagerParseCliFlag('--settings');
  if (settingsFile) {
    loadSettingsFromFlag(settingsFile);
  }
  const settingSourcesArg = eagerParseCliFlag('--setting-sources');
  if (settingSourcesArg !== undefined) {
    loadSettingSourcesFromFlag(settingSourcesArg);
  }
}
```

### 8.5 日志与调试系统

Claude Code 有完善的日志系统，分为多个层次：

**诊断日志**（无 PII）：

```typescript
// src/context.ts (第 40-45 行)
logForDiagnosticsNoPII('info', 'git_status_started');
logForDiagnosticsNoPII('info', 'git_commands_completed', {
  duration_ms: Date.now() - gitCmdsStart,
  status_length: status.length,
});
```

**调试日志**：

```typescript
// src/query.ts
logForDebugging(`Token budget continuation #${decision.continuationCount}: ${decision.pct}%`);
```

**分析事件**：

```typescript
// src/query.ts (第 530-545 行)
logEvent('tengu_auto_compact_succeeded', {
  originalMessageCount: messages.length,
  compactedMessageCount: compactionResult.summaryMessages.length + ...,
  preCompactTokenCount,
  postCompactTokenCount,
  compactionInputTokens: compactionUsage?.input_tokens,
  compactionOutputTokens: compactionUsage?.output_tokens,
  queryChainId: queryChainIdForAnalytics,
  queryDepth: queryTracking.depth,
});
```

**错误日志**：

```typescript
// src/query.ts (第 950-960 行)
logError(error);
logAntError('Query error', error);  // 仅 ant 构建的详细日志
```

**启动分析器**：

```typescript
// src/entrypoints/cli.tsx
profileCheckpoint('cli_entry');
profileCheckpoint('cli_before_main_import');
profileCheckpoint('cli_after_main_import');
profileCheckpoint('cli_after_main_complete');
// 启动后输出完整的性能报告
profileReport();
```

### 8.6 会话持久化

Claude Code 的会话持久化机制确保用户可以恢复中断的对话：

```typescript
// src/QueryEngine.ts (第 380-400 行)
    // 在进入 query 循环前就写入用户消息
    if (persistSession && messagesFromUserInput.length > 0) {
      const transcriptPromise = recordTranscript(messages);
      if (isBareMode()) {
        void transcriptPromise;  // bare 模式：fire-and-forget
      } else {
        await transcriptPromise;  // 正常模式：等待写入完成
        if (isEnvTruthy(process.env.CLAUDE_CODE_EAGER_FLUSH) ||
            isEnvTruthy(process.env.CLAUDE_CODE_IS_COWORK)) {
          await flushSessionStorage();  // 强制刷新到磁盘
        }
      }
    }
```

会话存储的设计考虑了多种边界情况：
- **进程被杀**：在 API 响应到达前就写入用户消息，确保 `--resume` 能恢复
- **bare 模式**：fire-and-forget，不阻塞关键路径（~4ms SSD, ~30ms 磁盘竞争）
- **compact 边界**：在写入 compact boundary 前先刷新所有内存中的消息

### 8.7 工具结果预算管理

大型工具结果（如 `cat` 一个大文件）会消耗大量上下文窗口。Claude Code 通过 `toolResultStorage` 管理工具结果预算：

```typescript
// src/query.ts (第 290-310 行)
    // 对工具结果应用聚合预算
    messagesForQuery = await applyToolResultBudget(
      messagesForQuery,
      toolUseContext.contentReplacementState,
      persistReplacements
        ? records => void recordContentReplacement(records, toolUseContext.agentId).catch(logError)
        : undefined,
      new Set(
        toolUseContext.options.tools
          .filter(t => !Number.isFinite(t.maxResultSizeChars))
          .map(t => t.name),
      ),
    );
```

当工具结果超过 `maxResultSizeChars` 限制时：
1. 结果被保存到磁盘文件
2. LLM 收到的是**预览 + 文件路径**，而不是完整内容
3. LLM 可以通过 `FileRead` 工具按需读取

### 8.8 数据迁移

Claude Code 使用版本化的数据迁移：

```typescript
// src/main.tsx (第 280-300 行)
const CURRENT_MIGRATION_VERSION = 11;
function runMigrations(): void {
  if (getGlobalConfig().migrationVersion !== CURRENT_MIGRATION_VERSION) {
    migrateAutoUpdatesToSettings();
    migrateBypassPermissionsAcceptedToSettings();
    migrateEnableAllProjectMcpServersToSettings();
    resetProToOpusDefault();
    migrateSonnet1mToSonnet45();
    migrateLegacyOpusToCurrent();
    migrateSonnet45ToSonnet46();
    migrateOpusToOpus1m();
    // ... 更多迁移
    saveGlobalConfig(prev => prev.migrationVersion === CURRENT_MIGRATION_VERSION ? prev : {
      ...prev, migrationVersion: CURRENT_MIGRATION_VERSION
    });
  }
}
```

每个迁移函数是**幂等的**——可以安全地多次执行。

---

## 9. 初学者易错点

### 误区1：认为 `cli.tsx` 是"主入口"

很多初学者看到 `cli.tsx` 就认为它是主入口。实际上它只是一个**引导层**——真正的业务逻辑在 `main.tsx` 中。`cli.tsx` 的存在是为了：
- 最小化 `--version` 等快速路径的模块加载
- 分发到不同的子系统（daemon、bridge、bg 等）

**正确理解**：`cli.tsx` 是路由器，`main.tsx` 是主程序。

从代码中可以清楚看到这个分层：

```typescript
// src/entrypoints/cli.tsx (第 266-275 行)
  // No special flags detected, load and run the full CLI
  const { startCapturingEarlyInput } = await import('../utils/earlyInput.js');
  startCapturingEarlyInput();
  profileCheckpoint('cli_before_main_import');
  const { main: cliMain } = await import('../main.js');  // 延迟加载 main.js
  profileCheckpoint('cli_after_main_import');
  await cliMain();
```

注意 `await import('../main.js')` 是**动态导入**——只有当没有快速路径匹配时，才会加载 `main.js` 的 4684 行代码。这就是为什么 `--version` 可以在几毫秒内响应。

### 误区2：认为 `query()` 是一个普通函数

`query()` 是一个 **AsyncGenerator**（`async function*`），不是普通函数。这意味着：
- 它不会"返回"一个结果，而是"产出"一系列消息
- 调用方必须用 `for await...of` 消费
- 可以被中途取消（`.return()`）
- 可以被 `yield*` 委托

```typescript
// ❌ 错误理解
const result = await query(params);

// ✅ 正确理解
for await (const message of query(params)) {
  // 每个 message 是流式到达的
  handleMessage(message);
}
```

更重要的是，`query()` 内部使用 `yield*` 委托给 `queryLoop()`：

```typescript
// src/query.ts (第 200-215 行)
export async function* query(params: QueryParams): AsyncGenerator<...> {
  const consumedCommandUuids: string[] = [];
  const terminal = yield* queryLoop(params, consumedCommandUuids);
  // 只有当 queryLoop 正常返回时才会执行到这里
  for (const uuid of consumedCommandUuids) {
    notifyCommandLifecycle(uuid, 'completed');
  }
  return terminal;
}
```

`yield*` 是**零成本委托**——它将子 Generator 的所有产出直接传递给调用方，没有额外的包装开销。

### 误区3：认为工具是"注册后就固定"的

工具注册是**动态的**：
- MCP 工具在运行时发现和注册
- 工具可以通过 `isEnabled()` 动态禁用
- 工具可以通过 `shouldDefer` 延迟加载
- `refreshTools()` 可以在查询期间刷新工具池

```typescript
// src/Tool.ts (第 430-440 行)
  /**
   * When true, this tool is deferred (sent with defer_loading: true) and requires
   * ToolSearch to be used before it can be called.
   */
  readonly shouldDefer?: boolean
```

工具的生命周期远比"注册-使用"复杂：

```
工具生命周期：
  1. 定义：tools/MyTool/MyTool.ts 中定义工具
  2. 注册：getAllBaseTools() 返回工具数组
  3. 过滤：filterToolsByDenyRules() 移除被拒绝的工具
  4. 组装：assembleToolPool() 合并内置 + MCP 工具
  5. 延迟加载：shouldDefer=true 的工具不立即发送给 LLM
  6. 搜索发现：LLM 使用 ToolSearch 工具发现延迟加载的工具
  7. 描述注入：tool.description() 生成的文本注入系统提示
  8. 调用：LLM 返回 tool_use → canUseTool() 权限检查 → tool.call()
  9. 结果映射：tool.mapToolResultToToolResultBlockParam() 转换结果
  10. 渲染：tool.renderToolResultMessage() 在 UI 中显示
```

### 误区4：认为消息是可变的

`AppState` 使用 `DeepImmutable` 类型，消息一旦创建就不会被修改。新的状态通过创建新消息来表达。初学者常犯的错误是直接修改消息对象：

```typescript
// ❌ 错误：直接修改消息
message.content = 'new content';

// ✅ 正确：创建新消息
const newMessage = { ...message, content: 'new content' };
```

这个约束在 TypeScript 编译时就被强制执行：

```typescript
// src/state/AppStateStore.ts
export type AppState = DeepImmutable<{
  settings: SettingsJson
  verbose: boolean
  // ...
}>;

// 如果你尝试这样做，TypeScript 会报编译错误：
// appState.verbose = true;  // Error: Cannot assign to 'verbose' because it is a read-only property
```

### 误区5：认为 `memoize` 等于"只执行一次"

`context.ts` 中的 `getSystemContext` 和 `getUserContext` 使用 `memoize`，但它们的缓存可以通过 `cache.clear()` 手动清除：

```typescript
// src/context.ts (第 30-35 行)
export function setSystemPromptInjection(value: string | null): void {
  systemPromptInjection = value;
  getUserContext.cache.clear?.();
  getSystemContext.cache.clear?.();
}
```

`memoize` 是"按参数缓存"，不是"全局单例"。当注入改变时，缓存必须清除以确保下次调用生成新的上下文。

### 误区6：认为 `feature()` 是运行时检查

`feature()` 来自 `bun:bundle`，是**编译时常量**。在非 ant 构建中，`feature('COORDINATOR_MODE')` 始终返回 `false`，对应的代码块会被完全移除（死代码消除）。这不是运行时的 `if` 判断。

```typescript
// 这段代码在外部构建中完全不存在
if (feature('COORDINATOR_MODE')) {
  const module = require('./coordinator/coordinatorMode.js');
}
```

要验证这一点，你可以比较外部构建和内部构建的包大小——外部构建不包含任何 `feature('X')` 门控的代码。

### 误区7：认为 `ToolUseContext` 是全局的

`ToolUseContext` 是**每个查询实例**创建的，包含：
- 当前的工具集合
- 权限上下文
- 文件缓存
- 中止控制器
- Agent ID（子代理场景）

它不是全局单例，而是通过函数参数传递的依赖注入。每个子代理都有自己的 `ToolUseContext`：

```typescript
// 在 Fork 子代理中，创建独立的 ToolUseContext
const childContext = {
  ...parentContext,
  agentId: childAgentId,
  messages: forkedMessages,
  renderedSystemPrompt: parentContext.renderedSystemPrompt,
  // 独立的 abort controller
  abortController: createAbortController(),
};
```

### 误区8：认为所有工具调用都是串行的

虽然传统的 `runTools()` 是串行执行的，但 `StreamingToolExecutor` 允许在 API 流式传输期间就开始执行工具：

```typescript
// src/query.ts (第 695-705 行)
    const useStreamingToolExecution = config.gates.streamingToolExecution;
    let streamingToolExecutor = useStreamingToolExecution
      ? new StreamingToolExecutor(
          toolUseContext.options.tools,
          canUseTool,
          toolUseContext,
        )
      : null;
```

这意味着当 LLM 还在流式输出第二个工具调用时，第一个工具可能已经在执行了。这是重要的性能优化。

---

## 10. 本章总结

### 核心知识点回顾

1. **四层架构**：用户界面层 → 引擎层 → 工具层 → 基础设施层
2. **入口链**：`cli.tsx` → `main.tsx::main()` → `main.tsx::run()` → `init()` → REPL/Headless
3. **核心循环**：`QueryEngine.submitMessage()` → `query()` → `queryLoop()` — 无限 Agentic Loop
4. **消息流**：用户输入 → processUserInput → 系统提示构建 → LLM 调用 → 响应解析 → 工具执行 → 结果反馈
5. **压缩系统**：Snip → Microcompact → Context Collapse → Auto-compact（四级级联）
6. **工具系统**：`Tool` 接口 → `buildTool()` → `getAllBaseTools()` → `assembleToolPool()`
7. **状态管理**：自研 `Store<T>` + `DeepImmutable<AppState>`
8. **设计模式**：Generator 驱动、消息驱动、工具隔离、缓存友好、编译时门控

### 关键架构洞察

**洞察1：Generator 是整个系统的骨架**

Claude Code 的所有核心数据流都通过 AsyncGenerator 传递。这不是偶然的选择——Generator 提供了：
- **流式处理**：数据在产生的同时被消费，不需要缓冲整个响应
- **背压控制**：消费者按自己的速度处理，生产者不会溢出
- **取消语义**：`.return()` 提供优雅的终止机制
- **组合性**：`yield*` 实现零成本的函数组合

**洞察2：消息是一等公民**

所有交互都通过消息传递。消息是不可变的、可序列化的、可比较的。这使得：
- 会话可以持久化到磁盘并恢复
- 消息可以被重放以重建任何历史状态
- 消息可以被压缩、裁剪、折叠而保持一致性

**洞察3：工具是可插拔的**

工具系统通过统一的 `Tool` 接口实现高度可插拔性：
- 内置工具在编译时注册
- MCP 工具在运行时发现
- 工具可以动态启用/禁用
- 工具可以延迟加载（`shouldDefer`）
- 工具可以并发执行（`isConcurrencySafe`）

**洞察4：错误恢复是多层的**

从模型降级到 prompt 压缩到 token 限制升级，Claude Code 实现了多层错误恢复。每一层都有独立的恢复策略，确保系统在各种异常情况下都能优雅降级而不是崩溃。

**洞察5：缓存友好是核心设计约束**

从工具列表的排序一致性到 Fork 子代理的字节级前缀共享，缓存友好性贯穿整个架构。这不是事后优化，而是从设计之初就内化的约束。

### 关键文件速查表

| 文件 | 行数 | 核心职责 |
|------|------|---------|
| `entrypoints/cli.tsx` | 303 | 快速路径分发、模块延迟加载 |
| `main.tsx` | 4684 | CLI 定义、REPL 启动、配置迁移 |
| `QueryEngine.ts` | 1297 | 会话管理、消息处理、SDK 接口 |
| `query.ts` | 1730 | Agentic Loop、上下文压缩、错误恢复 |
| `Tool.ts` | 794 | 工具接口定义、权限类型、渲染方法 |
| `tools.ts` | 390 | 工具注册、过滤、组装 |
| `context.ts` | 190 | Git 状态、CLAUDE.md、上下文生成 |
| `state/store.ts` | 25 | 极简 Store 实现 |
| `state/AppStateStore.ts` | 480+ | AppState 类型定义 |
| `coordinator/coordinatorMode.ts` | 370+ | Coordinator 模式逻辑 |
| `tools/AgentTool/forkSubagent.ts` | 220+ | Fork 子代理实现 |
| `entrypoints/init.ts` | 340+ | 初始化逻辑 |

### 架构图（简化版）

```
cli.tsx ──→ main.tsx ──→ init() ──→ REPL ──→ QueryEngine
                                                │
                                    ┌───────────┴───────────┐
                                    │   submitMessage()     │
                                    │       │               │
                                    │   query()             │
                                    │   ┌───┴───┐           │
                                    │   │ Loop  │           │
                                    │   │       │           │
                                    │   │ callModel()       │
                                    │   │ ↓                 │
                                    │   │ parseResponse()   │
                                    │   │ ↓                 │
                                    │   │ executeTools()    │
                                    │   │ ↓                 │
                                    │   │ continue? ──→ Loop│
                                    │   └─────────┘         │
                                    └───────────────────────┘
```

---

## 11. 延伸思考

### 11.1 优化方向

**启动时间优化**：
- 当前的启动时间约为 150-200ms（从 `cli.tsx` 到 REPL 首次渲染）。可以通过以下方式进一步优化：
  - 将更多模块移到 `feature()` 门控后
  - 使用 Worker threads 并行初始化
  - 实现增量配置加载

**内存优化**：
- `mutableMessages` 数组在长对话中会无限增长。虽然有压缩系统，但压缩本身也有开销。可以考虑：
  - 实现分层存储（热/温/冷）
  - 使用 WeakRef 管理旧消息的 UI 渲染

**并发优化**：
- 当前的 `StreamingToolExecutor` 在 API 流式传输期间执行工具，但工具之间仍然是串行的。可以考虑：
  - 实现工具依赖图分析
  - 自动并行执行无依赖的工具

### 11.2 替代方案对比

**Agent 框架对比**：

| 特性 | Claude Code | LangChain | AutoGPT | Cursor |
|------|------------|-----------|---------|--------|
| 架构模式 | Generator 驱动 | Chain 驱动 | 任务分解 | IDE 集成 |
| 工具系统 | 强类型 Tool 接口 | 函数工具 | 命令式 | 预定义 |
| 上下文管理 | 四级压缩 | 无内建 | 简单截断 | 项目索引 |
| 子代理 | Fork + Coordinator | Agent Executor | 递归分解 | 无 |
| 权限系统 | 细粒度权限 | 无 | 无 | IDE 权限 |
| 缓存策略 | Prompt cache 友好 | 无 | 无 | 向量缓存 |

**Generator 模式 vs Chain 模式**：

Claude Code 选择 Generator 而非 Chain 模式的原因：
1. **流式友好**：Generator 天然支持流式数据
2. **取消语义**：Generator 的 `.return()` 提供优雅的取消机制
3. **组合性**：`yield*` 提供零成本的函数组合
4. **内存效率**：Generator 是惰性求值的，不需要缓存整个链的结果

### 11.3 架构演进展望

当前架构已经相当成熟，但仍有一些可能的演进方向：

1. **插件系统增强**：当前的 MCP 工具是外部的，但内置工具仍然是硬编码的。未来可能实现内置工具的插件化
2. **多模型协作**：当前的 fallback 模型只是简单的降级，未来可能实现多模型协作（如 Haiku 处理简单任务，Opus 处理复杂任务）
3. **分布式 Agent**：当前的子代理在同一进程中运行，未来可能实现跨进程/跨机器的 Agent 协作
4. **持久化状态**：当前的状态管理是内存中的，未来可能实现更完整的持久化状态机

### 11.4 设计模式总结

Claude Code 中使用的关键设计模式：

| 模式 | 应用场景 | 代码位置 |
|------|---------|---------|
| **AsyncGenerator** | 数据流、Agentic Loop | `query.ts`, `QueryEngine.ts` |
| **Builder** | 工具构建 | `Tool.ts::buildTool()` |
| **Strategy** | 上下文压缩策略 | `query.ts` 的四级压缩 |
| **Observer** | 状态变化通知 | `state/store.ts` |
| **Dependency Injection** | 查询配置 | `QueryEngineConfig` |
| **Memoization** | 上下文缓存 | `context.ts` |
| **Feature Flag** | 编译时门控 | `bun:bundle` 的 `feature()` |
| **Fast Path** | 启动优化 | `cli.tsx` 的快速路径分发 |
| **Circuit Breaker** | 错误恢复 | `query.ts` 的 `consecutiveFailures` |

### 11.5 核心设计决策的权衡

每个架构决策都有其权衡。让我们分析 Claude Code 的几个关键权衡：

**权衡1：巨石文件 vs 微模块**

`main.tsx` 有 4684 行，是一个典型的"巨石文件"。这与现代"小模块"理念相悖，但有其合理性：
- **优势**：减少模块边界开销、简化依赖管理、提高启动速度
- **劣势**：代码导航困难、合并冲突风险高、难以并行开发
- **权衡结果**：启动时间是 CLI 工具的关键指标，因此选择了巨石文件

**权衡2：AsyncGenerator vs Promise 链**

AsyncGenerator 提供了更好的流式支持和取消语义，但也有代价：
- **优势**：天然背压、优雅取消、零成本组合
- **劣势**：调试困难（调用栈不直观）、学习曲线高、IDE 支持较弱
- **权衡结果**：流式响应是核心需求，AsyncGenerator 的优势远大于劣势

**权衡3：自研 Store vs Redux/Zustand**

Claude Code 使用了仅 25 行的自研 Store，而不是成熟的第三方方案：
- **优势**：零依赖、极小体积、完全可控
- **劣势**：缺少中间件、缺少 DevTools、缺少社区支持
- **权衡结果**：状态管理需求简单，自研方案足够且无额外开销

**权衡4：编译时门控 vs 运行时配置**

`feature()` 提供编译时死代码消除，但增加了构建复杂度：
- **优势**：最小化包体积、完全移除未使用代码
- **劣势**：需要构建时配置、不能运行时切换、调试困难
- **权衡结果**：CLI 工具的包体积直接影响下载和启动时间

### 11.6 Claude Code vs 同类项目架构对比

**与 Cursor 的架构对比**：

| 维度 | Claude Code | Cursor |
|------|------------|--------|
| 运行环境 | 独立 CLI | VS Code 扩展 |
| 核心循环 | Generator 驱动 | 事件驱动 |
| 工具系统 | 40+ 内置 + MCP | 预定义 + LSP |
| 上下文管理 | 四级压缩 | 代码索引 + 向量搜索 |
| 权限模型 | 细粒度工具权限 | IDE 权限 |
| 子代理 | Fork + Coordinator | 无 |
| 扩展性 | MCP 协议 | 扩展 API |

**与 Aider 的架构对比**：

| 维度 | Claude Code | Aider |
|------|------------|-------|
| 语言 | TypeScript | Python |
| 架构模式 | Generator 驱动 | 简单循环 |
| 工具系统 | 强类型接口 | 函数式 |
| 上下文管理 | 四级压缩 | Repo Map |
| 代码编辑 | FileEdit + FileWrite | SEARCH/REPLACE 块 |
| Git 集成 | 透明（后台） | 显式（自动提交） |

**与 GitHub Copilot CLI 的架构对比**：

| 维度 | Claude Code | Copilot CLI |
|------|------------|-------------|
| 核心能力 | Agentic 工具调用 | Shell 命令建议 |
| 工具系统 | 40+ 工具 | 无（纯 shell） |
| 上下文 | 文件 + Git + CLAUDE.md | Shell 历史 |
| 自主性 | 高（可执行多步任务） | 低（建议为主） |
| 扩展性 | MCP 协议 | 无 |

---

## 源码文件引用汇总

本章引用了以下源码文件（共 15+ 个）：

1. `src/entrypoints/cli.tsx` — 引导入口（303行）
2. `src/main.tsx` — 主入口（4684行）
3. `src/QueryEngine.ts` — 查询引擎（1297行）
4. `src/query.ts` — Agentic Loop（1730行）
5. `src/Tool.ts` — 工具类型定义（794行）
6. `src/tools.ts` — 工具注册表（390行）
7. `src/context.ts` — 上下文生成（190行）
8. `src/coordinator/coordinatorMode.ts` — Coordinator 模式
9. `src/tools/AgentTool/forkSubagent.ts` — Fork 子代理
10. `src/state/store.ts` — Store 实现（25行）
11. `src/state/AppStateStore.ts` — AppState 类型定义
12. `src/entrypoints/init.ts` — 初始化逻辑
13. `src/services/tools/StreamingToolExecutor.ts` — 流式工具执行器
14. `src/services/tools/toolOrchestration.ts` — 工具编排
15. `src/utils/messages.ts` — 消息工具函数
16. `src/types/message.ts` — 消息类型定义
17. `src/services/compact/autoCompact.ts` — 自动压缩
18. `src/services/compact/reactiveCompact.ts` — 反应式压缩
