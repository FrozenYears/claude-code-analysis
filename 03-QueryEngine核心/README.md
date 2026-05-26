# 第03章：QueryEngine 核心 — Agentic Loop 的心脏

---

## 1. 本章目标

本章将深入剖析 Claude Code 中最核心的运行时引擎 — **QueryEngine** 及其驱动的 **Agentic Loop**。这是整个 Claude Code 系统中最重要的一章，因为 QueryEngine 是所有交互的最终执行者。无论用户通过 REPL 终端、SDK 接口还是 Headless 模式与 Claude 交互，最终都会走到这里。

读完本章，你将能够：

1. 理解 `QueryEngine` 类如何封装一次完整的对话生命周期，包括消息管理、使用量追踪、权限拒绝记录等核心状态
2. 掌握 `query()` 函数中 `while(true)` 无限循环的驱动机制，理解每次迭代代表一轮"思考-行动"周期
3. 理解 `AsyncGenerator`（异步生成器）如何成为整个系统的"血管"，将事件流从底层 API 调用逐层泵送到上层 UI
4. 追踪从用户输入 → LLM 调用 → 工具执行 → 停止判断 → 继续/退出 的完整调用链，理解每个环节的职责
5. 理解自动压缩（Auto-Compact）、流式工具执行（StreamingToolExecutor）、停止钩子（Stop Hooks）等关键子系统如何协作
6. 掌握状态机设计模式在复杂异步系统中的应用，理解 `Terminal` 和 `Continue` 两种状态转移类型
7. 理解依赖注入模式如何使核心循环可测试，Feature Gate 如何实现编译时死代码消除
8. 掌握错误恢复策略的多层设计：模型回退、输出 token 恢复、prompt-too-long 恢复、流式回退

---

## 2. 前置知识

在阅读本章之前，你需要了解以下概念。如果你对某些概念已经熟悉，可以跳过对应小节。

### 2.1 TypeScript AsyncGenerator — 理解整个系统的数据流基础

这是本章最核心的语言特性。`AsyncGenerator` 结合了 `async/await` 和 `yield`，创建了一个可以异步产出多个值的函数：

```typescript
// 一个简单的 AsyncGenerator
async function* countSlowly(): AsyncGenerator<number, string> {
  yield 1
  await new Promise(r => setTimeout(r, 1000))
  yield 2
  await new Promise(r => setTimeout(r, 1000))
  yield 3
  return 'done'  // 返回值，for await 看不到
}

// 消费端：for await...of 自动处理异步迭代
for await (const value of countSlowly()) {
  console.log(value)  // 1, 2, 3（每次间隔1秒）
}
// 注意：'done' 不会被打印，for await 只消费 yield 的值
```

关键点：
- `yield` 暂停执行，将值"推"给消费者，消费者处理完后调用 `.next()` 才会恢复执行
- `yield*` 委托给另一个 generator，将其所有产出值逐个传递（本章大量使用）
- 消费者通过 `for await...of` 或手动 `.next()` 驱动执行
- Generator 可以有返回值（`return`），但 `for await` 看不到，需要用 `.next()` 手动获取
- Generator 的 `finally` 块在消费者提前退出（`break`）时也会执行，确保资源清理

**在 Claude Code 中的应用**：整个数据流管道由多层 AsyncGenerator 组成。`callModel()` 产出流式事件，`queryLoop()` 通过 `yield*` 接收并转发，`query()` 再委托给 `submitMessage()`，最终到达 SDK 消费者。每一层都可以在传递过程中插入自己的处理逻辑。

### 2.2 Agentic Loop 模式 — AI Agent 的标准架构

Agentic Loop 是当前 AI Agent 系统的标准架构模式。其核心思想是让 LLM 在一个循环中不断"思考"和"行动"：

```
┌─────────────────────────────────────────┐
│  1. 将用户消息 + 历史发送给 LLM         │
│  2. LLM 返回文本或工具调用请求          │
│  3. 如果有工具调用：                    │
│     a. 执行工具                         │
│     b. 将工具结果追加到消息历史          │
│     c. 回到步骤 1                       │
│  4. 如果没有工具调用（纯文本回答）：    │
│     a. 将回答返回给用户                 │
│     b. 等待下一次用户输入               │
└─────────────────────────────────────────┘
```

这个模式的关键洞察是：**LLM 本身不具备执行能力，但它可以请求外部工具来完成任务**。通过循环，LLM 可以：
- 读取文件 → 分析内容 → 修改文件 → 验证修改
- 搜索代码 → 理解上下文 → 生成修复 → 运行测试

每次循环迭代称为一个 "turn"（轮次）。一个复杂的任务可能需要 5-20 个 turn 才能完成。

### 2.3 状态机与状态转移 — 管理复杂循环退出逻辑

状态机是一种将系统行为建模为"状态 + 转移"的设计模式。在 QueryEngine 中：

- **状态**：循环当前处于什么阶段（压缩、调用模型、执行工具、判断停止）
- **转移**：从一个状态到另一个状态的条件和动作

```
状态: {messages, toolUseContext, turnCount, ...}
转移: Terminal（退出）或 Continue（继续）
```

每次迭代结束时，系统根据当前情况决定：
- `return { reason: 'completed' }` — 任务完成，退出循环
- `state = next; continue` — 还有工作要做，进入下一轮

`transitions.ts` 文件定义了所有可能的转移类型，TypeScript 的类型系统确保每种情况都被处理。

### 2.4 消息（Message）类型体系 — 统一的数据表示

Claude Code 使用统一的 `Message` 类型表示对话中的所有内容。这是一个联合类型（union type），包含：

- `AssistantMessage`：LLM 的回复，包含文本、思考块、工具调用请求
- `UserMessage`：用户输入或工具执行结果（`tool_result` 块）
- `SystemMessage`：系统级消息，如压缩边界（`compact_boundary`）、API 错误（`api_error`）
- `StreamEvent`：流式传输的原始 SSE 事件（`message_start`、`content_block_delta` 等）
- `AttachmentMessage`：附件消息，如文件变更记录、内存附件
- `TombstoneMessage`：墓碑消息，用于标记需要从 UI 移除的消息
- `ToolUseSummaryMessage`：工具使用摘要，用于移动端 UI 展示

每种消息类型都有 `type` 字段用于区分，`uuid` 字段用于唯一标识，`timestamp` 字段用于排序。

---

## 3. 宏观概览

### 3.1 架构全景

```
用户输入 (SDK / REPL / Headless)
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│                    QueryEngine (会话生命周期管理)                 │
│                                                                  │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │           submitMessage() (每轮对话入口)                   │  │
│  │                                                            │  │
│  │  ┌────────────────────────────────────────────────────┐   │  │
│  │  │          query() (Agentic Loop 包装)                │   │  │
│  │  │                                                     │   │  │
│  │  │  ┌──────────────────────────────────────────────┐  │   │  │
│  │  │  │      queryLoop() (核心 while(true) 循环)      │  │   │  │
│  │  │  │                                               │  │   │  │
│  │  │  │  while (true) {                               │  │   │  │
│  │  │  │    ├─ 阶段1: 压缩 (snip → micro → collapse    │  │   │  │
│  │  │  │    │          → autocompact → reactive)        │  │   │  │
│  │  │  │    ├─ 阶段2: 调用 LLM (callModel 流式)        │  │   │  │
│  │  │  │    ├─ 阶段3: 工具执行 (StreamingToolExecutor   │  │   │  │
│  │  │  │    │          或 runTools)                      │  │   │  │
│  │  │  │    ├─ 阶段4: 停止判断 (handleStopHooks)        │  │   │  │
│  │  │  │    ├─ 阶段5: 预算检查 (checkTokenBudget)       │  │   │  │
│  │  │  │    └─ 阶段6: 状态转移 (continue / return)      │  │   │  │
│  │  │  └──────────────────────────────────────────────┘  │   │  │
│  │  └────────────────────────────────────────────────────┘   │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                  │
│  外部依赖:                                                       │
│  ├─ services/api/claude.ts (底层 API 调用)                      │
│  ├─ services/tools/StreamingToolExecutor.ts (流式工具执行)       │
│  ├─ services/tools/toolOrchestration.ts (工具编排)              │
│  ├─ query/stopHooks.ts (停止钩子)                               │
│  ├─ services/compact/ (压缩系统)                                │
│  └─ cost-tracker.ts (成本追踪)                                  │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
SDK 消息流 (AsyncGenerator<SDKMessage>) → 消费者 (cowork / desktop / CLI)
```

### 3.2 核心文件分工

| 文件 | 行数 | 职责 | 核心类/函数 |
|------|------|------|------------|
| `QueryEngine.ts` | 1297 | 会话生命周期管理、SDK 消息格式化、结果封装 | `QueryEngine` 类 |
| `query.ts` | 1730 | Agentic Loop 核心循环、模型调用、工具执行、压缩 | `query()`, `queryLoop()` |
| `query/config.ts` | ~60 | 查询配置快照（不可变） | `buildQueryConfig()` |
| `query/deps.ts` | ~40 | 依赖注入接口（方便测试） | `QueryDeps`, `productionDeps()` |
| `query/transitions.ts` | ~40 | 状态转移类型定义 | `Terminal`, `Continue` |
| `query/tokenBudget.ts` | ~100 | Token 预算控制 | `checkTokenBudget()` |
| `query/stopHooks.ts` | ~500 | 停止钩子执行与决策 | `handleStopHooks()` |
| `services/api/claude.ts` | 3420 | 底层 API 调用、流式处理、重试逻辑 | `queryModelWithStreaming()` |
| `cost-tracker.ts` | ~300 | 成本追踪与统计 | `getTotalCost()`, `accumulateUsage()` |
| `services/tools/StreamingToolExecutor.ts` | ~400 | 流式工具执行器 | `StreamingToolExecutor` 类 |
| `services/tools/toolOrchestration.ts` | ~190 | 工具编排（并发/串行） | `runTools()`, `partitionToolCalls()` |

### 3.3 数据流方向 — Generator 管道

整个系统的数据流通过 Generator 管道实现。每一层都是一个 `AsyncGenerator`，通过 `yield*` 委托连接：

```
callModel() ──yield StreamEvent──► queryLoop() ──yield*──► query() ──yield*──► submitMessage()
     │                                    │                    │                    │
     │ API 原始事件                       │ 处理压缩/工具       │ 转发               │ 格式转换为 SDKMessage
     ▼                                    ▼                    ▼                    ▼
  SSE 流                             内部 Message          内部 Message         SDKMessage
```

这种管道设计的优势：
1. **背压控制**：消费者决定何时处理下一条消息，生产者不会过快产出导致内存溢出
2. **组合性**：每层可以独立添加逻辑（过滤、转换、状态更新），互不干扰
3. **内存效率**：不需要缓冲所有消息，流式处理，内存占用恒定
4. **中断支持**：消费者可以随时停止消费（`break` 或 `.return()`），生产者的 `finally` 块会执行清理

---

## 4. 源码入口定位

### 4.1 QueryEngine 类定义

**文件**：`src/QueryEngine.ts`，第 113-128 行

```typescript
export class QueryEngine {
  private config: QueryEngineConfig
  private mutableMessages: Message[]
  private abortController: AbortController
  private permissionDenials: SDKPermissionDenial[]
  private totalUsage: NonNullableUsage
  private hasHandledOrphanedPermission = false
  private readFileState: FileStateCache
  // Turn-scoped skill discovery tracking
  private discoveredSkillNames = new Set<string>()
  private loadedNestedMemoryPaths = new Set<string>()

  constructor(config: QueryEngineConfig) {
    this.config = config
    this.mutableMessages = config.initialMessages ?? []
    this.abortController = config.abortController ?? createAbortController()
    this.permissionDenials = []
    this.readFileState = config.readFileCache
    this.totalUsage = EMPTY_USAGE
  }
```

**关键状态字段详解**：

- **`mutableMessages: Message[]`**：整个对话的消息历史，是整个引擎的"记忆"。每次用户输入、LLM 回复、工具执行结果都会追加到这个数组。它贯穿整个对话生命周期，在多次 `submitMessage()` 调用之间持久化。

- **`abortController: AbortController`**：用于中断正在运行的查询。当用户按下 Ctrl+C 或发送新消息时，通过 `abort()` 方法通知所有正在等待的异步操作停止。这是浏览器标准 API，在 Node.js 中也被支持。

- **`totalUsage: NonNullableUsage`**：累计的 API 使用量，包括 `input_tokens`、`output_tokens`、`cache_read_input_tokens` 等。每次 LLM 响应完成后，通过 `accumulateUsage()` 累加。

- **`permissionDenials: SDKPermissionDenial[]`**：记录所有被拒绝的工具调用权限。SDK 消费者可以通过最终的 `result` 消息获取这些记录，用于审计和调试。

- **`readFileState: FileStateCache`**：文件读取状态缓存，记录哪些文件已经被读取过。用于去重内存附件（避免重复注入相同的文件内容）和文件历史追踪。

- **`discoveredSkillNames: Set<string>`**：本轮对话中发现的技能名称集合。每次 `submitMessage()` 开始时清空，避免跨轮次累积导致内存泄漏。

### 4.2 QueryEngineConfig — 引擎配置类型

**文件**：`src/QueryEngine.ts`，第 92-131 行

```typescript
export type QueryEngineConfig = {
  cwd: string                           // 当前工作目录
  tools: Tools                          // 可用工具列表
  commands: Command[]                   // 斜杠命令列表
  mcpClients: MCPServerConnection[]     // MCP 服务器连接
  agents: AgentDefinition[]             // Agent 定义
  canUseTool: CanUseToolFn              // 权限检查函数
  getAppState: () => AppState           // 获取应用状态
  setAppState: (f: (prev: AppState) => AppState) => void  // 更新应用状态
  initialMessages?: Message[]           // 初始消息（用于恢复会话）
  readFileCache: FileStateCache         // 文件读取缓存
  customSystemPrompt?: string           // 自定义系统提示
  appendSystemPrompt?: string           // 追加系统提示
  userSpecifiedModel?: string           // 用户指定的模型
  fallbackModel?: string                // 备用模型
  thinkingConfig?: ThinkingConfig       // 思考配置
  maxTurns?: number                     // 最大轮次限制
  maxBudgetUsd?: number                 // 最大预算（美元）
  taskBudget?: { total: number }        // 任务预算（token 数）
  jsonSchema?: Record<string, unknown>  // 结构化输出 schema
  verbose?: boolean                     // 详细模式
  replayUserMessages?: boolean          // 是否回放用户消息
  handleElicitation?: ToolUseContext['handleElicitation']  // URL 引导处理
  includePartialMessages?: boolean      // 是否包含部分消息
  setSDKStatus?: (status: SDKStatus) => void  // SDK 状态更新
  abortController?: AbortController     // 外部传入的 AbortController
  orphanedPermission?: OrphanedPermission  // 孤立权限处理
  snipReplay?: (                        // Snip 边界处理回调
    yieldedSystemMsg: Message,
    store: Message[],
  ) => { messages: Message[]; executed: boolean } | undefined
}
```

这个配置类型的设计哲学是：**将所有外部依赖和配置集中在一个对象中**，使得 `QueryEngine` 类本身不直接依赖全局状态（除了少数通过函数获取的运行时状态）。这提高了可测试性和可组合性。

### 4.3 submitMessage() — 每轮对话的入口

**文件**：`src/QueryEngine.ts`，第 129 行

```typescript
async *submitMessage(
  prompt: string | ContentBlockParam[],
  options?: { uuid?: string; isMeta?: boolean },
): AsyncGenerator<SDKMessage, void, unknown> {
```

这是一个 `AsyncGenerator` 方法，返回 `SDKMessage` 类型的异步迭代器。它负责：

1. **准备阶段**：构建系统提示词、处理用户输入、初始化工具上下文
2. **执行阶段**：调用 `query()` 进入 Agentic Loop
3. **格式转换阶段**：将内部消息类型转换为 SDK 消费者期望的 `SDKMessage` 格式
4. **结果封装阶段**：在循环结束后生成 `result` 消息（包含成本、使用量等统计信息）

### 4.4 query() — Agentic Loop 的入口

**文件**：`src/query.ts`，第 217-228 行

```typescript
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
  const consumedCommandUuids: string[] = []
  const terminal = yield* queryLoop(params, consumedCommandUuids)
  // Only reached if queryLoop returned normally.
  for (const uuid of consumedCommandUuids) {
    notifyCommandLifecycle(uuid, 'completed')
  }
  return terminal
}
```

`query()` 是一个薄包装，主要工作委托给 `queryLoop()`。它的额外职责是：
- 在循环结束后通知已消费的命令生命周期完成
- 返回 `Terminal` 类型的值，描述循环退出的原因

注意 `yield* queryLoop(...)` 的使用：这会将 `queryLoop()` 的所有 yield 值逐个传递给 `query()` 的消费者，而 `queryLoop()` 的返回值（`Terminal`）会成为 `query()` 的返回值。

### 4.5 queryLoop() — while(true) 的心脏

**文件**：`src/query.ts`，第 229 行

```typescript
async function* queryLoop(
  params: QueryParams,
  consumedCommandUuids: string[],
): AsyncGenerator<...> {
  // ... 初始化 ...
  // eslint-disable-next-line no-constant-condition
  while (true) {
    // ... 核心循环体 ...
  }
}
```

这个 `while(true)` 是整个 Agentic Loop 的心脏。注释 `// eslint-disable-next-line no-constant-condition` 告诉 ESLint 这个无限循环是故意的——循环的退出通过 `return` 语句实现，而不是 `while` 的条件。

### 4.6 ask() — 一次性查询的便捷包装

**文件**：`src/QueryEngine.ts`，第 1198-1297 行

```typescript
export async function* ask({
  commands, prompt, cwd, tools, mcpClients, ...
}: {...}): AsyncGenerator<SDKMessage, void, unknown> {
  const engine = new QueryEngine({
    cwd, tools, commands, mcpClients, agents, canUseTool,
    getAppState, setAppState,
    initialMessages: mutableMessages,
    readFileCache: cloneFileStateCache(getReadFileCache()),
    // ... 其他配置 ...
  })

  try {
    yield* engine.submitMessage(prompt, { uuid: promptUuid, isMeta })
  } finally {
    setReadFileCache(engine.getReadFileState())
  }
}
```

`ask()` 是面向 SDK/一次性调用的便捷函数。它：
1. 创建一个临时的 `QueryEngine` 实例
2. 调用 `submitMessage()` 执行查询
3. 在 `finally` 块中将文件读取状态写回全局缓存

这种设计使得 `ask()` 可以在不需要管理 `QueryEngine` 生命周期的场景下使用。

---

## 5. 调用链分析

### 5.1 完整调用链概览

```
ask()
  └─► new QueryEngine()
  └─► engine.submitMessage()
        ├─► fetchSystemPromptParts()          // 准备系统提示
        ├─► processUserInput()                // 处理用户输入/斜杠命令
        ├─► buildSystemInitMessage()          // 构建系统初始化消息
        └─► for await (message of query())    // 进入 Agentic Loop
              └─► queryLoop()
                    └─► while (true) {
                          │
                          ├─► [压缩阶段]
                          │   ├─► applyToolResultBudget()     // 工具结果预算
                          │   ├─► snipCompactIfNeeded()       // Snip 压缩
                          │   ├─► microcompact()              // 微压缩
                          │   ├─► applyCollapsesIfNeeded()    // 上下文折叠
                          │   └─► autocompact()               // 自动压缩
                          │
                          ├─► [模型调用阶段]
                          │   ├─► checkBlockingLimit()        // 阻塞限制检查
                          │   └─► deps.callModel()            // 调用 LLM
                          │       └─► queryModelWithStreaming()
                          │           └─► withStreamingVCR()
                          │               └─► queryModel()
                          │                   └─► anthropic.messages.create()
                          │
                          ├─► [工具执行阶段]
                          │   ├─► StreamingToolExecutor.addTool()  // 流式添加
                          │   └─► StreamingToolExecutor.getRemainingResults()
                          │       或 runTools()                    // 传统执行
                          │
                          ├─► [停止判断阶段]
                          │   ├─► handleStopHooks()           // 停止钩子
                          │   └─► checkTokenBudget()          // Token 预算
                          │
                          └─► [状态转移]
                              ├─► return { reason: 'completed' }    // 退出
                              └─► state = next; continue            // 继续
                        }
```

### 5.2 submitMessage() 的详细流程

**文件**：`src/QueryEngine.ts`，第 129-500 行

让我们逐步追踪 `submitMessage()` 的执行流程，理解每一步的职责：

**步骤 1：初始化与系统提示准备**（第 130-250 行）

```typescript
async *submitMessage(
  prompt: string | ContentBlockParam[],
  options?: { uuid?: string; isMeta?: boolean },
): AsyncGenerator<SDKMessage, void, unknown> {
  const {
    cwd, commands, tools, mcpClients, verbose = false,
    thinkingConfig, maxTurns, maxBudgetUsd, taskBudget,
    canUseTool, customSystemPrompt, appendSystemPrompt,
    // ... 解构所有配置
  } = this.config

  this.discoveredSkillNames.clear()  // 清空上一轮的技能发现
  setCwd(cwd)                         // 设置当前工作目录
  const persistSession = !isSessionPersistenceDisabled()
  const startTime = Date.now()
```

**步骤 2：包装权限检查函数**（第 150-170 行）

```typescript
  const wrappedCanUseTool: CanUseToolFn = async (
    tool, input, toolUseContext, assistantMessage, toolUseID, forceDecision,
  ) => {
    const result = await canUseTool(
      tool, input, toolUseContext, assistantMessage, toolUseID, forceDecision,
    )
    // Track denials for SDK reporting
    if (result.behavior !== 'allow') {
      this.permissionDenials.push({
        tool_name: sdkCompatToolName(tool.name),
        tool_use_id: toolUseID,
        tool_input: input,
      })
    }
    return result
  }
```

这个包装函数在原始权限检查的基础上，额外记录所有被拒绝的权限请求。这些记录最终会包含在 `result` 消息中，供 SDK 消费者审计。

**步骤 3：准备系统提示词**（第 170-200 行）

```typescript
  const initialMainLoopModel = userSpecifiedModel
    ? parseUserSpecifiedModel(userSpecifiedModel)
    : getMainLoopModel()

  const initialThinkingConfig: ThinkingConfig = thinkingConfig
    ? thinkingConfig
    : shouldEnableThinkingByDefault() !== false
      ? { type: 'adaptive' }
      : { type: 'disabled' }

  const {
    defaultSystemPrompt,
    userContext: baseUserContext,
    systemContext,
  } = await fetchSystemPromptParts({
    tools,
    mainLoopModel: initialMainLoopModel,
    additionalWorkingDirectories: Array.from(
      initialAppState.toolPermissionContext.additionalWorkingDirectories.keys(),
    ),
    mcpClients,
    customSystemPrompt: customPrompt,
  })
```

`fetchSystemPromptParts()` 收集所有需要注入到系统提示中的信息：
- 工具列表和描述
- 模型信息（上下文窗口大小等）
- MCP 客户端信息
- 自定义系统提示
- 工作目录信息

**步骤 4：处理用户输入**（第 250-320 行）

```typescript
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
    context: { ...processUserInputContext, messages: this.mutableMessages },
    messages: this.mutableMessages,
    uuid: options?.uuid,
    isMeta: options?.isMeta,
    querySource: 'sdk',
  })

  // Push new messages, including user input and any attachments
  this.mutableMessages.push(...messagesFromUserInput)
```

`processUserInput()` 是一个复杂的消息处理函数，它：
- 解析斜杠命令（如 `/help`、`/compact`）
- 处理文件附件
- 生成用户消息
- 判断是否需要调用 LLM（`shouldQuery`）

如果用户输入的是斜杠命令，`shouldQuery` 会是 `false`，`submitMessage()` 会直接返回命令结果而不进入 `query()` 循环。

**步骤 5：进入 query() 循环**（第 680-1100 行）

```typescript
  for await (const message of query({
    messages,
    systemPrompt,
    userContext,
    systemContext,
    canUseTool: wrappedCanUseTool,
    toolUseContext: processUserInputContext,
    fallbackModel,
    querySource: 'sdk',
    maxTurns,
    taskBudget,
  })) {
    // 处理每条消息...
    switch (message.type) {
      case 'assistant':
        this.mutableMessages.push(message)
        yield* normalizeMessage(message)
        break
      case 'user':
        this.mutableMessages.push(message)
        yield* normalizeMessage(message)
        break
      case 'stream_event':
        // 处理流式事件（usage 统计等）
        if (message.event.type === 'message_start') {
          currentMessageUsage = EMPTY_USAGE
          currentMessageUsage = updateUsage(
            currentMessageUsage, message.event.message.usage,
          )
        }
        if (message.event.type === 'message_delta') {
          currentMessageUsage = updateUsage(
            currentMessageUsage, message.event.usage,
          )
        }
        if (message.event.type === 'message_stop') {
          this.totalUsage = accumulateUsage(this.totalUsage, currentMessageUsage)
        }
        break
      // ... 其他消息类型
    }

    // Check if USD budget has been exceeded
    if (maxBudgetUsd !== undefined && getTotalCost() >= maxBudgetUsd) {
      yield { type: 'result', subtype: 'error_max_budget_usd', ... }
      return
    }
  }
```

`submitMessage()` 的 `for await` 循环消费 `query()` 产出的每一条消息，进行：
1. **消息持久化**：将消息追加到 `mutableMessages` 和转录记录
2. **格式转换**：将内部 `Message` 转换为 `SDKMessage` 格式
3. **使用量统计**：从流式事件中提取 token 使用量
4. **预算检查**：检查是否超过美元预算限制

### 5.3 queryLoop() 内部的完整循环 — 逐阶段分析

**文件**：`src/query.ts`，第 229-1730 行

每次循环迭代代表一轮"思考-行动"周期。让我们详细分析每个阶段：

#### 阶段 A：初始化与状态解构（第 270-340 行）

```typescript
  // eslint-disable-next-line no-constant-condition
  while (true) {
    // Destructure state at the top of each iteration
    let { toolUseContext } = state
    const {
      messages,
      autoCompactTracking,
      maxOutputTokensRecoveryCount,
      hasAttemptedReactiveCompact,
      maxOutputTokensOverride,
      pendingToolUseSummary,
      stopHookActive,
      turnCount,
    } = state
```

每次迭代开始时，从 `state` 对象解构出所有需要的变量。这种模式确保每次迭代的状态是"快照"式的——即使在迭代过程中修改了局部变量，也不会影响 `state` 对象本身（除非显式赋值 `state = next`）。

#### 阶段 B：压缩阶段（第 350-580 行）

压缩是 Agentic Loop 中最重要的优化机制。当对话历史的 token 数接近模型的上下文窗口限制时，系统会自动压缩历史，避免 API 返回 `prompt_too_long` 错误。

```typescript
    // 1. Snip 压缩（历史片段截断）
    let snipTokensFreed = 0
    if (feature('HISTORY_SNIP')) {
      queryCheckpoint('query_snip_start')
      const snipResult = snipModule!.snipCompactIfNeeded(messagesForQuery)
      messagesForQuery = snipResult.messages
      snipTokensFreed = snipResult.tokensFreed
      if (snipResult.boundaryMessage) {
        yield snipResult.boundaryMessage
      }
      queryCheckpoint('query_snip_end')
    }

    // 2. 微压缩（缓存编辑优化）
    queryCheckpoint('query_microcompact_start')
    const microcompactResult = await deps.microcompact(
      messagesForQuery, toolUseContext, querySource,
    )
    messagesForQuery = microcompactResult.messages
    queryCheckpoint('query_microcompact_end')

    // 3. 上下文折叠（CONTEXT_COLLAPSE 特性）
    if (feature('CONTEXT_COLLAPSE') && contextCollapse) {
      const collapseResult = await contextCollapse.applyCollapsesIfNeeded(
        messagesForQuery, toolUseContext, querySource,
      )
      messagesForQuery = collapseResult.messages
    }

    // 4. 自动压缩（调用 LLM 总结历史）
    queryCheckpoint('query_autocompact_start')
    const { compactionResult, consecutiveFailures } = await deps.autocompact(
      messagesForQuery, toolUseContext, {
        systemPrompt, userContext, systemContext, toolUseContext,
        forkContextMessages: messagesForQuery,
      }, querySource, tracking, snipTokensFreed,
    )
    queryCheckpoint('query_autocompact_end')
```

**压缩的层次结构**（从轻量到重量级）：

1. **Snip 压缩**：截断历史片段，保留最近的消息。适合对话很长但最近的上下文更重要的场景。
2. **微压缩（Microcompact）**：缓存编辑优化，减少重复内容。利用 API 的 prompt caching 机制。
3. **上下文折叠（Context Collapse）**：将详细内容替换为摘要。例如，将 100 行代码读取结果折叠为"读取了 file.ts 的 100 行"。
4. **自动压缩（Autocompact）**：调用 LLM 生成完整的对话摘要。这是最重量级的方法，会消耗额外的 API 调用。
5. **响应式压缩（Reactive Compact）**：在 API 返回 `prompt_too_long` 错误后的应急压缩。

**压缩后的处理**：

```typescript
    if (compactionResult) {
      const postCompactMessages = buildPostCompactMessages(compactionResult)
      for (const message of postCompactMessages) {
        yield message  // 将压缩结果传递给上层
      }
      messagesForQuery = postCompactMessages
    }
```

#### 阶段 C：模型调用阶段（第 630-950 行）

这是循环中最核心的阶段——调用 LLM 并接收流式响应。

```typescript
    // 创建流式工具执行器（如果启用）
    const useStreamingToolExecution = config.gates.streamingToolExecution
    let streamingToolExecutor = useStreamingToolExecution
      ? new StreamingToolExecutor(
          toolUseContext.options.tools, canUseTool, toolUseContext,
        )
      : null

    // 调用模型
    try {
      while (attemptWithFallback) {
        attemptWithFallback = false
        try {
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
              // ... 大量选项
            },
          })) {
            // 处理流式消息
            if (message.type === 'assistant') {
              assistantMessages.push(message)
              const msgToolUseBlocks = message.message.content.filter(
                content => content.type === 'tool_use',
              ) as ToolUseBlock[]
              if (msgToolUseBlocks.length > 0) {
                toolUseBlocks.push(...msgToolUseBlocks)
                needsFollowUp = true
              }

              // 流式工具执行：在 LLM 还在输出时就开始执行工具
              if (streamingToolExecutor && !toolUseContext.abortController.signal.aborted) {
                for (const toolBlock of msgToolUseBlocks) {
                  streamingToolExecutor.addTool(toolBlock, message)
                }
              }
            }
          }
        } catch (innerError) {
          if (innerError instanceof FallbackTriggeredError && fallbackModel) {
            // 模型回退：切换到备用模型重试
            currentModel = fallbackModel
            attemptWithFallback = true
            assistantMessages.length = 0
            continue
          }
          throw innerError
        }
      }
    } catch (error) {
      // 全局错误处理
      yield* yieldMissingToolResultBlocks(assistantMessages, errorMessage)
      yield createAssistantAPIErrorMessage({ content: errorMessage })
      return { reason: 'model_error', error }
    }
```

**关键设计点**：

1. **流式工具执行**：`StreamingToolExecutor.addTool()` 在 LLM 还在流式输出时就开始执行工具。当 `tool_use` 块到达时，工具立即启动，不需要等待 LLM 完整输出。这可以将工具执行时间与 LLM 生成时间重叠。

2. **模型回退**：当主模型返回特定错误（如过载）时，`FallbackTriggeredError` 被抛出，循环切换到备用模型重试。

3. **withheld 消息**：某些错误消息（如 `prompt_too_long`、`max_output_tokens`）会被"扣留"（withhold），不立即 yield 给消费者。这是因为系统可能能够通过压缩或恢复策略自动处理这些错误。

#### 阶段 D：工具执行阶段（第 1300-1450 行）

```typescript
    // 获取工具执行结果
    const toolUpdates = streamingToolExecutor
      ? streamingToolExecutor.getRemainingResults()
      : runTools(toolUseBlocks, assistantMessages, canUseTool, toolUseContext)

    for await (const update of toolUpdates) {
      if (update.message) {
        yield update.message
        if (
          update.message.type === 'attachment' &&
          update.message.attachment.type === 'hook_stopped_continuation'
        ) {
          shouldPreventContinuation = true
        }
        toolResults.push(
          ...normalizeMessagesForAPI(
            [update.message], toolUseContext.options.tools,
          ).filter(_ => _.type === 'user'),
        )
      }
      if (update.newContext) {
        updatedToolUseContext = { ...update.newContext, queryTracking }
      }
    }
```

工具执行有两种模式：
- **流式执行**（`StreamingToolExecutor`）：工具在 LLM 输出过程中就开始执行，结果在 LLM 输出完成后统一收集
- **传统执行**（`runTools`）：等待 LLM 完整输出后，再执行所有工具

#### 阶段 E：停止判断阶段（第 1200-1300 行）

```typescript
    if (!needsFollowUp) {
      // 没有工具调用 → 检查是否需要恢复或退出

      // 检查 prompt-too-long 错误恢复
      const isWithheld413 =
        lastMessage?.type === 'assistant' &&
        lastMessage.isApiErrorMessage &&
        isPromptTooLongMessage(lastMessage)

      if (isWithheld413) {
        // 先尝试上下文折叠恢复
        if (feature('CONTEXT_COLLAPSE') && contextCollapse &&
            state.transition?.reason !== 'collapse_drain_retry') {
          const drained = contextCollapse.recoverFromOverflow(messagesForQuery, querySource)
          if (drained.committed > 0) {
            state = { messages: drained.messages, ..., transition: { reason: 'collapse_drain_retry' } }
            continue
          }
        }
        // 再尝试响应式压缩
        if (reactiveCompact) {
          const compacted = await reactiveCompact.tryReactiveCompact({...})
          if (compacted) {
            state = { ..., transition: { reason: 'reactive_compact_retry' } }
            continue
          }
        }
      }

      // 检查 max_output_tokens 恢复
      if (isWithheldMaxOutputTokens(lastMessage)) {
        if (maxOutputTokensRecoveryCount < MAX_OUTPUT_TOKENS_RECOVERY_LIMIT) {
          const recoveryMessage = createUserMessage({
            content: 'Output token limit hit. Resume directly...',
            isMeta: true,
          })
          state = {
            messages: [...messagesForQuery, ...assistantMessages, recoveryMessage],
            ..., transition: { reason: 'max_output_tokens_recovery' },
          }
          continue
        }
      }

      // 执行停止钩子
      const stopHookResult = yield* handleStopHooks(
        messagesForQuery, assistantMessages, systemPrompt,
        userContext, systemContext, toolUseContext, querySource, stopHookActive,
      )

      if (stopHookResult.preventContinuation) {
        return { reason: 'stop_hook_prevented' }
      }

      if (stopHookResult.blockingErrors.length > 0) {
        state = {
          messages: [...messagesForQuery, ...assistantMessages, ...stopHookResult.blockingErrors],
          ..., transition: { reason: 'stop_hook_blocking' },
        }
        continue
      }

      // 检查 token 预算
      const decision = checkTokenBudget(
        budgetTracker!, toolUseContext.agentId,
        getCurrentTurnTokenBudget(), getTurnOutputTokens(),
      )
      if (decision.action === 'continue') {
        state = {
          messages: [...messagesForQuery, ...assistantMessages, createUserMessage({
            content: decision.nudgeMessage, isMeta: true,
          })],
          ..., transition: { reason: 'token_budget_continuation' },
        }
        continue
      }

      return { reason: 'completed' }
    }
```

这是循环中最复杂的阶段，涉及多种恢复策略和停止判断。我们会在第 6 节详细分析每种策略。

#### 阶段 F：构建下一轮状态（第 1680-1730 行）

```typescript
    // 构建下一轮的状态
    const next: State = {
      messages: [...messagesForQuery, ...assistantMessages, ...toolResults],
      toolUseContext: toolUseContextWithQueryTracking,
      autoCompactTracking: tracking,
      turnCount: nextTurnCount,
      maxOutputTokensRecoveryCount: 0,
      hasAttemptedReactiveCompact: false,
      pendingToolUseSummary: nextPendingToolUseSummary,
      maxOutputTokensOverride: undefined,
      stopHookActive,
      transition: { reason: 'next_turn' },
    }
    state = next
  } // while (true)
```

注意状态的不可变更新：每次 `continue` 时都创建一个新的 `State` 对象，而不是修改现有对象。这使得每次迭代的状态都是可追溯的。

---

## 6. 核心源码解析

### 6.1 State 类型 — 循环的可变状态

**文件**：`src/query.ts`，第 201-214 行

```typescript
type State = {
  messages: Message[]                                    // 当前消息历史
  toolUseContext: ToolUseContext                         // 工具执行上下文
  autoCompactTracking: AutoCompactTrackingState | undefined  // 自动压缩追踪
  maxOutputTokensRecoveryCount: number                  // 输出 token 恢复次数
  hasAttemptedReactiveCompact: boolean                  // 是否已尝试响应式压缩
  maxOutputTokensOverride: number | undefined           // 输出 token 上限覆盖
  pendingToolUseSummary: Promise<ToolUseSummaryMessage | null> | undefined  // 待处理的工具摘要
  stopHookActive: boolean | undefined                   // 停止钩子是否激活
  turnCount: number                                     // 当前轮次
  transition: Continue | undefined                      // 进入本次迭代的原因
}
```

这个类型封装了循环迭代之间需要传递的所有可变状态。每个字段的用途：

- **`messages`**：当前轮次的消息历史。每次迭代开始时从 `state` 解构，迭代结束时构建新的消息数组。
- **`toolUseContext`**：工具执行的上下文，包含工具定义、权限检查函数、abort 控制器等。可能在工具执行过程中被修改（如添加新的 MCP 工具）。
- **`autoCompactTracking`**：追踪自动压缩的状态，包括是否已压缩、连续失败次数等。用于实现压缩的断路器模式。
- **`maxOutputTokensRecoveryCount`**：当 LLM 输出被截断时的恢复尝试次数。最多 3 次（`MAX_OUTPUT_TOKENS_RECOVERY_LIMIT`）。
- **`hasAttemptedReactiveCompact`**：是否已经尝试过响应式压缩。防止在压缩失败后无限重试。
- **`transition`**：记录"为什么进入了这次迭代"。用于调试和条件判断（如避免重复执行某些恢复策略）。

### 6.2 Terminal 与 Continue — 状态转移类型

**文件**：`src/query/transitions.ts`

```typescript
/** Terminal transition — the query loop returned. */
export type Terminal = {
  reason:
    | 'completed'           // 正常完成
    | 'blocking_limit'      // 达到阻塞限制
    | 'image_error'         // 图片错误
    | 'model_error'         // 模型错误
    | 'aborted_streaming'   // 流式传输被中断
    | 'aborted_tools'       // 工具执行被中断
    | 'prompt_too_long'     // 提示词过长
    | 'stop_hook_prevented' // 停止钩子阻止继续
    | 'hook_stopped'        // 钩子停止
    | 'max_turns'           // 达到最大轮次
    | (string & {})         // 允许自定义原因
  error?: unknown
}

/** Continue transition — the loop will iterate again. */
export type Continue = {
  reason:
    | 'tool_use'                    // 有工具调用需要执行
    | 'reactive_compact_retry'      // 响应式压缩后重试
    | 'max_output_tokens_recovery'  // 输出 token 恢复
    | 'max_output_tokens_escalate'  // 输出 token 升级
    | 'collapse_drain_retry'        // 上下文折叠后重试
    | 'stop_hook_blocking'          // 停止钩子阻塞
    | 'token_budget_continuation'   // Token 预算继续
    | 'queued_command'              // 队列中有命令
    | (string & {})                 // 允许自定义原因
}
```

**设计要点**：

1. **穷举性**：TypeScript 的类型系统确保每种转移类型都被处理。添加新的转移类型时，编译器会标记未处理的情况。

2. **`(string & {})` 技巧**：这是一个常见的 TypeScript 模式。它允许任意字符串字面量通过类型检查，同时保持 IDE 的自动补全建议。`(string & {})` 在类型系统中等价于 `string`，但 TypeScript 的自动补全引擎会优先显示联合类型中的字面量成员。

3. **错误携带**：`Terminal` 可以携带 `error` 字段，将底层错误传递给上层消费者。`Continue` 不携带错误，因为继续意味着错误已被处理。

### 6.3 QueryDeps — 依赖注入

**文件**：`src/query/deps.ts`

```typescript
export type QueryDeps = {
  // -- model
  callModel: typeof queryModelWithStreaming    // 模型调用函数

  // -- compaction
  microcompact: typeof microcompactMessages   // 微压缩函数
  autocompact: typeof autoCompactIfNeeded     // 自动压缩函数

  // -- platform
  uuid: () => string                          // UUID 生成函数
}

export function productionDeps(): QueryDeps {
  return {
    callModel: queryModelWithStreaming,
    microcompact: microcompactMessages,
    autocompact: autoCompactIfNeeded,
    uuid: randomUUID,
  }
}
```

这是一个经典的依赖注入模式。`queryLoop()` 接受一个可选的 `deps` 参数：

```typescript
const deps = params.deps ?? productionDeps()
```

**为什么使用依赖注入？**

1. **可测试性**：测试时可以注入 mock，精确控制 LLM 的行为
2. **可替换性**：可以替换压缩策略、模型调用实现等
3. **解耦**：核心循环不直接依赖具体的实现

**测试示例**：

```typescript
const testDeps: QueryDeps = {
  callModel: async function* () {
    // 返回预设的 LLM 响应
    yield { type: 'assistant', message: { content: [{ type: 'text', text: 'Hello' }] } }
  },
  microcompact: async (messages) => ({ messages, compactionInfo: undefined }),
  autocompact: async () => ({ compactionResult: undefined, consecutiveFailures: undefined }),
  uuid: () => 'test-uuid-123',
}
```

### 6.4 StreamingToolExecutor — 流式工具执行

**文件**：`src/services/tools/StreamingToolExecutor.ts`，第 40-100 行

```typescript
/**
 * Executes tools as they stream in with concurrency control.
 * - Concurrent-safe tools can execute in parallel with other concurrent-safe tools
 * - Non-concurrent tools must execute alone (exclusive access)
 * - Results are buffered and emitted in the order tools were received
 */
export class StreamingToolExecutor {
  private tools: TrackedTool[] = []
  private toolUseContext: ToolUseContext
  private hasErrored = false
  private erroredToolDescription = ''
  // Child of toolUseContext.abortController. Fires when a Bash tool errors
  // so sibling subprocesses die immediately instead of running to completion.
  private siblingAbortController: AbortController
  private discarded = false
  private progressAvailableResolve?: () => void

  constructor(
    private readonly toolDefinitions: Tools,
    private readonly canUseTool: CanUseToolFn,
    toolUseContext: ToolUseContext,
  ) {
    this.toolUseContext = toolUseContext
    this.siblingAbortController = createChildAbortController(
      toolUseContext.abortController,
    )
  }
```

**核心创新**：`StreamingToolExecutor` 在 LLM 还在流式输出时就开始执行工具。传统的工具执行需要等 LLM 完整输出后才能开始，而流式执行器在收到 `tool_use` 块时就立即启动。

**addTool() 方法**（第 120-160 行）：

```typescript
  addTool(block: ToolUseBlock, assistantMessage: AssistantMessage): void {
    const toolDefinition = findToolByName(this.toolDefinitions, block.name)
    if (!toolDefinition) {
      // 工具不存在，立即标记为完成并生成错误结果
      this.tools.push({
        id: block.id, block, assistantMessage,
        status: 'completed', isConcurrencySafe: true, pendingProgress: [],
        results: [createUserMessage({
          content: [{ type: 'tool_result',
            content: `<tool_use_error>Error: No such tool available: ${block.name}</tool_use_error>`,
            is_error: true, tool_use_id: block.id,
          }],
          toolUseResult: `Error: No such tool available: ${block.name}`,
          sourceToolAssistantUUID: assistantMessage.uuid,
        })],
      })
      return
    }

    const parsedInput = toolDefinition.inputSchema.safeParse(block.input)
    const isConcurrencySafe = parsedInput?.success
      ? (() => {
          try { return Boolean(toolDefinition.isConcurrencySafe(parsedInput.data)) }
          catch { return false }
        })()
      : false

    this.tools.push({
      id: block.id, block, assistantMessage,
      status: 'queued', isConcurrencySafe, pendingProgress: [],
    })

    void this.processQueue()  // 立即尝试执行
  }
```

**并发控制**（第 160-180 行）：

```typescript
  private canExecuteTool(isConcurrencySafe: boolean): boolean {
    const executingTools = this.tools.filter(t => t.status === 'executing')
    return (
      executingTools.length === 0 ||
      (isConcurrencySafe && executingTools.every(t => t.isConcurrencySafe))
    )
  }

  private async processQueue(): Promise<void> {
    for (const tool of this.tools) {
      if (tool.status !== 'queued') continue
      if (this.canExecuteTool(tool.isConcurrencySafe)) {
        await this.executeTool(tool)
      } else {
        // Can't execute this tool yet
        if (!tool.isConcurrencySafe) break
      }
    }
  }
```

并发规则：
- 如果当前没有工具在执行，任何工具都可以启动
- 如果有工具在执行，只有并发安全的工具可以同时执行
- 非并发安全的工具（如写文件）必须独占执行
- 遇到非并发安全的工具时，停止处理队列（保持顺序）

**executeTool() 方法**（第 260-400 行）：

```typescript
  private async executeTool(tool: TrackedTool): Promise<void> {
    tool.status = 'executing'
    this.toolUseContext.setInProgressToolUseIDs(prev => new Set(prev).add(tool.id))
    this.updateInterruptibleState()

    const messages: Message[] = []
    const contextModifiers: Array<(context: ToolUseContext) => ToolUseContext> = []

    const collectResults = async () => {
      // 检查是否已被中断
      const initialAbortReason = this.getAbortReason(tool)
      if (initialAbortReason) {
        messages.push(this.createSyntheticErrorMessage(tool.id, initialAbortReason, tool.assistantMessage))
        tool.results = messages
        tool.status = 'completed'
        return
      }

      // 创建子 AbortController
      const toolAbortController = createChildAbortController(this.siblingAbortController)
      toolAbortController.signal.addEventListener('abort', () => {
        // 当工具被中止时，传播到父控制器
        if (toolAbortController.signal.reason !== 'sibling_error' &&
            !this.toolUseContext.abortController.signal.aborted &&
            !this.discarded) {
          this.toolUseContext.abortController.abort(toolAbortController.signal.reason)
        }
      }, { once: true })

      // 执行工具
      const generator = runToolUse(
        tool.block, tool.assistantMessage, this.canUseTool,
        { ...this.toolUseContext, abortController: toolAbortController },
      )

      let thisToolErrored = false
      for await (const update of generator) {
        // 检查是否被兄弟工具错误中断
        const abortReason = this.getAbortReason(tool)
        if (abortReason && !thisToolErrored) {
          messages.push(this.createSyntheticErrorMessage(tool.id, abortReason, tool.assistantMessage))
          break
        }

        const isErrorResult = update.message.type === 'user' &&
          Array.isArray(update.message.message.content) &&
          update.message.message.content.some(_ => _.type === 'tool_result' && _.is_error === true)

        if (isErrorResult) {
          thisToolErrored = true
          // 只有 Bash 工具的错误会取消兄弟工具
          if (tool.block.name === BASH_TOOL_NAME) {
            this.hasErrored = true
            this.erroredToolDescription = this.getToolDescription(tool)
            this.siblingAbortController.abort('sibling_error')
          }
        }

        if (update.message) {
          if (update.message.type === 'progress') {
            tool.pendingProgress.push(update.message)
            // 通知进度可用
            if (this.progressAvailableResolve) {
              this.progressAvailableResolve()
              this.progressAvailableResolve = undefined
            }
          } else {
            messages.push(update.message)
          }
        }
      }

      tool.results = messages
      tool.status = 'completed'
      this.updateInterruptibleState()

      // 非并发安全工具的上下文修改器立即应用
      if (!tool.isConcurrencySafe && contextModifiers.length > 0) {
        for (const modifier of contextModifiers) {
          this.toolUseContext = modifier(this.toolUseContext)
        }
      }
    }

    const promise = collectResults()
    tool.promise = promise
    // 完成后继续处理队列
    void promise.finally(() => { void this.processQueue() })
  }
```

**关键设计点**：

1. **兄弟中断**：当 Bash 工具执行失败时，会通过 `siblingAbortController` 中断所有正在执行的兄弟工具。这是因为 Bash 命令通常有隐式依赖链（如 `mkdir` 失败 → 后续命令无意义）。但对于只读工具（如 `Read`、`Grep`），一个失败不会影响其他。

2. **进度消息**：进度消息（`progress`）存储在 `pendingProgress` 中，可以立即 yield 给消费者，不需要等待工具完成。这提供了实时的执行反馈。

3. **上下文修改器**：某些工具执行后需要修改上下文（如添加新的 MCP 工具）。非并发安全工具的修改器立即应用，并发安全工具的修改器在所有并发工具完成后统一应用。

**getCompletedResults() 和 getRemainingResults()**（第 400-530 行）：

```typescript
  // 非阻塞地获取已完成的结果
  *getCompletedResults(): Generator<MessageUpdate, void> {
    if (this.discarded) return
    for (const tool of this.tools) {
      // 立即 yield 进度消息
      while (tool.pendingProgress.length > 0) {
        const progressMessage = tool.pendingProgress.shift()!
        yield { message: progressMessage, newContext: this.toolUseContext }
      }
      if (tool.status === 'yielded') continue
      if (tool.status === 'completed' && tool.results) {
        tool.status = 'yielded'
        for (const message of tool.results) {
          yield { message, newContext: this.toolUseContext }
        }
        markToolUseAsComplete(this.toolUseContext, tool.id)
      } else if (tool.status === 'executing' && !tool.isConcurrencySafe) {
        break  // 非并发安全工具正在执行，停止收集
      }
    }
  }

  // 等待所有工具完成并 yield 结果
  async *getRemainingResults(): AsyncGenerator<MessageUpdate, void> {
    if (this.discarded) return
    while (this.hasUnfinishedTools()) {
      await this.processQueue()
      // yield 所有已完成的结果
      for (const tool of this.tools) {
        // ... 类似 getCompletedResults 的逻辑
      }
      // 等待进度或工具完成
      if (this.hasUnfinishedTools()) {
        await new Promise<void>(resolve => {
          this.progressAvailableResolve = resolve
        })
      }
    }
  }
```

### 6.5 runTools() — 传统的工具编排

**文件**：`src/services/tools/toolOrchestration.ts`，第 19-80 行

```typescript
export async function* runTools(
  toolUseMessages: ToolUseBlock[],
  assistantMessages: AssistantMessage[],
  canUseTool: CanUseToolFn,
  toolUseContext: ToolUseContext,
): AsyncGenerator<MessageUpdate, void> {
  let currentContext = toolUseContext
  for (const { isConcurrencySafe, blocks } of partitionToolCalls(
    toolUseMessages, currentContext,
  )) {
    if (isConcurrencySafe) {
      // 并发执行只读工具
      for await (const update of runToolsConcurrently(
        blocks, assistantMessages, canUseTool, currentContext,
      )) {
        if (update.contextModifier) {
          // 收集上下文修改器
          queuedContextModifiers[toolUseID] = queuedContextModifiers[toolUseID] || []
          queuedContextModifiers[toolUseID].push(update.modifyContext)
        }
        yield { message: update.message, newContext: currentContext }
      }
      // 应用所有上下文修改器
      for (const block of blocks) {
        const modifiers = queuedContextModifiers[block.id]
        if (modifiers) {
          for (const modifier of modifiers) {
            currentContext = modifier(currentContext)
          }
        }
      }
      yield { newContext: currentContext }
    } else {
      // 串行执行写入工具
      for await (const update of runToolsSerially(
        blocks, assistantMessages, canUseTool, currentContext,
      )) {
        if (update.newContext) {
          currentContext = update.newContext
        }
        yield { message: update.message, newContext: currentContext }
      }
    }
  }
}
```

**partitionToolCalls()** 将工具调用分组：
- 连续的只读工具（`isConcurrencySafe = true`）归为一组，并发执行
- 遇到写入工具时，切换为串行模式
- 这种分组策略在保持正确性的同时最大化并行度

### 6.6 自动压缩（Auto-Compact）系统详解

自动压缩是 Agentic Loop 中最重要的优化机制。当对话历史的 token 数接近模型的上下文窗口限制时，系统会自动调用 LLM 将历史压缩为摘要。

**压缩触发条件**：

```typescript
// src/services/compact/autoCompact.ts
export function calculateTokenWarningState(
  tokenCount: number,
  model: string,
): { isAtBlockingLimit: boolean; isNearLimit: boolean } {
  const contextWindow = getContextWindowForModel(model)
  const blockingThreshold = contextWindow * 0.9  // 90% 时阻塞
  const warningThreshold = contextWindow * 0.8   // 80% 时警告
  return {
    isAtBlockingLimit: tokenCount >= blockingThreshold,
    isNearLimit: tokenCount >= warningThreshold,
  }
}
```

**压缩流程**：

```typescript
// src/query.ts 中的压缩调用
const { compactionResult, consecutiveFailures } = await deps.autocompact(
  messagesForQuery,          // 当前消息历史
  toolUseContext,            // 工具上下文
  {
    systemPrompt,            // 系统提示（需要保留）
    userContext,              // 用户上下文（需要保留）
    systemContext,            // 系统上下文（需要保留）
    toolUseContext,           // 工具上下文
    forkContextMessages: messagesForQuery,  // 用于 fork 的消息
  },
  querySource,               // 查询来源
  tracking,                  // 压缩追踪状态
  snipTokensFreed,           // Snip 已释放的 token 数
)
```

**压缩后的消息构建**：

```typescript
// src/services/compact/compact.ts
export function buildPostCompactMessages(
  compactionResult: CompactionResult,
): Message[] {
  const messages: Message[] = []
  // 1. 添加压缩边界消息
  messages.push(createCompactBoundaryMessage(compactionResult))
  // 2. 添加压缩摘要
  messages.push(...compactionResult.summaryMessages)
  // 3. 添加需要保留的附件
  messages.push(...compactionResult.attachments)
  // 4. 添加钩子结果
  messages.push(...compactionResult.hookResults)
  return messages
}
```

**断路器模式**：

```typescript
// 压缩追踪状态
tracking = {
  compacted: true,           // 是否已压缩
  turnId: deps.uuid(),       // 压缩后的轮次 ID
  turnCounter: 0,            // 压缩后的轮次计数
  consecutiveFailures: 0,    // 连续失败次数
}

// 如果连续失败太多次，停止尝试压缩
if (consecutiveFailures !== undefined) {
  tracking = {
    ...(tracking ?? { compacted: false, turnId: '', turnCounter: 0 }),
    consecutiveFailures,
  }
}
```

### 6.7 handleStopHooks() — 停止钩子详解

**文件**：`src/query/stopHooks.ts`，第 60-200 行

停止钩子在每轮 LLM 回复后执行，用于扩展系统行为。

```typescript
export async function* handleStopHooks(
  messagesForQuery: Message[],
  assistantMessages: AssistantMessage[],
  systemPrompt: SystemPrompt,
  userContext: { [k: string]: string },
  systemContext: { [k: string]: string },
  toolUseContext: ToolUseContext,
  querySource: QuerySource,
  stopHookActive?: boolean,
): AsyncGenerator<...> {
  const hookStartTime = Date.now()

  // 构建钩子上下文
  const stopHookContext: REPLHookContext = {
    messages: [...messagesForQuery, ...assistantMessages],
    systemPrompt, userContext, systemContext, toolUseContext, querySource,
  }

  // 保存缓存安全参数（用于 Prompt Suggestion 等）
  if (querySource === 'repl_main_thread' || querySource === 'sdk') {
    saveCacheSafeParams(createCacheSafeParams(stopHookContext))
  }

  // 后台任务（非阻塞）
  if (!isBareMode()) {
    // Prompt 建议
    if (!isEnvDefinedFalsy(process.env.CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION)) {
      void executePromptSuggestion(stopHookContext)
    }
    // 自动记忆提取
    if (feature('EXTRACT_MEMORIES') && !toolUseContext.agentId && isExtractModeActive()) {
      void extractMemoriesModule!.executeExtractMemories(stopHookContext, ...)
    }
    // 自动做梦（Auto Dream）
    if (!toolUseContext.agentId) {
      void executeAutoDream(stopHookContext, toolUseContext.appendSystemMessage)
    }
  }

  // 执行停止钩子
  try {
    const blockingErrors: Message[] = []
    const generator = executeStopHooks(
      permissionMode, toolUseContext.abortController.signal,
      undefined, stopHookActive ?? false, toolUseContext.agentId,
      toolUseContext, [...messagesForQuery, ...assistantMessages], toolUseContext.agentType,
    )

    let hookCount = 0
    let preventedContinuation = false
    let stopReason = ''
    const hookErrors: string[] = []
    const hookInfos: StopHookInfo[] = []

    for await (const result of generator) {
      if (result.message) {
        yield result.message
        // 追踪钩子进度
        if (result.message.type === 'progress' && result.message.toolUseID) {
          stopHookToolUseID = result.message.toolUseID
          hookCount++
        }
      }
      if (result.blockingError) {
        const userMessage = createUserMessage({
          content: getStopHookMessage(result.blockingError),
          isMeta: true,
        })
        blockingErrors.push(userMessage)
        yield userMessage
      }
      if (result.preventContinuation) {
        preventedContinuation = true
        stopReason = result.stopReason || 'Stop hook prevented continuation'
        yield createAttachmentMessage({
          type: 'hook_stopped_continuation',
          message: stopReason,
          hookName: 'Stop',
          toolUseID: stopHookToolUseID,
          hookEvent: 'Stop',
        })
      }
      // 检查是否被中断
      if (toolUseContext.abortController.signal.aborted) {
        yield createUserInterruptionMessage({ toolUse: false })
        return { blockingErrors: [], preventContinuation: true }
      }
    }

    // 创建摘要消息
    if (hookCount > 0) {
      yield createStopHookSummaryMessage(
        hookCount, hookInfos, hookErrors,
        preventedContinuation, stopReason, hasOutput,
        'suggestion', stopHookToolUseID,
      )
    }

    if (preventedContinuation) {
      return { blockingErrors: [], preventContinuation: true }
    }
    if (blockingErrors.length > 0) {
      return { blockingErrors, preventContinuation: false }
    }
    return { blockingErrors: [], preventContinuation: false }

  } catch (error) {
    // 钩子执行失败不影响主流程
    yield createSystemMessage(`Stop hook failed: ${errorMessage(error)}`, 'warning')
    return { blockingErrors: [], preventContinuation: false }
  }
}
```

**停止钩子的三种结果**：

1. **正常通过**：`{ blockingErrors: [], preventContinuation: false }` — 循环继续
2. **阻塞错误**：`{ blockingErrors: [...], preventContinuation: false }` — 错误消息注入到下一轮，LLM 需要修正
3. **阻止继续**：`{ blockingErrors: [], preventContinuation: true }` — 强制退出循环

### 6.8 Token 预算控制详解

**文件**：`src/query/tokenBudget.ts`

```typescript
const COMPLETION_THRESHOLD = 0.9   // 90% 时考虑完成
const DIMINISHING_THRESHOLD = 500  // 边际收益递减阈值

export function checkTokenBudget(
  tracker: BudgetTracker,
  agentId: string | undefined,
  budget: number | null,
  globalTurnTokens: number,
): TokenBudgetDecision {
  // 子 Agent 或无预算时，不控制
  if (agentId || budget === null || budget <= 0) {
    return { action: 'stop', completionEvent: null }
  }

  const turnTokens = globalTurnTokens
  const pct = Math.round((turnTokens / budget) * 100)
  const deltaSinceLastCheck = globalTurnTokens - tracker.lastGlobalTurnTokens

  // 边际收益递减检测
  // 条件：已继续 3 次以上，且最近两次增量都 < 500 tokens
  const isDiminishing =
    tracker.continuationCount >= 3 &&
    deltaSinceLastCheck < DIMINISHING_THRESHOLD &&
    tracker.lastDeltaTokens < DIMINISHING_THRESHOLD

  // 未达到阈值且未递减 → 继续
  if (!isDiminishing && turnTokens < budget * COMPLETION_THRESHOLD) {
    tracker.continuationCount++
    tracker.lastDeltaTokens = deltaSinceLastCheck
    tracker.lastGlobalTurnTokens = globalTurnTokens
    return {
      action: 'continue',
      nudgeMessage: getBudgetContinuationMessage(pct, turnTokens, budget),
      continuationCount: tracker.continuationCount,
      pct, turnTokens, budget,
    }
  }

  // 达到阈值或递减 → 停止
  if (isDiminishing || tracker.continuationCount > 0) {
    return {
      action: 'stop',
      completionEvent: {
        continuationCount: tracker.continuationCount,
        pct, turnTokens, budget,
        diminishingReturns: isDiminishing,
        durationMs: Date.now() - tracker.startedAt,
      },
    }
  }

  return { action: 'stop', completionEvent: null }
}
```

**Token 预算的工作流程**：

1. 用户设置一个 token 预算（如 500k tokens）
2. 每轮结束后检查已用 token 是否达到预算的 90%
3. 如果未达到，注入一条"继续工作"的 nudge 消息，让 LLM 继续
4. 如果检测到边际收益递减（连续 3 轮增量 < 500 tokens），提前停止
5. 防止 LLM 在一个任务上消耗过多 token 而没有实质进展

### 6.9 yieldMissingToolResultBlocks — 错误恢复工具

**文件**：`src/query.ts`，第 140-160 行

```typescript
function* yieldMissingToolResultBlocks(
  assistantMessages: AssistantMessage[],
  errorMessage: string,
) {
  for (const assistantMessage of assistantMessages) {
    // 提取所有 tool_use 块
    const toolUseBlocks = assistantMessage.message.content.filter(
      content => content.type === 'tool_use',
    ) as ToolUseBlock[]

    // 为每个 tool_use 生成对应的 tool_result 错误消息
    for (const toolUse of toolUseBlocks) {
      yield createUserMessage({
        content: [{
          type: 'tool_result',
          content: errorMessage,
          is_error: true,
          tool_use_id: toolUse.id,
        }],
        toolUseResult: errorMessage,
        sourceToolAssistantUUID: assistantMessage.uuid,
      })
    }
  }
}
```

这个函数确保 API 的"工具配对"规则被满足：每个 `tool_use` 块必须有对应的 `tool_result` 块。当中途出错时，系统会为所有未完成的工具调用生成合成的错误结果。

### 6.10 QueryConfig — 不可变配置快照

**文件**：`src/query/config.ts`

```typescript
export type QueryConfig = {
  sessionId: SessionId
  gates: {
    streamingToolExecution: boolean    // 是否启用流式工具执行
    emitToolUseSummaries: boolean      // 是否生成工具使用摘要
    isAnt: boolean                     // 是否是 Anthropic 内部用户
    fastModeEnabled: boolean           // 是否启用快速模式
  }
}

export function buildQueryConfig(): QueryConfig {
  return {
    sessionId: getSessionId(),
    gates: {
      streamingToolExecution: checkStatsigFeatureGate_CACHED_MAY_BE_STALE(
        'tengu_streaming_tool_execution2',
      ),
      emitToolUseSummaries: isEnvTruthy(
        process.env.CLAUDE_CODE_EMIT_TOOL_USE_SUMMARIES,
      ),
      isAnt: process.env.USER_TYPE === 'ant',
      fastModeEnabled: !isEnvTruthy(process.env.CLAUDE_CODE_DISABLE_FAST_MODE),
    },
  }
}
```

**设计要点**：在循环开始时快照一次配置，避免在循环过程中配置发生变化导致行为不一致。注释明确说明：**不包含 `feature()` 门控**，因为那些是编译时的死代码消除边界，必须在使用处内联。

---

## 7. 架构设计思想

### 7.1 Generator 管道模式 — 整个系统的数据流架构

整个系统的核心架构是一个 **Generator 管道**：

```
callModel() ──yield──► queryLoop() ──yield*──► query() ──yield*──► submitMessage() ──yield──► 消费者
   │                      │                     │                     │
   │ API 原始事件         │ 处理压缩/工具       │ 转发               │ 格式转换
   ▼                      ▼                     ▼                     ▼
StreamEvent          Message/Tombstone      Message/Tombstone      SDKMessage
```

每一层都是一个 `AsyncGenerator`，通过 `yield*` 委托连接。这种设计的优势：

1. **背压控制**：消费者决定何时处理下一条消息，生产者不会过快产出。如果消费者处理慢（如 UI 渲染），生产者会自动暂停在 `yield` 处。

2. **组合性**：每层可以独立添加逻辑。`queryLoop()` 在转发消息时可以插入压缩逻辑、工具执行逻辑；`submitMessage()` 可以插入格式转换逻辑。

3. **内存效率**：不需要缓冲所有消息。消息流式传递，处理完就释放。这在长对话中尤为重要。

4. **中断支持**：消费者可以随时停止消费（`break` 或 `.return()`），生产者的 `finally` 块会执行清理。这使得 Ctrl+C 中断可以优雅地传播到整个管道。

### 7.2 状态机模式 — 管理复杂循环退出逻辑

`queryLoop()` 的 `while(true)` 实际上实现了一个隐式状态机。每次迭代结束时，根据 `transition` 类型决定下一步：

```
                    ┌──────────────────┐
                    │   初始化状态      │
                    └────────┬─────────┘
                             │
              ┌──────────────▼──────────────┐
              │   压缩 + 模型调用 + 工具执行 │◄─────────┐
              └──────────────┬──────────────┘          │
                             │                         │
              ┌──────────────▼──────────────┐          │
              │     停止判断                  │          │
              └───────┬──────────┬──────────┘          │
                      │          │                     │
        ┌─────────────▼───┐  ┌───▼──────────────┐     │
        │  Terminal        │  │  Continue         │─────┘
        │  (return)        │  │  (state = next)   │
        └─────────────────┘  └──────────────────┘
```

这种状态机设计的关键优势：
- **显式状态转移**：每次 `continue` 都需要构建新的 `State` 对象，显式声明转移原因
- **可追溯性**：`transition` 字段记录了"为什么进入这次迭代"，便于调试
- **穷举检查**：TypeScript 的类型系统确保每种转移类型都被处理

### 7.3 依赖注入与可测试性

`QueryDeps` 接口使得核心循环可以完全脱离真实的 API 调用进行测试。这是软件工程中"依赖倒置原则"的经典应用。

**生产环境**：

```typescript
const deps = params.deps ?? productionDeps()
// productionDeps() 返回真实实现：queryModelWithStreaming, autoCompactIfNeeded, ...
```

**测试环境**：

```typescript
const testDeps: QueryDeps = {
  callModel: mockCallModel,       // 返回预设的 LLM 响应
  microcompact: mockMicrocompact, // 不做任何压缩
  autocompact: mockAutocompact,   // 不做任何压缩
  uuid: () => 'test-uuid',       // 固定 UUID 便于断言
}
```

这种设计让测试可以：
- 精确控制 LLM 的响应（测试各种边界情况）
- 模拟压缩失败（测试错误恢复）
- 固定 UUID（便于快照测试）

### 7.4 错误恢复策略 — 多层防御

系统实现了多层错误恢复，形成一个"防御纵深"：

```
错误发生
  │
  ├─► 模型回退 (FallbackTriggeredError)
  │   └─ 切换到备用模型，重试整个请求
  │
  ├─► 输出 token 恢复 (max_output_tokens)
  │   ├─ 先尝试升级 token 上限 (8k → 64k)
  │   └─ 再尝试注入恢复消息 (最多 3 次)
  │
  ├─► Prompt-too-long 恢复
  │   ├─ 先尝试上下文折叠 (collapse_drain_retry)
  │   ├─ 再尝试响应式压缩 (reactive_compact_retry)
  │   └─ 最终返回 prompt_too_long 错误
  │
  ├─► 流式回退 (streamingFallbackOccured)
  │   └─ 清理失败的尝试，回退到非流式请求
  │
  └─► 全局错误处理
      └─ 生成合成的 tool_result 错误，返回 model_error
```

每层恢复都有明确的"单次尝试"限制，防止无限重试：

```typescript
// 输出 token 恢复限制
const MAX_OUTPUT_TOKENS_RECOVERY_LIMIT = 3

if (maxOutputTokensRecoveryCount < MAX_OUTPUT_TOKENS_RECOVERY_LIMIT) {
  state = {
    ...state,
    maxOutputTokensRecoveryCount: maxOutputTokensRecoveryCount + 1,
    transition: { reason: 'max_output_tokens_recovery' },
  }
  continue
}
// 恢复次数耗尽，表面错误
yield lastMessage
```

### 7.5 Feature Gate 与编译时死代码消除

代码中大量使用 `feature('HISTORY_SNIP')` 这样的门控：

```typescript
if (feature('HISTORY_SNIP')) {
  const snipResult = snipModule!.snipCompactIfNeeded(messagesForQuery)
  messagesForQuery = snipResult.messages
}
```

`feature()` 来自 `bun:bundle`，是编译时的特性开关。当特性未启用时，整个 `if` 块会被打包工具移除，不会增加运行时开销。这允许：

1. **多版本维护**：在一个代码库中维护开源版、内部版、企业版
2. **渐进式发布**：新特性可以通过门控逐步推出
3. **性能优化**：未启用的特性不会增加打包体积和运行时开销

**条件导入**也是类似的目的：

```typescript
const snipModule = feature('HISTORY_SNIP')
  ? (require('./services/compact/snipCompact.js') as typeof import('./services/compact/snipCompact.js'))
  : null
```

当 `feature('HISTORY_SNIP')` 为 `false` 时，`require()` 调用也会被移除，不会加载对应的模块。

---

## 8. 工程实践细节

### 8.1 性能优化

**流式工具执行的时间重叠**：

```
传统模式:
|-- LLM 生成 (10s) --|-- 工具执行 (5s) --|  总计: 15s

流式模式:
|-- LLM 生成 (10s) --|
       |-- 工具执行 (5s) --|              总计: 10s
```

`StreamingToolExecutor` 将工具执行时间与 LLM 生成时间重叠，减少总延迟。在包含多个工具调用的场景中，这种优化尤为显著。

**并发工具执行**：

```typescript
// partitionToolCalls 将工具分为并发和串行组
for (const { isConcurrencySafe, blocks } of partitionToolCalls(toolUseMessages)) {
  if (isConcurrencySafe) {
    // 并发执行：10 个只读工具同时执行
    for await (const update of runToolsConcurrently(blocks, ...)) {
      yield update
    }
  } else {
    // 串行执行：写入工具逐个执行
    for await (const update of runToolsSerially(blocks, ...)) {
      yield update
    }
  }
}
```

最大并发数由环境变量控制：

```typescript
function getMaxToolUseConcurrency(): number {
  return parseInt(process.env.CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY || '', 10) || 10
}
```

**内存管理**：压缩后主动释放旧消息：

```typescript
if (message.subtype === 'compact_boundary' && message.compactMetadata) {
  const mutableBoundaryIdx = this.mutableMessages.length - 1
  if (mutableBoundaryIdx > 0) {
    this.mutableMessages.splice(0, mutableBoundaryIdx)  // 释放压缩前的消息
  }
}
```

**缓存优化**：微压缩通过缓存编辑减少重复内容，利用 API 的 prompt caching 机制减少 token 消耗。`applyToolResultBudget()` 限制单个工具结果的大小，避免大文件内容占用过多上下文。

### 8.2 可维护性

**类型安全的状态转移**：`Terminal` 和 `Continue` 类型确保所有可能的转移都被处理。添加新的转移类型时，TypeScript 会标记未处理的情况。

**注释驱动的开发**：代码中有大量详细的注释，解释"为什么"而不仅仅是"做什么"。例如：

```typescript
/**
 * The rules of thinking are lengthy and fortuitous. They require plenty of thinking
 * of most long duration and deep meditation for a wizard to wrap one's noggin around.
 *
 * The rules follow:
 * 1. A message that contains a thinking or redacted_thinking block must be part of
 *    a query whose max_thinking_length > 0
 * 2. A thinking block may not be the last message in a block
 * 3. Thinking blocks must be preserved for the duration of an assistant trajectory
 */
```

**关注点分离**：每个文件都有明确的职责边界：

| 文件 | 关注点 |
|------|--------|
| `QueryEngine.ts` | 会话生命周期、SDK 格式化、结果封装 |
| `query.ts` | 核心循环逻辑、状态管理 |
| `query/config.ts` | 不可变配置 |
| `query/deps.ts` | 依赖注入 |
| `query/transitions.ts` | 状态转移类型 |
| `query/tokenBudget.ts` | 预算控制 |
| `query/stopHooks.ts` | 停止钩子 |
| `services/tools/StreamingToolExecutor.ts` | 流式工具执行 |
| `services/tools/toolOrchestration.ts` | 工具编排 |

### 8.3 扩展性

**依赖注入**：添加新的 I/O 依赖只需扩展 `QueryDeps` 接口。

**Feature Gate**：新特性可以通过 `feature()` 门控逐步推出，不影响现有功能。

**停止钩子系统**：用户可以通过配置自定义停止脚本，扩展系统行为而无需修改核心代码。

**工具系统**：新工具只需实现 `Tool` 接口即可被系统识别和执行。`StreamingToolExecutor` 自动处理并发控制。

**MCP 集成**：通过 `mcpClients` 配置，可以动态添加新的工具提供者。`refreshTools()` 方法在每轮结束后刷新工具列表。

### 8.4 并发与异步

**Generator 的异步性质**：`AsyncGenerator` 天然支持异步操作，每一步（`yield`、`next()`）都可以 `await`。这使得整个管道可以流式处理数据，而不需要缓冲。

**AbortController 贯穿整个系统**：

```typescript
// 从顶层到底层，AbortController 一路传递
QueryEngine.abortController
  → queryLoop() 的 toolUseContext.abortController
    → callModel() 的 signal 参数
    → StreamingToolExecutor 的 siblingAbortController
      → 每个工具的 toolAbortController
```

当中断发生时，信号从底层向上传播，确保所有正在执行的操作都被正确取消。

**Fire-and-forget 模式**：某些非关键操作（如转录记录、分析事件）使用 `void` 忽略 Promise：

```typescript
if (message.type === 'assistant') {
  void recordTranscript(messages)  // 不阻塞主流程
} else {
  await recordTranscript(messages)  // 关键消息需要等待
}
```

这种模式在"关键路径"上避免不必要的等待，提高响应速度。

### 8.5 错误处理

**多层 try-catch**：

```typescript
try {
  while (attemptWithFallback) {
    attemptWithFallback = false
    try {
      for await (const message of deps.callModel({...})) {
        // 处理流式消息
      }
    } catch (innerError) {
      if (innerError instanceof FallbackTriggeredError && fallbackModel) {
        // 内层 catch：模型回退
        currentModel = fallbackModel
        attemptWithFallback = true
        continue
      }
      throw innerError  // 无法处理的错误，抛到外层
    }
  }
} catch (error) {
  // 外层 catch：全局错误处理
  yield* yieldMissingToolResultBlocks(assistantMessages, errorMessage)
  yield createAssistantAPIErrorMessage({ content: errorMessage })
  return { reason: 'model_error', error }
}
```

**工具执行结果的配对**：当模型返回 `tool_use` 块但没有对应的 `tool_result` 块时（如中途出错），系统会生成合成的错误结果。这确保了 API 的"工具配对"规则被满足。

---

## 9. 初学者易错点

### 9.1 混淆 `yield` 和 `yield*`

```typescript
// ❌ 错误：这不会委托，只是 yield 一个 generator 对象
yield queryLoop(params, consumedCommandUuids)

// ✅ 正确：yield* 委托，会逐个 yield 内部 generator 的值
yield* queryLoop(params, consumedCommandUuids)
```

`yield` 只产出一个值，`yield*` 会委托给另一个 generator，将其所有产出值逐个传递。在 `query.ts` 中：

```typescript
export async function* query(params: QueryParams): AsyncGenerator<...> {
  const terminal = yield* queryLoop(params, consumedCommandUuids)  // 委托
  return terminal
}
```

如果错误地使用 `yield` 而不是 `yield*`，消费者会收到一个 generator 对象而不是实际的消息值。

### 9.2 Generator 的返回值与 yield 值的区别

```typescript
function* myGen(): Generator<number, string> {
  yield 1
  yield 2
  return 'done'
}

// for await 只消费 yield 的值
for (const v of myGen()) {
  console.log(v)  // 1, 2（不会打印 'done'）
}

// 需要手动调用 .next() 获取 return 值
const gen = myGen()
gen.next()  // { value: 1, done: false }
gen.next()  // { value: 2, done: false }
gen.next()  // { value: 'done', done: true }  ← return 值在这里
```

在 QueryEngine 中，`query()` 的 `Terminal` 返回值通过 `yield*` 委托传递给 `submitMessage()`，但 `submitMessage()` 的 `for await` 循环看不到它。`Terminal` 的信息通过其他方式（如 `result` 消息）传递给消费者。

### 9.3 不可变状态更新的陷阱

```typescript
// ❌ 错误：直接修改 state 对象
state.messages.push(newMessage)
state.turnCount++

// ✅ 正确：创建新的 state 对象
state = {
  ...state,
  messages: [...state.messages, newMessage],
  turnCount: state.turnCount + 1,
}
```

直接修改可能导致：
- 难以追踪的 bug（其他地方可能持有对旧 state 的引用）
- 状态不可回溯（无法调试"为什么进入这次迭代"）
- 并发问题（如果未来引入并发迭代）

### 9.4 Generator 的提前退出与资源清理

```typescript
// 消费者提前 break
for await (const msg of query(params)) {
  if (someCondition) break  // Generator 的 finally 块会执行
}
```

当消费者提前退出时，Generator 的 `finally` 块会执行，确保资源被清理。这在 `ask()` 中使用：

```typescript
try {
  yield* engine.submitMessage(prompt, { ... })
} finally {
  setReadFileCache(engine.getReadFileState())  // 确保缓存被保存
}
```

### 9.5 异步 Generator 的并发消费

```typescript
// ❌ 危险：不要同时消费同一个 generator
const gen = query(params)
const p1 = gen.next()  // 启动循环
const p2 = gen.next()  // 灾难！在 p1 完成前调用 next()
```

`AsyncGenerator` 不是线程安全的。每次 `.next()` 调用必须等待前一个完成。在 Claude Code 中，`query()` 只被一个消费者（`submitMessage()`）消费，避免了这个问题。

### 9.6 理解 `needsFollowUp` 标志

```typescript
let needsFollowUp = false
// ...
if (msgToolUseBlocks.length > 0) {
  toolUseBlocks.push(...msgToolUseBlocks)
  needsFollowUp = true
}
// ...
if (!needsFollowUp) {
  // 没有工具调用 → 检查停止条件
  return { reason: 'completed' }
}
```

`needsFollowUp` 是循环是否继续的关键判断。当 LLM 返回了工具调用请求时，必须执行工具并将结果反馈给 LLM，这就是"follow-up"。如果 LLM 只返回了文本回答（没有工具调用），循环就可以结束了。

### 9.7 理解 `withheld` 消息

```typescript
let withheld = false
if (reactiveCompact?.isWithheldPromptTooLong(message)) {
  withheld = true
}
if (!withheld) {
  yield yieldMessage  // 只 yield 未被扣留的消息
}
```

某些错误消息（如 `prompt_too_long`、`max_output_tokens`）会被"扣留"（withhold），不立即 yield 给消费者。这是因为系统可能能够通过压缩或恢复策略自动处理这些错误。如果恢复成功，消费者永远不会看到这些错误；如果恢复失败，消息会被"释放"（yield）给消费者。

---

## 10. 本章总结

### 10.1 核心概念回顾

1. **QueryEngine** 是会话级的生命周期管理器，封装了消息历史（`mutableMessages`）、使用量统计（`totalUsage`）、权限拒绝记录（`permissionDenials`）等核心状态。每个 `QueryEngine` 实例代表一个完整的对话。

2. **query() / queryLoop()** 是 Agentic Loop 的核心，通过 `while(true)` 驱动"思考-行动"循环。每次迭代代表一轮完整的 LLM 交互：压缩 → 调用模型 → 执行工具 → 停止判断。

3. **AsyncGenerator 管道** 是整个系统的数据流架构。从底层 API 调用到上层 SDK 输出，通过 `yield*` 层层传递。每一层都可以在传递过程中插入自己的处理逻辑。

4. **状态机模式** 通过 `State` 类型和 `Terminal`/`Continue` 转移类型实现。每次迭代的状态是不可变的快照，`transition` 字段记录转移原因，确保状态转移是显式和可追溯的。

5. **多层压缩系统** 确保对话不会因为历史过长而超出上下文窗口。从轻量级的 Snip/Microcompact 到重量级的 Autocompact/Reactive Compact，形成一个渐进式的压缩策略。

6. **流式工具执行** 通过 `StreamingToolExecutor` 实现 LLM 输出与工具执行的时间重叠，减少总延迟。并发控制确保写入工具独占执行，只读工具可以并行。

7. **停止钩子系统** 允许用户自定义每轮结束时的行为，支持任务完成检测、自动记忆提取、Prompt 建议等扩展功能。钩子可以阻止继续、注入阻塞错误或正常通过。

8. **依赖注入** 使得核心循环可以完全脱离真实的 API 调用进行测试。`QueryDeps` 接口定义了所有外部依赖，测试时可以注入 mock。

### 10.2 设计模式总结

| 模式 | 应用位置 | 解决的问题 |
|------|---------|-----------|
| Generator 管道 | 整个系统 | 流式数据传递、背压控制、组合性 |
| 状态机 | queryLoop() | 复杂的循环退出/继续逻辑 |
| 依赖注入 | QueryDeps | 可测试性、可替换性 |
| 不可变状态 | State 类型 | 状态可追溯性、调试友好 |
| Feature Gate | feature() 调用 | 编译时死代码消除、多版本维护 |
| 策略模式 | 压缩层次 | 不同场景的压缩策略 |
| 断路器 | autoCompactTracking | 防止压缩失败无限重试 |
| 观察者模式 | StreamingToolExecutor | 工具执行状态通知 |
| 责任链 | 错误恢复策略 | 多层错误处理 |

### 10.3 关键数据流

```
用户消息
  → processUserInput() [斜杠命令处理、附件生成]
  → query() [进入 Agentic Loop]
    → queryLoop() while(true)
      → [压缩] snip → microcompact → collapse → autocompact
      → [调用 LLM] callModel() → 流式接收
        → StreamingToolExecutor.addTool() [流式添加工具]
      → [工具执行] getRemainingResults() 或 runTools()
      → [停止判断] handleStopHooks() / checkTokenBudget()
      → [状态转移] continue / return
  → submitMessage() [格式转换为 SDKMessage]
  → SDKMessage [输出给消费者]
```

---

## 11. 延伸思考

### 11.1 为什么用 Generator 而不是 EventEmitter 或 Observable？

Generator 提供了**拉取式**（pull-based）的数据流，而 EventEmitter 和 Observable 是**推送式**（push-based）的。在 Agentic Loop 场景中：

- **拉取式天然支持背压**：消费者处理完一条消息后才请求下一条，生产者自动暂停
- **Generator 的执行是顺序的**：避免了并发回调的复杂性，代码更容易理解和调试
- **`yield*` 委托提供了优雅的组合机制**：不需要手动管理订阅和取消
- **Generator 的 `return()` 方法天然支持取消**：`finally` 块确保资源清理

但代价是：Generator 的执行模型是单线程的，无法利用多核 CPU。对于 I/O 密集的场景（如 API 调用），这通常不是问题。

### 11.2 自动压缩的代价与优化

每次自动压缩都会消耗一次额外的 API 调用（调用 LLM 生成摘要）。在长对话中，可能需要多次压缩。系统通过以下策略优化：

1. **渐进式压缩**：先用轻量级方法（Snip、Microcompact），失败后再用重量级方法（Autocompact）。大部分情况下，轻量级方法就足够了。

2. **缓存利用**：压缩后的摘要可以利用 prompt caching，减少后续调用的 token 消耗。

3. **压缩边界标记**：通过 `compact_boundary` 消息标记压缩点，避免重复压缩。`getMessagesAfterCompactBoundary()` 只返回压缩边界之后的消息。

4. **断路器模式**：连续失败多次后停止尝试压缩，避免无谓的 API 调用。

### 11.3 流式工具执行的局限与改进方向

`StreamingToolExecutor` 的并发控制基于工具的 `isConcurrencySafe` 属性。但这个属性是静态声明的，无法反映运行时的实际状态。例如，一个"只读"工具可能因为文件系统状态变化而产生副作用。

更精确的并发控制可能需要：
- **运行时的依赖分析**：如通过文件路径判断是否有冲突
- **乐观并发控制**：执行后验证，冲突时回滚
- **用户显式的并发提示**：如 `@parallel` 注解

### 11.4 从 Agentic Loop 到 Multi-Agent

当前的 `QueryEngine` 是单 Agent 架构。但代码中已经有多 Agent 的痕迹：

```typescript
if (!toolUseContext.agentId) {
  headlessProfilerCheckpoint('query_started')
}
```

`agentId` 用于区分主 Agent 和子 Agent。子 Agent 的 `query()` 调用与主 Agent 共享同一套循环逻辑，但有独立的消息历史和工具上下文。

这暗示了未来的 Multi-Agent 架构：多个 `QueryEngine` 实例并行运行，通过消息队列或共享状态进行协调。

### 11.5 思考模型（Thinking）的特殊处理

代码注释中详细描述了 thinking 块的三条规则：

```
1. 包含 thinking 块的消息必须在 max_thinking_length > 0 的查询中
2. thinking 块不能是消息中的最后一个块
3. thinking 块必须在 assistant 轨迹期间保留
```

这些规则的实现散布在整个循环中：
- 在流式接收时检查 thinking 块的合法性
- 在压缩时保留 thinking 块
- 在模型回退时剥离签名块（`stripSignatureBlocks`）

这是一个很好的例子，说明了横切关注点（cross-cutting concern）如何在不使用 AOP（面向切面编程）的情况下，通过条件检查分散在核心逻辑中。

### 11.6 未来可能的改进方向

1. **更智能的压缩策略**：基于对话内容的语义分析，而不是简单的 token 计数。例如，识别"已完成的子任务"并将其压缩，保留"正在进行的子任务"的完整上下文。

2. **预测性工具执行**：根据对话上下文预测 LLM 可能需要的工具，提前执行。例如，当 LLM 说"让我看看这个文件"时，提前读取文件。

3. **分布式 Agentic Loop**：将循环的不同阶段分布到不同的服务上。例如，模型调用在 GPU 集群上执行，工具执行在本地机器上执行。

4. **可视化调试**：将状态机的每次转移可视化，方便调试复杂对话。可以记录每次迭代的状态快照，支持"时间旅行"调试。

5. **自适应 Token 预算**：根据任务复杂度动态调整预算，而不是用户手动设置。例如，简单问题自动设置低预算，复杂问题自动设置高预算。

6. **增量式压缩**：不是每次压缩整个历史，而是增量式地压缩已完成的子任务。这样可以保留更多最近的上下文，同时减少总 token 数。

### 11.7 queryModelWithStreaming() 与 API 层的交互

虽然 API 调用的详细分析是下一章的内容，但理解 `queryLoop()` 如何与 API 层交互对于掌握整个系统至关重要。

`deps.callModel()` 实际调用的是 `queryModelWithStreaming()`，定义在 `src/services/api/claude.ts` 第 752 行：

```typescript
export async function* queryModelWithStreaming({
  messages, systemPrompt, thinkingConfig, tools, signal, options,
}: {...}): AsyncGenerator<StreamEvent | AssistantMessage | SystemAPIErrorMessage, void> {
  return yield* withStreamingVCR(messages, async function* () {
    yield* queryModel(
      messages, systemPrompt, thinkingConfig, tools, signal, options,
    )
  })
}
```

这个函数是 `queryLoop()` 与 Anthropic API 之间的桥梁。它：

1. **通过 `withStreamingVCR` 包装**：VCR（Video Cassette Recorder）模式用于录制和回放 API 响应，支持测试和调试。在测试环境中，可以录制一次 API 响应，后续测试直接回放，避免重复调用真实 API。

2. **调用 `queryModel()`**：这是实际的 API 调用函数。它：
   - 将内部 `Message` 类型转换为 Anthropic API 的 `MessageParam` 格式
   - 构建系统提示块（包含缓存控制）
   - 配置工具 schema
   - 设置 thinking 配置
   - 调用 `anthropic.messages.create()` 并处理流式响应

3. **流式事件传递**：`queryModel()` 通过 `yield` 产出 `StreamEvent`（如 `message_start`、`content_block_delta`、`message_stop`），这些事件被 `queryLoop()` 接收并处理。

**重试机制**：`queryModel()` 内部使用 `withRetry()` 包装 API 调用，处理速率限制（429）、服务器错误（5xx）等可重试错误。当重试次数耗尽时，可能会触发模型回退（`FallbackTriggeredError`）。

**非流式回退**：当流式请求失败时，系统会回退到非流式请求。这是通过 `executeNonStreamingRequest()` 实现的，它使用 `anthropic.messages.create()` 而不是 `.stream()`。

### 11.8 从 submitMessage() 到 result 消息的完整生命周期

让我们追踪一条用户消息从输入到最终 `result` 消息的完整生命周期：

```
1. 用户输入 "帮我修复这个 bug"
   │
   ▼
2. submitMessage() 接收 prompt
   ├─ processUserInput() 生成 UserMessage
   ├─ 追加到 mutableMessages
   └─ yield* query() 进入循环
   │
   ▼
3. queryLoop() 第 1 轮
   ├─ 压缩检查 → 无需压缩
   ├─ callModel() → LLM 返回：
   │   ├─ TextBlock: "让我看看这个文件"
   │   └─ ToolUseBlock: Read(file_path="buggy.ts")
   ├─ yield AssistantMessage
   ├─ StreamingToolExecutor.addTool(Read)
   ├─ Read 工具执行 → 返回文件内容
   ├─ yield UserMessage (tool_result)
   ├─ needsFollowUp = true → continue
   └─ state = { messages: [...], transition: { reason: 'tool_use' } }
   │
   ▼
4. queryLoop() 第 2 轮
   ├─ 压缩检查 → 无需压缩
   ├─ callModel() → LLM 返回：
   │   ├─ TextBlock: "我找到了问题，第 42 行有个空指针"
   │   └─ ToolUseBlock: Edit(file_path="buggy.ts", old_string="...", new_string="...")
   ├─ yield AssistantMessage
   ├─ StreamingToolExecutor.addTool(Edit)
   ├─ Edit 工具执行 → 修改文件
   ├─ yield UserMessage (tool_result)
   ├─ needsFollowUp = true → continue
   └─ state = { messages: [...], transition: { reason: 'tool_use' } }
   │
   ▼
5. queryLoop() 第 3 轮
   ├─ 压缩检查 → 无需压缩
   ├─ callModel() → LLM 返回：
   │   └─ TextBlock: "已修复！问题是在未检查 null 的情况下访问属性"
   ├─ yield AssistantMessage
   ├─ needsFollowUp = false
   ├─ handleStopHooks() → 正常通过
   ├─ checkTokenBudget() → stop
   └─ return { reason: 'completed' }
   │
   ▼
6. query() 返回 Terminal
   └─ notifyCommandLifecycle() 通知命令完成
   │
   ▼
7. submitMessage() 的 for await 循环结束
   ├─ 检查 isResultSuccessful()
   ├─ 提取 textResult
   └─ yield SDKMessage { type: 'result', subtype: 'success', ... }
   │
   ▼
8. 消费者收到 result 消息
   └─ 显示最终结果给用户
```

这个生命周期展示了 Agentic Loop 的完整工作流程：用户消息经过多轮"思考-行动"循环，每轮可能涉及多次工具调用，最终在 LLM 给出纯文本回答时结束。

### 11.9 queryLoop() 中的队列命令处理

`queryLoop()` 不仅处理用户消息，还处理队列中的命令。这些命令可能来自：
- 通知系统（如任务完成通知）
- MCP 服务器事件
- 后台任务结果

```typescript
// 获取队列中的命令
const queuedCommandsSnapshot = getCommandsByMaxPriority(
  sleepRan ? 'later' : 'next',
).filter(cmd => {
  if (isSlashCommand(cmd)) return false
  if (isMainThread) return cmd.agentId === undefined
  return cmd.mode === 'task-notification' && cmd.agentId === currentAgentId
})

// 将命令作为附件注入到消息中
for await (const attachment of getAttachmentMessages(
  null, updatedToolUseContext, null, queuedCommandsSnapshot,
  [...messagesForQuery, ...assistantMessages, ...toolResults],
  querySource,
)) {
  yield attachment
  toolResults.push(attachment)
}

// 从队列中移除已消费的命令
const consumedCommands = queuedCommandsSnapshot.filter(
  cmd => cmd.mode === 'prompt' || cmd.mode === 'task-notification',
)
if (consumedCommands.length > 0) {
  for (const cmd of consumedCommands) {
    if (cmd.uuid) {
      consumedCommandUuids.push(cmd.uuid)
      notifyCommandLifecycle(cmd.uuid, 'started')
    }
  }
  removeFromQueue(consumedCommands)
}
```

这种设计使得 Claude Code 可以在一个对话中处理多个异步事件，而不需要用户显式地发送每条消息。

### 11.10 微压缩（Microcompact）与缓存编辑

微压缩是一种轻量级的压缩策略，通过编辑消息历史中的缓存标记来减少 token 消耗：

```typescript
const microcompactResult = await deps.microcompact(
  messagesForQuery, toolUseContext, querySource,
)
messagesForQuery = microcompactResult.messages
```

微压缩的工作原理：
1. 识别消息历史中可以安全删除的缓存标记
2. 移除旧的 `cache_control` 标记，只保留最近的
3. 利用 API 的 prompt caching 机制，让服务端缓存大部分历史

这种策略的优势是：
- 不需要额外的 API 调用（纯客户端操作）
- 不丢失任何信息（只是优化缓存标记）
- 可以在每轮迭代中执行（无额外延迟）

缓存编辑的结果通过 `MicrocompactBoundaryMessage` 传递给消费者，记录删除的 token 数量。

---

*本章深入剖析了 Claude Code 的核心引擎——QueryEngine 及其驱动的 Agentic Loop。这个看似简单的 `while(true)` 循环背后，是精心设计的状态机、多层压缩策略、流式工具执行、停止钩子系统等复杂子系统的协作。理解这个核心，就理解了整个 Claude Code 的运行机制。*

*从宏观来看，整个系统可以概括为：**一个由 Generator 管道驱动的、状态机控制的、多层压缩保障的 Agentic Loop**。每一层都有明确的职责，通过 `yield*` 委托优雅地组合在一起。错误恢复策略形成"防御纵深"，确保系统在各种异常情况下都能优雅地降级。*

*下一章我们将深入分析 API 调用层，了解 `queryModelWithStreaming()` 如何与 Anthropic API 交互，包括流式响应处理、重试机制、模型回退等细节。*
