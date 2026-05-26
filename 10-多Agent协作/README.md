# 第10章：多Agent协作

## 1. 本章目标

本章将深入剖析 Claude Code 中多 Agent 协作系统的设计与实现。这是一个庞大而精密的架构体系，涵盖了从单一对话分叉到多节点集群协作的完整谱系。

通过本章学习，读者将掌握：

- **三种协作机制**的内部原理：常规 Subagent、Fork Subagent、Coordinator 模式
- **父子通信机制**：消息队列（`messageQueueManager`）与 XML 通知协议（`task-notification`）
- **Coordinator 模式**的五大核心工具及其协作流程
- **并行执行机制**：异步生命周期管理、前台/后台切换、进度追踪
- **工具权限隔离**：不同角色（Coordinator、Worker、Teammate）的工具白名单/黑名单策略
- **三种机制的适用场景对比**及工程权衡

---

## 2. 前置知识

在深入本章之前，建议读者先理解以下概念：

- **第4章（工具系统）**：理解 Tool 接口、`buildTool`、`ToolUseContext` 的结构
- **第7章（权限系统）**：理解 `permissionMode`、`bubble` 权限传播机制
- **第8章（消息与状态管理）**：理解 `AppState`、`Message` 类型、`setAppState` 的不可变更新模式
- **AbortController 模式**：JavaScript 的 `AbortController`/`AbortSignal` 用于取消异步操作
- **AsyncLocalStorage**：Node.js 的异步上下文传播机制，用于在异步调用链中传递工作负载信息
- **React 的 useSyncExternalStore**：`messageQueueManager` 使用此接口向 React 组件同步外部状态

---

## 3. 宏观概览

Claude Code 的多 Agent 协作系统呈现出清晰的三层架构：

```
┌──────────────────────────────────────────────────────────────┐
│                    用户交互层 (UI)                            │
│  Coordinator Task Panel │ Pill 渲染 │ 进度追踪 │ 消息队列预览  │
├──────────────────────────────────────────────────────────────┤
│                    协调层 (Coordinator)                       │
│  coordinatorMode.ts ── 五大工具管理 ── XML 通知协议            │
│  AgentTool ── spawn/resume/fork ── 工具权限过滤               │
├──────────────────────────────────────────────────────────────┤
│                    执行层 (Workers/Teammates)                 │
│  LocalAgentTask ── 异步生命周期 ── 进度追踪 ── 磁盘持久化      │
│  runAgent.ts ── 模型调用 ── 工具执行 ── 流式输出               │
│  InProcessTeammateTask ── 进程内 teammate ── 邮箱通信          │
└──────────────────────────────────────────────────────────────┘
```

三种协作机制从简单到复杂排列：

| 机制 | 复杂度 | 隔离度 | 并行度 | 适用场景 |
|------|--------|--------|--------|----------|
| **常规 Subagent** | 低 | 进程内隔离 | 可并行 | 明确的子任务委派 |
| **Fork Subagent** | 中 | 上下文继承 + 进程内 | 天然并行 | 探索性研究、上下文密集型分叉 |
| **Coordinator 模式** | 高 | 完全隔离 | 强并行 | 复杂项目、多步骤工作流 |

核心通信协议是 XML 格式的 `<task-notification>`，所有子 Agent 完成后都通过统一的通知格式回传结果。

---

## 4. 源码入口定位

### 4.1 Coordinator 模式入口

**文件**：`src/coordinator/coordinatorMode.ts`

```typescript
// 行 44-49: 模式检测函数
export function isCoordinatorMode(): boolean {
  if (feature('COORDINATOR_MODE')) {
    return isEnvTruthy(process.env.CLAUDE_CODE_COORDINATOR_MODE)
  }
  return false
}
```

这是一个基于环境变量 + Feature Gate 的双重检测。`CLAUDE_CODE_COORDINATOR_MODE` 环境变量控制开关，`COORDINATOR_MODE` feature gate 控制是否允许此功能。

**文件**：`src/coordinator/coordinatorMode.ts`，行 100-170

```typescript
// 行 100: Coordinator 系统提示词生成
export function getCoordinatorSystemPrompt(): string {
```

此函数生成 Coordinator 的完整系统提示词，定义了 Coordinator 的角色、工具使用规则、任务工作流和 Worker 管理策略。

### 4.2 Fork Subagent 入口

**文件**：`src/tools/AgentTool/forkSubagent.ts`

```typescript
// 行 26-33: Fork 功能检测
export function isForkSubagentEnabled(): boolean {
  if (feature('FORK_SUBAGENT')) {
    if (isCoordinatorMode()) return false  // 与 Coordinator 互斥
    if (getIsNonInteractiveSession()) return false
    return true
  }
  return false
}
```

关键设计：Fork 与 Coordinator 模式互斥。Coordinator 已经拥有自己的编排模型，不需要 Fork 的上下文继承能力。

### 4.3 AgentTool 主入口

**文件**：`src/tools/AgentTool/AgentTool.tsx`

这是整个多 Agent 系统的核心入口点，包含约 1400 行代码。`call` 方法（约行 250-900）是实际执行逻辑。

### 4.4 消息队列入口

**文件**：`src/utils/messageQueueManager.ts`

模块级的命令队列实现，使用 `useSyncExternalStore` 兼容接口向 React 组件同步状态。

---

## 5. 调用链分析

### 5.1 常规 Subagent 调用链

```
用户输入 → AgentTool.call()
  ├─ 解析 subagent_type → 选择 AgentDefinition
  ├─ 组装工具池 (assembleToolPool)
  ├─ 构建系统提示词 (getSystemPrompt)
  ├─ 判断是否异步 (shouldRunAsync)
  │   ├─ true → registerAsyncAgent() → runAsyncAgentLifecycle() → runAgent()
  │   └─ false → registerAgentForeground() → runAgent() (同步迭代)
  └─ 返回结果或 async_launched 状态
```

详细追踪：

1. **AgentTool.call()** (`AgentTool.tsx`，约行 250)
   - 接收 `prompt`, `subagent_type`, `description` 等参数
   - 从 `toolUseContext.options.agentDefinitions.activeAgents` 查找匹配的 AgentDefinition

2. **工具池组装** (`AgentTool.tsx`，约行 440)
   ```typescript
   const workerTools = assembleToolPool(workerPermissionContext, appState.mcp.tools);
   ```
   - 使用 `selectedAgent.permissionMode` 构建独立的权限上下文
   - Worker 工具池独立于父 Agent，不受父级工具限制影响

3. **异步注册** (`LocalAgentTask.tsx`，行 396-433)
   ```typescript
   export function registerAsyncAgent({ agentId, description, prompt, ... }): LocalAgentTaskState {
     const abortController = parentAbortController 
       ? createChildAbortController(parentAbortController) 
       : createAbortController();
     const taskState: LocalAgentTaskState = {
       ...createTaskStateBase(agentId, 'local_agent', description, toolUseId),
       type: 'local_agent',
       status: 'running',
       // ...完整状态初始化
     };
     registerTask(taskState, setAppState);
     return taskState;
   }
   ```

4. **异步生命周期** (`agentToolUtils.ts`，`runAsyncAgentLifecycle` 函数)
   - 创建流式迭代器
   - 追踪进度（token 计数、工具调用次数）
   - 处理完成/失败/中止状态
   - 发送 `<task-notification>` 通知

### 5.2 Fork Subagent 调用链

```
用户输入 (omit subagent_type) → AgentTool.call()
  ├─ isForkSubagentEnabled() → true
  ├─ effectiveType = undefined → isForkPath = true
  ├─ 递归分叉检测: isInForkChild() / querySource 检查
  ├─ selectedAgent = FORK_AGENT (内置虚拟定义)
  ├─ 系统提示词: 继承父级 renderedSystemPrompt
  ├─ 消息构建: buildForkedMessages()
  │   ├─ 克隆父级 assistantMessage (所有 tool_use blocks)
  │   ├─ 构建 placeholder tool_results (缓存友好)
  │   └─ 追加 per-child directive
  ├─ useExactTools: true (继承父级工具池)
  └─ runAgent() → 异步执行
```

关键追踪：

1. **buildForkedMessages()** (`forkSubagent.ts`，行 90-130)
   ```typescript
   export function buildForkedMessages(
     directive: string,
     assistantMessage: AssistantMessage,
   ): MessageType[] {
     // 克隆 assistant message 保留所有 content blocks
     const fullAssistantMessage: AssistantMessage = { ...assistantMessage, ... };
     // 为每个 tool_use block 构建 placeholder tool_result
     const toolResultBlocks = toolUseBlocks.map(block => ({
       type: 'tool_result' as const,
       tool_use_id: block.id,
       content: [{ type: 'text' as const, text: FORK_PLACEHOLDER_RESULT }],
     }));
     // 组合: [...history, assistant(all_tool_uses), user(placeholder_results..., directive)]
     return [fullAssistantMessage, toolResultMessage];
   }
   ```

   **设计精髓**：所有 Fork 子 Agent 的 placeholder tool_result 文本完全相同（`FORK_PLACEHOLDER_RESULT = 'Fork started — processing in background'`），这样不同子 Agent 的 API 请求前缀是字节级一致的，最大化 prompt cache 命中率。

2. **递归分叉防护** (`forkSubagent.ts`，行 72-82)
   ```typescript
   export function isInForkChild(messages: MessageType[]): boolean {
     return messages.some(m => {
       if (m.type !== 'user') return false
       const content = m.message.content
       if (!Array.isArray(content)) return false
       return content.some(
         block => block.type === 'text' && 
           block.text.includes(`<${FORK_BOILERPLATE_TAG}>`)
       )
     })
   }
   ```
   Fork 子 Agent 的消息历史中包含 `<fork-boilerplate>` 标签，通过扫描此标签检测递归。

### 5.3 Coordinator 模式调用链

```
环境变量 CLAUDE_CODE_COORDINATOR_MODE=1 → isCoordinatorMode() = true
  ├─ getCoordinatorSystemPrompt() → 编排型系统提示词
  ├─ getCoordinatorUserContext() → 注入 Worker 工具列表信息
  ├─ COORDINATOR_MODE_ALLOWED_TOOLS = { Agent, TaskStop, SendMessage, StructuredOutput }
  │
  ├─ Coordinator 调用 AgentTool({ subagent_type: "worker", prompt: "..." })
  │   ├─ shouldRunAsync = true (Coordinator 强制异步)
  │   └─ registerAsyncAgent() → Worker 执行
  │
  ├─ Worker 完成 → enqueueAgentNotification() → <task-notification> XML
  │   └─ 通知入队 messageQueueManager → Coordinator 在下一轮收到
  │
  ├─ Coordinator 调用 SendMessage({ to: agentId, message: "..." })
  │   ├─ 查找任务: appState.tasks[agentId]
  │   ├─ 任务运行中 → queuePendingMessage() (排队到 pendingMessages)
  │   └─ 任务已停止 → resumeAgentBackground() (自动恢复)
  │
  └─ Coordinator 调用 TaskStop({ task_id: "..." })
      └─ stopTask() → abortController.abort() → killAsyncAgent()
```

---

## 6. 核心源码解析

### 6.1 Coordinator 模式的系统提示词

**文件**：`src/coordinator/coordinatorMode.ts`，行 100-170

```typescript
export function getCoordinatorSystemPrompt(): string {
  const workerCapabilities = isEnvTruthy(process.env.CLAUDE_CODE_SIMPLE)
    ? 'Workers have access to Bash, Read, and Edit tools...'
    : 'Workers have access to standard tools, MCP tools...';

  return `You are Claude Code, an AI assistant that orchestrates 
  software engineering tasks across multiple workers.

## 1. Your Role
You are a **coordinator**. Your job is to:
- Help the user achieve their goal
- Direct workers to research, implement and verify code changes
- Synthesize results and communicate with the user

## 2. Your Tools
- **Agent** - Spawn a new worker
- **SendMessage** - Continue an existing worker
- **TaskStop** - Stop a running worker
...
`;
}
```

这段系统提示词的设计精妙之处：

1. **角色定位清晰**：Coordinator 是编排者，不是执行者
2. **Worker 结果是内部信号**："Worker results and system notifications are internal signals, not conversation partners"
3. **强制综合**："you must understand them before directing follow-up work" — 禁止"based on your findings"式的懒惰委派
4. **并行优先**："Parallelism is your superpower... launch independent workers concurrently"

### 6.2 Coordinator 的工具权限隔离

**文件**：`src/constants/tools.ts`，行 96-102

```typescript
export const COORDINATOR_MODE_ALLOWED_TOOLS = new Set([
  AGENT_TOOL_NAME,       // "Agent" — 生成 Worker
  TASK_STOP_TOOL_NAME,   // "TaskStop" — 停止 Worker
  SEND_MESSAGE_TOOL_NAME, // "SendMessage" — 与 Worker 通信
  SYNTHETIC_OUTPUT_TOOL_NAME, // "StructuredOutput" — 结构化输出
])
```

Coordinator 只有 4 个工具（加上 StructuredOutput 是 4 个，系统提示词里提到 3 个 + subscribe_pr_activity）。这是一种**最小权限原则**的体现：Coordinator 不需要 Bash、文件读写等执行类工具，它的职责是编排而非执行。

同时，Worker 也有自己的工具白名单：

```typescript
export const ASYNC_AGENT_ALLOWED_TOOLS = new Set([
  FILE_READ_TOOL_NAME,
  WEB_SEARCH_TOOL_NAME,
  TODO_WRITE_TOOL_NAME,
  GREP_TOOL_NAME,
  WEB_FETCH_TOOL_NAME,
  GLOB_TOOL_NAME,
  ...SHELL_TOOL_NAMES,
  FILE_EDIT_TOOL_NAME,
  FILE_WRITE_TOOL_NAME,
  NOTEBOOK_EDIT_TOOL_NAME,
  SKILL_TOOL_NAME,
  SYNTHETIC_OUTPUT_TOOL_NAME,
  TOOL_SEARCH_TOOL_NAME,
  ENTER_WORKTREE_TOOL_NAME,
  EXIT_WORKTREE_TOOL_NAME,
])
```

Worker 拥有文件操作、搜索、Shell 等执行工具，但**不能**生成子 Agent（`AgentTool` 被 `ALL_AGENT_DISALLOWED_TOOLS` 阻止）、不能停止任务、不能发送消息给其他 Worker。

### 6.3 任务通知协议（XML）

**文件**：`src/tasks/LocalAgentTask/LocalAgentTask.tsx`，行 224-272

```typescript
export function enqueueAgentNotification({
  taskId, description, status, error, setAppState,
  finalMessage, usage, toolUseId, worktreePath, worktreeBranch
}: { ... }): void {
  // 原子性检查：防止重复通知
  let shouldEnqueue = false;
  updateTaskState<LocalAgentTaskState>(taskId, setAppState, task => {
    if (task.notified) return task;
    shouldEnqueue = true;
    return { ...task, notified: true };
  });
  if (!shouldEnqueue) return;

  // 中止推测（speculation）— 后台任务状态变化
  abortSpeculation(setAppState);

  // 构建 XML 通知
  const message = `<${TASK_NOTIFICATION_TAG}>
<${TASK_ID_TAG}>${taskId}</${TASK_ID_TAG}>${toolUseIdLine}
<${OUTPUT_FILE_TAG}>${outputPath}</${OUTPUT_FILE_TAG}>
<${STATUS_TAG}>${status}</${STATUS_TAG}>
<${SUMMARY_TAG}>${summary}</${SUMMARY_TAG}>${resultSection}${usageSection}${worktreeSection}
</${TASK_NOTIFICATION_TAG}>`;

  enqueuePendingNotification({
    value: message,
    mode: 'task-notification'
  });
}
```

通知的 XML 结构：

```xml
<task-notification>
  <task-id>agent-a1b2c3</task-id>
  <tool-use-id>toolu_xxx</tool-use-id>
  <output-file>/path/to/output.jsonl</output-file>
  <status>completed</status>
  <summary>Agent "Investigate auth bug" completed</summary>
  <result>Found null pointer in src/auth/validate.ts:42...</result>
  <usage>
    <total_tokens>15000</total_tokens>
    <tool_uses>12</tool_uses>
    <duration_ms>45000</duration_ms>
  </usage>
  <worktree>
    <worktreePath>/path/to/worktree</worktreePath>
    <worktreeBranch>agent-fix-abc123</worktreeBranch>
  </worktree>
</task-notification>
```

关键设计决策：

1. **通知通过消息队列投递**：`enqueuePendingNotification` 将通知放入 `commandQueue`，优先级为 `later`（低于用户输入的 `next`），确保用户输入不会被系统通知饿死
2. **`notified` 标志的原子性检查**：使用 `updateTaskState` 的原子更新防止重复通知（TaskStopTool 也可能触发通知）
3. **推测中断**：后台任务状态变化时，中止正在进行的推测（speculation），避免预计算结果引用过期数据

### 6.4 消息队列管理器

**文件**：`src/utils/messageQueueManager.ts`

这是整个多 Agent 通信的基础设施，一个模块级的优先级队列：

```typescript
// 模块级队列 — 独立于 React 状态
const commandQueue: QueuedCommand[] = []
let snapshot: readonly QueuedCommand[] = Object.freeze([])

// 优先级定义
const PRIORITY_ORDER: Record<QueuePriority, number> = {
  now: 0,   // 最高优先级 — 立即处理
  next: 1,  // 普通优先级 — 用户输入
  later: 2, // 低优先级 — 系统通知
}
```

核心操作：

```typescript
// 入队 — 普通命令
export function enqueue(command: QueuedCommand): void {
  commandQueue.push({ ...command, priority: command.priority ?? 'next' })
  notifySubscribers()
}

// 入队 — 任务通知（默认低优先级）
export function enqueuePendingNotification(command: QueuedCommand): void {
  commandQueue.push({ ...command, priority: command.priority ?? 'later' })
  notifySubscribers()
}

// 出队 — 按优先级
export function dequeue(filter?: (cmd: QueuedCommand) => boolean): QueuedCommand | undefined {
  let bestIdx = -1
  let bestPriority = Infinity
  for (let i = 0; i < commandQueue.length; i++) {
    const cmd = commandQueue[i]!
    if (filter && !filter(cmd)) continue
    const priority = PRIORITY_ORDER[cmd.priority ?? 'next']
    if (priority < bestPriority) {
      bestIdx = i
      bestPriority = priority
    }
  }
  if (bestIdx === -1) return undefined
  const [dequeued] = commandQueue.splice(bestIdx, 1)
  notifySubscribers()
  return dequeued
}
```

设计要点：

1. **useSyncExternalStore 兼容**：通过 `subscribeToCommandQueue` 和 `getCommandQueueSnapshot` 提供 React 18 的外部存储接口
2. **不可变快照**：每次队列变更时创建冻结副本（`Object.freeze`），触发 React 重新渲染
3. **可过滤出队**：`dequeue(filter)` 允许只出队特定类型的命令，例如只出队主线程命令而不触碰子 Agent 通知
4. **可编辑性分类**：`task-notification` 模式的命令不可编辑（`NON_EDITABLE_MODES`），不会出现在用户输入缓冲区

### 6.5 LocalAgentTask 状态管理

**文件**：`src/tasks/LocalAgentTask/LocalAgentTask.tsx`

`LocalAgentTaskState` 是子 Agent 的完整状态表示：

```typescript
export type LocalAgentTaskState = TaskStateBase & {
  type: 'local_agent';
  agentId: string;
  prompt: string;
  selectedAgent?: AgentDefinition;
  agentType: string;
  model?: string;
  abortController?: AbortController;
  error?: string;
  result?: AgentToolResult;
  progress?: AgentProgress;
  retrieved: boolean;
  messages?: Message[];
  lastReportedToolCount: number;
  lastReportedTokenCount: number;
  isBackgrounded: boolean;       // 是否已后台化
  pendingMessages: string[];     // 排队中的 SendMessage 消息
  retain: boolean;               // UI 是否持有此任务
  diskLoaded: boolean;           // 是否已从磁盘加载
  evictAfter?: number;           // 面板可见性截止时间
};
```

#### 6.5.1 前台/后台切换机制

这是一个精巧的状态机设计：

```typescript
// 前台 Agent 注册
export function registerAgentForeground({ ... }): {
  taskId: string;
  backgroundSignal: Promise<void>;  // 后台化信号
  cancelAutoBackground?: () => void;
} {
  // 创建后台信号 Promise
  let resolveBackgroundSignal: () => void;
  const backgroundSignal = new Promise<void>(resolve => {
    resolveBackgroundSignal = resolve;
  });
  backgroundSignalResolvers.set(agentId, resolveBackgroundSignal!);

  // 可选：自动后台化定时器
  if (autoBackgroundMs !== undefined && autoBackgroundMs > 0) {
    const timer = setTimeout(() => {
      setAppState(prev => { /* 标记 isBackgrounded = true */ });
      resolver();  // 解除 Promise，中断前台等待
    }, autoBackgroundMs);
  }
}
```

在 `AgentTool.call()` 的同步执行路径中，使用 `Promise.race` 实现前台/后台切换：

```typescript
// AgentTool.tsx 中的同步执行循环
while (true) {
  const nextMessagePromise = agentIterator.next();
  const raceResult = backgroundPromise 
    ? await Promise.race([
        nextMessagePromise.then(r => ({ type: 'message', result: r })),
        backgroundPromise  // 后台化信号
      ])
    : { type: 'message', result: await nextMessagePromise };

  if (raceResult.type === 'background' && foregroundTaskId) {
    // 后台化！中断前台循环，启动后台执行
    wasBackgrounded = true;
    void runWithAgentContext(syncAgentContext, async () => {
      // 在后台继续执行 agent...
    });
    return { data: { status: 'async_launched', ... } };
  }
  // 正常处理消息...
}
```

这种设计的精妙之处在于：**同一个 Agent 实例可以从前台无缝切换到后台**，不需要重启或重新初始化。前台循环被中断，后台循环使用 `runAgent` 重新创建迭代器，但共享相同的 `taskId` 和 `abortController`。

#### 6.5.2 待处理消息队列

**文件**：`src/tasks/LocalAgentTask/LocalAgentTask.tsx`，行 161-175

```typescript
export function queuePendingMessage(taskId: string, msg: string, 
  setAppState: (f: (prev: AppState) => AppState) => void): void {
  updateTaskState<LocalAgentTaskState>(taskId, setAppState, task => ({
    ...task,
    pendingMessages: [...task.pendingMessages, msg]
  }));
}

export function drainPendingMessages(taskId: string, 
  getAppState: () => AppState,
  setAppState: (f: (prev: AppState) => AppState) => void): string[] {
  const task = getAppState().tasks[taskId];
  if (!isLocalAgentTask(task) || task.pendingMessages.length === 0) {
    return [];
  }
  const drained = task.pendingMessages;
  updateTaskState<LocalAgentTaskState>(taskId, setAppState, t => ({
    ...t,
    pendingMessages: []
  }));
  return drained;
}
```

当 Coordinator 通过 `SendMessage` 向一个正在运行的 Worker 发送消息时，消息被排入 `pendingMessages` 数组。Worker 在下一次工具调用的边界（tool-round boundary）时 drain 这些消息。这避免了在 Worker 执行中途插入消息导致的状态不一致。

### 6.6 SendMessageTool 通信机制

**文件**：`src/tools/SendMessageTool/SendMessageTool.ts`

`SendMessageTool` 是一个多功能通信工具，支持多种路由：

```typescript
async call(input, context, canUseTool, assistantMessage) {
  // 1. 跨会话通信 (UDS socket / Remote Control bridge)
  if (feature('UDS_INBOX') && typeof input.message === 'string') {
    const addr = parseAddress(input.to)
    if (addr.scheme === 'bridge') { /* 通过 Remote Control 发送 */ }
    if (addr.scheme === 'uds') { /* 通过 Unix Domain Socket 发送 */ }
  }

  // 2. 进程内子 Agent 通信
  if (typeof input.message === 'string' && input.to !== '*') {
    const registered = appState.agentNameRegistry.get(input.to)
    const agentId = registered ?? toAgentId(input.to)
    if (agentId) {
      const task = appState.tasks[agentId]
      if (isLocalAgentTask(task) && !isMainSessionTask(task)) {
        if (task.status === 'running') {
          // 运行中 → 排队消息
          queuePendingMessage(agentId, input.message, ...)
          return { data: { success: true, message: 'Message queued...' } }
        }
        // 已停止 → 自动恢复
        const result = await resumeAgentBackground({ agentId, prompt: input.message, ... })
        return { data: { success: true, message: 'Agent resumed...' } }
      }
    }
  }

  // 3. 广播到所有 teammate
  if (input.to === '*') { return handleBroadcast(input.message, ...) }
  
  // 4. 邮箱通信 (teammate 间)
  return handleMessage(input.to, input.message, ...)
}
```

路由策略总结：

| 目标 | 条件 | 行为 |
|------|------|------|
| 运行中的子 Agent | `isLocalAgentTask && status === 'running'` | `queuePendingMessage` |
| 已停止的子 Agent | `isLocalAgentTask && status !== 'running'` | `resumeAgentBackground`（自动恢复） |
| 已清理的子 Agent | 任务不在 AppState 中 | 从磁盘 transcript 恢复 |
| UDS socket | `parseAddress.scheme === 'uds'` | `sendToUdsSocket` |
| Remote Control | `parseAddress.scheme === 'bridge'` | `postInterClaudeMessage` |
| 所有 teammate | `to === '*'` | 遍历 teamFile.members 写邮箱 |
| 特定 teammate | 普通名称 | `writeToMailbox` |

### 6.7 Fork Subagent 的缓存优化

**文件**：`src/tools/AgentTool/forkSubagent.ts`

Fork 的核心设计目标之一是 **prompt cache 共享**。这通过以下机制实现：

```typescript
// FORK_AGENT 定义
export const FORK_AGENT = {
  agentType: FORK_SUBAGENT_TYPE,
  tools: ['*'],           // 通配符 → 继承父级工具池
  model: 'inherit',       // 继承父级模型
  permissionMode: 'bubble', // 权限冒泡到父级
  getSystemPrompt: () => '', // 未使用 — 实际使用父级的 renderedSystemPrompt
} satisfies BuiltInAgentDefinition
```

在 `AgentTool.call()` 中：

```typescript
// Fork 路径的特殊处理
const runAgentParams = {
  // ...
  override: isForkPath ? {
    systemPrompt: forkParentSystemPrompt  // 父级的系统提示词（字节级一致）
  } : ...,
  availableTools: isForkPath ? toolUseContext.options.tools : workerTools,
  // ↑ Fork 使用父级的工具数组（而非重新组装），保证序列化一致
  forkContextMessages: isForkPath ? toolUseContext.messages : undefined,
  // ↑ 继承父级的完整对话历史
  useExactTools: isForkPath ? true : undefined,
  // ↑ 精确使用父级工具定义（包括 thinkingConfig 等）
};
```

**`buildChildMessage` 的指令格式**：

```typescript
export function buildChildMessage(directive: string): string {
  return `<${FORK_BOILERPLATE_TAG}>
STOP. READ THIS FIRST.

You are a forked worker process. You are NOT the main agent.

RULES (non-negotiable):
1. Your system prompt says "default to forking." IGNORE IT — 
   that's for the parent. You ARE the fork. Do NOT spawn sub-agents.
2. Do NOT converse, ask questions, or suggest next steps
3. Do NOT editorialize or add meta-commentary
4. USE your tools directly: Bash, Read, Write, etc.
5. If you modify files, commit your changes before reporting.
6. Do NOT emit text between tool calls.
7. Stay strictly within your directive's scope.
8. Keep your report under 500 words.
9. Your response MUST begin with "Scope:".
10. REPORT structured facts, then stop

Output format:
  Scope: <echo back your assigned scope>
  Result: <the answer or key findings>
  Key files: <relevant file paths>
  Files changed: <list with commit hash>
  Issues: <list — if any>
</${FORK_BOILERPLATE_TAG}>

${FORK_DIRECTIVE_PREFIX}${directive}`
}
```

这段 boilerplate 的作用是**约束 Fork 子 Agent 的行为**：

1. **禁止递归**："Do NOT spawn sub-agents" — 即使工具池包含 AgentTool
2. **禁止对话**："Do NOT converse, ask questions" — Fork 是执行者，不是对话者
3. **结构化输出**：强制 Scope/Result/Key files 格式，便于 Coordinator 解析
4. **简洁报告**：500 字限制，避免返回过多信息污染父级上下文

### 6.8 团队创建与管理

**文件**：`src/tools/TeamCreateTool/TeamCreateTool.ts`

```typescript
async call(input, context) {
  // 检查是否已在团队中
  const existingTeam = appState.teamContext?.teamName
  if (existingTeam) {
    throw new Error(`Already leading team "${existingTeam}"...`)
  }

  // 生成唯一团队名
  const finalTeamName = generateUniqueTeamName(team_name)

  // 创建团队文件
  const teamFile: TeamFile = {
    name: finalTeamName,
    leadAgentId: formatAgentId(TEAM_LEAD_NAME, finalTeamName),
    leadSessionId: getSessionId(),
    members: [{
      agentId: leadAgentId,
      name: TEAM_LEAD_NAME,
      agentType: leadAgentType,
      model: leadModel,
      // ...
    }],
  }

  await writeTeamFileAsync(finalTeamName, teamFile)
  registerTeamForSessionCleanup(finalTeamName)

  // 重置任务列表
  await resetTaskList(sanitizeName(finalTeamName))
  await ensureTasksDir(sanitizeName(finalTeamName))

  // 更新 AppState
  setAppState(prev => ({
    ...prev,
    teamContext: {
      teamName: finalTeamName,
      teamFilePath,
      leadAgentId,
      teammates: { [leadAgentId]: { ... } },
    },
  }))
}
```

团队系统的关键设计：

1. **Team = TaskList**：团队和任务列表是 1:1 对应的，共享同一命名空间
2. **扁平结构**：团队成员列表是扁平的，teammate 不能嵌套生成 teammate
3. **session 清理**：`registerTeamForSessionCleanup` 确保会话结束时清理团队文件
4. **确定性 ID**：`leadAgentId = formatAgentId(TEAM_LEAD_NAME, teamName)` — 团队领导的 ID 是确定性的，不依赖随机数

### 6.9 TeamDeleteTool 清理机制

**文件**：`src/tools/TeamDeleteTool/TeamDeleteTool.ts`

```typescript
async call(_input, context) {
  const teamName = appState.teamContext?.teamName

  if (teamName) {
    const teamFile = readTeamFile(teamName)
    if (teamFile) {
      // 检查是否有活跃成员
      const nonLeadMembers = teamFile.members.filter(m => m.name !== TEAM_LEAD_NAME)
      const activeMembers = nonLeadMembers.filter(m => m.isActive !== false)

      if (activeMembers.length > 0) {
        return {
          data: {
            success: false,
            message: `Cannot cleanup team with ${activeMembers.length} active member(s)...`
          }
        }
      }
    }

    // 清理目录
    await cleanupTeamDirectories(teamName)
    unregisterTeamForSessionCleanup(teamName)
    clearTeammateColors()
    clearLeaderTeamName()
  }

  // 清除 AppState
  setAppState(prev => ({
    ...prev,
    teamContext: undefined,
    inbox: { messages: [] },
  }))
}
```

安全机制：只有当所有非领导成员都处于非活跃状态（`isActive === false`，即已完成或崩溃）时，才允许删除团队。这防止了误删正在工作的团队。

### 6.10 TaskStopTool 停止机制

**文件**：`src/tools/TaskStopTool/TaskStopTool.ts`

```typescript
async call({ task_id, shell_id }, { getAppState, setAppState, abortController }) {
  const id = task_id ?? shell_id
  const result = await stopTask(id, { getAppState, setAppState })
  return {
    data: {
      message: `Successfully stopped task: ${result.taskId} (${result.command})`,
      task_id: result.taskId,
      task_type: result.taskType,
      command: result.command,
    },
  }
}
```

`stopTask` 函数最终调用 `killAsyncAgent`：

```typescript
// LocalAgentTask.tsx
export function killAsyncAgent(taskId: string, setAppState: SetAppState): void {
  let killed = false;
  updateTaskState<LocalAgentTaskState>(taskId, setAppState, task => {
    if (task.status !== 'running') return task;
    killed = true;
    task.abortController?.abort();  // 触发 AbortSignal
    task.unregisterCleanup?.();
    return {
      ...task,
      status: 'killed',
      endTime: Date.now(),
      evictAfter: task.retain ? undefined : Date.now() + PANEL_GRACE_MS,
      abortController: undefined,
      unregisterCleanup: undefined,
    };
  });
  if (killed) void evictTaskOutput(taskId);
}
```

停止是通过 `AbortController.abort()` 实现的。`runAgent` 内部的 API 调用和工具执行都会检查 `AbortSignal`，收到中止信号后抛出 `AbortError`。

---

## 7. 架构设计思想

### 7.1 最小权限原则

三种角色拥有不同的工具集：

| 角色 | 可用工具 | 禁止工具 |
|------|---------|---------|
| **Coordinator** | Agent, SendMessage, TaskStop, StructuredOutput | Bash, FileRead, FileEdit, 所有执行工具 |
| **Worker** | Bash, FileRead, FileEdit, Grep, Glob, WebSearch, Skill... | Agent, TaskStop, SendMessage, TaskOutput |
| **Teammate (in-process)** | Worker 工具 + TaskCreate/Get/List/Update, SendMessage | Agent (防止嵌套) |

这种隔离通过 `constants/tools.ts` 中的三个 `Set` 实现：

- `ALL_AGENT_DISALLOWED_TOOLS`：所有子 Agent 都不能用的工具
- `ASYNC_AGENT_ALLOWED_TOOLS`：异步 Agent 的白名单
- `COORDINATOR_MODE_ALLOWED_TOOLS`：Coordinator 的白名单

### 7.2 通知驱动的异步模型

Coordinator 模式采用**纯异步通知驱动**模型：

```
Coordinator 发起任务 → 返回 async_launched
     ↓
Worker 独立执行（不阻塞 Coordinator）
     ↓
Worker 完成 → enqueueAgentNotification() → commandQueue
     ↓
commandQueue drain → <task-notification> XML 作为 user message
     ↓
Coordinator 在下一轮处理通知 → 决定后续行动
```

关键设计决策：

1. **通知是 user-role 消息**：`<task-notification>` 被注入为用户消息，这样 Coordinator 的对话轮次会被触发
2. **低优先级**：通知使用 `later` 优先级，不会抢占用户输入
3. **不阻塞**：Coordinator 发起任务后立即返回，可以继续处理其他事情
4. **幂等性**：`notified` 标志防止重复通知

### 7.3 缓存友好的分叉设计

Fork Subagent 的缓存优化是一个精彩的工程决策：

```
父 Agent API 请求:
  [system_prompt | tool_definitions | ...messages | assistant(tool_uses)]

Fork 子 Agent API 请求:
  [system_prompt | tool_definitions | ...messages | assistant(all_tool_uses) | user(placeholder_results..., directive)]
```

所有 Fork 子 Agent 共享相同的前缀（system_prompt + tool_definitions + messages + assistant），只有最后的 directive 不同。这使得 Claude API 的 prompt cache 可以跨 Fork 子 Agent 共享。

为了实现这一点：
1. **`useExactTools: true`**：使用父级的工具数组（而非重新组装），保证序列化字节一致
2. **`override.systemPrompt`**：直接传递父级的已渲染系统提示词，而非重新调用 `getSystemPrompt()`
3. **`FORK_PLACEHOLDER_RESULT`**：所有 tool_result 使用相同的占位符文本
4. **`model: 'inherit'`**：不同模型的 context length 不同，会破坏缓存

### 7.4 渐进式后台化

Agent 的执行模式不是一开始就固定的，而是可以渐进式切换：

```
注册为前台任务 (registerAgentForeground)
     ↓
执行 2 秒后显示 BackgroundHint UI
     ↓
用户按 Enter → 后台化 (backgroundAgentTask)
或
自动超时 → autoBackgroundMs 后自动后台化
或
执行完成 → unregisterAgentForeground
```

这种设计让用户有选择权：可以等待 Agent 完成，也可以让它后台运行。`Promise.race` 是实现这一机制的核心。

### 7.5 工作区隔离（Worktree）

```typescript
if (effectiveIsolation === 'worktree') {
  const slug = `agent-${earlyAgentId.slice(0, 8)}`;
  worktreeInfo = await createAgentWorktree(slug);
}
```

当指定 `isolation: "worktree"` 时，Agent 在一个临时的 git worktree 中工作：

1. **创建**：`createAgentWorktree` 创建独立的工作目录
2. **路径翻译**：Fork 子 Agent 收到 `buildWorktreeNotice`，告知其路径需要翻译
3. **变更检测**：完成后 `hasWorktreeChanges` 检查是否有实际变更
4. **清理**：无变更则自动删除 worktree，有变更则保留并返回路径

---

## 8. 工程实践细节

### 8.1 AbortController 层级

```typescript
// 注册异步 Agent 时
const abortController = parentAbortController 
  ? createChildAbortController(parentAbortController)  // 子控制器：父 abort 时自动 abort
  : createAbortController();  // 独立控制器
```

AbortController 支持父子关系：
- 后台 Agent 使用独立的 AbortController（用户 ESC 只取消主线程）
- 进程内 Teammate 的子 Agent 使用子 AbortController（teammate 终止时子 Agent 也终止）

### 8.2 进度追踪系统

```typescript
export type ProgressTracker = {
  toolUseCount: number;
  latestInputTokens: number;      // API 的 input_tokens 是累积的
  cumulativeOutputTokens: number;  // API 的 output_tokens 是每轮的，需累加
  recentActivities: ToolActivity[];
};

export function updateProgressFromMessage(
  tracker: ProgressTracker, 
  message: Message,
  resolveActivityDescription?: ActivityDescriptionResolver,
  tools?: Tools
): void {
  if (message.type !== 'assistant') return;
  
  const usage = message.message.usage;
  tracker.latestInputTokens = usage.input_tokens + 
    (usage.cache_creation_input_tokens ?? 0) + 
    (usage.cache_read_input_tokens ?? 0);
  tracker.cumulativeOutputTokens += usage.output_tokens;

  for (const content of message.message.content) {
    if (content.type === 'tool_use') {
      tracker.toolUseCount++;
      tracker.recentActivities.push({
        toolName: content.name,
        input: content.input,
        activityDescription: resolveActivityDescription?.(content.name, input),
        isSearch: classification?.isSearch,
        isRead: classification?.isRead,
      });
    }
  }
  // 保持最近 5 个活动
  while (tracker.recentActivities.length > MAX_RECENT_ACTIVITIES) {
    tracker.recentActivities.shift();
  }
}
```

进度追踪的两个独立计数维度：
- **input_tokens**：取最新值（API 返回的是累积值）
- **output_tokens**：累加（API 返回的是每轮增量）

### 8.3 磁盘持久化与恢复

子 Agent 的 transcript 被写入磁盘，支持会话恢复：

```typescript
// resumeAgent.ts
export async function resumeAgentBackground({ agentId, prompt, ... }): Promise<ResumeAgentResult> {
  const [transcript, meta] = await Promise.all([
    getAgentTranscript(asAgentId(agentId)),
    readAgentMetadata(asAgentId(agentId)),
  ])
  
  if (!transcript) {
    throw new Error(`No transcript found for agent ID: ${agentId}`)
  }
  
  const resumedMessages = filterWhitespaceOnlyAssistantMessages(
    filterOrphanedThinkingOnlyMessages(
      filterUnresolvedToolUses(transcript.messages),
    ),
  )
  // ... 使用恢复的消息重新启动 Agent
}
```

清理策略：
- 完成的 Agent 的 `outputFile` 通过 `evictTaskOutput` 清理
- 面板有 `PANEL_GRACE_MS` 宽限期，之后可被 GC
- `retain: true` 的任务延迟清理

### 8.4 Agent 名称注册表

```typescript
// AgentTool.tsx
if (name) {
  rootSetAppState(prev => {
    const next = new Map(prev.agentNameRegistry);
    next.set(name, asAgentId(asyncAgentId));
    return { ...prev, agentNameRegistry: next };
  });
}
```

通过 `name` 参数，Agent 可以被 SendMessage 按名称寻址，而不必使用难以记忆的 UUID。注册表存储在 `AppState.agentNameRegistry` 中。

### 8.5 推测中断（Speculation Abort）

```typescript
// enqueueAgentNotification 中
abortSpeculation(setAppState);
```

当后台任务状态变化（完成/失败/停止）时，正在进行的"推测"（speculated response）可能引用过期数据。`abortSpeculation` 确保预计算的响应被丢弃，模型需要基于最新状态重新生成。

### 8.6 结构化消息协议

SendMessageTool 支持三种结构化消息类型：

```typescript
const StructuredMessage = z.discriminatedUnion('type', [
  z.object({
    type: z.literal('shutdown_request'),
    reason: z.string().optional(),
  }),
  z.object({
    type: z.literal('shutdown_response'),
    request_id: z.string(),
    approve: semanticBoolean(),
    reason: z.string().optional(),
  }),
  z.object({
    type: z.literal('plan_approval_response'),
    request_id: z.string(),
    approve: semanticBoolean(),
    feedback: z.string().optional(),
  }),
])
```

这些结构化消息用于团队协调：
- **shutdown_request/response**：优雅关闭 teammate
- **plan_approval_response**：计划审批（teammate 提交计划，领导审批）

---

## 9. 初学者易错点

### 9.1 混淆"异步"的两层含义

Claude Code 中的"异步"有两层含义：

1. **API 调用的异步**（`async/await`）：JavaScript 的异步编程模型
2. **任务执行的异步**（`run_in_background`）：Agent 在后台独立执行，不阻塞调用者

新手常犯的错误是认为"异步 Agent"意味着"非阻塞 API 调用"。实际上，同步 Agent 的 API 调用也是异步的（`await`），只是它会阻塞 Coordinator 的对话轮次直到完成。

### 9.2 误以为 Fork 子 Agent 有独立的系统提示词

```typescript
// FORK_AGENT.getSystemPrompt 返回空字符串
getSystemPrompt: () => '',
```

Fork 子 Agent 的 `getSystemPrompt` 是空的！实际的系统提示词通过 `override.systemPrompt` 传递，是父级的**已渲染**系统提示词的字节级副本。这是为了 prompt cache 优化。如果你试图通过修改 `FORK_AGENT.getSystemPrompt` 来定制 Fork 行为，不会生效。

### 9.3 忽略工具池隔离

```typescript
const workerPermissionContext = {
  ...appState.toolPermissionContext,
  mode: selectedAgent.permissionMode ?? 'acceptEdits'
};
const workerTools = assembleToolPool(workerPermissionContext, appState.mcp.tools);
```

每个子 Agent 有自己独立的工具池。不要假设子 Agent 能使用父 Agent 的所有工具。特别是：
- 子 Agent 不能使用 `AgentTool`（防止递归）
- 子 Agent 不能使用 `TaskStopTool`（需要主线程状态）
- Fork 子 Agent 是例外（`useExactTools: true` 继承父级工具池）

### 9.4 混淆 `queuePendingMessage` 和 `enqueuePendingNotification`

这是两个不同层面的队列：

| 函数 | 作用于 | 场景 |
|------|--------|------|
| `queuePendingMessage` | `task.pendingMessages` | Coordinator → Worker 的消息排队 |
| `enqueuePendingNotification` | `commandQueue` | Worker → Coordinator 的完成通知 |

前者是任务级别的消息队列（存储在 `LocalAgentTaskState` 中），后者是全局的命令队列（模块级变量）。

### 9.5 不理解 Coordinator 的"综合"职责

系统提示词明确要求：

```
Never write "based on your findings" or "based on the research."
These phrases delegate understanding to the worker instead of doing it yourself.
```

新手 Coordinator 会写出类似这样的 prompt：
```
Agent({ prompt: "Based on your findings, fix the auth bug" })
```

正确做法是 Coordinator 必须先理解研究结果，然后写出具体的实现规范：
```
Agent({ prompt: "Fix the null pointer in src/auth/validate.ts:42. 
The user field is undefined when Session.expired is true but the 
token is still cached. Add a null check before accessing user.id — 
if null, return 401 with 'Session expired'. Commit and report the hash." })
```

### 9.6 误用 `run_in_background` 参数

在以下场景中 `run_in_background` 会被忽略或报错：

1. **Fork 模式开启时**：`inputSchema` 会 `.omit({ run_in_background: true })`，模型看不到此参数
2. **Coordinator 模式**：所有 Agent 强制异步
3. **进程内 Teammate**：不能生成后台 Agent（`isInProcessTeammate() && teamName && run_in_background === true` 抛错）
4. **`CLAUDE_CODE_DISABLE_BACKGROUND_TASKS`**：环境变量禁用后台任务

### 9.7 忘记 `<task-notification>` 不是真正的用户消息

Coordinator 的系统提示词明确说：

```
Worker results and system notifications are internal signals, 
not conversation partners — never thank or acknowledge them.
```

但模型有时会把 `<task-notification>` 当作用户消息来"感谢"或"回应"。正确的做法是将通知视为内部信号，只提取其中的信息用于决策。

---

## 10. 本章总结

本章深入剖析了 Claude Code 多 Agent 协作系统的三大机制及其底层基础设施。以下是关键要点：

### 核心架构

1. **三种协作机制形成递进谱系**：
   - **常规 Subagent**：最基本的父子委派，共享进程但隔离工具和上下文
   - **Fork Subagent**：上下文继承 + prompt cache 共享，适合探索性任务
   - **Coordinator 模式**：完全隔离的编排模型，适合复杂项目

2. **通信机制双轨并行**：
   - **消息队列**（`messageQueueManager`）：全局优先级队列，连接所有 Agent
   - **任务内消息**（`pendingMessages`）：特定于 Worker 的消息缓冲
   - **XML 通知协议**：`<task-notification>` 统一格式，支持结构化结果和使用统计

3. **五大 Coordinator 工具**：
   - `Agent`：生成 Worker
   - `SendMessage`：与 Worker 通信（支持自动恢复）
   - `TaskStop`：停止 Worker
   - `StructuredOutput`：结构化输出
   - `subscribe_pr_activity`（可选）：GitHub PR 事件订阅

### 设计原则

1. **最小权限**：每个角色只有完成其职责所需的最少工具
2. **通知驱动**：异步结果通过统一的 XML 通知协议回传
3. **缓存友好**：Fork 的消息构建最大化 prompt cache 命中
4. **渐进式后台化**：前台 Agent 可无缝切换到后台
5. **优雅降级**：Agent 失败时可通过 `SendMessage` 继续同一 Worker 或生成新 Worker

### 关键文件索引

| 文件 | 职责 |
|------|------|
| `coordinator/coordinatorMode.ts` | 模式检测、系统提示词、Worker 工具上下文 |
| `tools/AgentTool/AgentTool.tsx` | Agent 生成主入口（~1400 行） |
| `tools/AgentTool/forkSubagent.ts` | Fork 逻辑、缓存优化、递归防护 |
| `tools/AgentTool/agentToolUtils.ts` | 工具过滤、异步生命周期、进度追踪 |
| `tools/AgentTool/resumeAgent.ts` | Agent 恢复机制 |
| `tasks/LocalAgentTask/LocalAgentTask.tsx` | 任务状态管理、通知发送 |
| `tools/SendMessageTool/SendMessageTool.ts` | 多路由通信工具 |
| `tools/TeamCreateTool/TeamCreateTool.ts` | 团队创建与初始化 |
| `tools/TeamDeleteTool/TeamDeleteTool.ts` | 团队清理 |
| `tools/TaskStopTool/TaskStopTool.ts` | 任务停止 |
| `constants/tools.ts` | 工具权限白名单/黑名单 |
| `utils/messageQueueManager.ts` | 全局命令队列 |

---

## 11. 延伸思考

### 11.1 为什么 Fork 与 Coordinator 互斥？

```typescript
export function isForkSubagentEnabled(): boolean {
  if (feature('FORK_SUBAGENT')) {
    if (isCoordinatorMode()) return false  // ← 互斥
    ...
  }
}
```

这两种机制解决的是不同层面的问题：
- **Fork** 优化的是**上下文继承和缓存共享**——适合单个 Agent 需要分身处理多个独立子任务
- **Coordinator** 优化的是**职责分离和并行编排**——适合复杂项目的多阶段工作流

如果同时启用，Coordinator 的 Worker 已经是异步隔离的，Fork 的上下文继承优势就不存在了（Worker 从零开始）。更重要的是，两套通知机制会产生冲突。

**思考题**：如果要设计一个"Coordinator + Fork"的混合模式，你会如何处理通知路由和上下文继承？

### 11.2 消息队列的优先级策略

当前的三级优先级（`now` / `next` / `later`）是否足够？

考虑以下场景：
- 用户正在等待 Coordinator 回复
- 3 个 Worker 同时完成，各发送一个 `<task-notification>`
- 用户输入了新消息

当前策略下，用户消息（`next`）会排在通知（`later`）之前。但如果 Coordinator 已经在处理第一个通知，后两个通知需要等待。这可能导致 Worker 完成的延迟反馈。

**思考题**：你会如何设计更细粒度的优先级策略？是否需要考虑"饥饿"问题（低优先级通知永远不被处理）？

### 11.3 Fork 子 Agent 的工具池继承

```typescript
availableTools: isForkPath ? toolUseContext.options.tools : workerTools,
useExactTools: isForkPath ? true : undefined,
```

Fork 子 Agent 继承了父级的**完整**工具池，包括 `AgentTool`。这意味着 Fork 子 Agent 理论上可以再次 Fork，形成递归。代码通过 `isInForkChild` 和 `querySource` 双重检查来防护。

**思考题**：如果未来要支持有限深度的递归 Fork（例如深度 2），你会如何修改架构？需要考虑哪些安全和资源限制？

### 11.4 进程内 Teammate vs 后台 Agent 的取舍

代码中有两种"后台执行"模式：
1. **后台 Agent**（`run_in_background: true`）：同一进程，独立 AbortController
2. **进程内 Teammate**（`isInProcessTeammate`）：通过 `spawnTeammate` 创建，可能使用 tmux 或进程内执行

后台 Agent 更轻量（无 tmux 开销），但 Teammate 有独立的邮箱通信和任务管理系统。

**思考题**：在什么场景下应该选择 Teammate 而非后台 Agent？团队规模的上限是多少？

### 11.5 从 XML 通知到结构化回调

当前的通知格式是 XML 字符串，需要 Coordinator 模型解析。这是一种"文本协议"，优势是灵活（模型可以理解自然语言变体），劣势是不可靠（模型可能误解格式）。

**思考题**：如果要将通知改为结构化的函数调用（tool_use），需要修改哪些组件？这种改变会带来什么 trade-off？

### 11.6 扩展阅读

- Anthropic 的 Multi-Agent 研究论文
- Actor Model（Erlang/Akka）与 Agent 通信模式的对比
- OpenAI Swarm 框架与 Claude Code 多 Agent 系统的设计差异
- 消息队列理论：优先级队列、死信队列、幂等性保证
- Git Worktree 的高级用法与工作区隔离策略

---

*本章源码分析基于 Claude Code 开源代码库，文件路径均相对于 `src/` 目录。行号可能随版本更新有所变化，但核心设计模式保持稳定。*
