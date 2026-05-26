---
title: 第04章：Agent系统 — 子Agent创建、工具过滤与Fork机制
---

# 第04章：Agent系统 — 子Agent创建、工具过滤与Fork机制

---

## 1. 本章目标

本章将深入剖析 Claude Code 的 **Agent系统**，这是整个框架中最复杂、最精密的子系统之一。Agent系统负责：

- **创建和管理子Agent**：当主Agent遇到复杂任务时，它可以"派遣"子Agent去独立完成工作
- **工具权限控制**：通过"三道门"机制精确控制每个子Agent能使用哪些工具
- **上下文隔离**：确保子Agent不会意外污染主Agent的状态
- **Fork机制**：一种特殊的子Agent创建方式，继承父级对话上下文以共享Prompt缓存
- **内置Agent体系**：Explore、Plan、GeneralPurpose等专用Agent的设计与实现
- **持久化记忆**：Agent级别的持久化记忆系统，支持user/project/local三种作用域

完成本章学习后，你将能够：

1. 理解 `AgentTool` 的完整执行流程（从调用到返回结果）
2. 掌握 `filterToolsForAgent` 的三道门过滤机制及其设计哲学
3. 理解 `createSubagentContext` 的四维隔离设计
4. 分析 `Fork Subagent` 的缓存共享策略及其对API成本的影响
5. 了解 `CacheSafeParams` 类型的五个关键字段及其缓存语义
6. 掌握内置Agent（Explore、Plan、GeneralPurpose）的设计理念和工具限制
7. 理解Agent记忆系统的三种作用域和持久化机制
8. 了解Agent Swarms（多Agent协作）的门控机制

---

## 2. 前置知识

在阅读本章之前，你需要了解以下概念：

| 前置知识 | 说明 | 对应章节 |
|---------|------|---------|
| Tool系统基础 | `Tool` 接口、`ToolUseContext` 类型 | 第05章 |
| 消息系统 | `Message` 类型、`AssistantMessage`、`UserMessage` | 第03章 |
| 查询循环 | `query()` 函数、API调用流程 | 第03章 |
| 状态管理 | `AppState`、`setAppState` 模式 | 第08章 |
| 缓存机制 | Anthropic API的Prompt缓存、cache key组成 | 第09章 |

### 关键类型速查

```typescript
// ToolUseContext —— 贯穿本章的核心上下文对象
type ToolUseContext = {
  options: Options           // 全局选项（tools、model、agentDefinitions等）
  messages: Message[]        // 当前对话历史
  readFileState: Map<...>    // 文件读取缓存
  abortController: AbortController
  getAppState: () => AppState
  setAppState: (f: (prev: AppState) => AppState) => void
  setAppStateForTasks: (f: (prev: AppState) => AppState) => void
  queryTracking: { chainId: string; depth: number }
  renderedSystemPrompt?: string  // 已渲染的system prompt（Fork使用）
  toolUseId?: string
  // ... 更多字段
}

// AgentDefinition —— Agent定义的基础类型
type AgentDefinition = {
  agentType: string           // Agent类型标识符
  whenToUse: string           // 何时使用此Agent的描述
  tools?: string[]            // 允许的工具列表（白名单）
  disallowedTools?: string[]  // 禁用的工具列表（黑名单）
  model?: string              // 模型覆盖（'inherit'继承父级）
  permissionMode?: PermissionMode  // 权限模式
  maxTurns?: number           // 最大轮次数
  source: 'built-in' | SettingSource | 'plugin'  // 来源
  getSystemPrompt: (params?) => string  // 系统提示词生成器
  background?: boolean        // 是否始终后台运行
  memory?: 'user' | 'project' | 'local'  // 持久化记忆作用域
  isolation?: 'worktree' | 'remote'  // 隔离模式
  // ... 更多字段
}
```

---

## 3. 宏观概览

### 3.1 Agent系统架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                         主Agent (Main Loop)                      │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐       │
│  │ query()循环   │───▶│ AgentTool    │───▶│ runAgent()   │       │
│  │              │    │ call()       │    │              │       │
│  └──────────────┘    └──────┬───────┘    └──────┬───────┘       │
│                             │                    │               │
│              ┌──────────────┼────────────────────┤               │
│              ▼              ▼                    ▼               │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │ Agent定义加载 │  │ 工具过滤     │  │ 子Agent执行   │          │
│  │ loadAgentsDir│  │ filterTools  │  │ runAgent     │          │
│  │              │  │ ForAgent     │  │              │          │
│  │ 3层来源:     │  │              │  │              │          │
│  │ - built-in   │  │ 三道门:      │  │ 3种模式:     │          │
│  │ - plugin     │  │ 1.MCP白名单  │  │ - 同步前台   │          │
│  │ - custom     │  │ 2.全局禁用   │  │ - 异步后台   │          │
│  │   (md/json)  │  │ 3.异步限制   │  │ - Fork继承   │          │
│  └──────────────┘  └──────────────┘  └──────┬───────┘          │
│                                              │                  │
│                              ┌───────────────┼──────────────┐   │
│                              ▼               ▼              ▼   │
│                    ┌──────────────┐ ┌────────────┐ ┌──────────┐ │
│                    │ 同步执行     │ │ 异步执行    │ │ Fork执行  │ │
│                    │ (foreground) │ │ (background)│ │(继承上下文)│ │
│                    │              │ │            │ │          │ │
│                    │ 逐消息迭代   │ │ fire-and-  │ │ 共享     │ │
│                    │ 可后台化     │ │ forget     │ │ Prompt   │ │
│                    │              │ │ 通知完成   │ │ 缓存     │ │
│                    └──────────────┘ └────────────┘ └──────────┘ │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              createSubagentContext (四维隔离)              │   │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐   │   │
│  │  │文件状态   │ │Abort控制 │ │状态回调   │ │UI回调    │   │   │
│  │  │cloneFile │ │create    │ │setApp    │ │setTool   │   │   │
│  │  │StateCache│ │Child     │ │State:    │ │JSX:      │   │   │
│  │  │()        │ │Abort     │ │no-op     │ │undefined │   │   │
│  │  │          │ │Controller│ │          │ │          │   │   │
│  │  └──────────┘ └──────────┘ └──────────┘ └──────────┘   │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              CacheSafeParams (缓存安全参数)                │   │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐   │   │
│  │  │system    │ │user      │ │system    │ │toolUse   │   │   │
│  │  │Prompt    │ │Context   │ │Context   │ │Context   │   │   │
│  │  └──────────┘ └──────────┘ └──────────┘ └──────────┘   │   │
│  │  ┌──────────────────────────────────────────────────┐   │   │
│  │  │ forkContextMessages (父级对话历史前缀)             │   │   │
│  │  └──────────────────────────────────────────────────┘   │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

### 3.2 核心文件清单

| 文件 | 职责 | 关键导出 |
|------|------|---------|
| `AgentTool.tsx` | AgentTool定义、call()入口、同步/异步/Fork路由 | `AgentTool` |
| `agentToolUtils.ts` | filterToolsForAgent、resolveAgentTools、finalizeAgentTool、runAsyncAgentLifecycle | `filterToolsForAgent`, `finalizeAgentTool` |
| `forkSubagent.ts` | FORK_AGENT定义、buildForkedMessages、递归保护、worktree通知 | `FORK_AGENT`, `buildForkedMessages`, `isForkSubagentEnabled` |
| `loadAgentsDir.ts` | Agent定义加载（built-in/custom/plugin）、Markdown/JSON解析 | `getAgentDefinitionsWithOverrides`, `parseAgentFromMarkdown` |
| `constants.ts` | AGENT_TOOL_NAME、ONE_SHOT类型 | `AGENT_TOOL_NAME`, `ONE_SHOT_BUILTIN_AGENT_TYPES` |
| `builtInAgents.ts` | 内置Agent注册 | `getBuiltInAgents` |
| `prompt.ts` | AgentTool的prompt生成 | `getPrompt`, `shouldInjectAgentListInMessages` |
| `agentMemory.ts` | Agent持久化记忆系统 | `loadAgentMemoryPrompt`, `getAgentMemoryDir` |
| `agentColorManager.ts` | Agent颜色管理（UI显示） | `setAgentColor`, `getAgentColor` |
| `utils/forkedAgent.ts` | CacheSafeParams类型、createSubagentContext、runForkedAgent | `CacheSafeParams`, `createSubagentContext`, `runForkedAgent` |
| `utils/agentSwarmsEnabled.ts` | Agent Swarms特性开关 | `isAgentSwarmsEnabled` |

### 3.3 内置Agent一览

| Agent类型 | 模型 | 工具限制 | 特点 |
|----------|------|---------|------|
| `general-purpose` | 默认子Agent模型 | `tools: ['*']` 全部工具 | 通用Agent，处理搜索、分析、多步任务 |
| `Explore` | haiku（外部）/ inherit（内部） | 禁用Agent/Edit/Write/Notebook | 只读搜索专家，omitClaudeMd省token |
| `Plan` | inherit | 禁用Agent/Edit/Write/Notebook | 只读架构规划专家 |
| `verification` | - | 特定工具 | 验证Agent（实验性） |
| `claude-code-guide` | - | - | Claude Code使用指南 |
| `statusline-setup` | - | - | 状态栏配置 |

---

## 4. 源码入口定位

### 4.1 AgentTool定义入口

**文件**: `src/tools/AgentTool/AgentTool.tsx`

AgentTool 通过 `buildTool()` 函数定义，是整个Agent系统的入口点。其核心结构：

```typescript
// AgentTool.tsx (简化结构，展示关键骨架)
export const AgentTool = buildTool({
  // 工具名称和别名
  name: AGENT_TOOL_NAME,  // 'Agent'
  searchHint: 'delegate work to a subagent',
  aliases: [LEGACY_AGENT_TOOL_NAME],  // 'Task' (旧名，向后兼容)
  maxResultSizeChars: 100_000,

  // prompt生成 — 动态构建工具描述，包含可用Agent列表
  async prompt({ agents, tools, getToolPermissionContext, allowedAgentTypes }) {
    const toolPermissionContext = await getToolPermissionContext()
    // MCP server过滤：只显示有工具的MCP server
    const mcpServersWithTools: string[] = []
    for (const tool of tools) {
      if (tool.name?.startsWith('mcp__')) {
        const parts = tool.name.split('__')
        const serverName = parts[1]
        if (serverName && !mcpServersWithTools.includes(serverName)) {
          mcpServersWithTools.push(serverName)
        }
      }
    }
    // Agent过滤：MCP依赖 → 权限拒绝
    const agentsWithMcpRequirementsMet = filterAgentsByMcpRequirements(agents, mcpServersWithTools)
    const filteredAgents = filterDeniedAgents(agentsWithMcpRequirementsMet, toolPermissionContext, AGENT_TOOL_NAME)
    return getPrompt(filteredAgents, isCoordinator, allowedAgentTypes)
  },

  // 输入Schema — 定义调用参数
  get inputSchema() { return inputSchema() },
  get outputSchema() { return outputSchema() },

  // 核心执行逻辑 — 约2000行的call()方法
  async call({ prompt, subagent_type, description, model, run_in_background, name, team_name, mode, isolation, cwd }, toolUseContext, ...) {
    // ... 完整的Agent创建和执行流程
  },

  // 其他Tool接口实现
  isReadOnly() { return true },  // 委托权限检查给底层工具
  isConcurrencySafe() { return true },
  mapToolResultToToolResultBlockParam(data, toolUseID) { ... },
  checkPermissions(input, context) { ... },
})
```

### 4.2 工具过滤入口

**文件**: `src/tools/AgentTool/agentToolUtils.ts`

```typescript
// agentToolUtils.ts:filterToolsForAgent — 三道门机制
export function filterToolsForAgent({
  tools, isBuiltIn, isAsync = false, permissionMode,
}: {
  tools: Tools; isBuiltIn: boolean; isAsync?: boolean; permissionMode?: PermissionMode;
}): Tools
```

### 4.3 子Agent上下文创建入口

**文件**: `src/utils/forkedAgent.ts`

```typescript
// forkedAgent.ts:createSubagentContext — 四维隔离
export function createSubagentContext(
  parentContext: ToolUseContext,
  overrides?: SubagentContextOverrides,
): ToolUseContext
```

### 4.4 Fork Subagent入口

**文件**: `src/tools/AgentTool/forkSubagent.ts`

```typescript
// forkSubagent.ts:FORK_AGENT定义 — Fork子Agent的"灵魂"
export const FORK_AGENT = {
  agentType: FORK_SUBAGENT_TYPE,  // 'fork'
  tools: ['*'],
  maxTurns: 200,
  model: 'inherit',
  permissionMode: 'bubble',
  source: 'built-in',
  baseDir: 'built-in',
  getSystemPrompt: () => '',  // 未使用：Fork路径传递override.systemPrompt
} satisfies BuiltInAgentDefinition
```

### 4.5 Agent定义加载入口

**文件**: `src/tools/AgentTool/loadAgentsDir.ts`

```typescript
// loadAgentsDir.ts — 异步加载所有Agent定义（带memoize缓存）
export const getAgentDefinitionsWithOverrides = memoize(
  async (cwd: string): Promise<AgentDefinitionsResult> => {
    // 1. 加载自定义Agent（Markdown文件）
    const markdownFiles = await loadMarkdownFilesForSubdir('agents', cwd)
    const customAgents = markdownFiles.map(({ filePath, baseDir, frontmatter, content, source }) =>
      parseAgentFromMarkdown(filePath, baseDir, frontmatter, content, source)
    ).filter(agent => agent !== null)

    // 2. 加载插件Agent
    const pluginAgents = await loadPluginAgents()

    // 3. 加载内置Agent
    const builtInAgents = getBuiltInAgents()

    // 4. 合并并确定活跃Agent（优先级覆盖）
    const allAgentsList = [...builtInAgents, ...pluginAgents, ...customAgents]
    const activeAgents = getActiveAgentsFromList(allAgentsList)

    return { activeAgents, allAgents: allAgentsList }
  }
)
```

---

## 5. 调用链分析

### 5.1 完整调用链（从用户请求到子Agent执行）

```
用户请求 "帮我重构这个模块"
    │
    ▼
主Agent决定使用AgentTool
    │
    ▼
AgentTool.call() [AgentTool.tsx]
    │
    ├─── 1. 获取AppState和permissionMode
    │      const appState = toolUseContext.getAppState()
    │      const permissionMode = appState.toolPermissionContext.mode
    │
    ├─── 2. 检查teammate模式限制
    │      if (team_name && !isAgentSwarmsEnabled()) throw Error
    │      if (isInProcessTeammate() && team_name && name) throw Error
    │
    ├─── 3. Agent解析与选择
    │      ├─ [Fork路径] isForkPath = !subagent_type && forkEnabled
    │      │   ├── 递归保护: isInForkChild()检查消息历史
    │      │   └── selectedAgent = FORK_AGENT
    │      └─ [正常路径] effectiveType = subagent_type ?? 'general-purpose'
    │          ├── filterDeniedAgents() → 权限过滤
    │          └── agents.find(type => type === effectiveType)
    │
    ├─── 4. 检查MCP server依赖
    │      hasRequiredMcpServers(selectedAgent, serversWithTools)
    │      // 等待pending MCP servers连接（最多30秒）
    │
    ├─── 5. 解析隔离模式和worktree
    │      if (effectiveIsolation === 'worktree')
    │        worktreeInfo = await createAgentWorktree(slug)
    │
    ├─── 6. 组装 runAgentParams
    │      { agentDefinition, promptMessages, toolUseContext,
    │        canUseTool, isAsync, querySource, model,
    │        availableTools: workerTools,  // 独立组装！
    │        forkContextMessages, useExactTools, ... }
    │
    ├─── 7. 决定执行模式
    │      shouldRunAsync = run_in_background || background || isCoordinator
    │                       || forceAsync || assistantForceAsync
    │                       && !isBackgroundTasksDisabled
    │
    ├─── [异步路径] ─────────────────────────────────────────────┐
    │    registerAsyncAgent() → 后台任务注册                      │
    │    void runAsyncAgentLifecycle() → fire-and-forget          │
    │    return { status: 'async_launched', agentId, ... }        │
    │                                                            │
    └─── [同步路径] ─────────────────────────────────────────────┤
         registerAgentForeground() → 前台任务注册（可后台化）     │
         runAgent()[Symbol.asyncIterator]() → 逐消息迭代          │
         │                                                      │
         ├── 每条消息: updateProgressFromMessage()              │
         ├── 后台竞争: Promise.race([msg, backgroundSignal])     │
         ├── 超时2秒: 显示BackgroundHint UI                     │
         ├── 被后台化: void继续执行, return async_launched       │
         └── 正常完成: finalizeAgentTool() → 返回结果           │
```

### 5.2 runAgent 函数内部调用链

```
runAgent() [runAgent.ts]
    │
    ├── 1. 构建Agent系统提示词
    │      ├── agent.getSystemPrompt({ toolUseContext })
    │      │     // 内置Agent: 返回预定义的系统提示词
    │      │     // 自定义Agent: 加载memory + 返回markdown内容
    │      │     // Fork: 使用override.systemPrompt (父级已渲染的字节)
    │      └── enhanceSystemPromptWithEnvDetails()
    │            // 添加环境详情：cwd、平台、日期等
    │
    ├── 2. 组装工具池
    │      ├── resolveAgentTools(agentDefinition, availableTools, isAsync, isMainThread)
    │      │     ├── filterToolsForAgent() ← 三道门
    │      │     │     ├── 第一道门: MCP工具白名单 (mcp__前缀直接放行)
    │      │     │     ├── 第二道门: ALL_AGENT_DISALLOWED_TOOLS
    │      │     │     └── 第三道门: CUSTOM_AGENT_DISALLOWED_TOOLS + ASYNC_AGENT_ALLOWED_TOOLS
    │      │     ├── disallowedTools黑名单过滤
    │      │     └── 返回 { resolvedTools, validTools, invalidTools, hasWildcard }
    │      └── 返回最终可用工具列表
    │
    ├── 3. 创建隔离上下文
    │      └── createSubagentContext(toolUseContext, overrides) ← 四维隔离
    │            ├── 文件状态: cloneFileStateCache()
    │            ├── Abort控制: createChildAbortController()
    │            ├── 状态回调: setAppState: () => {} (no-op)
    │            └── UI回调: setToolJSX: undefined
    │
    ├── 4. query() 循环
    │      ├── messages: [...forkContextMessages, ...promptMessages]
    │      ├── systemPrompt, userContext, systemContext
    │      ├── canUseTool, toolUseContext: isolatedContext
    │      ├── maxOutputTokensOverride, maxTurns
    │      └── skipCacheWrite (Fork fire-and-forget时跳过)
    │
    └── 5. 逐消息yield → AgentTool处理进度和结果
```

### 5.3 Fork vs 正常路径的分叉点

```
AgentTool.call() 入口
    │
    ▼
effectiveType = subagent_type ?? (forkEnabled ? undefined : 'general-purpose')
    │
    ├── effectiveType === undefined (Fork路径)
    │   │
    │   ├── 递归保护检查
    │   │   ├── querySource包含FORK_AGENT.agentType → 报错
    │   │   └── isInForkChild(messages) → 消息历史扫描 → 报错
    │   │
    │   ├── selectedAgent = FORK_AGENT
    │   │   { tools:['*'], model:'inherit', permissionMode:'bubble' }
    │   │
    │   ├── 系统提示词
    │   │   forkParentSystemPrompt = toolUseContext.renderedSystemPrompt
    │   │                              ?? buildEffectiveSystemPrompt(...)
    │   │   // 使用父级已渲染的字节，避免GrowthBook状态变化导致缓存失效
    │   │
    │   └── 消息构建
    │       promptMessages = buildForkedMessages(prompt, assistantMessage)
    │       // [fullAssistantMessage, user(placeholder_results + directive)]
    │
    └── effectiveType !== undefined (正常路径)
        │
        ├── selectedAgent = agents.find(type === effectiveType)
        │
        ├── 系统提示词
        │   enhancedSystemPrompt = [agent.getSystemPrompt(), envDetails]
        │   // 每次重新生成（可能因GrowthBook状态不同而变化）
        │
        └── 消息构建
            promptMessages = [createUserMessage({ content: prompt })]
            // 简单的用户消息
```

---

## 6. 核心源码解析

### 6.1 AgentTool.call() — 核心执行逻辑

`AgentTool.tsx` 中的 `call()` 方法是整个Agent系统的调度中心。让我们逐段分析其关键逻辑：

#### 6.1.1 Agent解析与选择

```typescript
// AgentTool.tsx — call() 方法内部（简化展示关键逻辑）

// Fork subagent 实验路由：
// - subagent_type 设置：使用指定类型（显式选择）
// - subagent_type 省略 + fork 实验开启：走 fork 路径（继承上下文）
// - subagent_type 省略 + fork 实验关闭：使用 general-purpose（默认）
const effectiveType =
  subagent_type ??
  (isForkSubagentEnabled() ? undefined : GENERAL_PURPOSE_AGENT.agentType)
const isForkPath = effectiveType === undefined

let selectedAgent: AgentDefinition
if (isForkPath) {
  // 递归 fork 保护：fork 子级保留了 Agent 工具（保持缓存一致），
  // 所以在调用时通过检测对话历史中的 fork 标记来拒绝递归 fork。
  // 主要检查是 querySource（编译时确定），消息扫描是兜底。
  if (
    toolUseContext.options.querySource ===
      `agent:builtin:${FORK_AGENT.agentType}` ||
    isInForkChild(toolUseContext.messages)
  ) {
    throw new Error(
      'Fork is not available inside a forked worker. Complete your task directly using your tools.',
    )
  }
  selectedAgent = FORK_AGENT
} else {
  // 常规路径：过滤被权限规则拒绝的Agent
  const allAgents = toolUseContext.options.agentDefinitions.activeAgents
  const { allowedAgentTypes } = toolUseContext.options.agentDefinitions
  const agents = filterDeniedAgents(
    allowedAgentTypes
      ? allAgents.filter(a => allowedAgentTypes.includes(a.agentType))
      : allAgents,
    appState.toolPermissionContext,
    AGENT_TOOL_NAME,
  )
  const found = agents.find(agent => agent.agentType === effectiveType)
  if (!found) {
    // 检查是否存在但被拒绝 — 提供详细的错误信息
    const agentExistsButDenied = allAgents.find(
      agent => agent.agentType === effectiveType,
    )
    if (agentExistsButDenied) {
      const denyRule = getDenyRuleForAgent(
        appState.toolPermissionContext,
        AGENT_TOOL_NAME,
        effectiveType,
      )
      throw new Error(
        `Agent type '${effectiveType}' has been denied by permission rule ` +
        `'${AGENT_TOOL_NAME}(${effectiveType})' from ${denyRule?.source ?? 'settings'}.`,
      )
    }
    throw new Error(
      `Agent type '${effectiveType}' not found. Available agents: ${agents.map(a => a.agentType).join(', ')}`,
    )
  }
  selectedAgent = found
}
```

**设计要点**：
1. **Fork路径与正常路径的分流**完全基于 `subagent_type` 是否省略 + 实验开关
2. **递归Fork保护**通过两种机制：`querySource` 检查（编译时确定）和消息历史扫描（运行时兜底）
3. **权限拒绝**提供详细的错误信息，帮助用户理解为什么Agent不可用

#### 6.1.2 MCP Server依赖检查

```typescript
// AgentTool.tsx — MCP server依赖检查

// 检查Agent所需的MCP server是否有工具可用
const requiredMcpServers = selectedAgent.requiredMcpServers

if (requiredMcpServers?.length) {
  // 如果有required servers还在pending状态，等待它们连接
  const hasPendingRequiredServers = appState.mcp.clients.some(
    c => c.type === 'pending' &&
         requiredMcpServers.some(pattern =>
           c.name.toLowerCase().includes(pattern.toLowerCase()),
         ),
  )

  let currentAppState = appState
  if (hasPendingRequiredServers) {
    const MAX_WAIT_MS = 30_000
    const POLL_INTERVAL_MS = 500
    const deadline = Date.now() + MAX_WAIT_MS

    while (Date.now() < deadline) {
      await sleep(POLL_INTERVAL_MS)
      currentAppState = toolUseContext.getAppState()

      // 早期退出：如果任何required server已经失败
      const hasFailedRequiredServer = currentAppState.mcp.clients.some(
        c => c.type === 'failed' &&
             requiredMcpServers.some(pattern =>
               c.name.toLowerCase().includes(pattern.toLowerCase()),
             ),
      )
      if (hasFailedRequiredServer) break

      const stillPending = currentAppState.mcp.clients.some(
        c => c.type === 'pending' &&
             requiredMcpServers.some(pattern =>
               c.name.toLowerCase().includes(pattern.toLowerCase()),
             ),
      )
      if (!stillPending) break
    }
  }

  // 检查是否有实际工具可用（已连接且已认证的server）
  const serversWithTools: string[] = []
  for (const tool of currentAppState.mcp.tools) {
    if (tool.name?.startsWith('mcp__')) {
      const parts = tool.name.split('__')
      const serverName = parts[1]
      if (serverName && !serversWithTools.includes(serverName)) {
        serversWithTools.push(serverName)
      }
    }
  }

  if (!hasRequiredMcpServers(selectedAgent, serversWithTools)) {
    const missing = requiredMcpServers.filter(
      pattern => !serversWithTools.some(server =>
        server.toLowerCase().includes(pattern.toLowerCase()),
      ),
    )
    throw new Error(
      `Agent '${selectedAgent.agentType}' requires MCP servers matching: ${missing.join(', ')}. ` +
      `MCP servers with tools: ${serversWithTools.length > 0 ? serversWithTools.join(', ') : 'none'}. ` +
      `Use /mcp to configure and authenticate the required MCP servers.`,
    )
  }
}
```

**设计要点**：
- 最多等待30秒让MCP server连接
- 如果任何required server已经失败，立即退出等待循环
- 提供详细的错误信息，指导用户如何配置MCP server

#### 6.1.3 工具池独立组装

```typescript
// AgentTool.tsx — 工具池独立组装

// 组装工人工具池（独立于父级）
// 工人始终从 assembleToolPool 获取工具，使用自己的权限模式，
// 所以不受父级工具限制影响。这里计算以避免 runAgent 导入 tools.ts
// （会创建循环依赖）。
const workerPermissionContext = {
  ...appState.toolPermissionContext,
  mode: selectedAgent.permissionMode ?? 'acceptEdits',
}
const workerTools = assembleToolPool(
  workerPermissionContext,
  appState.mcp.tools,
)
```

**关键洞察**：子Agent的工具池是**独立组装**的，不受父Agent工具限制的影响。这是通过传递 `workerPermissionContext` 实现的——子Agent可以有自己独立的 `permissionMode`。例如，Fork Agent的 `permissionMode: 'bubble'` 意味着权限提示会冒泡到父级终端。

#### 6.1.4 Fork路径的系统提示词处理

```typescript
// AgentTool.tsx — Fork路径的系统提示词

// Fork子级继承父级的system prompt（不是FORK_AGENT的）。
// 这是为了缓存一致的API请求前缀。
let enhancedSystemPrompt: string[] | undefined
let forkParentSystemPrompt:
  | ReturnType<typeof buildEffectiveSystemPrompt>
  | undefined

if (isForkPath) {
  if (toolUseContext.renderedSystemPrompt) {
    // 优先使用已渲染的system prompt字节
    forkParentSystemPrompt = toolUseContext.renderedSystemPrompt
  } else {
    // 回退：重新计算。可能与父级缓存的字节不同，
    // 如果GrowthBook状态在父级turn开始和fork spawn之间变化。
    const mainThreadAgentDefinition = appState.agent
      ? appState.agentDefinitions.activeAgents.find(
          a => a.agentType === appState.agent,
        )
      : undefined
    const additionalWorkingDirectories = Array.from(
      appState.toolPermissionContext.additionalWorkingDirectories.keys(),
    )
    const defaultSystemPrompt = await getSystemPrompt(
      toolUseContext.options.tools,
      toolUseContext.options.mainLoopModel,
      additionalWorkingDirectories,
      toolUseContext.options.mcpClients,
    )
    forkParentSystemPrompt = buildEffectiveSystemPrompt({
      mainThreadAgentDefinition,
      toolUseContext,
      customSystemPrompt: toolUseContext.options.customSystemPrompt,
      defaultSystemPrompt,
      appendSystemPrompt: toolUseContext.options.appendSystemPrompt,
    })
  }
  promptMessages = buildForkedMessages(prompt, assistantMessage)
} else {
  // 正常路径：构建Agent专属的系统提示词
  try {
    const additionalWorkingDirectories = Array.from(
      appState.toolPermissionContext.additionalWorkingDirectories.keys(),
    )
    const agentPrompt = selectedAgent.getSystemPrompt({ toolUseContext })
    if (selectedAgent.memory) {
      logEvent('tengu_agent_memory_loaded', {
        agent_type: selectedAgent.agentType,
        scope: selectedAgent.memory,
        source: 'subagent',
      })
    }
    enhancedSystemPrompt = await enhanceSystemPromptWithEnvDetails(
      [agentPrompt],
      resolvedAgentModel,
      additionalWorkingDirectories,
    )
  } catch (error) {
    logForDebugging(`Failed to get system prompt for agent ${selectedAgent.agentType}: ${errorMessage(error)}`)
  }
  promptMessages = [createUserMessage({ content: prompt })]
}
```

**关键设计**：
1. Fork路径优先使用 `toolUseContext.renderedSystemPrompt`（已渲染的字节），避免GrowthBook状态变化导致缓存失效
2. 正常路径每次都重新生成系统提示词，因为Agent可能有不同的memory配置

#### 6.1.5 同步 vs 异步执行的判定

```typescript
// AgentTool.tsx — 执行模式判定

// Fork subagent 实验：强制所有 spawn 异步，统一
// <task-notification> 交互模型（不仅仅是fork spawn）。
const forceAsync = isForkSubagentEnabled()

// Assistant模式：强制所有Agent异步。同步子Agent会持有
// 主循环的turn直到完成——daemon的inputQueue会回退，
// 第一个override cron的catch-up变成N个串行子Agent turn阻塞所有用户输入。
const assistantForceAsync = feature('KAIRIOS')
  ? appState.kairosEnabled
  : false

const shouldRunAsync =
  (run_in_background === true ||
    selectedAgent.background === true ||
    isCoordinator ||
    forceAsync ||
    assistantForceAsync ||
    (proactiveModule?.isProactiveActive() ?? false)) &&
  !isBackgroundTasksDisabled
```

**设计决策**：
- Fork实验开启时，**所有Agent都异步运行**，统一使用 `<task-notification>` 交互模型
- Assistant模式（Kairos）也强制异步，避免子Agent阻塞主循环的inputQueue
- `isBackgroundTasksDisabled` 环境变量可以全局禁用后台任务

### 6.2 filterToolsForAgent — 三道门机制

这是Agent系统中最精巧的设计之一。`filterToolsForAgent` 函数通过三道"门"来决定一个工具是否对子Agent可用。

```typescript
// agentToolUtils.ts
export function filterToolsForAgent({
  tools,
  isBuiltIn,
  isAsync = false,
  permissionMode,
}: {
  tools: Tools
  isBuiltIn: boolean
  isAsync?: boolean
  permissionMode?: PermissionMode
}): Tools {
  return tools.filter(tool => {
    // ===== 第一道门：MCP工具白名单 =====
    // MCP工具由外部服务器定义和控制，应始终放行
    if (tool.name.startsWith('mcp__')) {
      return true
    }

    // ===== 特殊通道：ExitPlanMode =====
    // 计划模式的Agent需要能退出计划模式
    // 这绕过了ALL_AGENT_DISALLOWED_TOOLS和异步工具过滤
    if (
      toolMatchesName(tool, EXIT_PLAN_MODE_V2_TOOL_NAME) &&
      permissionMode === 'plan'
    ) {
      return true
    }

    // ===== 第二道门：全Agent禁用列表 =====
    // ALL_AGENT_DISALLOWED_TOOLS: 对所有Agent生效的禁用列表
    // 例如：AgentTool本身（防止递归）、某些危险工具
    if (ALL_AGENT_DISALLOWED_TOOLS.has(tool.name)) {
      return false
    }

    // ===== 第三道门A：自定义Agent额外禁用 =====
    // CUSTOM_AGENT_DISALLOWED_TOOLS: 仅对非内置Agent生效
    // 内置Agent有更高的信任级别，不受此限制
    if (!isBuiltIn && CUSTOM_AGENT_DISALLOWED_TOOLS.has(tool.name)) {
      return false
    }

    // ===== 第三道门B：异步Agent工具白名单 =====
    // ASYNC_AGENT_ALLOWED_TOOLS: 仅对异步Agent生效的白名单
    // 后台Agent不能使用交互式工具（如权限确认对话框）
    if (isAsync && !ASYNC_AGENT_ALLOWED_TOOLS.has(tool.name)) {
      // 特殊豁免：in-process teammate可以使用AgentTool和任务工具
      if (isAgentSwarmsEnabled() && isInProcessTeammate()) {
        // Allow AgentTool for in-process teammates to spawn sync subagents.
        // Validation in AgentTool.call() prevents background agents and teammate spawning.
        if (toolMatchesName(tool, AGENT_TOOL_NAME)) {
          return true
        }
        // Allow task tools for in-process teammates to coordinate via shared task list
        if (IN_PROCESS_TEAMMATE_ALLOWED_TOOLS.has(tool.name)) {
          return true
        }
      }
      return false
    }

    return true
  })
}
```

#### 三道门详解

```
工具进入 filterToolsForAgent
    │
    ▼
┌─── 第一道门：MCP工具白名单 ───────────────────────────────┐
│  tool.name.startsWith('mcp__') → 直接放行 ✓              │
│                                                           │
│  设计理由：MCP工具由外部服务器定义，权限应由MCP服务器控制  │
│  影响：所有MCP工具对所有Agent可用                          │
└───────────────────────────────────────────────────────────┘
    │ (非MCP工具)
    ▼
┌─── 特殊通道：ExitPlanMode ────────────────────────────────┐
│  permissionMode === 'plan' && tool === EXIT_PLAN_MODE     │
│  → 直接放行 ✓                                             │
│                                                           │
│  设计理由：计划模式的Agent需要能退出计划模式               │
│  影响：绕过后续所有门控                                    │
└───────────────────────────────────────────────────────────┘
    │
    ▼
┌─── 第二道门：ALL_AGENT_DISALLOWED_TOOLS ──────────────────┐
│  全局禁用列表，对所有Agent生效                             │
│  → 拒绝 ✗                                                │
│                                                           │
│  设计理由：某些工具对所有子Agent都不安全                   │
│  例如：AgentTool本身（防止递归spawn）                     │
└───────────────────────────────────────────────────────────┘
    │ (通过)
    ▼
┌─── 第三道门A：自定义Agent额外限制 ────────────────────────┐
│  !isBuiltIn && CUSTOM_AGENT_DISALLOWED_TOOLS.has(tool)    │
│  → 拒绝 ✗                                                │
│                                                           │
│  设计理由：内置Agent有更高的信任级别                       │
│  内置Agent（Explore、Plan等）由Anthropic维护，更可信       │
│  自定义Agent可能来自用户项目，需要额外限制                 │
└───────────────────────────────────────────────────────────┘
    │ (通过)
    ▼
┌─── 第三道门B：异步Agent工具白名单 ────────────────────────┐
│  isAsync && !ASYNC_AGENT_ALLOWED_TOOLS.has(tool)          │
│  → 拒绝 ✗                                                │
│                                                           │
│  设计理由：后台Agent不能使用交互式工具                     │
│  例如：权限确认对话框在后台没有终端交互                    │
│                                                           │
│  特殊豁免:                                                │
│  - in-process teammate 可用 AgentTool（spawn同步子Agent） │
│  - in-process teammate 可用任务协调工具                    │
└───────────────────────────────────────────────────────────┘
    │ (通过)
    ▼
  工具可用 ✓
```

**设计哲学**：
1. **MCP工具优先放行**：外部MCP服务器的工具应该由MCP服务器自己控制权限，Claude Code不应二次过滤
2. **内置 vs 自定义的差异化信任**：内置Agent（如Explore、Plan）由Anthropic维护，有更高的信任级别
3. **异步Agent的严格限制**：后台运行的Agent不能使用交互式工具（如权限确认对话框），因为没有终端交互
4. **特殊通道的设计**：ExitPlanMode需要绕过两道门，因为计划模式Agent的唯一退出方式就是使用这个工具

### 6.3 createSubagentContext — 四维隔离

`createSubagentContext` 是子Agent隔离的核心实现。它通过四个维度确保子Agent不会干扰主Agent的状态。

```typescript
// utils/forkedAgent.ts — createSubagentContext 完整实现

export function createSubagentContext(
  parentContext: ToolUseContext,
  overrides?: SubagentContextOverrides,
): ToolUseContext {

  // ===== 维度1：AbortController 隔离 =====
  // 优先级：explicit override > share parent's > new child controller
  const abortController =
    overrides?.abortController ??
    (overrides?.shareAbortController
      ? parentContext.abortController
      : createChildAbortController(parentContext.abortController))
  // 默认行为：创建子控制器
  // - 父级abort → 传播到子级（子级也会abort）
  // - 子级abort → 不影响父级

  // ===== 维度2：AppState 访问隔离 =====
  // getAppState: 子级看到的AppState应设置shouldAvoidPermissionPrompts
  // 除非是交互式子Agent（shareAbortController=true）
  const getAppState: ToolUseContext['getAppState'] = overrides?.getAppState
    ? overrides.getAppState
    : overrides?.shareAbortController
      ? parentContext.getAppState  // 交互式子Agent：共享父级的AppState
      : () => {
          const state = parentContext.getAppState()
          if (state.toolPermissionContext.shouldAvoidPermissionPrompts) {
            return state
          }
          return {
            ...state,
            toolPermissionContext: {
              ...state.toolPermissionContext,
              shouldAvoidPermissionPrompts: true,  // 子Agent避免权限提示
            },
          }
        }

  return {
    // ===== 维度1续：文件状态克隆 =====
    // cloneFileStateCache: 深拷贝文件读取缓存
    // 子Agent的文件读取不会污染父级的缓存
    readFileState: cloneFileStateCache(
      overrides?.readFileState ?? parentContext.readFileState,
    ),

    // 全新集合：防止子Agent的memory/dynamicSkill污染父级
    nestedMemoryAttachmentTriggers: new Set<string>(),
    loadedNestedMemoryPaths: new Set<string>(),
    dynamicSkillDirTriggers: new Set<string>(),
    // 每个子Agent独立追踪发现的skills（用于was_discovered遥测）
    discoveredSkillNames: new Set<string>(),
    toolDecisions: undefined,

    // ===== 维度2续：内容替换状态克隆 =====
    // 默认克隆（不是全新创建），原因：
    // 缓存共享的fork需要处理包含父级tool_use_id的消息。
    // 全新状态会将它们视为未见过的，做出不同的替换决策，
    // 导致wire prefix不一致 → 缓存不命中。
    // 克隆做出相同决策 → 缓存命中。
    contentReplacementState:
      overrides?.contentReplacementState ??
      (parentContext.contentReplacementState
        ? cloneContentReplacementState(parentContext.contentReplacementState)
        : undefined),

    // AbortController（已在上面确定）
    abortController,

    // ===== 维度3：状态变更回调隔离 =====
    getAppState,
    // setAppState: 默认no-op，子Agent的AppState变更不影响父级
    setAppState: overrides?.shareSetAppState
      ? parentContext.setAppState
      : () => {},
    // 关键：setAppStateForTasks始终指向根store
    // 即使setAppState是no-op，任务注册/销毁也必须到达根store
    // 否则异步子Agent的后台bash任务会成为僵尸进程（PPID=1）
    setAppStateForTasks:
      parentContext.setAppStateForTasks ?? parentContext.setAppState,
    // 异步子Agent需要独立的denial tracking状态
    // 因为setAppState是no-op，denial计数器无法通过AppState累积
    localDenialTracking: overrides?.shareSetAppState
      ? parentContext.localDenialTracking
      : createDenialTrackingState(),

    // ===== 维度4：UI回调隔离 =====
    // 进度追踪：默认no-op
    setInProgressToolUseIDs: () => {},
    // 响应长度：默认no-op（除非显式共享）
    setResponseLength: overrides?.shareSetResponseLength
      ? parentContext.setResponseLength
      : () => {},
    pushApiMetricsEntry: overrides?.shareSetResponseLength
      ? parentContext.pushApiMetricsEntry
      : undefined,
    updateFileHistoryState: () => {},
    // Attribution是函数式的(prev => next)，安全共享
    updateAttributionState: parentContext.updateAttributionState,

    // UI回调：子Agent不能控制父级UI
    addNotification: undefined,
    setToolJSX: undefined,
    setStreamMode: undefined,
    setSDKStatus: undefined,
    openMessageSelector: undefined,

    // 可覆盖字段
    options: overrides?.options ?? parentContext.options,
    messages: overrides?.messages ?? parentContext.messages,
    // 每个子Agent应有自己的ID
    agentId: overrides?.agentId ?? createAgentId(),
    agentType: overrides?.agentType,

    // 新的查询追踪链：depth递增
    queryTracking: {
      chainId: randomUUID(),
      depth: (parentContext.queryTracking?.depth ?? -1) + 1,
    },
    fileReadingLimits: parentContext.fileReadingLimits,
    userModified: parentContext.userModified,
    criticalSystemReminder_EXPERIMENTAL:
      overrides?.criticalSystemReminder_EXPERIMENTAL,
    requireCanUseTool: overrides?.requireCanUseTool,
  }
}
```

#### 四维隔离详解

| 维度 | 隔离策略 | 默认行为 | 可选共享 | 设计理由 |
|------|---------|---------|---------|---------|
| **文件状态** | `cloneFileStateCache()` 深拷贝 | 子Agent有独立的文件读取缓存 | `readFileState` override | 防止子Agent的文件读取污染父级缓存 |
| **Abort控制** | `createChildAbortController()` 子控制器 | 父级abort传播到子级，子级不影响父级 | `shareAbortController: true` | 父级取消应级联到子级，但子级不应影响父级 |
| **状态回调** | `setAppState: () => {}` no-op | 子Agent的AppState变更不影响父级 | `shareSetAppState: true` | 隔离子Agent的状态变更，防止意外污染 |
| **UI回调** | 全部设为 `undefined` 或 no-op | 子Agent无法控制父级的UI渲染 | `shareSetResponseLength: true` | 子Agent在后台运行，不应控制父级终端UI |

#### SubagentContextOverrides — 精细控制

```typescript
// utils/forkedAgent.ts — SubagentContextOverrides 类型

export type SubagentContextOverrides = {
  // 覆盖选项（如自定义tools、model）
  options?: ToolUseContext['options']
  // 覆盖agentId（用于有自己ID的子Agent）
  agentId?: AgentId
  // 覆盖agentType（用于特定类型的子Agent）
  agentType?: string
  // 覆盖消息数组
  messages?: Message[]
  // 覆盖readFileState（如使用全新缓存而非克隆）
  readFileState?: ToolUseContext['readFileState']
  // 覆盖abortController
  abortController?: AbortController
  // 覆盖getAppState函数
  getAppState?: ToolUseContext['getAppState']

  // 显式opt-in：共享父级的setAppState回调
  // @default false (isolated no-op)
  shareSetAppState?: boolean
  // 显式opt-in：共享父级的setResponseLength回调
  // @default false (isolated no-op)
  shareSetResponseLength?: boolean
  // 显式opt-in：共享父级的abortController
  // @default false (new controller linked to parent)
  shareAbortController?: boolean

  // 关键系统提醒：在每个user turn重新注入
  criticalSystemReminder_EXPERIMENTAL?: string
  // 当true时，即使hooks自动批准也必须调用canUseTool
  // 用于speculation的overlay文件路径重写
  requireCanUseTool?: boolean
  // 覆盖replacement state — 用于resumeAgentBackground
  // 从恢复的sidechain重建状态，确保相同结果被重新替换
  contentReplacementState?: ContentReplacementState
}
```

**使用模式**：

```typescript
// 模式1：完全隔离（后台Agent，如session memory）
const ctx = createSubagentContext(parentContext)

// 模式2：自定义选项和agentId（AgentTool异步Agent）
const ctx = createSubagentContext(parentContext, {
  options: customOptions,
  agentId: newAgentId,
  messages: initialMessages,
})

// 模式3：交互式子Agent（共享部分状态）
const ctx = createSubagentContext(parentContext, {
  options: customOptions,
  agentId: newAgentId,
  shareSetAppState: true,        // 共享AppState变更
  shareSetResponseLength: true,  // 共享响应长度追踪
  shareAbortController: true,    // 共享abort控制器
})
```

### 6.4 CacheSafeParams — 缓存安全参数

`CacheSafeParams` 是Fork Subagent机制的核心类型，它定义了哪些参数必须在父级和子级之间保持一致，以确保Prompt缓存命中。

```typescript
// utils/forkedAgent.ts
/**
 * Parameters that must be identical between the fork and parent API requests
 * to share the parent's prompt cache. The Anthropic API cache key is composed of:
 * system prompt, tools, model, messages (prefix), and thinking config.
 *
 * CacheSafeParams carries the first five. Thinking config is derived from the
 * inherited toolUseContext.options.thinkingConfig — but can be inadvertently
 * changed if the fork sets maxOutputTokens, which clamps budget_tokens in
 * claude.ts (but only for older models that do not use adaptive thinking).
 * See the maxOutputTokens doc on ForkedAgentParams.
 */
export type CacheSafeParams = {
  /** System prompt - must match parent for cache hits */
  systemPrompt: SystemPrompt

  /** User context - prepended to messages, affects cache */
  userContext: { [k: string]: string }

  /** System context - appended to system prompt, affects cache */
  systemContext: { [k: string]: string }

  /** Tool use context containing tools, model, and other options */
  toolUseContext: ToolUseContext

  /** Parent context messages for prompt cache sharing */
  forkContextMessages: Message[]
}
```

#### 五个字段的缓存语义

Anthropic API的缓存key由以下部分组成：

```
Cache Key = hash(
  system_prompt,      ← CacheSafeParams.systemPrompt
  tools,              ← CacheSafeParams.toolUseContext.options.tools
  model,              ← CacheSafeParams.toolUseContext.options.mainLoopModel
  messages (prefix),  ← CacheSafeParams.forkContextMessages
  thinking_config     ← 从toolUseContext.options.thinkingConfig派生
)
```

| 字段 | 缓存角色 | 不一致的后果 | 注意事项 |
|------|---------|-------------|---------|
| `systemPrompt` | 系统提示词（缓存key的第一部分） | 缓存完全失效 | Fork使用父级已渲染的字节 |
| `userContext` | 用户上下文（环境变量等，前置到消息） | 缓存失效 | 包含cwd、平台、日期等 |
| `systemContext` | 系统上下文（追加到system prompt） | 缓存失效 | 包含额外的系统级指令 |
| `toolUseContext` | 包含tools、model、thinkingConfig | 工具定义或模型变更导致失效 | Fork使用`useExactTools`继承父级工具 |
| `forkContextMessages` | 父级对话历史（作为消息前缀） | 前缀不一致导致缓存不命中 | Fork使用完整的父级对话历史 |

#### CacheSafeParams 的保存与复用

```typescript
// utils/forkedAgent.ts

// 模块级变量：保存最近一次的CacheSafeParams
let lastCacheSafeParams: CacheSafeParams | null = null

export function saveCacheSafeParams(params: CacheSafeParams | null): void {
  lastCacheSafeParams = params
}

export function getLastCacheSafeParams(): CacheSafeParams | null {
  return lastCacheSafeParams
}
```

这个设计支持**后轮次fork**（如promptSuggestion、postTurnSummary、`/btw`命令），这些fork在主循环的turn结束后执行，需要共享主循环的Prompt缓存。注释说明：

```
// Slot written by handleStopHooks after each turn so post-turn forks
// (promptSuggestion, postTurnSummary, /btw) can share the main loop's
// prompt cache without each caller threading params through.
```

#### maxOutputTokens的缓存陷阱

文档中特别警告了一个陷阱：

```typescript
/**
 * Optional cap on output tokens. CAUTION: setting this changes both max_tokens
 * AND budget_tokens (via clamping in claude.ts). If the fork uses cacheSafeParams
 * to share the parent's prompt cache, a different budget_tokens will invalidate
 * the cache — thinking config is part of the cache key. Only set this when cache
 * sharing is not a goal (e.g., compact summaries).
 */
maxOutputTokens?: number
```

**教训**：`maxOutputTokens` 的变化会通过clamping影响 `budget_tokens`，而 `thinking_config`（包含 `budget_tokens`）是缓存key的一部分。所以Fork不应设置 `maxOutputTokens`，除非不需要缓存共享。

### 6.5 FORK_AGENT — Fork Subagent定义

```typescript
// tools/AgentTool/forkSubagent.ts

/**
 * Synthetic agent definition for the fork path.
 *
 * Not registered in builtInAgents — used only when `!subagent_type` and the
 * experiment is active. `tools: ['*']` with `useExactTools` means the fork
 * child receives the parent's exact tool pool (for cache-identical API
 * prefixes). `permissionMode: 'bubble'` surfaces permission prompts to the
 * parent terminal. `model: 'inherit'` keeps the parent's model for context
 * length parity.
 *
 * The getSystemPrompt here is unused: the fork path passes
 * `override.systemPrompt` with the parent's already-rendered system prompt
 * bytes, threaded via `toolUseContext.renderedSystemPrompt`. Reconstructing
 * by re-calling getSystemPrompt() can diverge (GrowthBook cold→warm) and
 * bust the prompt cache; threading the rendered bytes is byte-exact.
 */
export const FORK_AGENT = {
  agentType: FORK_SUBAGENT_TYPE,  // 'fork'
  whenToUse:
    'Implicit fork — inherits full conversation context. Not selectable via subagent_type; triggered by omitting subagent_type when the fork experiment is active.',
  tools: ['*'],           // 继承父级的完整工具池
  maxTurns: 200,
  model: 'inherit',       // 保持父级模型（上下文长度一致性）
  permissionMode: 'bubble', // 权限提示冒泡到父级终端
  source: 'built-in',
  baseDir: 'built-in',
  getSystemPrompt: () => '',  // 不使用：fork路径传递override.systemPrompt
} satisfies BuiltInAgentDefinition
```

**关键设计**：

1. **`tools: ['*']` + `useExactTools`**：Fork子级接收父级的**完全相同的工具池**，确保API前缀中的工具定义与父级完全一致。这不是"所有可用工具"，而是"父级的精确工具列表"

2. **`model: 'inherit'`**：保持父级模型，避免因模型不同导致：
   - 缓存失效（模型是缓存key的一部分）
   - 上下文长度不一致（不同模型有不同的上下文窗口）

3. **`permissionMode: 'bubble'`**：权限提示冒泡到父级终端。因为fork继承了父级的对话上下文，用户应该在父级终端看到权限请求

4. **`getSystemPrompt: () => ''`**：这个返回值**实际上不被使用**。Fork路径传递 `override.systemPrompt` 为父级已经渲染好的system prompt字节。重新调用 `getSystemPrompt()` 可能因为GrowthBook状态变化（cold→warm）而产生不同结果，破坏Prompt缓存

5. **不在builtInAgents中注册**：FORK_AGENT是"合成"的Agent定义，只在 `!subagent_type && forkEnabled` 时使用

### 6.6 buildForkedMessages — Fork消息构建

```typescript
// tools/AgentTool/forkSubagent.ts

/**
 * Build the forked conversation messages for the child agent.
 *
 * For prompt cache sharing, all fork children must produce byte-identical
 * API request prefixes. This function:
 * 1. Keeps the full parent assistant message (all tool_use blocks, thinking, text)
 * 2. Builds a single user message with tool_results for every tool_use block
 *    using an identical placeholder, then appends a per-child directive text block
 *
 * Result: [...history, assistant(all_tool_uses), user(placeholder_results..., directive)]
 * Only the final text block differs per child, maximizing cache hits.
 */
export function buildForkedMessages(
  directive: string,
  assistantMessage: AssistantMessage,
): MessageType[] {
  // 克隆assistant消息以避免修改原始数据，保留所有内容块
  // （thinking、text和所有tool_use）
  const fullAssistantMessage: AssistantMessage = {
    ...assistantMessage,
    uuid: randomUUID(),  // 新UUID避免与原始消息冲突
    message: {
      ...assistantMessage.message,
      content: [...assistantMessage.message.content],
    },
  }

  // 收集assistant消息中的所有tool_use块
  const toolUseBlocks = assistantMessage.message.content.filter(
    (block): block is BetaToolUseBlock => block.type === 'tool_use',
  )

  if (toolUseBlocks.length === 0) {
    logForDebugging(
      `No tool_use blocks found in assistant message for fork directive: ${directive.slice(0, 50)}...`,
      { level: 'error' },
    )
    return [
      createUserMessage({
        content: [{ type: 'text' as const, text: buildChildMessage(directive) }],
      }),
    ]
  }

  // 为每个tool_use构建tool_result块，全部使用相同的占位符文本
  // 关键：所有子级的占位符必须完全相同，以最大化缓存命中
  const toolResultBlocks = toolUseBlocks.map(block => ({
    type: 'tool_result' as const,
    tool_use_id: block.id,
    content: [
      {
        type: 'text' as const,
        text: FORK_PLACEHOLDER_RESULT,  // 'Fork started — processing in background'
      },
    ],
  }))

  // 构建单个user消息：所有占位符tool_results + 每个子级的独立directive
  const toolResultMessage = createUserMessage({
    content: [
      ...toolResultBlocks,
      {
        type: 'text' as const,
        text: buildChildMessage(directive),
      },
    ],
  })

  return [fullAssistantMessage, toolResultMessage]
}
```

**缓存优化策略**：

```
API请求前缀结构:
[...history, assistant(all_tool_uses), user(placeholder_results..., directive)]
    ↑ 完全相同       ↑ 完全相同            ↑ tool_result占位符相同   ↑ 仅此处不同
    ↑ 所有子级共享   ↑ 所有子级共享         ↑ 所有子级共享           ↑ 每个子级不同
```

- 所有fork子级共享**完全相同的**assistant消息和tool_result占位符
- 唯一不同的是每个子级的directive文本块（在user消息的末尾）
- 这最大化了Prompt缓存命中率，因为缓存是前缀匹配的

### 6.7 buildChildMessage — Fork子级指令

```typescript
// tools/AgentTool/forkSubagent.ts

export function buildChildMessage(directive: string): string {
  return `<${FORK_BOILERPLATE_TAG}>
STOP. READ THIS FIRST.

You are a forked worker process. You are NOT the main agent.

RULES (non-negotiable):
1. Your system prompt says "default to forking." IGNORE IT — that's for the parent.
   You ARE the fork. Do NOT spawn sub-agents; execute directly.
2. Do NOT converse, ask questions, or suggest next steps
3. Do NOT editorialize or add meta-commentary
4. USE your tools directly: Bash, Read, Write, etc.
5. If you modify files, commit your changes before reporting.
   Include the commit hash in your report.
6. Do NOT emit text between tool calls. Use tools silently, then report once at the end.
7. Stay strictly within your directive's scope. If you discover related systems
   outside your scope, mention them in one sentence at most — other workers
   cover those areas.
8. Keep your report under 500 words unless the directive specifies otherwise.
   Be factual and concise.
9. Your response MUST begin with "Scope:". No preamble, no thinking-out-loud.
10. REPORT structured facts, then stop

Output format (plain text labels, not markdown headers):
  Scope: <echo back your assigned scope in one sentence>
  Result: <the answer or key findings, limited to the scope above>
  Key files: <relevant file paths — include for research tasks>
  Files changed: <list with commit hash — include only if you modified files>
  Issues: <list — include only if there are issues to flag>
</${FORK_BOILERPLATE_TAG}>

${FORK_DIRECTIVE_PREFIX}${directive}`
}
```

**设计要点**：
1. **严格的行为约束**：Fork子级不能spawn子Agent（规则1）、不能提问（规则2）、不能编辑性评论（规则3）
2. **结构化输出格式**：确保返回结果可预测、可解析，便于父级处理
3. **Scope回显**：让父级确认子级理解了任务范围
4. **500字限制**：防止子Agent返回过长的报告，节省父级的上下文窗口
5. **BOILERPLATE_TAG**：用于 `isInForkChild()` 检测递归fork

### 6.8 内置Agent详细分析

#### 6.8.1 GeneralPurpose Agent

```typescript
// built-in/generalPurposeAgent.ts

const SHARED_PREFIX = `You are an agent for Claude Code, Anthropic's official CLI for Claude.
Given the user's message, you should use the tools available to complete the task.
Complete the task fully—don't gold-plate, but don't leave it half-done.`

const SHARED_GUIDELINES = `Your strengths:
- Searching for code, configurations, and patterns across large codebases
- Analyzing multiple files to understand system architecture
- Investigating complex questions that require exploring many files
- Performing multi-step research tasks

Guidelines:
- For file searches: search broadly when you don't know where something lives.
  Use Read when you know the specific file path.
- For analysis: Start broad and narrow down. Use multiple search strategies if
  the first doesn't yield results.
- Be thorough: Check multiple locations, consider different naming conventions,
  look for related files.
- NEVER create files unless they're absolutely necessary for achieving your goal.
  ALWAYS prefer editing an existing file to creating a new one.
- NEVER proactively create documentation files (*.md) or README files.
  Only create documentation files if explicitly requested.`

export const GENERAL_PURPOSE_AGENT: BuiltInAgentDefinition = {
  agentType: 'general-purpose',
  whenToUse: 'General-purpose agent for researching complex questions, searching for code, and executing multi-step tasks...',
  tools: ['*'],  // 所有工具可用
  source: 'built-in',
  baseDir: 'built-in',
  // model is intentionally omitted - uses getDefaultSubagentModel().
  getSystemPrompt: getGeneralPurposeSystemPrompt,
}
```

**设计特点**：
- `tools: ['*']`：通配符，接收经过 `filterToolsForAgent` 过滤后的所有工具
- 不设置model：使用 `getDefaultSubagentModel()` 获取默认子Agent模型
- 强调"不要过度工程化"和"不要主动创建文档文件"

#### 6.8.2 Explore Agent — 只读搜索专家

```typescript
// built-in/exploreAgent.ts

export const EXPLORE_AGENT: BuiltInAgentDefinition = {
  agentType: 'Explore',
  whenToUse: 'Fast agent specialized for exploring codebases...',
  disallowedTools: [
    AGENT_TOOL_NAME,         // 不能spawn子Agent
    EXIT_PLAN_MODE_TOOL_NAME,
    FILE_EDIT_TOOL_NAME,     // 不能编辑文件
    FILE_WRITE_TOOL_NAME,    // 不能写入文件
    NOTEBOOK_EDIT_TOOL_NAME, // 不能编辑notebook
  ],
  source: 'built-in',
  baseDir: 'built-in',
  // Ant用户: inherit（使用主Agent模型）
  // 外部用户: haiku（追求速度）
  model: process.env.USER_TYPE === 'ant' ? 'inherit' : 'haiku',
  // 只读Agent不需要CLAUDE.md中的commit/PR/lint规则
  // 主Agent有完整上下文，可以解释Explore的结果
  // 每周节省约5-15 Gtok（34M+ Explore运行/周）
  omitClaudeMd: true,
  getSystemPrompt: () => getExploreSystemPrompt(),
}
```

**设计特点**：
- **只读模式**：通过 `disallowedTools` 严格禁止文件修改
- **omitClaudeMd: true**：省略CLAUDE.md层次结构中的commit/PR/lint规则，节省token
- **模型选择**：内部用户用 `inherit`（质量），外部用户用 `haiku`（速度）
- **系统提示词**：包含严格的只读约束和高效搜索指南

Explore的系统提示词中有明确的只读约束：

```
=== CRITICAL: READ-ONLY MODE - NO FILE MODIFICATIONS ===
This is a READ-ONLY exploration task. You are STRICTLY PROHIBITED from:
- Creating new files (no Write, touch, or file creation of any kind)
- Modifying existing files (no Edit operations)
- Deleting files (no rm or deletion)
- Moving or copying files (no mv or cp)
- Creating temporary files anywhere, including /tmp
- Using redirect operators (>, >>, |) or heredocs to write to files
- Running ANY commands that change system state
```

#### 6.8.3 Plan Agent — 架构规划专家

```typescript
// built-in/planAgent.ts

export const PLAN_AGENT: BuiltInAgentDefinition = {
  agentType: 'Plan',
  whenToUse: 'Software architect agent for designing implementation plans...',
  disallowedTools: [
    AGENT_TOOL_NAME,
    EXIT_PLAN_MODE_TOOL_NAME,
    FILE_EDIT_TOOL_NAME,
    FILE_WRITE_TOOL_NAME,
    NOTEBOOK_EDIT_TOOL_NAME,
  ],
  source: 'built-in',
  tools: EXPLORE_AGENT.tools,  // 与Explore相同的工具集
  baseDir: 'built-in',
  model: 'inherit',
  omitClaudeMd: true,
  getSystemPrompt: () => getPlanV2SystemPrompt(),
}
```

**设计特点**：
- 与Explore类似的只读约束
- 专注于架构设计和实现规划
- 输出包含"Critical Files for Implementation"部分
- `tools: EXPLORE_AGENT.tools`：复用Explore的工具集定义

### 6.9 Agent记忆系统

Agent记忆系统为Agent提供持久化记忆能力，支持三种作用域：

```typescript
// agentMemory.ts

export type AgentMemoryScope = 'user' | 'project' | 'local'

/**
 * Returns the agent memory directory for a given agent type and scope.
 * - 'user' scope: <memoryBase>/agent-memory/<agentType>/
 *   适用于所有项目的通用记忆（如用户偏好）
 * - 'project' scope: <cwd>/.claude/agent-memory/<agentType>/
 *   项目特定记忆，可通过版本控制共享
 * - 'local' scope: <cwd>/.claude/agent-memory-local/<agentType>/
 *   本地项目记忆，不纳入版本控制
 */
export function getAgentMemoryDir(
  agentType: string,
  scope: AgentMemoryScope,
): string {
  const dirName = sanitizeAgentTypeForPath(agentType)
  switch (scope) {
    case 'project':
      return join(getCwd(), '.claude', 'agent-memory', dirName) + sep
    case 'local':
      return getLocalAgentMemoryDir(dirName)
    case 'user':
      return join(getMemoryBaseDir(), 'agent-memory', dirName) + sep
  }
}
```

**三种作用域对比**：

| 作用域 | 存储位置 | 版本控制 | 适用场景 |
|--------|---------|---------|---------|
| `user` | `~/.claude/agent-memory/<agentType>/` | 不纳入 | 用户级别的通用记忆（如编码偏好） |
| `project` | `.claude/agent-memory/<agentType>/` | 可纳入 | 项目特定记忆（团队共享） |
| `local` | `.claude/agent-memory-local/<agentType>/` | 不纳入 | 本地项目记忆（不共享） |

**记忆加载机制**：

```typescript
// agentMemory.ts

export function loadAgentMemoryPrompt(
  agentType: string,
  scope: AgentMemoryScope,
): string {
  let scopeNote: string
  switch (scope) {
    case 'user':
      scopeNote = '- Since this memory is user-scope, keep learnings general since they apply across all projects'
      break
    case 'project':
      scopeNote = '- Since this memory is project-scope and shared with your team via version control, tailor your memories to this project'
      break
    case 'local':
      scopeNote = '- Since this memory is local-scope (not checked into version control), tailor your memories to this project and machine'
      break
  }

  const memoryDir = getAgentMemoryDir(agentType, scope)
  // Fire-and-forget: 创建目录（同步回调中不能await）
  void ensureMemoryDirExists(memoryDir)

  return buildMemoryPrompt({
    displayName: 'Persistent Agent Memory',
    memoryDir,
    extraGuidelines: [scopeNote],
  })
}
```

**与自定义Agent的集成**：

在 `loadAgentsDir.ts` 中，当Agent定义包含 `memory` 字段时，系统会：
1. 自动注入 Write/Edit/Read 工具（如果Agent的tools列表中没有）
2. 在 `getSystemPrompt()` 中追加记忆提示词

```typescript
// loadAgentsDir.ts — parseAgentFromMarkdown

// 如果memory启用，注入Write/Edit/Read工具
if (isAutoMemoryEnabled() && memory && tools !== undefined) {
  const toolSet = new Set(tools)
  for (const tool of [FILE_WRITE_TOOL_NAME, FILE_EDIT_TOOL_NAME, FILE_READ_TOOL_NAME]) {
    if (!toolSet.has(tool)) {
      tools = [...tools, tool]
    }
  }
}

// getSystemPrompt中追加记忆提示
getSystemPrompt: () => {
  if (isAutoMemoryEnabled() && memory) {
    const memoryPrompt = loadAgentMemoryPrompt(agentType, memory)
    return systemPrompt + '\n\n' + memoryPrompt
  }
  return systemPrompt
},
```

### 6.10 Agent颜色管理系统

Agent颜色系统为UI中的Agent显示提供视觉区分：

```typescript
// agentColorManager.ts

export type AgentColorName =
  | 'red' | 'blue' | 'green' | 'yellow'
  | 'purple' | 'orange' | 'pink' | 'cyan'

export const AGENT_COLOR_TO_THEME_COLOR = {
  red: 'red_FOR_SUBAGENTS_ONLY',
  blue: 'blue_FOR_SUBAGENTS_ONLY',
  green: 'green_FOR_SUBAGENTS_ONLY',
  yellow: 'yellow_FOR_SUBAGENTS_ONLY',
  purple: 'purple_FOR_SUBAGENTS_ONLY',
  orange: 'orange_FOR_SUBAGENTS_ONLY',
  pink: 'pink_FOR_SUBAGENTS_ONLY',
  cyan: 'cyan_FOR_SUBAGENTS_ONLY',
} as const satisfies Record<AgentColorName, keyof Theme>
```

**设计细节**：
- 颜色名称映射到 `_FOR_SUBAGENTS_ONLY` 后缀的主题颜色，避免与主Agent的颜色冲突
- `general-purpose` Agent没有颜色（返回undefined），因为它是默认Agent
- 颜色通过 `agentColorMap`（AppState中的Map）管理，支持运行时动态设置

### 6.11 resolveAgentTools — 工具解析

`resolveAgentTools` 在 `filterToolsForAgent` 之上增加了Agent特定的工具解析逻辑：

```typescript
// agentToolUtils.ts

export function resolveAgentTools(
  agentDefinition: Pick<AgentDefinition, 'tools' | 'disallowedTools' | 'source' | 'permissionMode'>,
  availableTools: Tools,
  isAsync = false,
  isMainThread = false,
): ResolvedAgentTools {
  const { tools: agentTools, disallowedTools, source, permissionMode } = agentDefinition

  // 当isMainThread为true时，跳过filterToolsForAgent
  // 主线程的工具池已经由useMergedTools()正确组装
  const filteredAvailableTools = isMainThread
    ? availableTools
    : filterToolsForAgent({
        tools: availableTools,
        isBuiltIn: source === 'built-in',
        isAsync,
        permissionMode,
      })

  // 创建disallowedTools集合
  const disallowedToolSet = new Set(
    disallowedTools?.map(toolSpec => {
      const { toolName } = permissionRuleValueFromString(toolSpec)
      return toolName
    }) ?? [],
  )

  // 过滤掉被禁止的工具
  const allowedAvailableTools = filteredAvailableTools.filter(
    tool => !disallowedToolSet.has(tool.name),
  )

  // 如果tools未定义或为['*']，允许所有工具（经过disallowed过滤后）
  const hasWildcard =
    agentTools === undefined ||
    (agentTools.length === 1 && agentTools[0] === '*')
  if (hasWildcard) {
    return {
      hasWildcard: true,
      validTools: [],
      invalidTools: [],
      resolvedTools: allowedAvailableTools,
    }
  }

  // 逐个解析Agent指定的工具
  const availableToolMap = new Map<string, Tool>()
  for (const tool of allowedAvailableTools) {
    availableToolMap.set(tool.name, tool)
  }

  const validTools: string[] = []
  const invalidTools: string[] = []
  const resolved: Tool[] = []
  let allowedAgentTypes: string[] | undefined

  for (const toolSpec of agentTools) {
    const { toolName, ruleContent } = permissionRuleValueFromString(toolSpec)

    // 特殊处理：Agent工具携带allowedAgentTypes元数据
    if (toolName === AGENT_TOOL_NAME) {
      if (ruleContent) {
        // 解析逗号分隔的agent类型: "worker, researcher" → ["worker", "researcher"]
        allowedAgentTypes = ruleContent.split(',').map(s => s.trim())
      }
      // 对于子Agent，Agent被filterToolsForAgent排除
      // 标记spec有效（用于allowedAgentTypes追踪）但跳过工具解析
      if (!isMainThread) {
        validTools.push(toolSpec)
        continue
      }
      // 对于主线程，filterToolsForAgent被跳过，Agent在availableToolMap中
      // 正常解析
    }

    const tool = availableToolMap.get(toolName)
    if (tool) {
      validTools.push(toolSpec)
      if (!resolved.includes(tool)) {
        resolved.push(tool)
      }
    } else {
      invalidTools.push(toolSpec)
    }
  }

  return {
    hasWildcard: false,
    validTools,
    invalidTools,
    resolvedTools: resolved,
    allowedAgentTypes,
  }
}
```

**关键逻辑**：
1. **主线程跳过filterToolsForAgent**：主线程的工具池已经由 `useMergedTools()` 正确组装
2. **通配符处理**：`tools: ['*']` 或 `tools: undefined` 表示所有工具可用
3. **Agent工具的特殊处理**：`AGENT_TOOL_NAME` 携带 `allowedAgentTypes` 元数据，用于限制子Agent能spawn哪些类型的Agent

### 6.12 Agent定义加载系统

`loadAgentsDir.ts` 实现了三层Agent来源的加载和合并：

```typescript
// loadAgentsDir.ts — getActiveAgentsFromList

export function getActiveAgentsFromList(
  allAgents: AgentDefinition[],
): AgentDefinition[] {
  const builtInAgents = allAgents.filter(a => a.source === 'built-in')
  const pluginAgents = allAgents.filter(a => a.source === 'plugin')
  const userAgents = allAgents.filter(a => a.source === 'userSettings')
  const projectAgents = allAgents.filter(a => a.source === 'projectSettings')
  const managedAgents = allAgents.filter(a => a.source === 'policySettings')
  const flagAgents = allAgents.filter(a => a.source === 'flagSettings')

  // 合并顺序决定了优先级（后覆盖前）
  const agentGroups = [
    builtInAgents,    // 最低优先级
    pluginAgents,
    userAgents,
    projectAgents,
    flagAgents,
    managedAgents,    // 最高优先级
  ]

  const agentMap = new Map<string, AgentDefinition>()
  for (const agents of agentGroups) {
    for (const agent of agents) {
      agentMap.set(agent.agentType, agent)  // 后者覆盖前者
    }
  }
  return Array.from(agentMap.values())
}
```

**优先级链**（从低到高）：
```
built-in < plugin < userSettings < projectSettings < flagSettings < policySettings
```

这意味着：
- 项目可以覆盖内置Agent的定义
- 用户设置可以覆盖项目设置
- 策略设置（managed）有最高优先级

#### Markdown Agent定义格式

```typescript
// loadAgentsDir.ts — parseAgentFromMarkdown

/**
 * Parses agent definition from markdown file data.
 * Frontmatter fields:
 * - name: Agent类型标识符（必需）
 * - description: 何时使用此Agent（必需）
 * - tools: 允许的工具列表
 * - disallowedTools: 禁用的工具列表
 * - model: 模型覆盖（'inherit'继承父级）
 * - effort: 推理努力级别
 * - permissionMode: 权限模式
 * - maxTurns: 最大轮次数
 * - background: 是否始终后台运行
 * - memory: 持久化记忆作用域（'user'|'project'|'local'）
 * - isolation: 隔离模式（'worktree'|'remote'）
 * - color: UI显示颜色
 * - mcpServers: MCP server依赖
 * - hooks: 会话级hooks
 * - skills: 预加载的skill名称
 * - initialPrompt: 首轮用户消息前缀
 */
export function parseAgentFromMarkdown(
  filePath: string,
  baseDir: string,
  frontmatter: Record<string, unknown>,
  content: string,
  source: SettingSource,
): CustomAgentDefinition | null
```

### 6.13 Agent Swarms开关

```typescript
// utils/agentSwarmsEnabled.ts

/**
 * Centralized runtime check for agent teams/teammate features.
 * This is the single gate that should be checked everywhere teammates
 * are referenced (prompts, code, tools isEnabled, UI, etc.).
 *
 * Ant builds: always enabled.
 * External builds require both:
 * 1. Opt-in via CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS env var OR --agent-teams flag
 * 2. GrowthBook gate 'tengu_amber_flint' enabled (killswitch)
 */
export function isAgentSwarmsEnabled(): boolean {
  // Ant用户：始终启用
  if (process.env.USER_TYPE === 'ant') {
    return true
  }

  // 外部用户：需要环境变量或CLI标志启用
  if (
    !isEnvTruthy(process.env.CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS) &&
    !isAgentTeamsFlagSet()
  ) {
    return false
  }

  // Killswitch — 对外部用户始终生效
  if (!getFeatureValue_CACHED_MAY_BE_STALE('tengu_amber_flint', true)) {
    return false
  }

  return true
}
```

**三层门控**：
1. **Ant用户**：始终启用（内部开发和测试）
2. **外部用户**：需要显式opt-in（环境变量 `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS` 或CLI标志 `--agent-teams`）
3. **Killswitch**：GrowthBook特性开关 `tengu_amber_flint`，可随时远程关闭

### 6.14 prompt.ts — AgentTool的Prompt生成

```typescript
// prompt.ts — getPrompt函数

export async function getPrompt(
  agentDefinitions: AgentDefinition[],
  isCoordinator?: boolean,
  allowedAgentTypes?: string[],
): Promise<string> {
  // 过滤Agent
  const effectiveAgents = allowedAgentTypes
    ? agentDefinitions.filter(a => allowedAgentTypes.includes(a.agentType))
    : agentDefinitions

  // Fork子agent特性：当启用时，插入"When to fork"部分
  const forkEnabled = isForkSubagentEnabled()

  const whenToForkSection = forkEnabled ? `
## When to fork

Fork yourself (omit \`subagent_type\`) when the intermediate tool output isn't worth keeping in your context.
- **Research**: fork open-ended questions. If research can be broken into independent questions,
  launch parallel forks in one message.
- **Implementation**: prefer to fork implementation work that requires more than a couple of edits.

Forks are cheap because they share your prompt cache. Don't set \`model\` on a fork —
a different model can't reuse the parent's cache.
` : ''

  // ... 更多prompt构建逻辑

  // 关键优化：Agent列表注入方式
  const listViaAttachment = shouldInjectAgentListInMessages()
  // 当启用时，Agent列表通过<system-reminder>消息注入，而非内联在工具描述中
  // 这解决了动态Agent列表变化导致tools-block缓存失效的问题
  // 动态Agent列表曾占全fleet cache_creation tokens的~10.2%
  const agentListSection = listViaAttachment
    ? `Available agent types are listed in <system-reminder> messages in the conversation.`
    : `Available agent types and the tools they have access to:\n${effectiveAgents.map(agent => formatAgentLine(agent)).join('\n')}`
}
```

**关键优化**：Agent列表从工具描述中移出，改为通过 `<system-reminder>` 消息注入。这解决了MCP连接、插件变化、权限模式变更导致Agent列表变化、进而破坏tools-block Prompt缓存的问题。

---

### 6.15 runForkedAgent — Fork Agent执行器

`runForkedAgent` 是Fork Agent的执行引擎，它在隔离上下文中运行fork子级的查询循环：

```typescript
// utils/forkedAgent.ts

export async function runForkedAgent({
  promptMessages,
  cacheSafeParams,
  canUseTool,
  querySource,
  forkLabel,
  overrides,
  maxOutputTokens,
  maxTurns,
  onMessage,
  skipTranscript,
  skipCacheWrite,
}: ForkedAgentParams): Promise<ForkedAgentResult> {
  const startTime = Date.now()
  const outputMessages: Message[] = []
  let totalUsage: NonNullableUsage = { ...EMPTY_USAGE }

  const {
    systemPrompt,
    userContext,
    systemContext,
    toolUseContext,
    forkContextMessages,
  } = cacheSafeParams

  // 创建隔离上下文以防止父级状态的突变
  const isolatedToolUseContext = createSubagentContext(
    toolUseContext,
    overrides,
  )

  // 不要在这里filterIncompleteToolCalls — 它会在部分工具批次上
  // 丢弃整个assistant消息，孤立配对的results（API 400）。
  // 悬空的tool_uses由claude.ts中的ensureToolResultPairing修复，
  // 与主线程相同 — 相同的post-repair前缀保持缓存命中。
  const initialMessages: Message[] = [...forkContextMessages, ...promptMessages]

  // 生成agent ID并记录初始消息（用于transcript）
  const agentId = skipTranscript ? undefined : createAgentId(forkLabel)
  let lastRecordedUuid: UUID | null = null
  if (agentId) {
    await recordSidechainTranscript(initialMessages, agentId).catch(err =>
      logForDebugging(`Forked agent [${forkLabel}] failed to record initial transcript: ${err}`),
    )
    lastRecordedUuid =
      initialMessages.length > 0
        ? initialMessages[initialMessages.length - 1]!.uuid
        : null
  }

  // 运行查询循环（使用隔离的上下文，缓存安全参数保留）
  try {
    for await (const message of query({
      messages: initialMessages,
      systemPrompt,
      userContext,
      systemContext,
      canUseTool,
      toolUseContext: isolatedToolUseContext,
      querySource,
      maxOutputTokensOverride: maxOutputTokens,
      maxTurns,
      skipCacheWrite,
    })) {
      // 从message_delta流事件中提取实际usage
      if (message.type === 'stream_event') {
        if (
          'event' in message &&
          message.event?.type === 'message_delta' &&
          message.event.usage
        ) {
          const turnUsage = updateUsage({ ...EMPTY_USAGE }, message.event.usage)
          totalUsage = accumulateUsage(totalUsage, turnUsage)
        }
        continue
      }
      if (message.type === 'stream_request_start') {
        continue
      }

      outputMessages.push(message as Message)
      onMessage?.(message as Message)

      // 记录transcript
      const msg = message as Message
      if (
        agentId &&
        (msg.type === 'assistant' || msg.type === 'user' || msg.type === 'progress')
      ) {
        await recordSidechainTranscript([msg], agentId, lastRecordedUuid).catch(
          err => logForDebugging(`Forked agent [${forkLabel}] failed to record transcript: ${err}`),
        )
        if (msg.type !== 'progress') {
          lastRecordedUuid = msg.uuid
        }
      }
    }
  } finally {
    // 释放克隆的文件状态缓存内存
    isolatedToolUseContext.readFileState.clear()
    // 释放克隆的fork上下文消息
    initialMessages.length = 0
  }

  const durationMs = Date.now() - startTime

  // 记录fork查询指标
  logForkAgentQueryEvent({
    forkLabel,
    querySource,
    durationMs,
    messageCount: outputMessages.length,
    totalUsage,
    queryTracking: toolUseContext.queryTracking,
  })

  return {
    messages: outputMessages,
    totalUsage,
  }
}
```

**关键设计细节**：

1. **不调用filterIncompleteToolCalls**：注释解释了为什么不在fork开始时过滤不完整的工具调用。过滤会丢弃整个assistant消息，孤立配对的tool_result，导致API 400错误。悬空的tool_uses由下游的 `ensureToolResultPairing` 修复。

2. **finally块清理**：释放克隆的文件状态缓存和fork上下文消息，防止内存泄漏。

3. **cache hit rate计算**：`logForkAgentQueryEvent` 计算缓存命中率：
```typescript
const cacheHitRate =
  totalInputTokens > 0
    ? totalUsage.cache_read_input_tokens / totalInputTokens
    : 0
```

### 6.16 finalizeAgentTool — 结果最终化

`finalizeAgentTool` 将子Agent的原始消息序列转换为结构化的结果：

```typescript
// agentToolUtils.ts

export function finalizeAgentTool(
  agentMessages: MessageType[],
  agentId: string,
  metadata: {
    prompt: string
    resolvedAgentModel: string
    isBuiltInAgent: boolean
    startTime: number
    agentType: string
    isAsync: boolean
  },
): AgentToolResult {
  const lastAssistantMessage = getLastAssistantMessage(agentMessages)
  if (lastAssistantMessage === undefined) {
    throw new Error('No assistant messages found')
  }

  // 提取文本内容。如果最后的assistant消息是纯tool_use块
  // （循环在turn中间退出），回退到最近的有文本内容的assistant消息
  let content = lastAssistantMessage.message.content.filter(_ => _.type === 'text')
  if (content.length === 0) {
    for (let i = agentMessages.length - 1; i >= 0; i--) {
      const m = agentMessages[i]!
      if (m.type !== 'assistant') continue
      const textBlocks = m.message.content.filter(_ => _.type === 'text')
      if (textBlocks.length > 0) {
        content = textBlocks
        break
      }
    }
  }

  const totalTokens = getTokenCountFromUsage(lastAssistantMessage.message.usage)
  const totalToolUseCount = countToolUses(agentMessages)

  // 记录完成事件
  logEvent('tengu_agent_tool_completed', {
    agent_type: agentType,
    model: resolvedAgentModel,
    prompt_char_count: prompt.length,
    response_char_count: content.length,
    assistant_message_count: agentMessages.length,
    total_tool_uses: totalToolUseCount,
    duration_ms: Date.now() - startTime,
    total_tokens: totalTokens,
    is_built_in_agent: isBuiltInAgent,
    is_async: isAsync,
  })

  return {
    agentId,
    agentType,
    content,
    totalDurationMs: Date.now() - startTime,
    totalTokens,
    totalToolUseCount,
    usage: lastAssistantMessage.message.usage,
  }
}
```

**关键逻辑**：
1. **文本内容提取**：如果最后的assistant消息是纯tool_use块（循环在中间退出），回退到最近的有文本内容的assistant消息
2. **Usage追踪**：从最后的assistant消息中提取usage信息
3. **缓存淘汰提示**：发送 `tengu_cache_eviction_hint` 事件，告诉推理服务可以淘汰这个子Agent的缓存链

### 6.17 AgentToolResultSchema — 结果验证

AgentTool的结果通过Zod schema进行严格验证：

```typescript
// agentToolUtils.ts

export const agentToolResultSchema = lazySchema(() =>
  z.object({
    agentId: z.string(),
    agentType: z.string().optional(),
    content: z.array(z.object({ type: z.literal('text'), text: z.string() })),
    totalToolUseCount: z.number(),
    totalDurationMs: z.number(),
    totalTokens: z.number(),
    usage: z.object({
      input_tokens: z.number(),
      output_tokens: z.number(),
      cache_creation_input_tokens: z.number().nullable(),
      cache_read_input_tokens: z.number().nullable(),
      server_tool_use: z.object({
        web_search_requests: z.number(),
        web_fetch_requests: z.number(),
      }).nullable(),
      service_tier: z.enum(['standard', 'priority', 'batch']).nullable(),
      cache_creation: z.object({
        ephemeral_1h_input_tokens: z.number(),
        ephemeral_5m_input_tokens: z.number(),
      }).nullable(),
    }),
  }),
)
```

这个schema确保了结果结构的完整性，包括详细的usage信息（缓存token、服务器工具使用等）。

---

## 7. 架构设计思想

### 7.1 分层信任模型

Agent系统实现了一个清晰的分层信任模型：

```
信任层级（从高到低）：
┌─────────────────────────────────────────────────────────┐
│  主Agent (Main Loop)                                    │  ← 完全信任
│  - 所有工具可用                                          │
│  - 可以修改全局状态                                      │
│  - 可以控制UI                                           │
│  - 有完整的CLAUDE.md上下文                               │
├─────────────────────────────────────────────────────────┤
│  内置Agent (Built-in)                                   │  ← 高信任
│  - Explore, Plan, GeneralPurpose                        │
│  - 不受CUSTOM_AGENT_DISALLOWED_TOOLS限制                 │
│  - 一次性使用（Explore/Plan），不接受SendMessage          │
│  - omitClaudeMd节省token                                │
├─────────────────────────────────────────────────────────┤
│  自定义Agent (Custom/Plugin)                            │  ← 中等信任
│  - 受CUSTOM_AGENT_DISALLOWED_TOOLS限制                   │
│  - 可以接受SendMessage继续对话                           │
│  - 可能来自用户项目（需要额外限制）                       │
├─────────────────────────────────────────────────────────┤
│  异步Agent (Background)                                 │  ← 低信任
│  - 受ASYNC_AGENT_ALLOWED_TOOLS严格限制                  │
│  - 不能使用交互式工具                                    │
│  - 独立的abort控制器                                     │
│  - setAppState为no-op                                   │
└─────────────────────────────────────────────────────────┘
```

### 7.2 缓存友好的Fork设计

Fork Subagent的设计核心是**最大化Prompt缓存命中率**：

1. **System Prompt共享**：Fork使用父级已渲染的system prompt字节（不是重新生成），避免GrowthBook状态变化导致差异
2. **工具定义共享**：`tools: ['*']` + `useExactTools` 确保工具定义完全相同
3. **消息前缀共享**：`buildForkedMessages` 构建的消息中，只有最后一个directive文本块不同
4. **模型继承**：`model: 'inherit'` 确保模型配置一致
5. **thinking config继承**：通过 `toolUseContext.options.thinkingConfig` 继承
6. **Agent列表注入优化**：从工具描述移出到 `<system-reminder>` 消息，避免动态变化破坏缓存

### 7.3 事件驱动的异步模型

异步Agent使用事件驱动模型，实现fire-and-forget：

```
AgentTool.call()
    │
    ├── registerAsyncAgent() → 注册后台任务（AppState.tasks）
    │
    ├── void runAsyncAgentLifecycle() → fire-and-forget（不阻塞主循环）
    │   │
    │   ├── for await (message of makeStream()) → 消息流迭代
    │   │   ├── rootSetAppState() → 更新retain任务的messages
    │   │   ├── updateProgressFromMessage() → 进度追踪
    │   │   └── emitTaskProgress() → VS Code子Agent面板
    │   │
    │   ├── finalizeAgentTool() → 最终化结果
    │   ├── completeAsyncAgent() → 标记完成（先于清理）
    │   ├── classifyHandoffIfNeeded() → 安全审查（可选）
    │   └── enqueueAgentNotification() → 发送完成通知
    │
    └── return { status: 'async_launched' } → 立即返回给主Agent
```

### 7.4 安全审查机制

`classifyHandoffIfNeeded` 实现了子Agent完成后的安全审查：

```typescript
// 当子Agent完成时，如果处于auto模式：
// 1. 构建子Agent的transcript
// 2. 使用classifier模型审查是否有危险操作
// 3. 如果检测到危险 → 返回SECURITY WARNING
// 4. 如果classifier不可用 → 返回警告但允许继续
// 5. 如果安全 → 返回null（正常完成）
```

这个机制只在 `TRANSCRIPT_CLASSIFIER` feature flag启用且处于auto模式时生效。

### 7.5 错误恢复策略

同步Agent的错误恢复策略：

```typescript
// AgentTool.tsx — 同步Agent错误恢复

// 如果发生错误，尝试用已收集的消息返回结果
if (syncAgentError) {
  const hasAssistantMessages = agentMessages.some(msg => msg.type === 'assistant')
  if (!hasAssistantMessages) {
    // 没有消息，重新抛出错误
    throw syncAgentError
  }
  // 有消息，尝试final化并返回
  // 这允许父Agent看到部分进度，即使发生了错误
  logForDebugging(`Sync agent recovering from error with ${agentMessages.length} messages`)
}
```

---

## 8. 工程实践细节

### 8.1 Worktree隔离

当 `isolation: 'worktree'` 时，Agent在一个独立的git worktree中运行：

```typescript
// AgentTool.tsx — worktree创建和清理

// 创建worktree
if (effectiveIsolation === 'worktree') {
  const slug = `agent-${earlyAgentId.slice(0, 8)}`
  worktreeInfo = await createAgentWorktree(slug)
}

// Fork + worktree：注入路径转换通知
if (isForkPath && worktreeInfo) {
  promptMessages.push(
    createUserMessage({
      content: buildWorktreeNotice(getCwd(), worktreeInfo.worktreePath),
    }),
  )
}

// Agent完成后检查是否有变更
const cleanupWorktreeIfNeeded = async () => {
  if (!worktreeInfo) return {}
  const { worktreePath, worktreeBranch, headCommit, gitRoot, hookBased } = worktreeInfo
  worktreeInfo = null  // 幂等保护

  if (hookBased) {
    // Hook-based worktree始终保留（无法检测VCS变更）
    return { worktreePath }
  }
  if (headCommit) {
    const changed = await hasWorktreeChanges(worktreePath, headCommit)
    if (!changed) {
      await removeAgentWorktree(worktreePath, worktreeBranch, gitRoot)
      return {}
    }
  }
  // 有变更 → 保留worktree
  return { worktreePath, worktreeBranch }
}
```

### 8.2 自动后台化机制

同步Agent可以在运行过程中被自动后台化：

```typescript
// AgentTool.tsx — 自动后台化

// 超过阈值（2秒）后显示BackgroundHint UI
const PROGRESS_THRESHOLD_MS = 2000

if (!isBackgroundTasksDisabled && !backgroundHintShown &&
    elapsed >= PROGRESS_THRESHOLD_MS && toolUseContext.setToolJSX) {
  backgroundHintShown = true
  toolUseContext.setToolJSX({
    jsx: <BackgroundHint />,
    shouldHidePromptInput: false,
    shouldContinueAnimation: true,
    showSpinner: true
  })
}

// 竞争：下一条消息 vs 后台信号
const raceResult = backgroundPromise
  ? await Promise.race([
      nextMessagePromise.then(r => ({ type: 'message' as const, result: r })),
      backgroundPromise,
    ])
  : { type: 'message' as const, result: await nextMessagePromise }

if (raceResult.type === 'background' && foregroundTaskId) {
  wasBackgrounded = true
  stopForegroundSummarization?.()

  // 继续在后台运行
  void runWithAgentContext(syncAgentContext, async () => {
    for await (const msg of runAgent({ isAsync: true, ... })) {
      // ... 后台执行逻辑
    }
  })

  // 立即返回async_launched结果
  return {
    data: {
      isAsync: true as const,
      status: 'async_launched' as const,
      agentId: backgroundedTaskId,
      ...
    }
  }
}
```

### 8.3 缓存淘汰提示

子Agent完成后，发送缓存淘汰提示以优化推理服务的缓存使用：

```typescript
// agentToolUtils.ts — finalizeAgentTool

// Signal to inference that this subagent's cache chain can be evicted.
const lastRequestId = lastAssistantMessage.requestId
if (lastRequestId) {
  logEvent('tengu_cache_eviction_hint', {
    scope: 'subagent_end',
    last_request_id: lastRequestId,
  })
}
```

这告诉推理服务可以淘汰这个子Agent的缓存链，因为不会有后续请求读取它。

### 8.4 One-Shot Agent优化

对于一次性Agent（Explore、Plan），省略agentId/SendMessage/usage trailer以节省token：

```typescript
// constants.ts
export const ONE_SHOT_BUILTIN_AGENT_TYPES: ReadonlySet<string> = new Set([
  'Explore',
  'Plan',
])

// AgentTool.tsx — mapToolResultToToolResultBlockParam
// 一次性内置Agent（Explore、Plan）不会被SendMessage继续
// 省略agentId/SendMessage/usage trailer以节省token
// （~135字符 × 34M Explore运行/周 ≈ 1-2 Gtok/周）
if (
  data.agentType &&
  ONE_SHOT_BUILTIN_AGENT_TYPES.has(data.agentType) &&
  !worktreeInfoText
) {
  return {
    tool_use_id: toolUseID,
    type: 'tool_result',
    content: contentOrMarker,  // 省略agentId/usage trailer
  }
}
```

### 8.5 Agent列表注入优化

动态Agent列表（MCP连接、插件变化时会改变）从工具描述中移出，改为通过 `<system-reminder>` 消息注入：

```typescript
// prompt.ts
export function shouldInjectAgentListInMessages(): boolean {
  if (isEnvTruthy(process.env.CLAUDE_CODE_AGENT_LIST_IN_MESSAGES)) return true
  if (isEnvDefinedFalsy(process.env.CLAUDE_CODE_AGENT_LIST_IN_MESSAGES)) return false
  return getFeatureValue_CACHED_MAY_BE_STALE('tengu_agent_list_attach', false)
}
```

**解决的问题**：动态Agent列表曾占全fleet cache_creation tokens的~10.2%。每次MCP连接、`/reload-plugins`、或权限模式变更都会改变列表 → 工具描述变化 → tools-block缓存完全失效。

### 8.6 缓冲式Agent摘要

异步Agent支持运行时摘要（用于进度报告）：

```typescript
// AgentTool.tsx — 异步Agent摘要

const onCacheSafeParams = enableSummarization
  ? (params: CacheSafeParams) => {
      const { stop } = startAgentSummarization(
        taskId,
        asAgentId(taskId),
        params,
        rootSetAppState,
      )
      stopSummarization = stop
    }
  : undefined
```

摘要系统使用CacheSafeParams来生成子Agent进度的摘要，这些摘要可以展示给用户（如在VS Code子Agent面板中）。

---

## 9. 初学者易错点

### 9.1 混淆Fork与正常Agent创建

**错误理解**：认为省略 `subagent_type` 就是使用默认的general-purpose agent。

**实际情况**：
- Fork实验关闭时：省略 `subagent_type` → 使用 `GENERAL_PURPOSE_AGENT`
- Fork实验开启时：省略 `subagent_type` → 走Fork路径（继承上下文）

```typescript
const effectiveType =
  subagent_type ??
  (isForkSubagentEnabled() ? undefined : GENERAL_PURPOSE_AGENT.agentType)
const isForkPath = effectiveType === undefined
```

**如何区分**：检查 `isForkSubagentEnabled()` 的返回值，它依赖于 `FORK_SUBAGENT` feature flag。

### 9.2 认为子Agent可以访问父级的AppState

**错误理解**：子Agent的 `setAppState` 调用会修改父级的状态。

**实际情况**：默认情况下，`setAppState` 被替换为 no-op `() => {}`，子Agent的状态变更完全隔离。

**例外情况**：
- `setAppStateForTasks` 始终指向根store（用于任务注册/销毁）
- 显式设置 `shareSetAppState: true` 时共享

### 9.3 忽略AbortController的传播方向

**错误理解**：子Agent的abort会影响父级。

**实际情况**：
- 父级abort → 传播到子级（通过 `createChildAbortController` 的信号链接）
- 子级abort → 不影响父级（子控制器是独立的）

### 9.4 误解CacheSafeParams的共享语义

**错误理解**：CacheSafeParams是通过引用共享的。

**实际情况**：CacheSafeParams中的关键字段（如 `readFileState`、`contentReplacementState`）通过**深拷贝**隔离，只有需要保持缓存一致的部分才共享。

### 9.5 忘记异步Agent的工具限制

**错误**：期望异步Agent能使用所有工具（包括交互式工具）。

**实际情况**：异步Agent受 `ASYNC_AGENT_ALLOWED_TOOLS` 严格限制，不能使用需要用户交互的工具（如权限确认对话框）。

### 9.6 忽略maxOutputTokens对缓存的影响

**错误**：在Fork Agent中设置 `maxOutputTokens`。

**实际情况**：`maxOutputTokens` 的变化会通过clamping影响 `budget_tokens`，而 `thinking_config` 是缓存key的一部分。设置不同的 `maxOutputTokens` 会破坏Prompt缓存。

### 9.7 混淆tools:['*']的含义

**错误理解**：`tools: ['*']` 表示"所有工具"。

**实际情况**：`tools: ['*']` 是通配符，表示"经过 `filterToolsForAgent` 过滤后的所有工具"。子Agent仍然受到三道门机制的限制。例如，异步Agent即使 `tools: ['*']`，也不能使用 `ASYNC_AGENT_ALLOWED_TOOLS` 之外的工具。

### 9.8 忽略Agent定义的优先级覆盖

**错误理解**：内置Agent的定义是固定的。

**实际情况**：自定义Agent可以通过同名覆盖内置Agent（项目设置 > 用户设置 > 插件 > 内置）。这可能导致意外的行为变化。

---

## 10. 本章总结

### 核心概念回顾

| 概念 | 文件 | 作用 |
|------|------|------|
| **AgentTool** | `AgentTool.tsx` | Agent系统的入口，调度同步/异步/Fork执行 |
| **filterToolsForAgent** | `agentToolUtils.ts` | 三道门机制：MCP白名单→全局禁用→异步限制 |
| **createSubagentContext** | `forkedAgent.ts` | 四维隔离：文件状态/Abort控制/状态回调/UI回调 |
| **CacheSafeParams** | `forkedAgent.ts` | 五个缓存关键字段 |
| **FORK_AGENT** | `forkSubagent.ts` | Fork子Agent定义 |
| **buildForkedMessages** | `forkSubagent.ts` | 构建缓存友好的Fork消息 |
| **runAsyncAgentLifecycle** | `agentToolUtils.ts` | 异步Agent完整生命周期 |
| **Agent Swarms** | `agentSwarmsEnabled.ts` | 三层门控 |
| **Agent记忆** | `agentMemory.ts` | 三种作用域的持久化记忆 |
| **Agent颜色** | `agentColorManager.ts` | UI视觉区分 |

### 设计模式总结

1. **分层信任**：主Agent > 内置Agent > 自定义Agent > 异步Agent
2. **缓存友好**：Fork通过共享system prompt、工具定义、消息前缀最大化缓存命中
3. **状态隔离**：默认隔离，显式共享（opt-in模式）
4. **事件驱动**：异步Agent使用消息流+通知模式，不阻塞主循环
5. **安全审查**：auto模式下，子Agent完成后进行handoff分类审查
6. **优雅降级**：错误时尝试返回部分结果，而非完全失败

### 关键数据流

```
用户请求 → AgentTool.call()
    │
    ├── Agent解析（Fork vs 正常）
    ├── 工具池组装（filterToolsForAgent三道门）
    ├── 上下文创建（createSubagentContext四维隔离）
    ├── 系统提示词（Fork: 父级字节 vs 正常: 重新生成）
    ├── 消息构建（Fork: buildForkedMessages vs 正常: createUserMessage）
    │
    ├── [同步] → runAgent() → 逐消息迭代 → finalizeAgentTool()
    ├── [异步] → registerAsyncAgent() → runAsyncAgentLifecycle() → 通知
    └── [Fork] → 继承上下文 → buildForkedMessages() → 异步执行
```

---

## 11. 延伸思考

### 11.1 递归Fork的限制与突破

当前设计通过 `isInForkChild()` 检测消息历史中的fork标记来阻止递归fork。这是一个运行时检查。思考：

- 是否可以在类型系统层面阻止递归fork？
- 如果需要支持有限深度的递归fork（如2层），如何修改 `querySource` 检查？
- 递归fork的缓存共享收益是否值得增加的复杂性？

### 11.2 缓存一致性的边界

`CacheSafeParams` 保证了API请求前缀的缓存一致性，但有一些隐含假设：

- 如果父级在fork spawn后修改了文件（通过其他工具），fork子级的 `readFileState` 克隆会看到过时的数据
- `thinking_config` 的一致性依赖于 `maxOutputTokens` 不被修改
- GrowthBook状态在父级turn-start和fork spawn之间可能变化

思考：如何在保持缓存一致性的同时，处理这些边界情况？是否需要一种"缓存版本"机制？

### 11.3 Agent Swarms的演进方向

`isAgentSwarmsEnabled()` 目前是一个简单的特性开关。思考：

- 如果Agent Swarms全面启用，`filterToolsForAgent` 中的 `IN_PROCESS_TEAMMATE_ALLOWED_TOOLS` 特殊豁免是否会成为安全隐患？
- teammate之间的通信目前通过 mailbox 模式（SendMessage），是否有更高效的直接通信机制？
- 如何防止teammate之间的死锁？

### 11.4 安全审查的false positive

`classifyHandoffIfNeeded` 使用classifier模型审查子Agent的输出。思考：

- 如何平衡安全性与延迟？每次子Agent完成都调用classifier会增加延迟
- classifier不可用时的"允许但警告"策略是否足够安全？
- 是否可以基于子Agent的信任层级（内置 vs 自定义）差异化审查策略？

### 11.5 从Agent系统看分布式系统设计

Claude Code的Agent系统本质上是一个**嵌入式分布式系统**：

- 主Agent是协调者（coordinator）
- 子Agent是工作者（worker）
- `createSubagentContext` 实现了类似进程隔离的效果
- `CacheSafeParams` 类似分布式缓存的一致性协议

**类比分析**：

| Claude Code概念 | 操作系统类比 | 分布式系统类比 |
|----------------|-------------|--------------|
| `createSubagentContext` | `fork()` 系统调用 | 容器隔离 |
| `createChildAbortController` | 信号传播（SIGTERM→子进程） | 级联取消 |
| `CacheSafeParams` | CPU缓存一致性（MESI协议） | 分布式缓存一致性 |
| `setAppState: () => {}` | 进程地址空间隔离 | 微服务状态隔离 |
| `FORK_AGENT` | `fork()` + `exec()` | 复制状态+执行 |
| `buildForkedMessages` | Copy-on-Write | 增量复制 |

思考：这个设计与操作系统中的进程管理（fork/exec/wait）有什么相似之处？Prompt缓存共享与CPU缓存一致性协议（如MESI）有什么类比？

### 11.6 Agent记忆的演进

当前Agent记忆系统是基于文件的（MEMORY.md），思考：

- 是否可以引入向量数据库来支持语义搜索？
- 如何在多Agent之间共享记忆（而非每个Agent独立存储）？
- 记忆的"遗忘"机制：如何自动清理过时的记忆？

### 11.7 性能优化的量化分析

Claude Code的Agent系统包含多项性能优化，让我们量化分析其影响：

**Explore Agent的omitClaudeMd优化**：
- 每次Explore运行节省约150-450 tokens（CLAUDE.md大小）
- 34M+ Explore运行/周 × 300 tokens = ~10 Gtok/周的节省
- 这是通过牺牲"Agent对项目规范的了解"换来的——但Explore是只读的，不需要知道commit/PR规范

**One-Shot Agent的trailer省略**：
- 每次省略约135字符的agentId/SendMessage/usage trailer
- 34M Explore运行/周 × 135字符 ≈ 4.6 G字符/周 ≈ 1-2 Gtok/周

**Agent列表注入优化**：
- 动态Agent列表曾占全fleet cache_creation tokens的~10.2%
- 将列表从工具描述移到 `<system-reminder>` 消息后，tools-block保持静态
- MCP连接/插件变化不再导致tools-block缓存失效

**Fork的缓存共享**：
- Fork与父级共享Prompt缓存，避免了完整的API前缀重新计算
- 缓存命中时的cache_read_input_tokens价格是cache_creation的10%
- 这使得Fork成为"廉价"的操作，鼓励用户将其用于研究型任务

### 11.8 工程权衡分析

Agent系统中存在多处值得深思的工程权衡：

**权衡1：隔离 vs 性能**
- `createSubagentContext` 默认深拷贝 `readFileState`，这有性能开销
- 但如果不拷贝，子Agent的文件读取会污染父级缓存，导致数据不一致
- 设计选择：默认隔离（安全），通过 `SubagentContextOverrides` 显式共享（性能）

**权衡2：安全 vs 延迟**
- `classifyHandoffIfNeeded` 增加了子Agent完成后的延迟
- 但它只在auto模式下启用，且使用classifier模型（可能不可用）
- 设计选择：安全优先，classifier不可用时"允许但警告"

**权衡3：缓存一致性 vs 灵活性**
- Fork要求所有子级共享相同的system prompt、工具定义、消息前缀
- 这限制了子级的定制能力（如不能使用不同模型）
- 设计选择：缓存优先，通过正常Agent路径处理需要定制的场景

**权衡4：内置Agent的信任假设**
- 内置Agent不受 `CUSTOM_AGENT_DISALLOWED_TOOLS` 限制
- 这假设Anthropic维护的Agent是可信的
- 如果内置Agent的系统提示词被注入恶意内容，可能绕过安全限制
- 设计选择：信任内置Agent，但保留了killswitch（GrowthBook）

**权衡5：异步Agent的状态隔离**
- 异步Agent的 `setAppState` 是no-op，无法看到其他Agent的状态变更
- 但 `setAppStateForTasks` 始终指向根store，用于任务注册
- 设计选择：严格隔离（防止状态污染），但保留任务管理通道

### 11.9 未来演进方向

基于当前架构，Agent系统可能的演进方向：

1. **流式结果返回**：当前同步Agent完成后一次性返回结果，未来可以支持流式返回部分结果
2. **Agent间通信**：当前Agent间通过SendMessage（mailbox模式）通信，未来可以支持更高效的直接通信
3. **动态工具发现**：当前工具列表在Agent创建时确定，未来可以支持运行时动态发现新工具
4. **Agent版本管理**：当前Agent定义没有版本概念，未来可以支持Agent的版本管理和回滚
5. **跨会话Agent**：当前Agent生命周期限于单次会话，未来可以支持跨会话的持久化Agent
6. **Agent编排语言**：当前Agent编排逻辑硬编码在TypeScript中，未来可以引入声明式的Agent编排语言

---

*本章基于 Claude Code 源码分析，所有代码引用均来自实际源文件。文件路径和行号可能随版本更新而变化。*
