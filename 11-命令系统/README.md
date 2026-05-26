# 第11章：命令系统

> "命令是用户与 Agent 之间的契约——每一条斜杠命令背后，都是一个精心设计的交互协议。"

## 1. 本章目标

本章将深入剖析 Claude Code 的命令系统（Command System），这是用户与 Claude Code 交互的核心入口之一。通过本章的学习，你将理解：

- 命令的注册、解析与执行的完整生命周期
- 三种命令类型（`prompt`、`local`、`local-jsx`）的设计哲学与实现差异
- 斜杠命令如何从用户输入到最终影响 Agent 行为的完整调用链
- 命令系统如何与 Skills、Plugins、MCP 等子系统协同工作
- 代表性命令（`/compact`、`/commit`、`/config`、`/diff`、`/btw`、`/review`）的实现细节

---

## 2. 前置知识

在阅读本章之前，你需要了解以下内容：

- **TypeScript 基础**：泛型、联合类型、`satisfies` 关键字、动态 `import()`
- **React/Ink 基础**：JSX 组件、`useState`、`useEffect`（用于理解 `local-jsx` 命令）
- **异步编程**：`Promise`、`async/await`、`for await...of` 异步迭代器
- **Memoization 模式**：`lodash-es/memoize` 的使用
- **Feature Flag 机制**：`bun:bundle` 的 `feature()` 函数用于条件编译

---

## 3. 宏观概览

### 3.1 命令系统的整体架构

Claude Code 的命令系统是一个典型的**注册-解析-分发**架构，但它的复杂度远超一般的 CLI 框架。系统需要同时处理：

1. **用户直接输入的斜杠命令**（如 `/compact`、`/help`）
2. **模型可调用的 Skill 命令**（如自定义的 `/verify` skill）
3. **来自 Plugin 和 MCP 服务器的外部命令**
4. **来自 Remote Control（移动端/网页端）的安全过滤命令**

整个命令系统的架构可以分为四层：

```
┌──────────────────────────────────────────────────────┐
│                  用户输入层                            │
│  handlePromptSubmit → processUserInput                │
│  (解析输入，判断是斜杠命令还是普通文本)                    │
├──────────────────────────────────────────────────────┤
│                  命令解析层                            │
│  parseSlashCommand → findCommand → getCommand         │
│  (从注册表中查找匹配的命令)                              │
├──────────────────────────────────────────────────────┤
│                  命令分发层                            │
│  processSlashCommand → getMessagesForSlashCommand     │
│  (根据命令类型分发到不同的处理逻辑)                      │
├──────────────────────────────────────────────────────┤
│                  命令执行层                            │
│  prompt: getPromptForCommand → 注入到对话              │
│  local: load() → call() → 返回文本结果                │
│  local-jsx: load() → call() → 返回 React 组件        │
│  fork: executeForkedSlashCommand → 子 Agent 执行      │
└──────────────────────────────────────────────────────┘
```

### 3.2 命令规模

Claude Code 的命令系统规模令人印象深刻：

- **101 个命令实现文件**（`src/commands/` 目录下）
- **3 种命令类型**：`prompt`、`local`、`local-jsx`
- **多个命令来源**：内置（builtin）、Skills 目录、Plugin、MCP、bundled
- **条件编译**：通过 `feature()` 实现的 Dead Code Elimination，确保不同构建版本只包含相关命令

### 3.3 命令的三种类型

理解命令系统的关键在于理解三种命令类型的设计哲学：

| 类型 | 执行方式 | 是否触发模型查询 | 典型用途 |
|------|---------|----------------|---------|
| `prompt` | 内容注入到对话中 | ✅ 是 | `/commit`、`/init`、`/review` |
| `local` | 直接执行函数 | ❌ 否 | `/compact`、`/clear` |
| `local-jsx` | 渲染 React 组件 | ❌ 否 | `/config`、`/diff`、`/btw`、`/help` |

这种三分法体现了一个核心设计思想：**有些命令需要模型参与（prompt），有些命令需要直接操作（local），有些命令需要丰富的 UI 交互（local-jsx）**。

---

## 4. 源码入口定位

### 4.1 命令注册中心

**文件**：`src/commands.ts`（754 行）

这是整个命令系统的"大脑"，负责：
- 导入所有内置命令（100+ 个 `import` 语句）
- 定义 `COMMANDS` 数组（通过 `memoize` 缓存）
- 提供 `getCommands()` 函数聚合所有命令来源
- 提供命令查找函数（`findCommand`、`getCommand`、`hasCommand`）
- 定义安全过滤集（`REMOTE_SAFE_COMMANDS`、`BRIDGE_SAFE_COMMANDS`）

### 4.2 命令类型定义

**文件**：`src/types/command.ts`

定义了 `Command` 联合类型和所有相关的类型别名，是理解命令系统的类型基础。

### 4.3 斜杠命令解析

**文件**：`src/utils/slashCommandParsing.ts`

简洁的解析器，将 `/command args` 分解为 `{ commandName, args, isMcp }`。

### 4.4 用户输入处理

**文件**：`src/utils/processUserInput/processUserInput.ts`（约 400 行）

用户输入的第一站，判断输入类型并路由到对应的处理器。

### 4.5 斜杠命令分发

**文件**：`src/utils/processUserInput/processSlashCommand.tsx`（922 行）

命令系统的核心分发逻辑，根据命令类型执行不同的处理路径。

### 4.6 Skills 加载

**文件**：`src/skills/loadSkillsDir.ts`

从文件系统加载 SKILL.md 文件并解析为 `Command` 对象。

### 4.7 SkillTool（模型调用 Skills 的工具）

**文件**：`src/tools/SkillTool/SkillTool.ts`

模型通过此工具调用 Skills，是"命令如何与 Agent 交互"的关键桥梁。

---

## 5. 调用链分析

### 5.1 用户输入斜杠命令的完整调用链

当用户在 REPL 中输入 `/compact` 并按下回车时，调用链如下：

```
用户输入 "/compact"
    │
    ▼
handlePromptSubmit()                    [handlePromptSubmit.ts]
    │ 检查是否是 exit 命令
    │ 检查是否是 immediate 命令（如 /config、/btw）
    │ 如果 queryGuard 正忙 → enqueue 到队列
    │
    ▼
executeUserInput()                      [handlePromptSubmit.ts]
    │ 创建 AbortController
    │ 保留 queryGuard
    │
    ▼
processUserInput()                      [processUserInput.ts]
    │ 处理图片、粘贴内容、引用
    │
    ▼
processUserInputBase()                  [processUserInput.ts]
    │ 检查是否以 "/" 开头
    │ 检查 skipSlashCommands 标志
    │ 检查 bridgeOrigin 安全过滤
    │
    ▼
processSlashCommand()                   [processSlashCommand.tsx]
    │ parseSlashCommand() 解析命令名和参数
    │ findCommand() 从注册表查找命令
    │ hasCommand() 检查是否存在
    │
    ▼
getMessagesForSlashCommand()            [processSlashCommand.tsx]
    │ 根据 command.type 分发：
    │
    ├── case 'prompt':
    │   ├── context === 'fork' → executeForkedSlashCommand()
    │   │   └── runAgent() 启动子 Agent 执行
    │   └── else → getMessagesForPromptSlashCommand()
    │       ├── command.getPromptForCommand() 获取提示内容
    │       ├── registerSkillHooks() 注册钩子
    │       ├── addInvokedSkill() 记录调用
    │       └── 返回 messages + shouldQuery: true
    │
    ├── case 'local':
    │   ├── command.load() 懒加载实现
    │   ├── mod.call(args, context) 执行
    │   ├── result.type === 'compact' → buildPostCompactMessages()
    │   ├── result.type === 'skip' → 返回空消息
    │   └── result.type === 'text' → 包装为 <local-command-stdout>
    │
    └── case 'local-jsx':
        ├── command.load() 懒加载实现
        ├── mod.call(onDone, context, args) 渲染 JSX
        ├── 非交互模式 → 返回空消息
        └── 交互模式 → setToolJSX() 显示 UI 组件
```

### 5.2 模型调用 Skill 的调用链

当模型通过 SkillTool 调用一个 Skill 时：

```
模型决定调用 Skill "verify"
    │
    ▼
SkillTool.call({ skill: "verify" })     [SkillTool.ts]
    │
    ▼
findCommand("verify", commands)         [commands.ts]
    │ 从注册表查找命令
    │
    ▼
prepareForkedCommandContext()           [forkedAgent.ts]
    │ 获取 Skill 内容
    │ 构建 Agent 定义
    │
    ▼
runAgent()                              [AgentTool/runAgent.ts]
    │ 启动子 Agent
    │ Skill 内容作为系统提示注入
    │ Agent 使用可用工具执行任务
    │
    ▼
extractResultText()                     [forkedAgent.ts]
    │ 提取执行结果
    │
    ▼
返回 ToolResult 给主对话
```

---

## 6. 核心源码解析

### 6.1 命令类型定义（`src/types/command.ts`）

命令系统的类型基础定义了三种命令类型的精确形状：

```typescript
// PromptCommand — 内容注入到对话中，触发模型查询
export type PromptCommand = {
  type: 'prompt'
  progressMessage: string           // 执行时显示的进度消息
  contentLength: number             // 内容长度（用于 token 估算）
  argNames?: string[]               // 参数名列表
  allowedTools?: string[]           // 允许使用的工具列表
  model?: string                    // 可选的模型覆盖
  source: SettingSource | 'builtin' | 'mcp' | 'plugin' | 'bundled'
  context?: 'inline' | 'fork'      // 执行上下文：内联或子 Agent
  agent?: string                    // fork 模式下的 Agent 类型
  effort?: EffortValue              // 推理努力级别
  paths?: string[]                  // 文件路径匹配模式
  getPromptForCommand(              // 获取提示内容的核心方法
    args: string,
    context: ToolUseContext,
  ): Promise<ContentBlockParam[]>
}

// LocalCommand — 直接执行函数，返回文本结果
type LocalCommand = {
  type: 'local'
  supportsNonInteractive: boolean
  load: () => Promise<LocalCommandModule>  // 懒加载实现
}

// LocalJSXCommand — 渲染 React 组件
type LocalJSXCommand = {
  type: 'local-jsx'
  load: () => Promise<LocalJSXCommandModule>  // 懒加载实现
}
```

**设计亮点**：`load()` 返回 `Promise<Module>` 而不是直接暴露函数，这是一种**懒加载模式**。命令的实现模块（可能包含大量依赖）只有在命令被实际调用时才会被 `import()`，从而优化启动性能。

```typescript
// CommandBase — 所有命令共享的基础属性
export type CommandBase = {
  name: string                      // 命令名称
  description: string               // 命令描述
  aliases?: string[]                // 别名（如 /settings → /config）
  isEnabled?: () => boolean         // 是否启用（默认 true）
  isHidden?: boolean                // 是否从 typeahead/help 隐藏
  availability?: CommandAvailability[]  // 可用性约束
  argumentHint?: string             // 参数提示（如 "<optional instructions>"）
  whenToUse?: string                // 何时使用（来自 Skill 规范）
  disableModelInvocation?: boolean  // 禁止模型调用
  userInvocable?: boolean           // 用户可否直接调用
  loadedFrom?: 'commands_DEPRECATED' | 'skills' | 'plugin' | 'managed' | 'bundled' | 'mcp'
  immediate?: boolean               // 是否立即执行（绕过队列）
  isSensitive?: boolean             // 参数是否敏感（显示为 ***）
}

// 最终的 Command 类型是基础类型与三种类型之一的交叉
export type Command = CommandBase & (PromptCommand | LocalCommand | LocalJSXCommand)
```

**设计亮点**：`satisfies` 关键字的使用。每个命令文件都使用 `satisfies Command` 来确保类型安全，同时保留具体的类型推断：

```typescript
const compact = {
  type: 'local',
  name: 'compact',
  description: '...',
  supportsNonInteractive: true,
  load: () => import('./compact.js'),
} satisfies Command
```

### 6.2 命令注册中心（`src/commands.ts`）

#### 6.2.1 导入策略

文件顶部有 100+ 个 import 语句，但它们被精心组织为三类：

**1. 普通静态导入**（大部分命令）：
```typescript
import addDir from './commands/add-dir/index.js'
import compact from './commands/compact/index.js'
import commit from './commands/commit.js'
```

**2. 条件编译导入**（通过 `feature()` 控制）：
```typescript
const proactive = feature('PROACTIVE') || feature('KAIROS')
  ? require('./commands/proactive.js').default
  : null

const voiceCommand = feature('VOICE_MODE')
  ? require('./commands/voice/index.js').default
  : null
```

这里使用 `require()` 而非 `import()` 是因为 Bun 的 bundler 在编译时需要同步解析——`feature()` 返回的是编译时常量，bundler 可以在构建阶段消除 dead code。

**3. 环境条件导入**：
```typescript
const agentsPlatform = process.env.USER_TYPE === 'ant'
  ? require('./commands/agents-platform/index.js').default
  : null
```

#### 6.2.2 COMMANDS 数组

```typescript
const COMMANDS = memoize((): Command[] => [
  addDir, advisor, agents, branch, btw, chrome, clear, color,
  compact, config, copy, desktop, context, contextNonInteractive,
  cost, diff, doctor, effort, exit, fast, files, heapDump, help,
  ide, init, keybindings, installGitHubApp, installSlackApp,
  mcp, memory, mobile, model, outputStyle, remoteEnv, plugin,
  pr_comments, releaseNotes, reloadPlugins, rename, resume,
  session, skills, stats, status, statusline, stickers, tag,
  theme, feedback, review, ultrareview, rewind, securityReview,
  terminalSetup, upgrade, extraUsage, extraUsageNonInteractive,
  rateLimitOptions, usage, usageReport, vim,
  // 条件命令
  ...(webCmd ? [webCmd] : []),
  ...(forkCmd ? [forkCmd] : []),
  ...(buddy ? [buddy] : []),
  // ... 更多条件命令
  // 内部命令（仅 ant 用户）
  ...(process.env.USER_TYPE === 'ant' && !process.env.IS_DEMO
    ? INTERNAL_ONLY_COMMANDS
    : []),
])
```

**关键设计**：`COMMANDS` 是一个 `memoize` 包装的函数而非静态数组。注释解释了原因："Declared as a function so that we don't run this until getCommands is called, since underlying functions read from config, which can't be read at module initialization time"。这是因为某些命令的 `isEnabled()` 依赖运行时配置，不能在模块加载时求值。

#### 6.2.3 getCommands() — 命令聚合

```typescript
export async function getCommands(cwd: string): Promise<Command[]> {
  const allCommands = await loadAllCommands(cwd)
  const dynamicSkills = getDynamicSkills()

  const baseCommands = allCommands.filter(
    _ => meetsAvailabilityRequirement(_) && isCommandEnabled(_),
  )

  if (dynamicSkills.length === 0) {
    return baseCommands
  }

  // 去重动态 Skills
  const baseCommandNames = new Set(baseCommands.map(c => c.name))
  const uniqueDynamicSkills = dynamicSkills.filter(
    s => !baseCommandNames.has(s.name) &&
         meetsAvailabilityRequirement(s) &&
         isCommandEnabled(s),
  )

  // 插入动态 Skills 到 Plugin Skills 之后、内置命令之前
  const builtInNames = new Set(COMMANDS().map(c => c.name))
  const insertIndex = baseCommands.findIndex(c => builtInNames.has(c.name))
  // ...
}
```

这个函数实现了命令的**多源聚合**：bundled skills → builtin plugin skills → skill dir commands → workflow commands → plugin commands → plugin skills → built-in commands。动态 Skills 被插入到 Plugin Skills 和内置命令之间，确保优先级正确。

#### 6.2.4 命令查找

```typescript
export function findCommand(
  commandName: string,
  commands: Command[],
): Command | undefined {
  return commands.find(
    _ => _.name === commandName ||
         getCommandName(_) === commandName ||
         _.aliases?.includes(commandName),
  )
}
```

查找逻辑支持三种匹配：原始 `name`、`userFacingName()`（可能去除了 plugin 前缀）、和 `aliases`。

#### 6.2.5 可用性过滤

```typescript
export function meetsAvailabilityRequirement(cmd: Command): boolean {
  if (!cmd.availability) return true  // 无约束 → 全局可用
  for (const a of cmd.availability) {
    switch (a) {
      case 'claude-ai':
        if (isClaudeAISubscriber()) return true
        break
      case 'console':
        if (!isClaudeAISubscriber() && !isUsing3PServices() &&
            isFirstPartyAnthropicBaseUrl())
          return true
        break
    }
  }
  return false
}
```

**设计亮点**：可用性检查不是 memoized 的，因为"auth state can change mid-session (e.g. after /login), so this must be re-evaluated on every getCommands() call"。这体现了对状态变化的精确处理。

### 6.3 斜杠命令解析（`src/utils/slashCommandParsing.ts`）

```typescript
export function parseSlashCommand(input: string): ParsedSlashCommand | null {
  const trimmedInput = input.trim()
  if (!trimmedInput.startsWith('/')) return null

  const withoutSlash = trimmedInput.slice(1)
  const words = withoutSlash.split(' ')

  if (!words[0]) return null

  let commandName = words[0]
  let isMcp = false
  let argsStartIndex = 1

  // MCP 命令的特殊格式："/mcp:tool (MCP) arg1 arg2"
  if (words.length > 1 && words[1] === '(MCP)') {
    commandName = commandName + ' (MCP)'
    isMcp = true
    argsStartIndex = 2
  }

  const args = words.slice(argsStartIndex).join(' ')
  return { commandName, args, isMcp }
}
```

解析器非常简洁，但有一个值得注意的细节：MCP 命令使用 `(MCP)` 后缀来标识，这是因为 MCP 工具名可能包含冒号（如 `mcp:tool`），需要额外的标记来区分。

### 6.4 命令分发（`src/utils/processUserInput/processSlashCommand.tsx`）

这是命令系统中最复杂的文件（922 行），包含了三种命令类型的完整分发逻辑。

#### 6.4.1 processSlashCommand — 入口函数

```typescript
export async function processSlashCommand(
  inputString: string,
  precedingInputBlocks: ContentBlockParam[],
  imageContentBlocks: ContentBlockParam[],
  attachmentMessages: AttachmentMessage[],
  context: ProcessUserInputContext,
  setToolJSX: SetToolJSXFn,
  uuid?: string,
  isAlreadyProcessing?: boolean,
  canUseTool?: CanUseToolFn,
): Promise<ProcessUserInputBaseResult> {
  const parsed = parseSlashCommand(inputString)
  if (!parsed) {
    // 格式错误
    return { messages: [...], shouldQuery: false, resultText: errorMessage }
  }

  const { commandName, args, isMcp } = parsed

  // 命令不存在
  if (!hasCommand(commandName, context.options.commands)) {
    // 检查是否是文件路径
    let isFilePath = false
    try {
      await getFsImplementation().stat(`/${commandName}`)
      isFilePath = true
    } catch {}

    if (looksLikeCommand(commandName) && !isFilePath) {
      return { messages: [...], shouldQuery: false, resultText: unknownMessage }
    }
    // 不是命令也不是文件路径 → 当作普通文本发给模型
    return { messages: [...], shouldQuery: true }
  }

  // 分发到具体的命令处理
  const result = await getMessagesForSlashCommand(
    commandName, parsedArgs, setToolJSX, context, ...
  )
  // ...
}
```

**关键设计**：当输入以 `/` 开头但不是已知命令时，系统会检查它是否是文件路径（如 `/var/log`）。如果是文件路径或不像命令名，就当作普通文本发给模型。这避免了用户输入文件路径时被误判为命令。

#### 6.4.2 getMessagesForSlashCommand — 核心分发

```typescript
async function getMessagesForSlashCommand(
  commandName: string, args: string, ...
): Promise<SlashCommandResult> {
  const command = getCommand(commandName, context.options.commands)

  // 检查是否用户可调用
  if (command.userInvocable === false) {
    return {
      messages: [...createUserMessage({
        content: `This skill can only be invoked by Claude, not directly by users.`
      })],
      shouldQuery: false, command
    }
  }

  switch (command.type) {
    case 'local-jsx':
      return new Promise<SlashCommandResult>(resolve => {
        let doneWasCalled = false
        const onDone = (result, options) => {
          doneWasCalled = true
          // 根据 display 选项处理结果
          if (options?.display === 'skip') {
            void resolve({ messages: [], shouldQuery: false, command })
            return
          }
          // 包装为用户消息
          void resolve({
            messages: [userMessage, stdoutMessage, ...metaMessages],
            shouldQuery: options?.shouldQuery ?? false,
            command
          })
        }

        void command.load().then(mod =>
          mod.call(onDone, { ...context, canUseTool }, args)
        ).then(jsx => {
          if (jsx == null || doneWasCalled) return
          setToolJSX({
            jsx, shouldHidePromptInput: true,
            isLocalJSXCommand: true, isImmediate: command.immediate
          })
        }).catch(e => {
          // 防止 Promise 挂起导致死锁
          if (doneWasCalled) return
          doneWasCalled = true
          setToolJSX({ jsx: null, clearLocalJSX: true })
          void resolve({ messages: [], shouldQuery: false, command })
        })
      })

    case 'local':
      const mod = await command.load()
      const result = await mod.call(args, context)
      if (result.type === 'compact') {
        // 特殊处理：压缩结果需要重建消息数组
        return { messages: buildPostCompactMessages(...), shouldQuery: false, command }
      }
      if (result.type === 'skip') {
        return { messages: [], shouldQuery: false, command }
      }
      return {
        messages: [userMessage, createCommandInputMessage(stdout)],
        shouldQuery: false, command, resultText: result.value
      }

    case 'prompt':
      if (command.context === 'fork') {
        // 在子 Agent 中执行
        return await executeForkedSlashCommand(command, args, context, ...)
      }
      // 内联执行：获取提示内容，注入到对话
      return await getMessagesForPromptSlashCommand(command, args, context, ...)
  }
}
```

**关键设计亮点**：

1. **`local-jsx` 的 Promise 包装**：由于 JSX 命令的 `onDone` 回调可能在任何时候被调用（同步或异步），使用 `new Promise` 包装确保了统一的异步接口。

2. **`doneWasCalled` 守卫**：防止 `onDone` 在 `mod.call()` 期间被同步调用后，`then(jsx)` 又设置 `setToolJSX`，导致 `isLocalJSXCommand` 卡在 `true` 状态。

3. **`prompt` 的 fork 模式**：当 `command.context === 'fork'` 时，命令在子 Agent 中执行，这对于耗时较长的 Skill（如 `/commit`）非常重要。

#### 6.4.3 executeForkedSlashCommand — 子 Agent 执行

```typescript
async function executeForkedSlashCommand(
  command: CommandBase & PromptCommand,
  args: string, context, ...
): Promise<SlashCommandResult> {
  const agentId = createAgentId()

  const { skillContent, modifiedGetAppState, baseAgent, promptMessages } =
    await prepareForkedCommandContext(command, args, context)

  // 合并 Skill 的 effort 设置
  const agentDefinition = command.effort !== undefined
    ? { ...baseAgent, effort: command.effort }
    : baseAgent

  // Assistant 模式：后台执行，不阻塞用户输入
  if (feature('KAIROS') && (await context.getAppState()).kairosEnabled) {
    const bgAbortController = createAbortController()
    const spawnTimeWorkload = getWorkload()

    // 后台执行，结果通过队列返回
    void (async () => {
      // 等待 MCP 服务器就绪
      const deadline = Date.now() + MCP_SETTLE_TIMEOUT_MS
      while (Date.now() < deadline) {
        if (!s.mcp.clients.some(c => c.type === 'pending')) break
        await sleep(MCP_SETTLE_POLL_MS)
      }

      for await (const message of runAgent({...})) {
        agentMessages.push(message)
      }

      // 将结果作为 isMeta 消息入队
      enqueueResult(`<scheduled-task-result command="/${commandName}">...`)
    })().catch(err => {
      enqueueResult(`<scheduled-task-result ... status="failed">...`)
    })

    return { messages: [], shouldQuery: false, command }
  }

  // 同步模式：显示进度 UI，等待完成
  for await (const message of runAgent({...})) {
    agentMessages.push(message)
    if (message.type === 'assistant') {
      progressMessages.push(createProgressMessage(message))
      updateProgress()  // 更新进度 UI
    }
  }

  return { messages: [...], shouldQuery: false, command, resultText }
}
```

**关键设计**：

1. **MCP 就绪等待**：后台 fork 命令在启动时可能遇到 MCP 服务器尚未连接的情况（因为多个定时任务同时启动）。通过轮询等待 `pending` 状态的客户端消失，确保工具可用。

2. **双模式执行**：Kairos 模式（assistant 模式）下后台执行不阻塞用户，结果通过 `enqueuePendingNotification` 异步返回；普通模式下同步执行并显示进度 UI。

### 6.5 提示命令的执行（`getMessagesForPromptSlashCommand`）

```typescript
async function getMessagesForPromptSlashCommand(
  command: CommandBase & PromptCommand,
  args: string, context, ...
): Promise<SlashCommandResult> {
  const result = await command.getPromptForCommand(args, context)

  // 注册 Skill 钩子
  if (command.hooks && hooksAllowedForThisSkill) {
    registerSkillHooks(context.setAppState, sessionId, command.hooks, ...)
  }

  // 记录 Skill 调用（用于压缩时保留）
  addInvokedSkill(command.name, skillPath, skillContent, agentId)

  // 构建消息
  const messages = [
    createUserMessage({ content: metadata, uuid }),           // 命令元数据
    createUserMessage({ content: mainMessageContent, isMeta: true }),  // Skill 内容（模型可见，用户不可见）
    ...attachmentMessages,                                     // 附件
    createAttachmentMessage({ type: 'command_permissions',     // 权限
      allowedTools: additionalAllowedTools, model: command.model }),
  ]

  return {
    messages, shouldQuery: true,
    allowedTools: additionalAllowedTools,
    model: command.model, effort: command.effort, command
  }
}
```

**关键设计**：

1. **`isMeta: true`**：Skill 内容对模型可见但对用户隐藏。用户只看到 `/commit` 这个命令输入，而模型看到完整的 Git 操作指令。

2. **`command_permissions` 附件**：通过 `allowedTools` 字段，Skill 可以声明它需要的工具权限（如 `/commit` 需要 `Bash(git add:*)`、`Bash(git commit:*)` 等）。

3. **`addInvokedSkill`**：记录被调用的 Skill，以便在压缩（compact）时保留这些 Skill 的内容。

### 6.6 用户输入处理入口（`processUserInput.ts`）

```typescript
export async function processUserInput({
  input, mode, setToolJSX, context, ...
}): Promise<ProcessUserInputBaseResult> {
  // ... 图片处理、粘贴内容处理 ...

  // Bash 命令模式
  if (inputString !== null && mode === 'bash') {
    return await processBashCommand(inputString, ...)
  }

  // 斜杠命令
  if (inputString !== null && !effectiveSkipSlash && inputString.startsWith('/')) {
    return await processSlashCommand(inputString, ...)
  }

  // 普通文本提示
  return processTextPrompt(normalizedInput, ...)
}
```

**Bridge 安全过滤**的关键逻辑：

```typescript
// 移动端/网页端通过 Remote Control 发送的命令需要安全过滤
if (bridgeOrigin && inputString !== null && inputString.startsWith('/')) {
  const parsed = parseSlashCommand(inputString)
  const cmd = parsed ? findCommand(parsed.commandName, context.options.commands) : undefined
  if (cmd) {
    if (isBridgeSafeCommand(cmd)) {
      effectiveSkipSlash = false  // 允许执行
    } else {
      // 不安全的命令 → 返回错误消息
      return { messages: [...], shouldQuery: false, resultText: msg }
    }
  }
}
```

### 6.7 handlePromptSubmit — immediate 命令的快速路径

```typescript
// 在 handlePromptSubmit 中，immediate 命令有特殊的快速路径
if (!skipSlashCommands && finalInput.trim().startsWith('/')) {
  const immediateCommand = commands.find(
    cmd => cmd.immediate && isCommandEnabled(cmd) && (
      cmd.name === commandName || cmd.aliases?.includes(commandName)
    )
  )

  if (immediateCommand && immediateCommand.type === 'local-jsx' &&
      (queryGuard.isActive || isExternalLoading)) {
    // 立即执行，不等待队列
    const impl = await immediateCommand.load()
    const jsx = await impl.call(onDone, context, commandArgs)
    if (jsx && !doneWasCalled) {
      setToolJSX({ jsx, isLocalJSXCommand: true, isImmediate: true })
    }
    return
  }
}
```

`immediate: true` 标志（如 `/btw`、`/model`）允许命令在模型正在生成响应时也能立即执行，无需等待当前轮次结束。这对于"不打断主对话"的侧问（`/btw`）至关重要。

---

### 6.8 代表性命令分析

#### 6.8.1 `/compact` — 压缩命令（`commands/compact/`）

**命令声明**（`index.ts`）：
```typescript
const compact = {
  type: 'local',
  name: 'compact',
  description: 'Clear conversation history but keep a summary in context...',
  isEnabled: () => !isEnvTruthy(process.env.DISABLE_COMPACT),
  supportsNonInteractive: true,
  argumentHint: '<optional custom summarization instructions>',
  load: () => import('./compact.js'),
} satisfies Command
```

**核心实现**（`compact.ts`）：

`/compact` 是最复杂的 `local` 命令之一，它的执行流程如下：

```typescript
export const call: LocalCommandCall = async (args, context) => {
  let { messages } = context
  messages = getMessagesAfterCompactBoundary(messages)  // 排除已压缩的消息

  // 1. 尝试 Session Memory 压缩（轻量级）
  if (!customInstructions) {
    const sessionMemoryResult = await trySessionMemoryCompaction(messages, context.agentId)
    if (sessionMemoryResult) {
      return { type: 'compact', compactionResult: sessionMemoryResult, ... }
    }
  }

  // 2. 尝试 Reactive 压缩（如果启用）
  if (reactiveCompact?.isReactiveOnlyMode()) {
    return await compactViaReactive(messages, context, customInstructions, reactiveCompact)
  }

  // 3. 传统压缩：先微压缩，再全量压缩
  const microcompactResult = await microcompactMessages(messages, context)
  const result = await compactConversation(
    microcompactResult.messages, context, cacheSafeParams, ...
  )

  return { type: 'compact', compactionResult: result, ... }
}
```

**设计亮点**：

1. **三级压缩策略**：Session Memory → Reactive → Traditional，逐级递进
2. **微压缩**（Microcompact）：在全量压缩前先进行轻量级的消息清理
3. **`type: 'compact'` 返回值**：特殊的返回类型，告诉分发层需要重建消息数组
4. **`suppressCompactWarning`**：压缩后立即抑制"剩余上下文"警告
5. **缓存失效**：压缩后清除 `getUserContext` 缓存，确保后续请求使用新的上下文

#### 6.8.2 `/commit` — 提交命令（`commands/commit.ts`）

`/commit` 是一个典型的 `prompt` 类型命令——它不直接执行 Git 操作，而是生成一个详细的提示让模型来执行：

```typescript
const command = {
  type: 'prompt',
  name: 'commit',
  description: 'Create a git commit',
  allowedTools: [
    'Bash(git add:*)',
    'Bash(git status:*)',
    'Bash(git commit:*)',
  ],
  contentLength: 0,
  progressMessage: 'creating commit',
  source: 'builtin',
  async getPromptForCommand(_args, context) {
    const promptContent = getPromptContent()
    // 执行提示中的 shell 命令（如 !`git status`）
    const finalContent = await executeShellCommandsInPrompt(
      promptContent, { ...context, getAppState() { ... } }, '/commit',
    )
    return [{ type: 'text', text: finalContent }]
  },
} satisfies Command
```

**getPromptContent() 的核心逻辑**：

```typescript
function getPromptContent(): string {
  return `## Context
- Current git status: !\`git status\`
- Current git diff: !\`git diff HEAD\`
- Current branch: !\`git branch --show-current\`
- Recent commits: !\`git log --oneline -10\`

## Git Safety Protocol
- NEVER update the git config
- NEVER skip hooks
- CRITICAL: ALWAYS create NEW commits
- Do not commit files that likely contain secrets

## Your task
Based on the above changes, create a single git commit:
1. Analyze all staged changes and draft a commit message
2. Stage relevant files and create the commit using HEREDOC syntax`
}
```

**设计亮点**：

1. **`!\`command\`` 语法**：`executeShellCommandsInPrompt` 会在发送前执行这些嵌入式 shell 命令，将实时的 Git 状态注入到提示中。
2. **`allowedTools`**：精确限制模型只能使用 Git 相关命令，防止误操作。
3. **安全协议**：明确禁止 `--amend`、`--no-verify` 等危险操作。
4. **`source: 'builtin'`**：标记为内置命令，在 `getSkillToolCommands` 中被过滤（模型不会把它当作 Skill 调用）。

#### 6.8.3 `/config` — 配置命令（`commands/config/`）

```typescript
// index.ts
const config = {
  aliases: ['settings'],
  type: 'local-jsx',
  name: 'config',
  description: 'Open config panel',
  load: () => import('./config.js'),
} satisfies Command

// config.tsx
export const call: LocalJSXCommandCall = async (onDone, context) => {
  return <Settings onClose={onDone} context={context} defaultTab="Config" />
}
```

`/config` 是最简单的 `local-jsx` 命令——它只是渲染一个 React 组件。但正是这种简洁性展示了 `local-jsx` 类型的威力：通过 Ink 框架，命令可以拥有完整的终端 UI 交互能力。

#### 6.8.4 `/diff` — 差异查看（`commands/diff/`）

```typescript
export default {
  type: 'local-jsx',
  name: 'diff',
  description: 'View uncommitted changes and per-turn diffs',
  load: () => import('./diff.js'),
} satisfies Command
```

`/diff` 同样是 `local-jsx` 类型，它渲染一个差异查看器 UI，允许用户浏览未提交的更改和每轮对话的代码变更。

#### 6.8.5 `/btw` — 侧问命令（`commands/btw/`）

```typescript
const btw = {
  type: 'local-jsx',
  name: 'btw',
  description: 'Ask a quick side question without interrupting the main conversation',
  immediate: true,  // 关键：立即执行
  argumentHint: '<question>',
  load: () => import('./btw.js'),
} satisfies Command
```

`/btw` 的独特之处在于 `immediate: true`。这意味着即使模型正在生成响应，用户也可以输入 `/btw 这个函数的作用是什么？` 来问一个不打断主对话的侧问。

**实现细节**（`btw.tsx`）：

```typescript
function BtwSideQuestion({ question, context, onDone }) {
  const [response, setResponse] = useState(null)
  // ... 动画帧、滚动等 UI 状态 ...

  useEffect(() => {
    // 启动一个独立的侧问上下文
    runSideQuestion(question, context, cacheSafeParams)
      .then(response => setResponse(response))
      .catch(err => setError(err))
  }, [])

  // 渲染 Markdown 响应，支持滚动
  return (
    <ScrollBox ref={scrollRef} height={availableRows}>
      <Markdown content={response} />
    </ScrollBox>
  )
}
```

#### 6.8.6 `/review` — 代码审查（`commands/review.ts`）

```typescript
const review: Command = {
  type: 'prompt',
  name: 'review',
  description: 'Review a pull request',
  progressMessage: 'reviewing pull request',
  contentLength: 0,
  source: 'builtin',
  async getPromptForCommand(args): Promise<ContentBlockParam[]> {
    return [{ type: 'text', text: LOCAL_REVIEW_PROMPT(args) }]
  },
}

const ultrareview: Command = {
  type: 'local-jsx',
  name: 'ultrareview',
  description: '~10–20 min · Finds and verifies bugs...',
  isEnabled: () => isUltrareviewEnabled(),
  load: () => import('./review/ultrareviewCommand.js'),
}
```

`/review` 和 `/ultrareview` 展示了同一个功能域的两种命令类型：
- `/review`（prompt）：生成审查提示，让模型使用 `gh` CLI 工具执行
- `/ultrareview`（local-jsx）：渲染一个 UI 组件，可能触发远程的 Claude Code on the web 服务

#### 6.8.7 `/agents` — Agent 管理（`commands/agents/`）

```typescript
// index.ts
const agents = {
  type: 'local-jsx',
  name: 'agents',
  description: 'Manage agent configurations',
  load: () => import('./agents.js'),
} satisfies Command

// agents.tsx
export async function call(onDone, context): Promise<React.ReactNode> {
  const appState = context.getAppState()
  const permissionContext = appState.toolPermissionContext
  const tools = getTools(permissionContext)
  return <AgentsMenu tools={tools} onExit={onDone} />
}
```

`/agents` 渲染一个 Agent 管理菜单，允许用户查看和配置 Agent。注意它需要从 `getTools()` 获取可用工具列表，这体现了命令与工具系统的交互。

#### 6.8.8 `/init` — 项目初始化（`commands/init.ts`）

`/init` 是最长的 `prompt` 命令之一（约 200 行提示文本），它引导模型完成一个多阶段的项目设置流程：

1. **Phase 1**：询问用户要设置什么（CLAUDE.md、skills、hooks）
2. **Phase 2**：探索代码库（读取 package.json、README 等）
3. **Phase 3**：填补空白（通过 AskUserQuestion 工具询问）
4. **Phase 4-7**：创建文件（CLAUDE.md、CLAUDE.local.md、skills、hooks）
5. **Phase 8**：总结和后续建议

```typescript
const command = {
  type: 'prompt',
  name: 'init',
  get description() {
    return feature('NEW_INIT') && ... ? 'Initialize new CLAUDE.md...' : 'Initialize a new CLAUDE.md...'
  },
  contentLength: 0,
  progressMessage: 'analyzing your codebase',
  source: 'builtin',
  async getPromptForCommand() {
    maybeMarkProjectOnboardingComplete()
    return [{
      type: 'text',
      text: feature('NEW_INIT') && ... ? NEW_INIT_PROMPT : OLD_INIT_PROMPT
    }]
  },
} satisfies Command
```

**设计亮点**：`description` 使用 `get` getter，根据 feature flag 动态返回不同的描述文本。

---

## 7. 架构设计思想

### 7.1 三种命令类型的设计哲学

命令系统的三分法不是随意的，它反映了三种根本不同的交互模式：

**`prompt` — "让模型来做"**
- 适用于需要 AI 推理的任务（如代码审查、Git 提交）
- 通过 `getPromptForCommand()` 生成详细的提示
- 可以声明 `allowedTools` 限制模型的工具使用
- 可以指定 `model` 和 `effort` 覆盖默认设置

**`local` — "直接执行"**
- 适用于确定性的操作（如压缩、清除历史）
- 通过 `load().call()` 直接执行函数
- 返回值可以是文本、压缩结果或跳过信号
- 不触发模型查询

**`local-jsx` — "丰富的 UI 交互"**
- 适用于需要用户交互的场景（如配置面板、差异查看器）
- 通过 Ink 渲染 React 组件
- 通过 `onDone` 回调通知完成
- 支持 `immediate` 标志实现不打断主对话

### 7.2 懒加载模式

几乎所有命令都使用 `load: () => import('./command.js')` 模式。这是因为：

1. **启动性能**：101 个命令的实现模块可能包含大量依赖（React 组件、Git 库等），在启动时全部加载会严重影响性能。
2. **Tree Shaking**：Bundler 可以更好地进行 dead code elimination。
3. **按需加载**：只有被实际调用的命令才会被加载。

### 7.3 Memoization 策略

命令系统在多个层面使用了 `memoize`：

```typescript
const COMMANDS = memoize((): Command[] => [...])
export const builtInCommandNames = memoize((): Set<string> => ...)
const loadAllCommands = memoize(async (cwd: string): Promise<Command[]> => ...)
export const getSkillToolCommands = memoize(async (cwd: string): Promise<Command[]> => ...)
export const getSlashCommandToolSkills = memoize(async (cwd: string): Promise<Command[]> => ...)
```

但 `meetsAvailabilityRequirement` 和 `isCommandEnabled` **不是** memoized 的，因为认证状态和 feature flag 可能在会话中改变。

**缓存失效**通过 `clearCommandsCache()` 和 `clearCommandMemoizationCaches()` 实现：

```typescript
export function clearCommandsCache(): void {
  clearCommandMemoizationCaches()
  clearPluginCommandCache()
  clearPluginSkillsCache()
  clearSkillCaches()
}
```

### 7.4 安全分层

命令系统实现了多层安全机制：

1. **`availability`**：静态的认证/提供者约束（如 `claude-ai`、`console`）
2. **`isEnabled`**：动态的 feature flag 检查
3. **`userInvocable`**：控制用户是否可以直接调用（vs 只能由模型调用）
4. **`isHidden`**：从 typeahead 和 help 中隐藏
5. **`isBridgeSafeCommand`**：Remote Control 安全白名单
6. **`REMOTE_SAFE_COMMANDS`**：远程模式安全集合
7. **`INTERNAL_ONLY_COMMANDS`**：内部命令集合（仅 ant 用户可见）

### 7.5 命令与 Agent 的交互模型

命令系统与 Agent 的交互有两种模式：

**内联模式（inline）**：命令内容直接注入到当前对话中
```
用户: /commit
↓
[COMMAND_MESSAGE_TAG] commit
[COMMAND_NAME_TAG] /commit
↓
[Skill 内容作为 isMeta 消息注入]
↓
模型看到完整提示，使用 Bash 工具执行 Git 操作
```

**Fork 模式（fork）**：命令在子 Agent 中独立执行
```
用户: /some-skill
↓
[启动子 Agent]
↓
子 Agent 独立执行（有自己的上下文和 token 预算）
↓
结果返回到主对话
```

### 7.6 动态 Skills 的发现机制

```typescript
export async function getCommands(cwd: string): Promise<Command[]> {
  // ...
  const dynamicSkills = getDynamicSkills()

  // 去重
  const uniqueDynamicSkills = dynamicSkills.filter(
    s => !baseCommandNames.has(s.name) && meetsAvailabilityRequirement(s) && isCommandEnabled(s),
  )

  // 插入到正确的位置
  const builtInNames = new Set(COMMANDS().map(c => c.name))
  const insertIndex = baseCommands.findIndex(c => builtInNames.has(c.name))

  return [
    ...baseCommands.slice(0, insertIndex),
    ...uniqueDynamicSkills,
    ...baseCommands.slice(insertIndex),
  ]
}
```

动态 Skills 是在文件操作过程中被发现的（例如用户创建了一个新的 SKILL.md 文件），它们被插入到 Plugin Skills 之后、内置命令之前，确保正确的优先级。

---

## 8. 工程实践细节

### 9.1 消息包装约定

命令系统使用 XML 标签来标记命令相关的消息：

```typescript
// src/constants/xml.ts
export const COMMAND_MESSAGE_TAG = 'command-message'
export const COMMAND_NAME_TAG = 'command-name'

// 输出格式
<local-command-stdout>命令输出</local-command-stdout>
<local-command-stderr>错误信息</local-command-stderr>
```

这种约定使得 UI 层可以识别并特殊渲染命令输出（如使用不同的颜色或样式）。

### 9.2 compact 结果的特殊处理

`/compact` 命令的返回值类型 `type: 'compact'` 需要特殊的处理路径：

```typescript
if (result.type === 'compact') {
  const slashCommandMessages = [
    syntheticCaveatMessage,
    userMessage,
    ...(result.displayText ? [createUserMessage({
      content: `<local-command-stdout>${result.displayText}</local-command-stdout>`,
      timestamp: new Date(Date.now() + 100).toISOString()  // 关键：时间戳稍晚于当前
    })] : [])
  ]
  const compactionResultWithSlashMessages = {
    ...result.compactionResult,
    messagesToKeep: [...(result.compactionResult.messagesToKeep ?? []), ...slashCommandMessages]
  }
  resetMicrocompactState()
  return { messages: buildPostCompactMessages(compactionResultWithSlashMessages), ... }
}
```

**时间戳技巧**：压缩后的消息时间戳设置为 `Date.now() + 100`（比当前时间晚 100ms），这是因为 `--resume` 功能通过最新时间戳的消息来确定恢复点，合成消息不应干扰这个逻辑。

### 9.3 错误处理策略

命令系统实现了多层错误处理：

1. **`local-jsx` 命令的 Promise 死锁防护**：
```typescript
.catch(e => {
  logError(e)
  if (doneWasCalled) return
  doneWasCalled = true
  setToolJSX({ jsx: null, clearLocalJSX: true })
  void resolve({ messages: [], shouldQuery: false, command })
})
```

2. **`prompt` 命令的 Abort 处理**：
```typescript
if (e instanceof AbortError) {
  return {
    messages: [userMessage, createUserInterruptionMessage({ toolUse: false })],
    shouldQuery: false, command
  }
}
```

3. **未知命令的优雅降级**：不是简单报错，而是检查是否是文件路径，如果不是才报告"Unknown skill"。

### 9.4 遥测事件

每个命令执行都会记录遥测事件：

```typescript
logEvent('tengu_input_command', {
  input: sanitizedCommandName,
  invocation_trigger: 'user-slash',
  // Plugin 相关的额外字段
  ...(returnedCommand.type === 'prompt' && returnedCommand.pluginInfo && {
    _PROTO_plugin_name: pluginManifest.name,
    _PROTO_marketplace_name: marketplace,
    plugin_repository: isOfficial ? repository : 'third-party',
  })
})
```

### 9.5 Coordinator Mode 的 Skill 简化

在 Coordinator 模式下（主 Agent 只有 Agent + TaskStop 工具），Skill 内容被大幅简化：

```typescript
if (feature('COORDINATOR_MODE') && isCoordinatorMode && !context.agentId) {
  const parts = [`Skill "/${command.name}" is available for workers.`]
  if (command.description) parts.push(`Description: ${command.description}`)
  if (command.whenToUse) parts.push(`When to use: ${command.whenToUse}`)
  parts.push(`Instruct a worker to use this skill by including "Use the /${command.name} skill" in your Agent prompt.`)
  // 返回简化的摘要，而非完整内容
}
```

---

## 9. 初学者易错点

### 10.1 混淆三种命令类型

**易错**：认为所有斜杠命令都是"让模型执行"的。

**正确理解**：
- `prompt` 类型：内容注入到对话，模型执行
- `local` 类型：直接执行函数，不涉及模型
- `local-jsx` 类型：渲染 UI 组件，用户交互

### 10.2 忽略懒加载的影响

**易错**：在模块顶层直接调用命令的实现函数。

**正确理解**：命令的实现通过 `load()` 懒加载，必须先 `await command.load()` 获取模块，再调用 `mod.call()`。

### 10.3 不理解 `isMeta` 的作用

**易错**：认为 Skill 内容对用户可见。

**正确理解**：Skill 内容（`isMeta: true`）对模型可见但对用户隐藏。用户只看到 `/command-name`，模型看到完整的 Skill 指令。

### 10.4 忽略 `immediate` 标志

**易错**：认为所有命令都必须等待当前轮次结束才能执行。

**正确理解**：`immediate: true` 的命令（如 `/btw`、`/model`）可以在模型生成响应时立即执行，通过 `handlePromptSubmit` 中的快速路径处理。

### 10.5 忽略缓存失效

**易错**：修改了命令配置后期望立即生效。

**正确理解**：命令列表通过 `memoize` 缓存，需要调用 `clearCommandsCache()` 才能使更改生效。

### 10.6 混淆 `userInvocable` 和 `disableModelInvocation`

**易错**：认为这两个标志是互斥的。

**正确理解**：
- `userInvocable: false`：用户不能直接输入 `/skill-name` 调用，但模型可以通过 SkillTool 调用
- `disableModelInvocation: true`：模型不能通过 SkillTool 调用，但用户可以直接输入 `/skill-name`

---

## 10. 本章总结

Claude Code 的命令系统是一个精心设计的多层架构，它的核心设计决策包括：

1. **三分法命令类型**：`prompt`（模型执行）、`local`（直接执行）、`local-jsx`（UI 交互），每种类型服务于不同的交互模式。

2. **懒加载 + Memoization**：通过 `load()` 懒加载和 `memoize` 缓存，在启动性能和运行时性能之间取得平衡。

3. **多源聚合**：内置命令、Skills、Plugins、MCP、bundled skills 统一通过 `getCommands()` 聚合，上层无需关心来源。

4. **安全分层**：从静态的 `availability` 到动态的 `isBridgeSafeCommand`，实现了多层安全控制。

5. **命令与 Agent 的桥接**：通过 `isMeta` 消息、`allowedTools` 权限、`context: 'fork'` 模式，命令系统优雅地连接了用户意图和 Agent 执行。

6. **健壮的错误处理**：从 Promise 死锁防护到 Abort 处理，系统在各种异常场景下都能优雅降级。

这个命令系统不仅是用户与 Claude Code 交互的入口，更是理解整个系统如何将"用户意图"转化为"Agent 行为"的关键。

---

## 11. 延伸思考

1. **命令系统的可扩展性**：Claude Code 的命令系统如何支持第三方 Plugin 和 MCP 服务器的命令注册？这种设计与 VS Code 的 Extension API 有何异同？

2. **Skill 与 Agent 的边界**：`context: 'fork'` 的 Skill 本质上就是一个子 Agent。在什么场景下应该使用 Skill 而非直接使用 AgentTool？两者的权衡是什么？

3. **命令的安全模型**：当前的安全模型（availability + isEnabled + userInvocable + isBridgeSafeCommand）是否足够？对于一个拥有文件系统和网络访问权限的 AI 编码助手，还有哪些潜在的安全风险？

4. **压缩与命令的关系**：`/compact` 命令的 `addInvokedSkill` 机制确保了压缩后保留被调用的 Skill。但如果一个 Skill 调用了另一个 Skill（嵌套调用），这个机制是否能正确处理？

5. **即时命令的并发控制**：`immediate: true` 的命令绕过了 `queryGuard`，这是否可能导致竞态条件？`doneWasCalled` 守卫是否足以防止所有可能的并发问题？

6. **从命令系统看产品哲学**：为什么 `/btw` 要设计为"不打断主对话"的侧问？这反映了 Claude Code 对人机交互的什么理解？为什么 `/commit` 是 `prompt` 类型而非 `local` 类型——让 AI 来决定 commit message 有什么好处？

7. **条件编译的工程价值**：通过 `feature()` 实现的 Dead Code Elimination 如何影响开发体验？与运行时的 feature flag 检查相比，编译时的条件编译有什么优势和限制？

---

> **下一章预告**：第12章将深入分析 Claude Code 的工具系统（Tool System），探索 Agent 如何通过工具与外部世界交互，以及工具权限模型如何保障安全性。
