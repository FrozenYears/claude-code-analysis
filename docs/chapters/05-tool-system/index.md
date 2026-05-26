---
title: 第05章：Tool系统 — 让AI拥有「手」和「眼」
---

# 第05章：Tool系统 — 让AI拥有「手」和「眼」

## 1. 本章目标

本章将深入剖析 Claude Code 的 **Tool 系统**——这是整个项目中最核心的子系统之一。工具（Tool）是 LLM 与外部世界交互的唯一桥梁：没有工具，AI 只是一个「能说话的脑袋」；有了工具，它才能读文件、写代码、执行命令、搜索代码库，甚至创建子 Agent 来并行处理任务。

读完本章，你将理解：

- **Tool 接口的完整定义**：一个工具需要实现哪些方法，每个方法的职责是什么
- **工具注册机制**：`buildTool` 工厂函数如何工作，工具如何被收集到工具池中
- **权限检查流程**：从 `validateInput` 到 `checkPermissions` 的完整权限链
- **BashTool 的安全机制**：这是整个系统中最复杂、安全要求最高的工具，其安全检查链包含 20+ 个独立的验证器
- **核心工具实现剖析**：FileReadTool、FileEditTool、GrepTool、AgentTool 等的设计思路

## 2. 前置知识

在阅读本章之前，你应该已经了解：

- **TypeScript 基础**：接口、泛型、类型推导
- **Zod 库**：用于运行时 schema 验证的 TypeScript 库
- **LLM Tool Use 概念**：理解 Anthropic API 的 `tool_use` 和 `tool_result` 消息块
- **前几章内容**：特别是消息系统和权限系统的基础概念

## 3. 宏观概览

### 3.1 Tool 系统在整体架构中的位置

```
┌──────────────────────────────────────────────┐
│                用户输入                        │
└──────────────┬───────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────┐
│            主循环 (query.ts)                   │
│  ┌────────────────────────────────────────┐  │
│  │  Claude API 调用 → 模型返回 tool_use    │  │
│  └──────────────┬─────────────────────────┘  │
│                 │                             │
│                 ▼                             │
│  ┌────────────────────────────────────────┐  │
│  │  Tool 系统（本章重点）                   │  │
│  │  ┌──────────┐  ┌──────────┐            │  │
│  │  │ 工具注册  │  │ 权限检查  │            │  │
│  │  └────┬─────┘  └────┬─────┘            │  │
│  │       │              │                  │  │
│  │       ▼              ▼                  │  │
│  │  ┌──────────────────────────────────┐  │  │
│  │  │  40+ 具体工具实现                  │  │  │
│  │  │  BashTool / FileReadTool / ...   │  │  │
│  │  └──────────────────────────────────┘  │  │
│  └────────────────────────────────────────┘  │
└──────────────────────────────────────────────┘
```

### 3.2 工具目录结构

```
src/
├── Tool.ts                    # 核心接口定义（794行）
├── tools.ts                   # 工具注册与组装（390行）
└── tools/
    ├── BashTool/              # 命令执行（最复杂的安全机制）
    │   ├── BashTool.tsx       # 主工具实现
    │   ├── bashSecurity.ts    # 安全验证器集合
    │   ├── bashPermissions.ts # 权限检查主逻辑
    │   ├── readOnlyValidation.ts
    │   ├── pathValidation.ts
    │   └── ...
    ├── FileReadTool/           # 文件读取
    ├── FileEditTool/           # 文件编辑
    ├── FileWriteTool/          # 文件写入
    ├── GrepTool/               # 代码搜索（基于 ripgrep）
    ├── GlobTool/               # 文件模式匹配
    ├── AgentTool/              # 子 Agent 创建
    ├── SendMessageTool/        # Agent 间通信
    ├── SyntheticOutputTool/    # 合成输出（SDK 模式）
    ├── WebFetchTool/           # 网页抓取
    ├── WebSearchTool/          # 网页搜索
    ├── SkillTool/              # 技能执行
    ├── MCPTool/                # MCP 协议工具
    └── ...                     # 更多工具
```

### 3.3 核心数据流

一个工具调用的完整生命周期：

```
1. 模型返回 tool_use block（包含工具名和输入参数）
2. 通过 findToolByName 查找工具实例
3. validateInput() —— 输入验证（纯检查，不执行）
4. checkPermissions() —— 工具特定权限检查
5. 通用权限系统检查（用户是否批准）
6. call() —— 实际执行工具逻辑
7. mapToolResultToToolResultBlockParam() —— 转换结果为 API 格式
8. 返回 tool_result block 给模型
```

## 4. 源码入口定位

### 4.1 Tool 接口定义

**文件**：`src/Tool.ts`，第 1-794 行

这是整个工具系统的类型基础。`Tool` 接口定义了一个工具必须（或可以）实现的所有方法和属性。

```typescript
// src/Tool.ts（简化版，保留核心结构）
export type Tool<
  Input extends AnyObject = AnyObject,
  Output = unknown,
  P extends ToolProgressData = ToolProgressData,
> = {
  // === 基本信息 ===
  readonly name: string                    // 工具的唯一标识名
  aliases?: string[]                       // 别名（用于向后兼容重命名）
  searchHint?: string                      // 工具搜索提示词
  readonly strict?: boolean                // 是否启用严格模式

  // === Schema 定义 ===
  readonly inputSchema: Input              // Zod schema，定义输入参数
  readonly inputJSONSchema?: ToolInputJSONSchema  // MCP 工具的 JSON Schema
  outputSchema?: z.ZodType<unknown>        // 输出 schema

  // === 核心行为 ===
  call(                                    // 执行工具的主函数
    args: z.infer<Input>,
    context: ToolUseContext,
    canUseTool: CanUseToolFn,
    parentMessage: AssistantMessage,
    onProgress?: ToolCallProgress<P>,
  ): Promise<ToolResult<Output>>

  description(                             // 动态描述（可基于输入生成）
    input: z.infer<Input>,
    options: { ... },
  ): Promise<string>

  prompt(options: { ... }): Promise<string> // 提供给模型的使用说明

  // === 权限与验证 ===
  validateInput?(                           // 输入验证（可选）
    input: z.infer<Input>,
    context: ToolUseContext,
  ): Promise<ValidationResult>

  checkPermissions(                         // 权限检查
    input: z.infer<Input>,
    context: ToolUseContext,
  ): Promise<PermissionResult>

  preparePermissionMatcher?(                // hook 条件匹配器
    input: z.infer<Input>,
  ): Promise<(pattern: string) => boolean>

  // === 行为属性 ===
  isEnabled(): boolean                      // 是否启用
  isReadOnly(input): boolean                // 是否只读操作
  isConcurrencySafe(input): boolean         // 是否可并发执行
  isDestructive?(input): boolean            // 是否破坏性操作
  interruptBehavior?(): 'cancel' | 'block'  // 中断行为
  isSearchOrReadCommand?(input): { ... }    // UI 折叠判断

  // === UI 渲染 ===
  userFacingName(input): string             // 用户可见的工具名
  renderToolUseMessage(input, options): React.ReactNode
  renderToolResultMessage?(content, ...): React.ReactNode
  renderToolUseProgressMessage?(...): React.ReactNode
  renderToolUseRejectedMessage?(...): React.ReactNode
  renderToolUseErrorMessage?(...): React.ReactNode

  // === 结果转换 ===
  mapToolResultToToolResultBlockParam(
    content: Output,
    toolUseID: string,
  ): ToolResultBlockParam

  // === 其他 ===
  maxResultSizeChars: number                // 结果大小阈值
  toAutoClassifierInput(input): unknown     // 安全分类器输入
  getPath?(input): string                   // 操作的文件路径
  backfillObservableInput?(input): void     // 输入后处理
}
```

### 4.2 buildTool 工厂函数

**文件**：`src/Tool.ts`，第 730-794 行

`buildTool` 是所有工具的创建入口。它为可选方法提供安全默认值：

```typescript
// src/Tool.ts
const TOOL_DEFAULTS = {
  isEnabled: () => true,
  isConcurrencySafe: (_input?) => false,    // 默认不安全（fail-closed）
  isReadOnly: (_input?) => false,            // 默认假设写操作
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
    ...def,  // 工具定义覆盖默认值
  } as BuiltTool<D>
}
```

**设计思想**：默认值采用 **fail-closed** 策略——假设工具不可并发、非只读，只有显式声明才放开。这避免了遗漏标记导致的安全漏洞。

### 4.3 工具注册入口

**文件**：`src/tools.ts`，第 1-390 行

```typescript
// src/tools.ts（核心函数）
export function getAllBaseTools(): Tools {
  return [
    AgentTool,
    TaskOutputTool,
    BashTool,
    ...(hasEmbeddedSearchTools() ? [] : [GlobTool, GrepTool]),
    FileReadTool,
    FileEditTool,
    FileWriteTool,
    NotebookEditTool,
    WebFetchTool,
    TodoWriteTool,
    WebSearchTool,
    // ... 更多工具
    ...(isToolSearchEnabledOptimistic() ? [ToolSearchTool] : []),
  ]
}

export const getTools = (permissionContext: ToolPermissionContext): Tools => {
  // 简单模式：只有 Bash、Read、Edit
  if (isEnvTruthy(process.env.CLAUDE_CODE_SIMPLE)) {
    const simpleTools: Tool[] = [BashTool, FileReadTool, FileEditTool]
    return filterToolsByDenyRules(simpleTools, permissionContext)
  }

  const tools = getAllBaseTools().filter(tool => !specialTools.has(tool.name))
  let allowedTools = filterToolsByDenyRules(tools, permissionContext)
  // REPL 模式下隐藏原始工具
  if (isReplModeEnabled()) { ... }
  return allowedTools.filter((_, i) => isEnabled[i])
}
```

## 5. 调用链分析

### 5.1 工具调用的完整链路

当模型返回一个 `tool_use` block 时，系统按以下顺序处理：

```
tool_use block (from API)
    │
    ▼
findToolByName(tools, name)          // 在工具池中查找
    │
    ▼
tool.backfillObservableInput(input)  // 补充/规范化输入字段
    │
    ▼
tool.validateInput(input, context)   // 输入验证（纯检查）
    │  └─ 失败 → 返回错误消息给模型
    ▼
PreToolUse hooks                      // 用户定义的前置钩子
    │  └─ hook 可以拒绝/修改
    ▼
tool.checkPermissions(input, ctx)    // 工具特定权限检查
    │  └─ deny → 拒绝执行
    │  └─ ask → 弹出用户确认对话框
    │  └─ allow → 继续
    ▼
通用权限系统（permissions.ts）        // 用户级别的权限规则
    │  └─ deny rules / ask rules / allow rules
    ▼
tool.call(input, context, ...)       // 实际执行
    │
    ▼
tool.mapToolResultToToolResultBlockParam(result, id)  // 转换结果
    │
    ▼
PostToolUse hooks                     // 用户定义的后置钩子
    │
    ▼
tool_result block → 发送给模型
```

### 5.2 ToolUseContext 的作用

`ToolUseContext` 是贯穿整个工具调用生命周期的上下文对象，它携带了：

```typescript
// src/Tool.ts（简化）
export type ToolUseContext = {
  options: {
    commands: Command[]
    tools: Tools
    mcpClients: MCPServerConnection[]
    agentDefinitions: AgentDefinitionsResult
    // ...
  }
  abortController: AbortController
  readFileState: FileStateCache        // 文件读取缓存
  getAppState(): AppState              // 获取应用状态
  setAppState(f): void                 // 更新应用状态
  messages: Message[]                  // 当前会话消息
  setToolJSX?: SetToolJSXFn           // 设置工具 UI
  agentId?: AgentId                    // 子 Agent ID
  // ...
}
```

**关键设计**：`ToolUseContext` 不仅传递执行环境，还负责状态管理（`readFileState`、`setAppState`）和 UI 控制（`setToolJSX`）。这使得工具可以在执行过程中更新 UI、追踪文件状态。

## 6. 核心源码解析

### 6.1 BashTool：最复杂的工具

BashTool 是 Claude Code 中最复杂、安全要求最高的工具。它允许 AI 执行任意 shell 命令，这意味着安全检查必须极其严格。

#### 6.1.1 输入 Schema 定义

```typescript
// src/tools/BashTool/BashTool.tsx
const fullInputSchema = lazySchema(() => z.strictObject({
  command: z.string().describe('The command to execute'),
  timeout: semanticNumber(z.number().optional())
    .describe('Optional timeout in milliseconds'),
  description: z.string().optional()
    .describe('Clear, concise description of what this command does'),
  run_in_background: semanticBoolean(z.boolean().optional())
    .describe('Set to true to run this command in the background'),
  dangerouslyDisableSandbox: semanticBoolean(z.boolean().optional())
    .describe('Set this to true to dangerously override sandbox mode'),
  _simulatedSedEdit: z.object({
    filePath: z.string(),
    newContent: z.string(),
  }).optional().describe('Internal: pre-computed sed edit result'),
}))
```

注意 `_simulatedSedEdit` 字段被标记为 `Internal`，从模型可见的 schema 中被 `.omit()` 掉——这是一个安全设计，防止模型绕过权限检查直接写入文件。

#### 6.1.2 权限检查主函数

`bashToolHasPermission` 是 BashTool 的权限检查入口，位于 `src/tools/BashTool/bashPermissions.ts`，这是一个长达 500+ 行的函数，体现了安全检查的复杂性：

```typescript
// src/tools/BashTool/bashPermissions.ts（简化流程）
export async function bashToolHasPermission(
  input: z.infer<typeof BashTool.inputSchema>,
  context: ToolUseContext,
): Promise<PermissionResult> {
  // 0. AST 安全解析（tree-sitter）
  let astResult = await parseForSecurity(input.command)

  // 1. too-complex → ask（无法静态分析的命令）
  if (astResult.kind === 'too-complex') {
    return { behavior: 'ask', ... }
  }

  // 2. 语义检查（zsh builtins, eval 等危险命令）
  if (astResult.kind === 'simple') {
    const sem = checkSemantics(astResult.commands)
    if (!sem.ok) return { behavior: 'ask', ... }
  }

  // 3. 沙箱自动放行检查
  if (SandboxManager.isSandboxingEnabled()) {
    const sandboxResult = checkSandboxAutoAllow(input, ...)
    if (sandboxResult.behavior !== 'passthrough') return sandboxResult
  }

  // 4. 精确匹配规则检查
  const exactMatchResult = bashToolCheckExactMatchPermission(input, ...)

  // 5. 分类器检查（使用 Haiku 模型判断安全性）
  if (isClassifierPermissionsEnabled()) {
    const [denyResult, askResult] = await Promise.all([
      classifyBashCommand(command, ..., 'deny', ...),
      classifyBashCommand(command, ..., 'ask', ...),
    ])
    // deny 优先级高于 ask
  }

  // 6. 管道/重定向操作符权限检查
  const commandOperatorResult = await checkCommandOperatorPermissions(...)

  // 7. 旧版安全检查（tree-sitter 不可用时的后备方案）
  const safetyResult = await bashCommandIsSafeAsync(input.command)

  // 8. 拆分子命令，逐个检查
  const subcommands = splitCommand(input.command)
  // ... 对每个子命令进行权限检查

  // 9. 路径约束检查
  const pathResult = checkPathConstraints(input, cwd, ...)

  // 10. 只读命令自动放行
  if (BashTool.isReadOnly(input)) {
    return { behavior: 'allow', ... }
  }

  // 11. 兜底：需要用户批准
  return { behavior: 'passthrough', ... }
}
```

#### 6.1.3 安全验证器集合

`src/tools/BashTool/bashSecurity.ts` 定义了 **20+ 个独立的安全验证器**，每个都专注于检测特定类型的攻击向量：

```typescript
// src/tools/BashTool/bashSecurity.ts（验证器列表）
const earlyValidators = [
  validateEmpty,                    // 空命令检查
  validateIncompleteCommands,       // 不完整命令片段
  validateSafeCommandSubstitution,  // 安全的 heredoc 替换
  validateGitCommit,                // git commit 的特殊处理
]

const validators = [
  validateJqCommand,                // jq 的 system() 函数
  validateObfuscatedFlags,          // 混淆的命令标志
  validateShellMetacharacters,      // shell 元字符
  validateDangerousVariables,       // 危险的变量使用
  validateCommentQuoteDesync,       // 注释中的引号脱同步
  validateQuotedNewline,            // 引号内的换行符
  validateCarriageReturn,           // 回车符（解析差异）
  validateNewlines,                 // 换行符（命令注入）
  validateIFSInjection,             // IFS 变量注入
  validateProcEnvironAccess,        // /proc/*/environ 访问
  validateDangerousPatterns,        // 命令替换模式（$, `, ${}）
  validateRedirections,             // 输入/输出重定向
  validateBackslashEscapedWhitespace, // 反斜杠转义空白
  validateBackslashEscapedOperators,  // 反斜杠转义操作符
  validateUnicodeWhitespace,        // Unicode 空白字符
  validateMidWordHash,              // 词中 # 号（解析差异）
  validateBraceExpansion,           // 花括号展开
  validateZshDangerousCommands,     // Zsh 危险命令
  validateMalformedTokenInjection,  // 畸形 token 注入
]
```

让我们看一个典型的验证器实现——**反斜杠转义操作符检测**：

```typescript
// src/tools/BashTool/bashSecurity.ts
const SHELL_OPERATORS = new Set([';', '|', '&', '<', '>'])

function hasBackslashEscapedOperator(command: string): boolean {
  let inSingleQuote = false
  let inDoubleQuote = false

  for (let i = 0; i < command.length; i++) {
    const char = command[i]

    // 反斜杠处理优先于引号切换
    if (char === '\\' && !inSingleQuote) {
      if (!inDoubleQuote) {
        const nextChar = command[i + 1]
        if (nextChar && SHELL_OPERATORS.has(nextChar)) {
          return true  // 检测到 \; 或 \| 等
        }
      }
      i++  // 跳过转义字符
      continue
    }

    // 引号状态切换
    if (char === "'" && !inDoubleQuote) {
      inSingleQuote = !inSingleQuote
      continue
    }
    if (char === '"' && !inSingleQuote) {
      inDoubleQuote = !inDoubleQuote
      continue
    }
  }
  return false
}
```

**为什么这个检查重要？** 因为 `splitCommand` 会将 `\;` 规范化为 `;`，导致下游重新解析时看到裸分号。攻击示例：`cat safe.txt \; echo ~/.ssh/id_rsa`——在 bash 中是一个命令，但规范化后被拆分为两个子命令，绕过路径验证。

#### 6.1.4 BashTool 的执行流程

```typescript
// src/tools/BashTool/BashTool.tsx（简化）
async call(input, toolUseContext, _canUseTool, parentMessage, onProgress) {
  // 1. 处理模拟的 sed 编辑
  if (input._simulatedSedEdit) {
    return applySedEdit(input._simulatedSedEdit, toolUseContext, parentMessage)
  }

  // 2. 创建异步生成器来执行命令
  const commandGenerator = runShellCommand({
    input,
    abortController,
    setAppState: toolUseContext.setAppStateForTasks ?? setAppState,
    setToolJSX,
    preventCwdChanges,
    isMainThread,
    toolUseId: toolUseContext.toolUseId,
    agentId: toolUseContext.agentId,
  })

  // 3. 消费生成器，收集进度更新
  do {
    generatorResult = await commandGenerator.next()
    if (!generatorResult.done && onProgress) {
      onProgress({ toolUseID: `bash-progress-${progressCounter++}`, data: { ... } })
    }
  } while (!generatorResult.done)

  // 4. 处理结果
  result = generatorResult.value
  trackGitOperations(input.command, result.code, result.stdout)

  // 5. 大输出持久化到磁盘
  if (result.outputFilePath && result.outputTaskId) {
    const dest = getToolResultPath(result.outputTaskId, false)
    await link(result.outputFilePath, dest)
    persistedOutputPath = dest
  }

  // 6. 返回结构化结果
  return { data: { stdout, stderr, interrupted, isImage, ... } }
}
```

**关键设计：异步生成器模式**

`runShellCommand` 使用 `AsyncGenerator` 来实现命令执行。这种设计允许：
- 命令在后台运行时，主循环可以继续处理其他任务
- 通过 `yield` 报告进度更新
- 支持超时自动后台化
- 支持用户手动 Ctrl+B 后台化

```typescript
// src/tools/BashTool/BashTool.tsx
async function* runShellCommand({ input, abortController, ... }) {
  const shellCommand = await exec(command, abortController.signal, 'bash', {
    timeout: timeoutMs,
    onProgress(lastLines, allLines, totalLines, totalBytes, isIncomplete) {
      // 更新进度，唤醒生成器
      const resolve = resolveProgress
      if (resolve) { resolveProgress = null; resolve() }
    },
    shouldUseSandbox: shouldUseSandbox(input),
    shouldAutoBackground,
  })

  // 等待初始阈值（2秒）再显示进度
  const initialResult = await Promise.race([
    resultPromise,
    new Promise(resolve => setTimeout(resolve, PROGRESS_THRESHOLD_MS)),
  ])

  // 进度循环
  while (true) {
    const progressSignal = createProgressSignal()
    const result = await Promise.race([resultPromise, progressSignal])
    if (result !== null) return result  // 命令完成
    if (backgroundShellId) return { backgroundTaskId: backgroundShellId, ... }

    // 产生进度更新
    yield { type: 'progress', output: lastProgressOutput, ... }
  }
}
```

### 6.2 FileReadTool：文件读取

#### 6.2.1 输入 Schema

```typescript
// src/tools/FileReadTool/FileReadTool.ts
const inputSchema = lazySchema(() => z.strictObject({
  file_path: z.string().describe('The absolute path to the file to read'),
  offset: z.number().int().nonnegative().optional()
    .describe('The line number to start reading from'),
  limit: z.number().int().positive().optional()
    .describe('The number of lines to read'),
  pages: z.string().optional()
    .describe('Page range for PDF files (e.g., "1-5")'),
}))
```

#### 6.2.2 核心特性

**1. 去重机制（Dedup）**

```typescript
// src/tools/FileReadTool/FileReadTool.ts
const existingState = readFileState.get(fullFilePath)
if (existingState && existingState.offset !== undefined) {
  const rangeMatch = existingState.offset === offset && existingState.limit === limit
  if (rangeMatch) {
    const mtimeMs = await getFileModificationTimeAsync(fullFilePath)
    if (mtimeMs === existingState.timestamp) {
      return { data: { type: 'file_unchanged', file: { filePath: file_path } } }
    }
  }
}
```

如果同一文件在同一偏移量被重复读取且文件未修改，返回一个 `file_unchanged` stub，避免重复发送完整内容浪费 token。

**2. Token 预算管理**

```typescript
async function validateContentTokens(content, ext, maxTokens) {
  const tokenEstimate = roughTokenCountEstimationForFileType(content, ext)
  if (!tokenEstimate || tokenEstimate <= effectiveMaxTokens / 4) return

  const tokenCount = await countTokensWithAPI(content)
  if (effectiveCount > effectiveMaxTokens) {
    throw new MaxFileReadTokenExceededError(effectiveCount, effectiveMaxTokens)
  }
}
```

**3. 多格式支持**

FileReadTool 支持多种文件格式：
- **文本文件**：标准的行号+内容格式
- **图片**：base64 编码，带自动压缩
- **PDF**：支持页面范围提取
- **Jupyter Notebook**：解析为 cell 数组

**4. macOS 截图路径兼容**

```typescript
function getAlternateScreenshotPath(filePath: string): string | undefined {
  // macOS 可能使用常规空格或窄空格 (U+202F) 在 AM/PM 前
  const currentSpace = match[2]
  const alternateSpace = currentSpace === ' ' ? THIN_SPACE : ' '
  return filePath.replace(`${currentSpace}${match[3]}${match[4]}`,
    `${alternateSpace}${match[3]}${match[4]}`)
}
```

### 6.3 FileEditTool：文件编辑

#### 6.3.1 核心机制

FileEditTool 使用 **old_string → new_string** 的替换模式：

```typescript
// src/tools/FileEditTool/FileEditTool.ts
const inputSchema = lazySchema(() => z.strictObject({
  file_path: z.string().describe('The absolute path to the file to modify'),
  old_string: z.string().describe('The text to replace'),
  new_string: z.string().describe('The text to replace it with'),
  replace_all: z.boolean().optional().default(false)
    .describe('Replace all occurrences'),
}))
```

#### 6.3.2 安全检查

```typescript
// src/tools/FileEditTool/FileEditTool.ts（validateInput）
async validateInput(input, toolUseContext) {
  // 1. 秘密检测
  const secretError = checkTeamMemSecrets(fullFilePath, new_string)
  if (secretError) return { result: false, message: secretError }

  // 2. 拒绝规则检查
  const denyRule = matchingRuleForInput(fullFilePath, ..., 'edit', 'deny')

  // 3. UNC 路径安全（防止 NTLM 凭据泄露）
  if (fullFilePath.startsWith('\\\\') || fullFilePath.startsWith('//')) {
    return { result: true }  // 让权限检查处理
  }

  // 4. 文件大小限制（1 GiB）
  const { size } = await fs.stat(fullFilePath)
  if (size > MAX_EDIT_FILE_SIZE) {
    return { result: false, message: 'File is too large to edit...' }
  }

  // 5. 必须先读取文件（防止盲写）
  if (!readTimestamp || readTimestamp.isPartialView) {
    return { result: false, message: 'File has not been read yet.' }
  }

  // 6. 文件修改时间检查（竞态保护）
  if (readTimestamp) {
    const lastWriteTime = getFileModificationTime(fullFilePath)
    if (lastWriteTime > readTimestamp.timestamp) {
      return { result: false, message: 'File has been modified since read...' }
    }
  }

  // 7. 字符串匹配验证
  const actualOldString = findActualString(file, old_string)
  if (!actualOldString) {
    return { result: false, message: 'String to replace not found in file.' }
  }

  // 8. 多匹配检查
  if (matches > 1 && !replace_all) {
    return { result: false, message: `Found ${matches} matches...` }
  }
}
```

#### 6.3.3 原子读-修改-写

```typescript
// src/tools/FileEditTool/FileEditTool.ts（call 方法）
async call(input, { readFileState, updateFileHistoryState, ... }) {
  // 1. 确保父目录存在（在临界区外）
  await fs.mkdir(dirname(absoluteFilePath))

  // 2. 文件历史备份（在临界区外，幂等操作）
  if (fileHistoryEnabled()) {
    await fileHistoryTrackEdit(updateFileHistoryState, absoluteFilePath, ...)
  }

  // 3. 同步读取当前状态（临界区开始）
  const { content, fileExists, encoding, lineEndings } = readFileForEdit(absoluteFilePath)

  // 4. 再次检查修改时间（竞态保护）
  if (fileExists) {
    const lastWriteTime = getFileModificationTime(absoluteFilePath)
    if (!lastRead || lastWriteTime > lastRead.timestamp) {
      throw new Error(FILE_UNEXPECTEDLY_MODIFIED_ERROR)
    }
  }

  // 5. 生成补丁并写入磁盘
  const { patch, updatedFile } = getPatchForEdit({ ... })
  writeTextContent(absoluteFilePath, updatedFile, encoding, endings)

  // 6. 通知 LSP 和 VS Code
  lspManager.changeFile(absoluteFilePath, updatedFile)
  notifyVscodeFileUpdated(absoluteFilePath, originalFileContents, updatedFile)

  // 7. 更新缓存
  readFileState.set(absoluteFilePath, {
    content: updatedFile,
    timestamp: getFileModificationTime(absoluteFilePath),
    offset: undefined, limit: undefined,
  })
}
```

### 6.4 GrepTool：代码搜索

GrepTool 是基于 ripgrep 的代码搜索工具，设计相对简洁：

```typescript
// src/tools/GrepTool/GrepTool.ts（简化）
export const GrepTool = buildTool({
  name: GREP_TOOL_NAME,
  isConcurrencySafe() { return true },   // 只读，可并发
  isReadOnly() { return true },

  async call({ pattern, path, glob, type, output_mode, ... }, { abortController }) {
    const args = ['--hidden']

    // 排除 VCS 目录
    for (const dir of VCS_DIRECTORIES_TO_EXCLUDE) {
      args.push('--glob', `!${dir}`)
    }

    // 限制行长度（防止 base64/压缩内容）
    args.push('--max-columns', '500')

    // 多行模式
    if (multiline) args.push('-U', '--multiline-dotall')

    // 模式以 - 开头时，使用 -e 标志
    if (pattern.startsWith('-')) args.push('-e', pattern)
    else args.push(pattern)

    // 忽略模式（来自权限上下文）
    for (const ignorePattern of ignorePatterns) {
      args.push('--glob', `!${ignorePattern}`)
    }

    const results = await ripGrep(args, absolutePath, abortController.signal)

    // files_with_matches 模式：按修改时间排序
    const sortedMatches = results
      .map((_, i) => [_, stats[i].mtimeMs] as const)
      .sort((a, b) => b[1] - a[1])

    return { data: { mode, numFiles, filenames: relativeMatches, ... } }
  },
})
```

**关键特性**：
- **自动排除 VCS 目录**：`.git`、`.svn` 等
- **结果分页**：`head_limit` 和 `offset` 参数
- **相对路径转换**：节省 token
- **按修改时间排序**：最近修改的文件优先

### 6.5 AgentTool：子 Agent 创建

AgentTool 是最"元"的工具——它不直接与外部世界交互，而是创建一个新的 Agent 实例来处理任务。

```typescript
// src/tools/AgentTool/AgentTool.tsx（输入 Schema）
const fullInputSchema = lazySchema(() => z.object({
  description: z.string().describe('A short (3-5 word) description of the task'),
  prompt: z.string().describe('The task for the agent to perform'),
  subagent_type: z.string().optional()
    .describe('The type of specialized agent to use'),
  model: z.enum(['sonnet', 'opus', 'haiku']).optional()
    .describe('Optional model override'),
  run_in_background: z.boolean().optional()
    .describe('Set to true to run in the background'),
  name: z.string().optional()
    .describe('Name for the spawned agent'),
  team_name: z.string().optional()
    .describe('Team name for spawning'),
  isolation: z.enum(['worktree', 'remote']).optional()
    .describe('Isolation mode'),
}))
```

AgentTool 的核心逻辑在 `runAgent.ts` 中：

```typescript
// src/tools/AgentTool/runAgent.ts（简化）
export async function runAgent({
  input, context, parentMessage, tools, ...
}) {
  // 1. 创建子 Agent 上下文
  const subagentContext = createSubagentContext(context, { ... })

  // 2. 构建系统提示
  const systemPrompt = buildEffectiveSystemPrompt({
    customSystemPrompt: agentDef.systemPrompt,
    ...
  })

  // 3. 组装工具池（可能过滤特定工具）
  const toolPool = assembleToolPool(permissionContext, mcpTools)

  // 4. 调用 query 循环
  const result = await query({
    messages: [...parentMessages, userMessage],
    systemPrompt,
    tools: toolPool,
    ...
  })

  return result
}
```

### 6.6 SendMessageTool：Agent 间通信

SendMessageTool 允许 Agent 之间发送消息，实现多 Agent 协作：

```typescript
// src/tools/SendMessageTool/SendMessageTool.ts
const inputSchema = lazySchema(() => z.object({
  to: z.string().describe('Recipient: teammate name, or "*" for broadcast'),
  summary: z.string().optional().describe('A 5-10 word summary'),
  message: z.union([
    z.string().describe('Plain text message content'),
    StructuredMessage(),  // 结构化消息（shutdown_request 等）
  ]),
}))
```

### 6.7 SyntheticOutputTool：合成输出

这个工具专为 SDK/非交互模式设计，让模型可以返回结构化 JSON 输出：

```typescript
// src/tools/SyntheticOutputTool/SyntheticOutputTool.ts
export const SyntheticOutputTool = buildTool({
  name: SYNTHETIC_OUTPUT_TOOL_NAME,
  isReadOnly() { return true },

  async call(input) {
    // 验证并返回输入作为结构化输出
    return {
      data: 'Structured output provided successfully',
      structured_output: input,
    }
  },

  async checkPermissions(input) {
    return { behavior: 'allow', updatedInput: input }
  },
})

// 创建带 schema 验证的工具实例
export function createSyntheticOutputTool(jsonSchema) {
  const ajv = new Ajv({ allErrors: true })
  const validateSchema = ajv.compile(jsonSchema)

  return {
    tool: {
      ...SyntheticOutputTool,
      inputJSONSchema: jsonSchema,
      async call(input) {
        const isValid = validateSchema(input)
        if (!isValid) throw new Error(`Output does not match required schema: ${errors}`)
        return { data: 'Structured output provided successfully', structured_output: input }
      },
    },
  }
}
```

## 7. 架构设计思想

### 7.1 接口隔离原则

`Tool` 接口遵循接口隔离原则：核心方法（`call`、`checkPermissions`）是必需的，而 UI 渲染方法（`renderToolUseMessage` 等）是可选的。`buildTool` 为可选方法提供默认实现，使得工具作者只需关注核心逻辑。

### 7.2 Fail-Closed 安全策略

工具系统采用 **fail-closed**（默认拒绝）策略：
- `isConcurrencySafe` 默认 `false`（假设不可并发）
- `isReadOnly` 默认 `false`（假设写操作）
- `isDestructive` 默认 `false`（但这是 UI 标记，不影响安全）
- 权限检查失败时默认 `ask`（需要用户确认）

### 7.3 多层防御（Defense in Depth）

BashTool 的安全机制体现了多层防御思想：

```
第1层：AST 解析（tree-sitter）
    │  └─ 结构化分析命令，识别危险模式
    ▼
第2层：语义检查（checkSemantics）
    │  └─ 检测 eval、zmodload 等危险命令名
    ▼
第3层：安全验证器链（20+ 个独立验证器）
    │  └─ 每个验证器专注于一种攻击向量
    ▼
第4层：权限规则匹配（deny/ask/allow）
    │  └─ 用户定义的权限规则
    ▼
第5层：路径约束检查（checkPathConstraints）
    │  └─ 文件系统路径验证
    ▼
第6层：分类器检查（Haiku 模型）
    │  └─ 使用 AI 判断命令安全性
    ▼
第7层：沙箱执行（可选）
       └─ 在沙箱中限制命令能力
```

### 7.4 生成器模式的妙用

BashTool 使用 `AsyncGenerator` 实现命令执行，这是一个精妙的设计：

```typescript
async function* runShellCommand({...}): AsyncGenerator<Progress, ExecResult> {
  // 启动命令
  const shellCommand = await exec(command, ...)

  // 进度循环
  while (true) {
    const result = await Promise.race([resultPromise, progressSignal])
    if (result !== null) return result  // 完成
    yield { type: 'progress', ... }     // 报告进度
  }
}
```

这种设计的优势：
- **流式进度报告**：调用方可以实时接收进度更新
- **自然的超时处理**：`Promise.race` 天然支持超时
- **后台化支持**：通过闭包变量 `backgroundShellId` 实现状态切换
- **资源清理**：`finally` 块确保轮询停止

### 7.5 工具池组装策略

`assembleToolPool` 函数体现了工具池的组装策略：

```typescript
// src/tools.ts
export function assembleToolPool(permissionContext, mcpTools): Tools {
  const builtInTools = getTools(permissionContext)
  const allowedMcpTools = filterToolsByDenyRules(mcpTools, permissionContext)

  // 按名称排序，确保 prompt cache 稳定性
  const byName = (a, b) => a.name.localeCompare(b.name)
  return uniqBy(
    [...builtInTools].sort(byName).concat(allowedMcpTools.sort(byName)),
    'name',  // 内置工具优先（去重保留第一个）
  )
}
```

**关键设计**：
- 内置工具排在前面，MCP 工具排在后面
- 同名工具去重时内置工具优先
- 排序确保 prompt cache 稳定性（避免工具顺序变化导致 cache 失效）

## 8. 工程实践细节

### 8.1 懒加载 Schema

所有工具的 schema 都使用 `lazySchema` 包装：

```typescript
// src/utils/lazySchema.ts
export function lazySchema<T>(factory: () => T): () => T {
  let cached: T | undefined
  return () => {
    if (cached === undefined) cached = factory()
    return cached
  }
}
```

**为什么用懒加载？**
- 避免模块加载时的性能开销
- 支持运行时特性标志（feature flags）动态决定 schema 结构
- 避免循环依赖问题

### 8.2 特性标志驱动的工具注册

工具注册大量使用特性标志：

```typescript
// src/tools.ts
const SleepTool = feature('PROACTIVE') || feature('KAIROS')
  ? require('./tools/SleepTool/SleepTool.js').SleepTool
  : null

const cronTools = feature('AGENT_TRIGGERS')
  ? [CronCreateTool, CronDeleteTool, CronListTool]
  : []
```

这允许在构建时通过 dead code elimination 移除未启用的工具代码。

### 8.3 工具结果持久化

当工具输出超过阈值时，结果会被持久化到磁盘：

```typescript
// src/tools/BashTool/BashTool.tsx
const MAX_PERSISTED_SIZE = 64 * 1024 * 1024  // 64 MB

if (result.outputFilePath && result.outputTaskId) {
  const fileStat = await fsStat(result.outputFilePath)
  await ensureToolResultsDir()
  const dest = getToolResultPath(result.outputTaskId, false)
  if (fileStat.size > MAX_PERSISTED_SIZE) {
    await fsTruncate(result.outputFilePath, MAX_PERSISTED_SIZE)
  }
  try {
    await link(result.outputFilePath, dest)
  } catch {
    await copyFile(result.outputFilePath, dest)
  }
  persistedOutputPath = dest
}
```

### 8.4 sed 编辑的特殊处理

BashTool 可以检测 sed 就地编辑命令，并将其转化为 FileEditTool 的预览：

```typescript
// src/tools/BashTool/BashTool.tsx
userFacingName(input) {
  if (input.command) {
    const sedInfo = parseSedEditCommand(input.command)
    if (sedInfo) {
      return fileEditUserFacingName({ file_path: sedInfo.filePath, old_string: 'x' })
    }
  }
  return 'Bash'
}
```

当用户批准 sed 编辑后，系统使用 `_simulatedSedEdit` 字段直接写入文件，而不是实际执行 sed 命令——确保用户预览的内容和实际写入的内容完全一致。

### 8.5 安全环境变量白名单

`bashPermissions.ts` 维护了一个精心设计的环境变量白名单：

```typescript
const SAFE_ENV_VARS = new Set([
  // Go
  'GOEXPERIMENT', 'GOOS', 'GOARCH', 'CGO_ENABLED', 'GO111MODULE',
  // Rust
  'RUST_BACKTRACE', 'RUST_LOG',
  // Node
  'NODE_ENV',
  // Python
  'PYTHONUNBUFFERED', 'PYTHONDONTWRITEBYTECODE',
  // Locale
  'LANG', 'LANGUAGE', 'LC_ALL', 'LC_CTYPE', 'LC_TIME', 'CHARSET',
  // Terminal
  'TERM', 'COLORTERM', 'NO_COLOR', 'FORCE_COLOR', 'TZ',
  // ...
])
```

**安全注释明确说明了哪些变量绝对不能加入白名单**：
- `PATH`、`LD_PRELOAD`、`DYLD_*`（执行/库加载）
- `PYTHONPATH`、`NODE_PATH`（模块加载）
- `GOFLAGS`、`RUSTFLAGS`、`NODE_OPTIONS`（可包含代码执行标志）

## 9. 初学者易错点

### 9.1 忘记实现 `checkPermissions`

如果你自定义一个工具但没有实现 `checkPermissions`，`buildTool` 会提供默认实现——允许所有操作。这在开发阶段可能没问题，但在生产环境中是危险的。

```typescript
// ❌ 危险：依赖默认的 checkPermissions（允许所有操作）
export const MyTool = buildTool({
  name: 'my_tool',
  inputSchema: ...,
  async call(input) { ... },
})

// ✅ 安全：显式实现权限检查
export const MyTool = buildTool({
  name: 'my_tool',
  inputSchema: ...,
  async checkPermissions(input, context) {
    // 你的权限逻辑
    return { behavior: 'allow', updatedInput: input }
  },
  async call(input) { ... },
})
```

### 9.2 混淆 `validateInput` 和 `checkPermissions`

- `validateInput`：纯检查，不涉及权限。用于验证输入格式、文件存在性等。
- `checkPermissions`：权限检查。用于判断用户是否有权执行此操作。

```typescript
// ❌ 错误：在 validateInput 中做权限判断
async validateInput(input) {
  if (!userHasPermission(input.path)) {
    return { result: false, message: 'Permission denied' }
  }
}

// ✅ 正确：分离验证和权限
async validateInput(input) {
  if (!fileExists(input.path)) {
    return { result: false, message: 'File not found' }
  }
}
async checkPermissions(input, context) {
  return checkWritePermissionForTool(FileEditTool, input, ...)
}
```

### 9.3 不理解 `isConcurrencySafe` 的含义

`isConcurrencySafe` 表示工具是否可以与其他工具并发执行。只读工具通常可以并发，写入工具通常不行。

```typescript
// ❌ 错误：写入工具标记为可并发
isConcurrencySafe() { return true }

// ✅ 正确：只读工具可以并发
isConcurrencySafe() { return this.isReadOnly?.(input) ?? false }
```

### 9.4 忽略 `backfillObservableInput`

`backfillObservableInput` 在 hooks 和权限检查之前被调用，用于规范化输入。如果不实现，hooks 中的路径匹配可能失效。

```typescript
// ❌ 潜在问题：不规范化路径
// hooks 中的 allowlist 可能无法匹配 ~ 或相对路径

// ✅ 正确：规范化路径
backfillObservableInput(input) {
  if (typeof input.file_path === 'string') {
    input.file_path = expandPath(input.file_path)
  }
}
```

### 9.5 BashTool 安全检查中的常见绕过

初学者在理解 BashTool 安全机制时常见的误区：

1. **以为简单的 `echo` 是安全的**：`echo $(cat /etc/passwd)` 会执行命令替换
2. **不理解引号的差异**：单引号内的 `$()` 不展开，双引号内的会
3. **忽略环境变量前缀**：`FOO=bar rm -rf /` 仍然会执行 `rm -rf /`
4. **不理解 heredoc 的安全性**：`<<'EOF'`（引号包围的分隔符）才是安全的

## 10. 本章总结

### 10.1 核心要点回顾

1. **Tool 接口是整个系统的基石**：它定义了工具的行为契约，包括执行、权限、验证、UI 渲染等维度

2. **`buildTool` 工厂函数**：通过 fail-closed 默认值确保安全性，工具作者只需关注核心逻辑

3. **多层安全防御**：BashTool 的安全检查链包含 20+ 个验证器，覆盖了命令注入、路径遍历、解析差异等多种攻击向量

4. **工具池组装**：`assembleToolPool` 负责收集内置工具和 MCP 工具，通过 deny rules 过滤，按名称排序确保 cache 稳定性

5. **权限检查流程**：从 `validateInput`（输入验证）到 `checkPermissions`（权限检查）再到通用权限系统，形成完整的权限链

6. **异步生成器模式**：BashTool 使用 `AsyncGenerator` 实现命令执行，支持流式进度报告、超时处理和后台化

### 10.2 设计模式总结

| 模式 | 应用场景 | 代表实现 |
|------|----------|----------|
| 接口隔离 | Tool 接口设计 | `buildTool` + 默认值 |
| Fail-Closed | 安全默认值 | `isConcurrencySafe: false` |
| 多层防御 | BashTool 安全 | 20+ 验证器链 |
| 懒加载 | Schema 定义 | `lazySchema()` |
| 生成器 | 命令执行 | `runShellCommand` |
| 策略模式 | 权限检查 | `checkPermissions` |
| 观察者模式 | 文件读取通知 | `fileReadListeners` |
| 工厂模式 | 工具创建 | `buildTool` / `createSyntheticOutputTool` |

## 11. 延伸思考

### 11.1 工具系统的可扩展性

Claude Code 的工具系统设计支持多种扩展方式：

- **MCP 协议工具**：通过 MCP 服务器动态注册新工具
- **自定义 Agent**：通过 AgentTool 创建具有特定工具集的子 Agent
- **技能系统**：通过 SkillTool 加载和执行预定义的技能

这种分层设计使得系统可以不断扩展能力，同时保持核心接口的稳定性。

### 11.2 安全与可用性的平衡

BashTool 的安全机制展示了安全与可用性之间的永恒张力：

- **过度安全**：每个命令都需要用户批准，降低效率
- **过度宽松**：自动放行危险命令，带来安全风险
- **当前方案**：通过多层防御 + 用户可控的权限规则，在两者之间找到平衡

### 11.3 AI 安全的未来方向

当前的安全机制主要基于：
- **静态分析**（正则表达式、AST 解析）
- **规则匹配**（deny/ask/allow 规则）
- **AI 分类器**（使用 Haiku 模型判断安全性）

未来可能的发展方向：
- **形式化验证**：证明命令不会执行危险操作
- **能力限制**：在更细粒度上限制命令的能力（如 seccomp、AppArmor）
- **行为监控**：实时监控命令的系统调用，检测异常行为

### 11.4 工具结果的 token 优化

工具结果的大小直接影响 token 消耗。Claude Code 的优化策略包括：

- **结果持久化**：大结果写入磁盘，只发送预览
- **去重机制**：FileReadTool 的 `file_unchanged` stub
- **分页机制**：GrepTool 的 `head_limit` 和 `offset`
- **相对路径**：搜索结果中的绝对路径转换为相对路径

这些优化对于控制 API 成本和保持上下文窗口效率至关重要。
