---
title: 第07章：Context 系统 — Claude Code 的「记忆与感知」架构
---

# 第07章：Context 系统 — Claude Code 的「记忆与感知」架构

---

## 1. 本章目标

本章深入剖析 Claude Code 的 **Context（上下文）系统**，这是整个代码助手中最核心的「信息注入层」。Context 系统决定了在每次对话轮次中，模型"看到"什么信息——从 git 状态、CLAUDE.md 指令文件、用户附件（图片/文件/URL）、文件变更检测，到智能记忆检索，所有这些信息都通过 Context 系统组装并注入到 API 请求中。

完成本章后，你将理解：

- **用户上下文（User Context）** 如何从 CLAUDE.md 文件体系中构建
- **系统上下文（System Context）** 如何附加 git 状态等环境信息
- **附件系统（Attachment System）** 如何处理 @-提及文件、图片、目录、IDE 选区等 30+ 种附件类型
- **文件状态缓存（File State Cache）** 如何追踪已读文件并在变更时生成 diff
- **记忆相关性检索（Relevant Memory Prefetch）** 如何异步预取最相关的记忆文件
- **上下文注入的完整调用链**：从用户输入到 API 请求的组装流程

---

## 2. 前置知识

在阅读本章之前，你应该已经了解：

- **TypeScript 基础**：async/await、Promise、Generator、Map/Set 等
- **Anthropic Messages API**：system prompt、messages 数组、content block 的基本结构
- **Claude Code 的消息模型**：UserMessage、AssistantMessage、AttachmentMessage、SystemMessage 等类型（参见前面的章节）
- **lodash-es/memoize**：函数级缓存（memoization）的概念
- **LRU Cache**：最近最少使用缓存淘汰策略

---

## 3. 宏观概览

### 3.1 Context 系统的三大支柱

Claude Code 的 Context 系统由三个层次构成：

```
┌─────────────────────────────────────────────────────┐
│                   API Request                        │
│  ┌───────────────────────────────────────────────┐  │
│  │  systemPrompt (系统提示)                       │  │
│  │  + systemContext (系统上下文：git状态等)        │  │
│  └───────────────────────────────────────────────┘  │
│  ┌───────────────────────────────────────────────┐  │
│  │  messages[]                                    │  │
│  │  ├── [0] UserMessage (userContext: CLAUDE.md)  │  │
│  │  ├── [1] AttachmentMessage (附件：文件/图片)   │  │
│  │  ├── [2] UserMessage (用户输入)                │  │
│  │  ├── [3] AssistantMessage (模型回复)           │  │
│  │  └── ...                                       │  │
│  └───────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────┘
```

**三层信息注入机制：**

| 层次 | 来源 | 注入位置 | 生命周期 |
|------|------|----------|----------|
| **System Context** | git 状态、缓存破坏器 | system prompt 末尾 | 整个会话（memoized） |
| **User Context** | CLAUDE.md、当前日期 | messages[0] 的 UserMessage | 整个会话（memoized） |
| **Attachments** | @-提及文件、变更检测、记忆等 | 每轮对话的 AttachmentMessage | 单轮（每轮重新生成） |

### 3.2 信息流全景

```
用户输入 "帮我修复 @src/utils.ts 中的 bug"
        │
        ▼
┌─ getUserContext() ──────── CLAUDE.md + 日期 ──── messages[0]
│
├─ getSystemContext() ────── git 状态 ──────────── systemPrompt 末尾
│
├─ getAttachmentMessages() ── @-提及文件内容 ──── AttachmentMessage
│   ├─ processAtMentionedFiles()
│   ├─ getChangedFiles()
│   ├─ getNestedMemoryAttachments()
│   ├─ getRelevantMemoryAttachments()
│   ├─ getTodoReminderAttachments()
│   └─ ... (30+ 种附件)
│
└─ query() ────────────────── 组装最终请求
    ├─ appendSystemContext(systemPrompt, systemContext)
    └─ prependUserContext(messagesForQuery, userContext)
```

---

## 4. 源码入口定位

### 4.1 核心文件清单

| 文件 | 行数 | 职责 |
|------|------|------|
| `src/context.ts` | ~190行 | `getUserContext()` / `getSystemContext()` 定义 |
| `src/utils/queryContext.ts` | ~145行 | `fetchSystemPromptParts()` / `buildSideQuestionFallbackParams()` |
| `src/utils/api.ts` (L437-485) | ~50行 | `appendSystemContext()` / `prependUserContext()` |
| `src/utils/attachments.ts` | ~4000行 | 附件系统的完整实现 |
| `src/utils/claudemd.ts` | ~1470行 | CLAUDE.md 文件发现与处理 |
| `src/utils/fileStateCache.ts` | ~140行 | 文件状态 LRU 缓存 |
| `src/utils/messages.ts` | ~5500行 | 消息序列化与渲染 |
| `src/query.ts` | ~1700行 | 查询主循环（组装最终请求） |
| `src/QueryEngine.ts` | ~1400行 | 查询引擎（高层编排） |

### 4.2 关键行号定位

**`src/context.ts`**：
- L116: `getSystemContext = memoize(async () => {...})` — 系统上下文
- L155: `getUserContext = memoize(async () => {...})` — 用户上下文
- L28-33: `setSystemPromptInjection()` — 缓存清除机制

**`src/utils/api.ts`**：
- L437: `appendSystemContext()` — 将系统上下文附加到 system prompt
- L449: `prependUserContext()` — 将用户上下文作为首条消息注入

**`src/utils/attachments.ts`**：
- L743: `getAttachments()` — 附件收集主函数
- L2937: `getAttachmentMessages()` — 将附件转为消息的 Generator
- L3201: `createAttachmentMessage()` — 创建 AttachmentMessage

**`src/query.ts`**：
- L449-450: `appendSystemContext(systemPrompt, systemContext)` — 组装完整系统提示
- L660: `prependUserContext(messagesForQuery, userContext)` — 注入用户上下文

---

## 5. 调用链分析

### 5.1 启动阶段：预热缓存

在 `src/main.tsx` 中，系统启动时就开始预热 Context 缓存：

```typescript
// src/main.tsx L367-368, L405
void getSystemContext()   // 异步预热，不阻塞启动
void getUserContext()     // 异步预热，不阻塞启动
```

这两个 `void` 调用是 **fire-and-forget** 模式——启动时立即触发异步操作，但不等待结果。当后续真正需要这些上下文时，memoize 缓存已经就绪，直接返回结果。

### 5.2 查询阶段：组装 API 请求

当用户发送消息后，QueryEngine 或 query() 函数接管：

```
QueryEngine.ask()
  │
  ├── fetchSystemPromptParts()                    [queryContext.ts L38]
  │   ├── getSystemPrompt(tools, model, ...)       → 系统提示词
  │   ├── getUserContext()                          → { claudeMd, currentDate }
  │   └── getSystemContext()                        → { gitStatus, cacheBreaker }
  │
  ├── 组装 systemPrompt
  │   └── [customPrompt | defaultPrompt] + memoryMechanics + appendSystemPrompt
  │
  ├── query() 主循环                              [query.ts L320]
  │   ├── getMessagesAfterCompactBoundary()         → 取压缩后的消息
  │   ├── applyToolResultBudget()                   → 工具结果预算
  │   ├── snipCompactIfNeeded()                     → 历史裁剪
  │   ├── microcompact()                            → 微压缩
  │   ├── contextCollapse                           → 上下文折叠
  │   │
  │   ├── appendSystemContext(systemPrompt, systemContext)  [L449]
  │   │   → 将 gitStatus 等拼接到 system prompt 末尾
  │   │
  │   ├── getAttachmentMessages()                    [L548]
  │   │   → 收集本轮所有附件（@-提及、变更检测、记忆等）
  │   │
  │   ├── prependUserContext(messagesForQuery, userContext)  [L660]
  │   │   → 将 CLAUDE.md 作为 messages[0] 注入
  │   │
  │   └── callModel(messages, systemPrompt)          → 发送 API 请求
```

### 5.3 附件收集的并行架构

`getAttachments()` 是附件系统的核心入口，它采用三层并行架构：

```typescript
// src/utils/attachments.ts L743-1045
export async function getAttachments(input, toolUseContext, ideSelection, queuedCommands, messages, querySource, options): Promise<Attachment[]> {
  // 第一层：用户输入相关附件（串行等待，因为后续依赖其结果）
  const userAttachmentResults = await Promise.all(userInputAttachments)

  // 第二层：线程安全附件（所有线程都执行）
  const allThreadAttachments = [
    maybe('queued_commands', ...),
    maybe('date_change', ...),
    maybe('changed_files', ...),
    maybe('nested_memory', ...),
    maybe('dynamic_skill', ...),
    maybe('plan_mode', ...),
    // ... 约 20 种
  ]

  // 第三层：仅主线程附件
  const mainThreadAttachments = isMainThread ? [
    maybe('ide_selection', ...),
    maybe('ide_opened_file', ...),
    maybe('diagnostics', ...),
    maybe('token_usage', ...),
    // ... 约 12 种
  ] : []

  // 并行执行第二层和第三层
  const [threadResults, mainResults] = await Promise.all([
    Promise.all(allThreadAttachments),
    Promise.all(mainThreadAttachments),
  ])

  return [...userResults.flat(), ...threadResults.flat(), ...mainResults.flat()]
    .filter(a => a !== undefined && a !== null)
}
```

**关键设计：`maybe()` 包装器**

每个附件收集器都用 `maybe()` 包装，它提供统一的错误处理和性能监控：

```typescript
// src/utils/attachments.ts L993-1015
async function maybe<A>(label: string, f: () => Promise<A[]>): Promise<A[]> {
  const startTime = Date.now()
  try {
    const result = await f()
    const duration = Date.now() - startTime
    if (Math.random() < 0.05) {  // 5% 采样率，避免日志爆炸
      logEvent('tengu_attachment_compute_duration', { label, duration_ms: duration })
    }
    return result
  } catch (e) {
    logError(e)
    return []  // 单个附件失败不影响整体
  }
}
```

---

## 6. 核心源码解析

### 6.1 getUserContext() — 用户上下文的构建

```typescript
// src/context.ts L155-189
export const getUserContext = memoize(
  async (): Promise<{ [k: string]: string }> => {
    // 1. 检查是否禁用 CLAUDE.md
    const shouldDisableClaudeMd =
      isEnvTruthy(process.env.CLAUDE_CODE_DISABLE_CLAUDE_MDS) ||
      (isBareMode() && getAdditionalDirectoriesForClaudeMd().length === 0)

    // 2. 读取 CLAUDE.md 文件
    const claudeMd = shouldDisableClaudeMd
      ? null
      : getClaudeMds(filterInjectedMemoryFiles(await getMemoryFiles()))

    // 3. 缓存供 yoloClassifier 使用
    setCachedClaudeMdContent(claudeMd || null)

    // 4. 返回结构化上下文
    return {
      ...(claudeMd && { claudeMd }),
      currentDate: `Today's date is ${getLocalISODate()}.`,
    }
  },
)
```

**关键逻辑解析：**

1. **禁用条件**：两种情况下跳过 CLAUDE.md——环境变量 `CLAUDE_CODE_DISABLE_CLAUDE_MDS` 为真，或 bare 模式下没有额外目录
2. **文件发现**：`getMemoryFiles()` 执行目录遍历，从当前目录向上查找 CLAUDE.md 文件
3. **过滤**：`filterInjectedMemoryFiles()` 移除通过 `@include` 指令注入的文件，避免重复
4. **合并**：`getClaudeMds()` 将多个文件合并为单一字符串（按优先级排序）
5. **缓存旁路**：`setCachedClaudeMdContent()` 将内容存入全局缓存，供自动模式分类器使用

### 6.2 getSystemContext() — 系统上下文的构建

```typescript
// src/context.ts L116-153
export const getSystemContext = memoize(
  async (): Promise<{ [k: string]: string }> => {
    // 1. 跳过条件：CCR 模式或禁用 git 指令
    const gitStatus =
      isEnvTruthy(process.env.CLAUDE_CODE_REMOTE) ||
      !shouldIncludeGitInstructions()
        ? null
        : await getGitStatus()

    // 2. 缓存破坏器（仅内部使用）
    const injection = feature('BREAK_CACHE_COMMAND')
      ? getSystemPromptInjection()
      : null

    return {
      ...(gitStatus && { gitStatus }),
      ...(feature('BREAK_CACHE_COMMAND') && injection
        ? { cacheBreaker: `[CACHE_BREAKER: ${injection}]` }
        : {}),
    }
  },
)
```

### 6.3 getGitStatus() — Git 状态收集

```typescript
// src/context.ts L37-113
export const getGitStatus = memoize(async (): Promise<string | null> => {
  if (!await getIsGit()) return null

  // 并行执行 5 个 git 命令
  const [branch, mainBranch, status, log, userName] = await Promise.all([
    getBranch(),
    getDefaultBranch(),
    execFileNoThrow(gitExe(), ['--no-optional-locks', 'status', '--short']),
    execFileNoThrow(gitExe(), ['--no-optional-locks', 'log', '--oneline', '-n', '5']),
    execFileNoThrow(gitExe(), ['config', 'user.name']),
  ])

  // 截断过长的 status（2000 字符限制）
  const truncatedStatus = status.length > MAX_STATUS_CHARS
    ? status.substring(0, MAX_STATUS_CHARS) + '\n... (truncated)'
    : status

  return [
    `This is the git status at the start of the conversation.`,
    `Current branch: ${branch}`,
    `Main branch: ${mainBranch}`,
    ...(userName ? [`Git user: ${userName}`] : []),
    `Status:\n${truncatedStatus || '(clean)'}`,
    `Recent commits:\n${log}`,
  ].join('\n\n')
})
```

**设计亮点：**
- 使用 `--no-optional-locks` 避免获取 git 锁，减少对用户操作的干扰
- 5 个 git 命令并行执行，大幅减少延迟
- `MAX_STATUS_CHARS = 2000` 的截断保护，防止大量未提交变更撑爆 context

### 6.4 appendSystemContext() — 系统上下文注入

```typescript
// src/utils/api.ts L437-448
export function appendSystemContext(
  systemPrompt: SystemPrompt,
  context: { [k: string]: string },
): string[] {
  return [
    ...systemPrompt,
    Object.entries(context)
      .map(([key, value]) => `${key}: ${value}`)
      .join('\n'),
  ].filter(Boolean)
}
```

这个函数将系统上下文（如 gitStatus）作为键值对拼接到 system prompt 的末尾。格式为 `key: value`，每对占一行。

### 6.5 prependUserContext() — 用户上下文注入

```typescript
// src/utils/api.ts L449-475
export function prependUserContext(
  messages: Message[],
  context: { [k: string]: string },
): Message[] {
  if (Object.entries(context).length === 0) return messages

  return [
    createUserMessage({
      content: `<system-reminder>\nAs you answer the user's questions, you can use the following context:\n${
        Object.entries(context)
          .map(([key, value]) => `# ${key}\n${value}`)
          .join('\n')
      }\n      IMPORTANT: this context may or may not be relevant to your tasks. You should not respond to this context unless it is highly relevant to your task.\n</system-reminder>\n`,
      isMeta: true,  // 标记为元消息，不计入对话历史
    }),
    ...messages,
  ]
}
```

**关键设计：**
- 用户上下文被包装在 `<system-reminder>` XML 标签中
- 使用 `isMeta: true` 标记，表示这不是用户实际输入，而是系统注入的上下文
- 包含明确的指令："除非高度相关，否则不要响应此上下文"
- 作为 messages[0] 注入，在整个会话中保持不变（memoized）

### 6.6 CLAUDE.md 文件体系

CLAUDE.md 文件按优先级从低到高加载：

```typescript
// src/utils/claudemd.ts L1-28（注释）
/**
 * Files are loaded in the following order:
 *
 * 1. Managed memory (eg. /etc/claude-code/CLAUDE.md) - Global instructions for all users
 * 2. User memory (~/.claude/CLAUDE.md) - Private global instructions for all projects
 * 3. Project memory (CLAUDE.md, .claude/CLAUDE.md, and .claude/rules/*.md in project roots)
 * 4. Local memory (CLAUDE.local.md in project roots) - Private project-specific instructions
 *
 * Files are loaded in reverse order of priority, i.e. the latest files are highest priority
 * with the model paying more attention to them.
 */
```

**文件发现机制：**
- 从当前目录向上遍历到根目录，查找 CLAUDE.md 和 .claude/ 目录
- 支持 `@include` 指令引用其他文件
- 支持 frontmatter 中的 `globs` 字段实现条件规则
- `MAX_MEMORY_CHARACTER_COUNT = 40000` 限制总大小

### 6.7 附件系统：类型体系

附件系统支持 30+ 种附件类型，定义在 `src/utils/attachments.ts` 中：

```typescript
// src/utils/attachments.ts L432-550（核心类型摘选）
export type Attachment =
  | FileAttachment                    // @-提及的文件
  | CompactFileReferenceAttachment    // 压缩后的文件引用
  | PDFReferenceAttachment            // PDF 文件引用（大文件轻量引用）
  | AlreadyReadFileAttachment         // 已读过的文件
  | { type: 'edited_text_file' }      // 被编辑的文本文件（diff）
  | { type: 'edited_image_file' }     // 被编辑的图片文件
  | { type: 'directory' }             // 目录内容
  | { type: 'selected_lines_in_ide' } // IDE 选中的代码行
  | { type: 'opened_file_in_ide' }    // IDE 中打开的文件
  | { type: 'todo_reminder' }         // TODO 提醒
  | { type: 'task_reminder' }         // 任务提醒
  | { type: 'nested_memory' }         // 嵌套的 CLAUDE.md
  | { type: 'relevant_memories' }     // 相关记忆
  | { type: 'dynamic_skill' }         // 动态技能
  | { type: 'skill_listing' }         // 技能列表
  | { type: 'skill_discovery' }       // 技能发现
  | { type: 'queued_command' }        // 队列中的命令
  | { type: 'date_change' }           // 日期变更通知
  | { type: 'ultrathink_effort' }     // 深度思考标记
  | { type: 'deferred_tools_delta' }  // 延迟工具增量
  | { type: 'agent_listing_delta' }   // 代理列表增量
  | { type: 'async_hook_response' }   // 异步钩子响应
  | HookAttachment                    // 各种钩子附件
  // ... 更多
```

### 6.8 @-提及文件处理

当用户输入 `@src/utils.ts` 时，系统通过 `processAtMentionedFiles()` 处理：

```typescript
// src/utils/attachments.ts L2757-2790
export function extractAtMentionedFiles(content: string): string[] {
  // 两种模式：引号路径和普通路径
  const quotedAtMentionRegex = /(^|\s)@"([^"]+)"/g
  const regularAtMentionRegex = /(^|\s)@([^\s]+)\b/g

  const quotedMatches: string[] = []
  const regularMatches: string[] = []

  // 提取引号内的路径（支持空格）
  let match
  while ((match = quotedAtMentionRegex.exec(content)) !== null) {
    if (match[2] && !match[2].endsWith(' (agent)')) {
      quotedMatches.push(match[2])
    }
  }

  // 提取普通路径
  const regularMatchArray = content.match(regularAtMentionRegex) || []
  regularMatchArray.forEach(match => {
    const filename = match.slice(match.indexOf('@') + 1)
    if (!filename.startsWith('"')) {
      regularMatches.push(filename)
    }
  })

  return uniq([...quotedMatches, ...regularMatches])
}
```

支持的语法：
- `@file.txt` — 普通文件引用
- `@"my file with spaces.txt"` — 带空格的文件
- `@file.txt#L10-20` — 带行号范围的引用
- `@server:resource/path` — MCP 资源引用
- `@agent-code-reviewer` — 代理引用

### 6.9 文件状态缓存

`FileStateCache` 是附件系统的基石，它追踪每个已读文件的状态：

```typescript
// src/utils/fileStateCache.ts L15-22
export type FileState = {
  content: string          // 文件内容（或原始字节）
  timestamp: number        // 读取时的时间戳
  offset: number | undefined  // 读取偏移量
  limit: number | undefined   // 读取行数限制
  isPartialView?: boolean  // 是否为部分视图（注入内容与磁盘不同）
}
```

**LRU 缓存实现：**

```typescript
// src/utils/fileStateCache.ts L30-65
export class FileStateCache {
  private cache: LRUCache<string, FileState>

  constructor(maxEntries: number, maxSizeBytes: number) {
    this.cache = new LRUCache<string, FileState>({
      max: maxEntries,           // 默认 100 个条目
      maxSize: maxSizeBytes,     // 默认 25MB
      sizeCalculation: value => Math.max(1, Buffer.byteLength(value.content)),
    })
  }

  get(key: string): FileState | undefined {
    return this.cache.get(normalize(key))  // 路径规范化
  }

  set(key: string, value: FileState): this {
    this.cache.set(normalize(key), value)
    return this
  }
}
```

**关键设计：**
- 所有路径在访问前都经过 `normalize()` 规范化，确保 `/foo/../bar` 和 `/bar` 命中同一缓存
- `maxSize: 25MB` 防止内存无限增长
- `isPartialView` 标记：当注入内容与磁盘不同时（如剥离了 HTML 注释），标记为部分视图，要求 Edit/Write 工具先执行 Read

### 6.10 变更文件检测

`getChangedFiles()` 对比文件状态缓存和磁盘上的文件修改时间：

```typescript
// src/utils/attachments.ts L2063-2150
export async function getChangedFiles(toolUseContext): Promise<Attachment[]> {
  const filePaths = cacheKeys(toolUseContext.readFileState)
  if (filePaths.length === 0) return []

  const results = await Promise.all(
    filePaths.map(async filePath => {
      const fileState = toolUseContext.readFileState.get(filePath)
      if (!fileState) return null

      // 跳过部分读取的文件（有 offset/limit）
      if (fileState.offset !== undefined || fileState.limit !== undefined) {
        return null
      }

      try {
        const mtime = await getFileModificationTimeAsync(normalizedPath)
        // 比较修改时间
        if (mtime <= fileState.timestamp) return null  // 未修改

        const result = await FileReadTool.call(fileInput, toolUseContext)

        if (result.data.type === 'text') {
          // 生成 diff 片段
          const snippet = getSnippetForTwoFileDiff(
            fileState.content,
            result.data.file.content,
          )
          if (snippet === '') return null  // 内容未变（可能是 touch）

          return { type: 'edited_text_file', filename: normalizedPath, snippet }
        }

        if (result.data.type === 'image') {
          return { type: 'edited_image_file', filename: normalizedPath, content: data }
        }

        return null
      } catch (err) {
        // 仅在文件真正删除时才清除缓存
        if (isENOENT(err)) {
          toolUseContext.readFileState.delete(filePath)
        }
        return null
      }
    })
  )

  return results.filter(r => r != null)
}
```

**设计亮点：**
- 仅在文件确实被删除（ENOENT）时才清除缓存，避免原子保存（tmp→rename）的竞态问题
- 对已读但部分读取的文件（有 offset/limit）跳过变更检测，避免不完整的 diff
- 使用 `getSnippetForTwoFileDiff()` 只生成变更部分，而非全量内容

### 6.11 嵌套记忆文件注入

当用户 @-提及一个文件时，系统会自动查找并注入该文件路径上的 CLAUDE.md 文件：

```typescript
// src/utils/attachments.ts L1656-1710
export function getDirectoriesToProcess(targetPath, originalCwd) {
  const targetDir = dirname(resolve(targetPath))
  const nestedDirs: string[] = []
  let currentDir = targetDir

  // 从目标目录向上遍历到原始 CWD
  while (currentDir !== originalCwd && currentDir !== parse(currentDir).root) {
    if (currentDir.startsWith(originalCwd)) {
      nestedDirs.push(currentDir)
    }
    currentDir = dirname(currentDir)
  }
  nestedDirs.reverse()  // 从 CWD 到目标的顺序

  // 从根目录到 CWD 的目录列表（用于条件规则）
  const cwdLevelDirs: string[] = []
  currentDir = originalCwd
  while (currentDir !== parse(currentDir).root) {
    cwdLevelDirs.push(currentDir)
    currentDir = dirname(currentDir)
  }
  cwdLevelDirs.reverse()

  return { nestedDirs, cwdLevelDirs }
}
```

**处理顺序：**
1. Managed/User 条件规则（匹配目标路径）
2. 嵌套目录（CWD → 目标）：每个目录的 CLAUDE.md + 无条件规则 + 条件规则
3. CWD 级目录（根 → CWD）：仅条件规则

### 6.12 记忆相关性预取

`startRelevantMemoryPrefetch()` 实现了异步、非阻塞的记忆检索：

```typescript
// src/utils/attachments.ts L2361-2460
export function startRelevantMemoryPrefetch(messages, toolUseContext): MemoryPrefetch | undefined {
  if (!isAutoMemoryEnabled() || !getFeatureValue('tengu_moth_copse', false)) {
    return undefined
  }

  // 提取最后一条真实用户消息（跳过 isMeta 的系统注入）
  const lastUserMessage = messages.findLast(m => m.type === 'user' && !m.isMeta)
  if (!lastUserMessage) return undefined

  const input = getUserMessageText(lastUserMessage)
  // 单词提示缺乏足够的上下文进行有意义的检索
  if (!input || !/\s/.test(input.trim())) return undefined

  // 检查会话级别的记忆字节限制
  const surfaced = collectSurfacedMemories(messages)
  if (surfaced.totalBytes >= RELEVANT_MEMORIES_CONFIG.MAX_SESSION_BYTES) {
    return undefined  // 已达到上限
  }

  // 创建子级 AbortController，与用户 Escape 联动
  const controller = createChildAbortController(toolUseContext.abortController)

  const promise = getRelevantMemoryAttachments(
    input,
    toolUseContext.options.agentDefinitions.activeAgents,
    toolUseContext.readFileState,
    collectRecentSuccessfulTools(messages, lastUserMessage),
    controller.signal,
    surfaced.paths,
  )

  return {
    promise,
    settledAt: null,
    consumedOnIteration: -1,
    [Symbol.dispose]() { controller.abort() },  // using 语法自动清理
  }
}
```

**关键设计：**
- **Disposable 模式**：使用 `using` 语法绑定，确保 generator 退出时自动中止请求
- **会话级限流**：`MAX_SESSION_BYTES = 60KB`，防止长期会话中累积过多记忆
- **去重**：已出现的记忆路径被排除，避免重复注入
- **每轮限制**：最多 5 个记忆文件，每个最大 4KB

### 6.13 附件到消息的转换

`getAttachmentMessages()` 是一个 AsyncGenerator，将附件逐个转换为 AttachmentMessage：

```typescript
// src/utils/attachments.ts L2937-2968
export async function* getAttachmentMessages(
  input, toolUseContext, ideSelection, queuedCommands, messages, querySource, options
): AsyncGenerator<AttachmentMessage, void> {
  const attachments = await getAttachments(input, toolUseContext, ideSelection, queuedCommands, messages, querySource, options)

  if (attachments.length === 0) return

  logEvent('tengu_attachments', {
    attachment_types: attachments.map(_ => _.type),
  })

  for (const attachment of attachments) {
    yield createAttachmentMessage(attachment)
  }
}

// src/utils/attachments.ts L3201-3210
export function createAttachmentMessage(attachment: Attachment): AttachmentMessage {
  return {
    attachment,
    type: 'attachment',
    uuid: randomUUID(),
    timestamp: new Date().toISOString(),
  }
}
```

**设计亮点：**
- 使用 AsyncGenerator 实现惰性求值——每个附件在需要时才被转换
- 附件类型列表被记录到分析事件，用于监控和调试
- 每个附件消息都有唯一的 UUID 和时间戳

---

## 7. 架构设计思想

### 7.1 分层注入策略

Context 系统采用三层注入策略，每层有不同的生命周期和更新频率：

```
┌─────────────────────────────────────────────────────┐
│  Layer 1: System Prompt + System Context             │
│  ────────────────────────────────────────            │
│  更新频率：会话级（memoized）                        │
│  内容：系统提示词 + git 状态                         │
│  注入方式：systemPrompt 参数                         │
├─────────────────────────────────────────────────────┤
│  Layer 2: User Context                               │
│  ────────────────────────────────────────            │
│  更新频率：会话级（memoized）                        │
│  内容：CLAUDE.md + 当前日期                          │
│  注入方式：messages[0] 的 isMeta UserMessage         │
├─────────────────────────────────────────────────────┤
│  Layer 3: Attachments                                │
│  ────────────────────────────────────────            │
│  更新频率：每轮（per-turn）                          │
│  内容：@-提及、变更检测、记忆等                      │
│  注入方式：AttachmentMessage                         │
└─────────────────────────────────────────────────────┘
```

**为什么这样分层？**

1. **缓存优化**：System Context 和 User Context 在整个会话中不变，可以被 API 的 prompt caching 机制缓存，大幅降低成本
2. **变化隔离**：每轮变化的附件不会破坏前两层的缓存
3. **关注点分离**：每层负责不同类型的信息，职责清晰

### 7.2 Memoize 模式的应用

`getUserContext()` 和 `getSystemContext()` 都使用 `lodash-es/memoize` 进行函数级缓存：

```typescript
export const getUserContext = memoize(async () => { ... })
export const getSystemContext = memoize(async () => { ... })
```

**缓存清除机制：**

```typescript
// src/context.ts L28-33
export function setSystemPromptInjection(value: string | null): void {
  systemPromptInjection = value
  // 注入变更时立即清除缓存
  getUserContext.cache.clear?.()
  getSystemContext.cache.clear?.()
}
```

在以下场景中缓存会被清除：
- `/compact` 命令执行后（`compact.ts L63, L117, L203`）
- `/clear` 命令执行后（`caches.ts L52-53`）
- 系统提示注入变更时
- 子代理压缩后（`postCompactCleanup.ts L59`）

### 7.3 并行与超时控制

`getAttachments()` 使用 1 秒超时保护：

```typescript
// src/utils/attachments.ts L760-763
const abortController = createAbortController()
const timeoutId = setTimeout(ac => ac.abort(), 1000, abortController)
const context = { ...toolUseContext, abortController }
```

所有附件收集器共享这个 AbortController，1 秒内未完成的会被中止。这确保了用户输入后的响应延迟不会因为附件收集而超过 1 秒。

### 7.4 Fail-Safe 设计

附件系统采用"单点失败不影响整体"的设计：

```typescript
async function maybe<A>(label: string, f: () => Promise<A[]>): Promise<A[]> {
  try {
    return await f()
  } catch (e) {
    logError(e)
    return []  // 失败时返回空数组，不抛出异常
  }
}
```

每个附件收集器都被 `maybe()` 包装，即使某个收集器抛出异常，其他收集器的结果仍然会被保留。

---

## 8. 工程实践细节

### 8.1 性能监控与采样

附件系统内置了细粒度的性能监控：

```typescript
// 5% 采样率，避免日志爆炸
if (Math.random() < 0.05) {
  logEvent('tengu_attachment_compute_duration', {
    label,
    duration_ms: duration,
    attachment_size_bytes: attachmentSizeBytes,
    attachment_count: result.length,
  })
}
```

**监控指标：**
- 每个附件收集器的执行时间
- 附件大小（字节）
- 附件数量
- 错误率

### 8.2 日期变更的智能处理

当用户编码到午夜时，系统会检测日期变更并注入通知：

```typescript
// src/utils/attachments.ts L1415-1455
export function getDateChangeAttachments(messages: Message[] | undefined): Attachment[] {
  const currentDate = getLocalISODate()
  const lastDate = getLastEmittedDate()

  if (lastDate === null) {
    setLastEmittedDate(currentDate)
    return []  // 第一轮，不需要通知
  }

  if (currentDate === lastDate) return []  // 同一天

  setLastEmittedDate(currentDate)
  return [{ type: 'date_change', newDate: currentDate }]
}
```

**设计考量：**
- 不清除 `getUserContext` 缓存中的旧日期——这会导致整个会话的缓存失效
- 而是在附件中注入日期变更通知，模型可以自行感知新日期
- 注释明确说明了这个设计决策的性能考量（~920K 有效 token 的缓存重建成本）

### 8.3 PDF 的轻量引用

大 PDF 文件不会被完整读取，而是生成轻量引用：

```typescript
// src/utils/attachments.ts L2986-3020
export async function tryGetPDFReference(filename: string): Promise<PDFReferenceAttachment | null> {
  const ext = parse(filename).ext.toLowerCase()
  if (!isPDFExtension(ext)) return null

  const [stats, pageCount] = await Promise.all([
    getFsImplementation().stat(filename),
    getPDFPageCount(filename),
  ])

  const effectivePageCount = pageCount ?? Math.ceil(stats.size / (100 * 1024))
  if (effectivePageCount > PDF_AT_MENTION_INLINE_THRESHOLD) {
    return {
      type: 'pdf_reference',
      filename,
      pageCount: effectivePageCount,
      fileSize: stats.size,
      displayPath: relative(getCwd(), filename),
    }
  }
  return null
}
```

对于超过阈值页数的 PDF，系统只返回引用（文件名、页数、大小），让模型使用 FileReadTool 按需读取特定页面。

### 8.4 路径规范化

`FileStateCache` 在所有操作前都进行路径规范化：

```typescript
get(key: string): FileState | undefined {
  return this.cache.get(normalize(key))
}
```

`path.normalize()` 处理：
- `/foo/../bar` → `/bar`
- `/foo/./bar` → `/foo/bar`
- `//foo//bar` → `/foo/bar`
- Windows 路径分隔符统一

这确保了不同调用者使用不同格式的路径时，仍能命中同一缓存条目。

### 8.5 嵌套记忆的去重机制

防止同一 CLAUDE.md 文件被重复注入：

```typescript
// src/utils/attachments.ts L1710-1750
export function memoryFilesToAttachments(memoryFiles, toolUseContext, triggerFilePath): Attachment[] {
  for (const memoryFile of memoryFiles) {
    // 双重去重：loadedNestedMemoryPaths（非淘汰 Set）+ readFileState（LRU）
    if (toolUseContext.loadedNestedMemoryPaths?.has(memoryFile.path)) {
      continue
    }
    if (!toolUseContext.readFileState.has(memoryFile.path)) {
      attachments.push({ type: 'nested_memory', ... })
      toolUseContext.loadedNestedMemoryPaths?.add(memoryFile.path)

      // 写入 readFileState，标记 isPartialView（如果内容与磁盘不同）
      toolUseContext.readFileState.set(memoryFile.path, {
        content: memoryFile.contentDiffersFromDisk
          ? (memoryFile.rawContent ?? memoryFile.content)
          : memoryFile.content,
        timestamp: Date.now(),
        isPartialView: memoryFile.contentDiffersFromDisk,
      })
    }
  }
  return attachments
}
```

**双重去重策略：**
1. `loadedNestedMemoryPaths`：非淘汰 Set，整个会话中持久追踪
2. `readFileState`：LRU 缓存，可能被淘汰，但淘汰后 `loadedNestedMemoryPaths` 仍保留记录

### 8.6 记忆注入的字节预算

```typescript
const MAX_MEMORY_LINES = 200
const MAX_MEMORY_BYTES = 4096  // 每个记忆文件最大 4KB

// 每轮最多 5 个记忆文件 → 5 × 4KB = 20KB/turn
// 会话级别上限 60KB
export const RELEVANT_MEMORIES_CONFIG = {
  MAX_SESSION_BYTES: 60 * 1024,
}
```

这个多层预算系统确保：
- 单个文件不会太大（4KB）
- 单轮注入不会太多（5 个文件 × 4KB = 20KB）
- 整个会话不会累积过多（60KB 后停止预取）

---

## 9. 初学者易错点

### 9.1 混淆 System Context 和 User Context 的注入位置

**易错点**：以为 System Context 和 User Context 都注入到 system prompt 中。

**正确理解**：
- **System Context**（gitStatus）→ 拼接到 system prompt 末尾
- **User Context**（CLAUDE.md）→ 作为 messages[0] 的 UserMessage 注入

```typescript
// System Context → system prompt
appendSystemContext(systemPrompt, systemContext)

// User Context → messages[0]
prependUserContext(messagesForQuery, userContext)
```

### 9.2 误解 Memoize 的缓存粒度

**易错点**：以为 memoize 缓存的是整个会话的所有调用。

**正确理解**：`getUserContext()` 的 memoize 使用函数引用作为缓存键（lodash 默认使用第一个参数）。由于无参数调用，所有调用共享同一缓存。缓存通过 `.cache.clear?.()` 手动清除。

### 9.3 忘记 isMeta 标记的作用

**易错点**：不清楚 `isMeta: true` 的含义。

**正确理解**：`isMeta: true` 标记的消息：
- 不计入对话历史的 token 计数
- 在 `/compact` 时可能被特殊处理
- 在某些上下文过滤逻辑中被跳过（如 `findLast(m => m.type === 'user' && !m.isMeta)`）

### 9.4 误解附件的生命周期

**易错点**：以为附件在整个会话中持久存在。

**正确理解**：附件在每轮对话中重新生成。`getAttachmentMessages()` 在每轮调用 `getAttachments()`，收集当前轮次需要的所有附件。这意味着：
- @-提及的文件每轮都会重新读取
- 变更检测每轮都会重新执行
- 记忆预取每轮都会重新触发

### 9.5 忽略 AbortController 的共享

**易错点**：以为每个附件收集器有独立的超时控制。

**正确理解**：所有附件收集器共享同一个 AbortController（1 秒超时）。如果某个收集器导致超时，所有正在进行的收集器都会被中止。这也是为什么每个收集器都被 `maybe()` 包装——即使被中止，也只是返回空数组。

### 9.6 混淆 readFileState 的用途

**易错点**：以为 `readFileState` 只用于缓存文件内容以避免重复读取。

**正确理解**：`readFileState` 有三个用途：
1. **缓存**：避免重复读取同一文件
2. **变更检测**：通过比较 `mtime` 和 `timestamp` 发现文件变更
3. **去重**：通过 `.has()` 检查避免重复注入嵌套记忆文件

### 9.7 不理解 isPartialView 的含义

**易错点**：忽略 `isPartialView` 标记。

**正确理解**：当注入的 CLAUDE.md 内容与磁盘不同时（如剥离了 HTML 注释、剥离了 frontmatter、截断了 MEMORY.md），系统将原始磁盘内容缓存到 `content` 字段，但标记 `isPartialView: true`。这告诉 Edit/Write 工具："模型看到的内容与磁盘不同，执行编辑前必须先 Read"。

---

## 10. 本章总结

### 10.1 核心架构

Claude Code 的 Context 系统是一个精心设计的三层信息注入架构：

1. **System Context**（会话级，memoized）：注入 git 状态等环境信息到 system prompt
2. **User Context**（会话级，memoized）：注入 CLAUDE.md 和当前日期到 messages[0]
3. **Attachments**（每轮动态）：注入 30+ 种类型的附件到当前轮次

### 10.2 关键设计决策

| 决策 | 理由 |
|------|------|
| Memoize + 手动清除 | 最大化缓存命中，同时允许按需刷新 |
| 三层分离 | 不同生命周期的信息不应耦合 |
| 1 秒超时 + maybe() 包装 | 附件收集不能阻塞用户交互 |
| LRU + 双重去重 | 平衡内存使用和去重可靠性 |
| 字节预算（4KB/文件，20KB/轮，60KB/会话） | 防止 context 膨胀 |
| AsyncGenerator 输出 | 惰性求值，按需转换 |

### 10.3 数据流总结

```
用户输入
  │
  ├─→ getUserContext() ──── CLAUDE.md + 日期 ──── messages[0]
  │     └─ getMemoryFiles() → filterInjectedMemoryFiles() → getClaudeMds()
  │
  ├─→ getSystemContext() ── git 状态 ──────────── system prompt 末尾
  │     └─ getGitStatus() → 5 个并行 git 命令
  │
  ├─→ getAttachmentMessages() ─────────────────── AttachmentMessage[]
  │     ├─ @-提及文件 → processAtMentionedFiles()
  │     ├─ 变更检测 → getChangedFiles() (mtime 对比)
  │     ├─ 嵌套记忆 → getNestedMemoryAttachments()
  │     ├─ 相关记忆 → startRelevantMemoryPrefetch() (异步)
  │     ├─ TODO 提醒 → getTodoReminderAttachments()
  │     ├─ 日期变更 → getDateChangeAttachments()
  │     └─ ... (25+ 更多类型)
  │
  └─→ query() ────────────── 组装最终 API 请求
        ├─ appendSystemContext(systemPrompt, systemContext)
        ├─ prependUserContext(messages, userContext)
        └─ callModel(messages, systemPrompt)
```

---

## 11. 延伸思考

### 11.1 缓存友好性设计的深层逻辑

Claude Code 的三层注入策略不仅仅是关注点分离，更是对 **Anthropic API prompt caching** 机制的深度优化。System Context 和 User Context 被设计为会话级不变量，这意味着：

- **前缀稳定性**：API 请求的前缀（system prompt + messages[0]）在整个会话中保持不变
- **缓存命中率**：prompt caching 基于前缀匹配，稳定的前缀确保高命中率
- **成本节约**：缓存的 token 成本约为非缓存的 1/10

日期变更的处理尤其体现了这一设计哲学——不清除 User Context 缓存，而是通过附件注入日期变更通知，避免了 ~920K 有效 token 的缓存重建。

### 11.2 记忆系统的演进方向

当前的记忆系统（CLAUDE.md + 相关记忆预取）可以进一步演进：

1. **语义记忆**：基于 embedding 的记忆检索，而非关键词匹配
2. **情景记忆**：记住特定的对话上下文和决策
3. **程序记忆**：学习用户的编码习惯和偏好
4. **跨会话记忆**：在不同会话间共享和累积知识

`relevant_memories` 附件类型和 `findRelevantMemoryAttachments()` 函数已经为这些方向奠定了基础。

### 11.3 附件系统的可扩展性

30+ 种附件类型的注册模式（`maybe('label', fn)`）展示了良好的可扩展性：

```typescript
const allThreadAttachments = [
  maybe('queued_commands', () => getQueuedCommandAttachments(queuedCommands)),
  maybe('date_change', () => Promise.resolve(getDateChangeAttachments(messages))),
  maybe('changed_files', () => getChangedFiles(context)),
  // 新增附件类型只需添加一行
  maybe('new_feature', () => getNewFeatureAttachments(context)),
]
```

这种模式使得：
- 新增附件类型无需修改核心逻辑
- 每个收集器独立演进
- 错误隔离（单点失败不影响整体）

### 11.4 文件状态缓存的一致性挑战

`FileStateCache` 使用 mtime 检测文件变更，但 mtime 有其局限性：

1. **时间精度**：某些文件系统的 mtime 精度只有秒级
2. **原子保存**：编辑器的 tmp→rename 模式可能导致 mtime 变化但内容未变
3. **网络文件系统**：NFS 等的 mtime 可能不准确

Claude Code 通过以下方式缓解：
- 仅在 ENOENT 时清除缓存（而非 mtime 不匹配时）
- 使用 `getSnippetForTwoFileDiff()` 验证内容确实变更
- `isPartialView` 标记处理注入内容与磁盘不同的情况

### 11.5 上下文预算管理的哲学

Claude Code 的上下文预算管理体现了"宁可少给，不要多给"的哲学：

- **截断而非丢弃**：记忆文件被截断时会添加提示，让模型知道完整内容可用
- **引用而非内联**：大 PDF 使用轻量引用，让模型按需读取
- **增量而非全量**：变更检测只注入 diff 片段，而非完整文件
- **预算而非无限**：多层字节预算防止 context 膨胀

这种设计确保了在有限的 context window 中，注入的信息都是高信号密度的。

---

### 11.6 附件渲染：从 Attachment 到 API Content Block

附件系统最精妙的设计之一是 `messages.ts` 中的渲染层——它将每种 Attachment 类型转换为模型可理解的 Content Block 序列。这个过程不是简单的序列化，而是模拟了工具调用的真实对话格式：

```typescript
// src/utils/messages.ts L3524-3750（核心渲染逻辑摘选）
switch (attachment.type) {
  case 'directory': {
    // 模拟一次 BashTool 的 ls 调用
    return wrapMessagesInSystemReminder([
      createToolUseMessage(BashTool.name, {
        command: `ls ${quote([attachment.path])}`,
        description: `Lists files in ${attachment.path}`,
      }),
      createToolResultMessage(BashTool, {
        stdout: attachment.content,
        stderr: '',
        interrupted: false,
      }),
    ])
  }

  case 'edited_text_file':
    // 以 isMeta UserMessage 注入变更通知
    return wrapMessagesInSystemReminder([
      createUserMessage({
        content: `Note: ${attachment.filename} was modified, either by the user or by a linter. This change was intentional, so make sure to take it into account as you proceed (ie. don't revert it unless the user asks you to). Don't tell the user this, since they are already aware. Here are the relevant changes (shown with line numbers):\n${attachment.snippet}`,
        isMeta: true,
      }),
    ])

  case 'file': {
    const fileContent = attachment.content as FileReadToolOutput
    switch (fileContent.type) {
      case 'image':
        // 模拟一次 FileReadTool 的图片读取
        return wrapMessagesInSystemReminder([
          createToolUseMessage(FileReadTool.name, { file_path: attachment.filename }),
          createToolResultMessage(FileReadTool, fileContent),
        ])
      case 'text':
        // 模拟一次 FileReadTool 的文本读取，附带截断提示
        return wrapMessagesInSystemReminder([
          createToolUseMessage(FileReadTool.name, { file_path: attachment.filename }),
          createToolResultMessage(FileReadTool, fileContent),
          ...(attachment.truncated ? [
            createUserMessage({
              content: `Note: The file ${attachment.filename} was too large and has been truncated to the first ${MAX_LINES_TO_READ} lines. Don't tell the user about this truncation. Use ${FileReadTool.name} to read more of the file if you need.`,
              isMeta: true,
            }),
          ] : []),
        ])
    }
  }

  case 'pdf_reference':
    // 大 PDF 的轻量引用——告诉模型如何使用 FileReadTool
    return wrapMessagesInSystemReminder([
      createUserMessage({
        content: `PDF file: ${attachment.filename} (${attachment.pageCount} pages, ${formatFileSize(attachment.fileSize)}). ` +
          `This PDF is too large to read all at once. You MUST use the ${FILE_READ_TOOL_NAME} tool with the pages parameter ` +
          `to read specific page ranges (e.g., pages: "1-5"). Do NOT call ${FILE_READ_TOOL_NAME} without the pages parameter ` +
          `or it will fail. Start by reading the first few pages to understand the structure, then read more as needed. ` +
          `Maximum 20 pages per request.`,
        isMeta: true,
      }),
    ])

  case 'selected_lines_in_ide': {
    const maxSelectionLength = 2000
    const content = attachment.content.length > maxSelectionLength
      ? attachment.content.substring(0, maxSelectionLength) + '\n... (truncated)'
      : attachment.content
    return wrapMessagesInSystemReminder([
      createUserMessage({
        content: `The user selected the lines ${attachment.lineStart} to ${attachment.lineEnd} from ${attachment.filename}:\n${content}\n\nThis may or may not be related to the current task.`,
        isMeta: true,
      }),
    ])
  }

  case 'relevant_memories':
    // 记忆文件以 system-reminder 注入
    return wrapMessagesInSystemReminder(
      attachment.memories.map(memory =>
        createUserMessage({
          content: `${memory.header ?? memoryHeader(memory.path, memory.mtimeMs)}\n\n${memory.content}`,
          isMeta: true,
        })
      )
    )

  case 'nested_memory':
    // 嵌套 CLAUDE.md 以 system-reminder 注入
    return wrapMessagesInSystemReminder([
      createUserMessage({
        content: `Contents of ${attachment.content.path}:\n\n${attachment.content.content}`,
        isMeta: true,
      }),
    ])

  case 'todo_reminder':
    // TODO 列表以 system-reminder 注入
    const todoItems = attachment.content
      .map(item => `- [${item.completed ? 'x' : ' '}] ${item.text}`)
      .join('\n')
    return wrapMessagesInSystemReminder([
      createUserMessage({
        content: `Your TODO list:\n${todoItems}\n\nConsider working on unchecked items.`,
        isMeta: true,
      }),
    ])
}
```

**关键渲染模式：**

1. **模拟工具调用**：`directory` 和 `file` 类型通过 `createToolUseMessage` + `createToolResultMessage` 模拟真实的工具调用格式。这让模型看到的格式与用户实际调用工具时完全一致。

2. **isMeta UserMessage**：大多数通知类附件（变更检测、记忆、提醒等）以 `isMeta: true` 的 UserMessage 注入，被包装在 `<system-reminder>` 标签中。

3. **截断提示**：当文件被截断时，会附加一条 `isMeta` 消息告诉模型截断情况，但指示不要告诉用户。

4. **指令性语言**：大 PDF 的引用包含明确的指令（"You MUST use..."、"Do NOT call..."），引导模型正确使用工具。

### 11.7 wrapMessagesInSystemReminder() 的作用

所有附件渲染都通过 `wrapMessagesInSystemReminder()` 包装：

```typescript
function wrapMessagesInSystemReminder(messages: Message[]): Message[] {
  // 将消息包装在 <system-reminder> 标签中
  // 这让模型知道这些是系统注入的信息，而非用户输入
  return messages
}
```

这个包装器确保了：
- 系统注入的信息不会被模型误认为是用户输入
- 在消息流中有明确的边界标记
- 便于后续的上下文管理和压缩

### 11.8 技能列表注入的增量策略

技能列表（skill_listing）采用增量注入策略，避免每轮重复发送完整的技能列表：

```typescript
// src/utils/attachments.ts L2633-2750
async function getSkillListingAttachments(toolUseContext): Promise<Attachment[]> {
  const allCommands = await getSkillToolCommands(cwd)
  
  // 追踪已发送的技能名称（按 agent 维度）
  const agentKey = toolUseContext.agentId ?? ''
  let sent = sentSkillNames.get(agentKey)
  if (!sent) {
    sent = new Set()
    sentSkillNames.set(agentKey, sent)
  }

  // Resume 路径：标记所有当前技能为已发送
  if (suppressNext) {
    suppressNext = false
    for (const cmd of allCommands) {
      sent.add(cmd.name)
    }
    return []
  }

  // 计算增量：只发送新技能
  const newCommands = allCommands.filter(cmd => !sent.has(cmd.name))
  if (newCommands.length === 0) return []

  // 标记为已发送
  for (const cmd of newCommands) {
    sent.add(cmd.name)
  }

  // 格式化并注入
  const content = formatCommandsWithinBudget(newCommands)
  return [{
    type: 'skill_listing',
    content,
    skillCount: newCommands.length,
    isInitial: sent.size === newCommands.length,
  }]
}
```

**增量策略的优势：**
- 第一轮注入完整技能列表（`isInitial: true`）
- 后续轮次只注入新发现的技能（如通过 `/reload-plugins` 加载的）
- Resume 时跳过注入，因为技能列表已在对话历史中
- 按 agent 维度追踪，确保子代理独立管理自己的技能列表

### 11.9 队列命令的附件注入

当用户在模型执行期间发送新消息时，这些消息会被放入队列，作为附件注入到下一轮：

```typescript
// src/utils/attachments.ts L1046-1085
export async function getQueuedCommandAttachments(queuedCommands: QueuedCommand[]): Promise<Attachment[]> {
  if (!queuedCommands) return []

  // 过滤出可内联的通知模式
  const filtered = queuedCommands.filter(_ =>
    INLINE_NOTIFICATION_MODES.has(_.mode)  // 'prompt' | 'task-notification'
  )

  return Promise.all(
    filtered.map(async _ => {
      // 处理图片粘贴
      const imageBlocks = await buildImageContentBlocks(_.pastedContents)
      let prompt: string | Array<ContentBlockParam> = _.value
      if (imageBlocks.length > 0) {
        const textValue = typeof _.value === 'string'
          ? _.value
          : extractTextContent(_.value, '\n')
        prompt = [{ type: 'text' as const, text: textValue }, ...imageBlocks]
      }

      return {
        type: 'queued_command' as const,
        prompt,
        source_uuid: _.uuid,
        imagePasteIds: getImagePasteIds(_.pastedContents),
        commandMode: _.mode,
        origin: _.origin,
        isMeta: _.isMeta,
      }
    }),
  )
}
```

**队列命令的两种模式：**
- `prompt`：用户主动发送的消息
- `task-notification`：系统事件通知（如后台任务完成）

### 11.10 Hook 附件系统

Hook 系统允许在工具执行前后注入自定义逻辑，其结果通过附件注入：

```typescript
// src/utils/attachments.ts L351-432（类型定义摘选）
export type HookAttachment =
  | HookCancelledAttachment        // Hook 被取消
  | { type: 'hook_blocking_error' } // 阻塞错误
  | HookNonBlockingErrorAttachment // 非阻塞错误
  | HookErrorDuringExecutionAttachment // 执行中错误
  | { type: 'hook_stopped_continuation' } // Hook 停止继续
  | HookSuccessAttachment          // Hook 成功
  | { type: 'hook_additional_context' } // 附加上下文
  | HookSystemMessageAttachment    // 系统消息
  | HookPermissionDecisionAttachment // 权限决策
```

Hook 附件的渲染：

```typescript
// src/utils/messages.ts L1033-1040
if (
  message.attachment.type === 'hook_blocking_error' ||
  message.attachment.type === 'hook_cancelled' ||
  message.attachment.type === 'hook_error_during_execution' ||
  message.attachment.type === 'hook_non_blocking_error' ||
  message.attachment.type === 'hook_success' ||
  message.attachment.type === 'hook_system_message' ||
  message.attachment.type === 'hook_additional_context' ||
  message.attachment.type === 'hook_stopped_continuation'
) {
  // 渲染为系统消息
}
```

### 11.11 异步钩子响应的注入

异步钩子（Async Hook）在后台执行，其结果通过附件注入：

```typescript
// src/utils/attachments.ts L3401-3455
async function getAsyncHookResponseAttachments(): Promise<Attachment[]> {
  const responses = await checkForAsyncHookResponses()
  if (responses.length === 0) return []

  return responses.map(({ processId, response, hookName, hookEvent, toolName, stdout, stderr, exitCode }) => ({
    type: 'async_hook_response' as const,
    processId,
    hookName,
    hookEvent,
    toolName,
    response,
    stdout,
    stderr,
    exitCode,
  }))
}
```

### 11.12 代理消息附件

子代理（Agent）之间的通信通过附件系统实现：

```typescript
// src/utils/attachments.ts L1085-1140
export function getAgentPendingMessageAttachments(toolUseContext): Attachment[] {
  const agentId = toolUseContext.agentId
  if (!agentId) return []  // 主线程不需要

  const drained = drainPendingMessages(agentId, toolUseContext.getAppState)
  if (drained.length === 0) return []

  return drained.map(msg => ({
    type: 'agent_pending_message' as const,
    from: msg.from,
    content: msg.content,
    summary: msg.summary,
  }))
}
```

### 11.13 上下文效率附件

当对话历史过长时，系统会注入上下文效率提示：

```typescript
// src/utils/attachments.ts L3963+
export function getContextEfficiencyAttachment(messages: Message[]): Attachment[] {
  // 计算当前上下文使用情况
  // 当超过阈值时，注入效率建议
}
```

### 11.14 压缩提醒附件

当对话即将触发自动压缩时，系统会提前提醒：

```typescript
// src/utils/attachments.ts L3931+
export function getCompactionReminderAttachment(messages, model): Attachment[] {
  // 检查当前 token 使用量
  // 当接近压缩阈值时，注入提醒
}
```

### 11.15 Query 主循环中的上下文组装

`query.ts` 中的主循环是上下文组装的最终执行点：

```typescript
// src/query.ts L320-680（核心流程摘选）
export async function* query(state, deps, toolUseContext, querySource, ...) {
  // 1. 获取压缩边界后的消息
  let messagesForQuery = [...getMessagesAfterCompactBoundary(messages)]

  // 2. 应用工具结果预算（防止工具输出撑爆上下文）
  messagesForQuery = await applyToolResultBudget(messagesForQuery, ...)

  // 3. 应用历史裁剪（snip）
  if (feature('HISTORY_SNIP')) {
    const snipResult = snipModule!.snipCompactIfNeeded(messagesForQuery)
    messagesForQuery = snipResult.messages
    snipTokensFreed = snipResult.tokensFreed
  }

  // 4. 应用微压缩（microcompact）
  const microcompactResult = await deps.microcompact(messagesForQuery, toolUseContext, querySource)
  messagesForQuery = microcompactResult.messages

  // 5. 应用上下文折叠（context collapse）
  if (feature('CONTEXT_COLLAPSE') && contextCollapse) {
    const collapseResult = await contextCollapse.applyCollapsesIfNeeded(messagesForQuery, toolUseContext, querySource)
    messagesForQuery = collapseResult.messages
  }

  // 6. 组装完整系统提示（system prompt + system context）
  const fullSystemPrompt = asSystemPrompt(
    appendSystemContext(systemPrompt, systemContext)
  )

  // 7. 自动压缩检查
  const { compactionResult } = await deps.autocompact(messagesForQuery, toolUseContext, {
    systemPrompt, userContext, systemContext, ...
  }, querySource, tracking, snipTokensFreed)

  if (compactionResult) {
    messagesForQuery = compactionResult.postCompactMessages
  }

  // 8. 收集附件消息
  const attachmentMessages: AttachmentMessage[] = []
  for await (const attachmentMessage of getAttachmentMessages(
    input, toolUseContext, ideSelection, queuedCommands, messagesForQuery, querySource
  )) {
    attachmentMessages.push(attachmentMessage)
  }

  // 9. 过滤重复记忆附件
  const filteredAttachments = filterDuplicateMemoryAttachments(attachmentMessages, messagesForQuery)

  // 10. 注入附件到消息流
  messagesForQuery = [...messagesForQuery, ...filteredAttachments]

  // 11. 注入用户上下文（CLAUDE.md + 日期）到 messages[0]
  const messagesWithContext = prependUserContext(messagesForQuery, userContext)

  // 12. 发送 API 请求
  for await (const message of deps.callModel({
    messages: messagesWithContext,
    systemPrompt: fullSystemPrompt,
    ...
  })) {
    yield message
  }
}
```

**这个流程展示了上下文组装的完整管线：**

1. **消息预处理**：压缩边界、工具结果预算、历史裁剪、微压缩、上下文折叠
2. **系统提示组装**：system prompt + system context
3. **自动压缩**：如果消息超过上下文窗口，自动压缩
4. **附件收集**：收集本轮所有附件
5. **去重过滤**：移除重复的记忆附件
6. **注入**：将附件和用户上下文注入消息流
7. **API 调用**：发送最终组装好的请求

### 11.16 fetchSystemPromptParts() 的并行优化

`queryContext.ts` 中的 `fetchSystemPromptParts()` 是上下文组装的入口点，它并行获取三个上下文组件：

```typescript
// src/utils/queryContext.ts L38-60
export async function fetchSystemPromptParts({
  tools, mainLoopModel, additionalWorkingDirectories, mcpClients, customSystemPrompt
}): Promise<{
  defaultSystemPrompt: string[]
  userContext: { [k: string]: string }
  systemContext: { [k: string]: string }
}> {
  const [defaultSystemPrompt, userContext, systemContext] = await Promise.all([
    customSystemPrompt !== undefined
      ? Promise.resolve([])  // 自定义提示时跳过默认提示
      : getSystemPrompt(tools, mainLoopModel, additionalWorkingDirectories, mcpClients),
    getUserContext(),
    customSystemPrompt !== undefined
      ? Promise.resolve({})  // 自定义提示时跳过系统上下文
      : getSystemContext(),
  ])
  return { defaultSystemPrompt, userContext, systemContext }
}
```

**关键设计：**
- 三个上下文组件并行获取，最大化 I/O 效率
- 当提供自定义系统提示时，跳过默认提示和系统上下文的构建
- 这是因为自定义提示会完全替代默认提示，附加系统上下文到不存在的默认提示上没有意义

### 11.17 buildSideQuestionFallbackParams() 的降级策略

当 SDK 侧问题处理器在 resume 时没有 stopHooks 快照时，使用降级路径：

```typescript
// src/utils/queryContext.ts L80-145
export async function buildSideQuestionFallbackParams({
  tools, commands, mcpClients, messages, readFileState, getAppState, setAppState,
  customSystemPrompt, appendSystemPrompt, thinkingConfig, agents
}): Promise<CacheSafeParams> {
  // 重新构建系统提示部分（与主循环相同的逻辑）
  const { defaultSystemPrompt, userContext, systemContext } =
    await fetchSystemPromptParts({ tools, mainLoopModel, ... })

  // 组装系统提示
  const systemPrompt = asSystemPrompt([
    ...(customSystemPrompt !== undefined ? [customSystemPrompt] : defaultSystemPrompt),
    ...(appendSystemPrompt ? [appendSystemPrompt] : []),
  ])

  // 过滤进行中的助手消息
  const last = messages.at(-1)
  const forkContextMessages =
    last?.type === 'assistant' && last.message.stop_reason === null
      ? messages.slice(0, -1)
      : messages

  return {
    systemPrompt,
    userContext,
    systemContext,
    toolUseContext: { ... },
    forkContextMessages,
  }
}
```

**降级策略的意义：**
- SDK 的侧问题处理器可能在主循环的 stopHooks 快照之前被触发
- 此时需要重新构建上下文参数，以保持 prompt cache 命中
- 如果主循环应用了额外的上下文（如 coordinator 模式、memory-mechanics），降级路径可能错过缓存——这是可接受的权衡

### 11.18 文件附件的完整生命周期

以用户输入 `@src/utils.ts` 为例，追踪文件附件的完整生命周期：

```
用户输入: "帮我修复 @src/utils.ts 中的 bug"
        │
        ▼
1. extractAtMentionedFiles(input)
   → ["src/utils.ts"]
        │
        ▼
2. processAtMentionedFiles(input, context)
   ├── expandPath("src/utils.ts") → "/home/user/project/src/utils.ts"
   ├── tryGetPDFReference() → null (不是 PDF)
   ├── generateFileAttachment(filename, toolUseContext, ..., 'at-mention')
   │   ├── isFileReadDenied() → false
   │   ├── isFileWithinReadSizeLimit() → true
   │   ├── FileReadTool.call({ file_path: filename })
   │   │   → { type: 'text', file: { content: "...", totalLines: 150 } }
   │   ├── readFileState.set(filename, { content, timestamp: Date.now() })
   │   └── return { type: 'file', filename, content, displayPath: "src/utils.ts" }
   └── return [{ type: 'file', filename, content, ... }]
        │
        ▼
3. getNestedMemoryAttachments(context)
   ├── nestedMemoryAttachmentTriggers = {"/home/user/project/src/utils.ts"}
   ├── getNestedMemoryAttachmentsForFile(filename, toolUseContext, appState)
   │   ├── getDirectoriesToProcess(filename, originalCwd)
   │   │   → { nestedDirs: ["/home/user/project/src"], cwdLevelDirs: [...] }
   │   ├── getManagedAndUserConditionalRules(filename)
   │   ├── getMemoryFilesForNestedDirectory("/home/user/project/src", ...)
   │   │   → [{ path: "/home/user/project/src/CLAUDE.md", content: "...", type: "Project" }]
   │   └── memoryFilesToAttachments(memoryFiles, toolUseContext)
   │       ├── loadedNestedMemoryPaths.has("/home/user/project/src/CLAUDE.md") → false
   │       ├── readFileState.has("/home/user/project/src/CLAUDE.md") → false
   │       ├── attachments.push({ type: 'nested_memory', ... })
   │       ├── loadedNestedMemoryPaths.add("/home/user/project/src/CLAUDE.md")
   │       └── readFileState.set("/home/user/project/src/CLAUDE.md", { content, timestamp })
   └── return [{ type: 'nested_memory', path, content }]
        │
        ▼
4. getAttachmentMessages() → yield createAttachmentMessage(attachment)
        │
        ▼
5. messages.ts 渲染
   ├── file 附件 → [ToolUseMessage(FileReadTool), ToolResultMessage(FileReadTool)]
   └── nested_memory 附件 → [UserMessage("Contents of ...", isMeta: true)]
        │
        ▼
6. query() 注入
   └── messagesForQuery = [...messagesForQuery, ...attachmentMessages]
        │
        ▼
7. API 请求发送
```

### 11.19 变更检测的完整流程

当模型修改了一个文件后，下一轮对话时系统会检测变更并注入 diff：

```
第 N 轮: 模型调用 FileEditTool 修改了 src/utils.ts
        │
        ▼
FileEditTool 执行成功后:
  readFileState.set("src/utils.ts", { content: "新内容", timestamp: Date.now() })
        │
        ▼
第 N+1 轮: 用户发送新消息
        │
        ▼
getChangedFiles(toolUseContext)
  ├── filePaths = cacheKeys(readFileState) → ["src/utils.ts", ...]
  ├── 对每个文件:
  │   ├── fileState = readFileState.get("src/utils.ts")
  │   │   → { content: "旧内容", timestamp: 1000, offset: undefined, limit: undefined }
  │   ├── getFileModificationTimeAsync("src/utils.ts") → 1500
  │   ├── 1500 > 1000 → 文件已修改
  │   ├── FileReadTool.call("src/utils.ts") → { type: 'text', file: { content: "新内容" } }
  │   ├── getSnippetForTwoFileDiff("旧内容", "新内容")
  │   │   → "  1 | - old line\n  1 | + new line"
  │   └── return { type: 'edited_text_file', filename: "src/utils.ts", snippet: "..." }
  └── return [{ type: 'edited_text_file', ... }]
        │
        ▼
渲染为:
  UserMessage("Note: src/utils.ts was modified... Here are the relevant changes:\n...")
```

### 11.20 记忆预取的异步执行模型

记忆预取是 Context 系统中最复杂的异步组件：

```typescript
// query.ts 中的使用模式
{
  using memoryPrefetch = startRelevantMemoryPrefetch(messages, toolUseContext)
  using skillPrefetch = startSkillDiscoveryPrefetch(null, messages, toolUseContext)

  // 主循环开始...
  while (attemptWithFallback) {
    // API 调用...
    for await (const message of deps.callModel(...)) {
      yield message
    }

    // 工具执行...

    // 收集预取结果（如果已就绪）
    if (memoryPrefetch && memoryPrefetch.settledAt !== null) {
      const memoryAttachments = await memoryPrefetch.promise
      // 注入到消息流
    }
  }
  // using 语法确保退出时自动中止预取请求
}
```

**异步模型的关键特性：**

1. **Disposable 模式**：`using` 语法确保 generator 退出时自动调用 `[Symbol.dispose]()`，中止进行中的请求
2. **非阻塞收集**：预取在后台运行，主循环不等待它完成
3. **就绪检查**：通过 `settledAt !== null` 检查是否已就绪，避免阻塞
4. **会话级限流**：`collectSurfacedMemories()` 扫描已注入的记忆，累积字节数超过 60KB 后停止预取
5. **AbortController 级联**：预取使用 `createChildAbortController()` 创建子控制器，与用户 Escape 联动

### 11.21 上下文预算的多层防护

Claude Code 的上下文预算管理是一个多层防护系统：

```
┌─────────────────────────────────────────────────────────────┐
│  Layer 1: System Prompt 预算                                 │
│  ────────────────────────────────────────                    │
│  • systemPrompt 本身由 getSystemPrompt() 控制                │
│  • systemContext 追加 git 状态（MAX_STATUS_CHARS = 2000）     │
├─────────────────────────────────────────────────────────────┤
│  Layer 2: User Context 预算                                  │
│  ────────────────────────────────────────                    │
│  • CLAUDE.md: MAX_MEMORY_CHARACTER_COUNT = 40000             │
│  • 日期: ~30 字符                                            │
├─────────────────────────────────────────────────────────────┤
│  Layer 3: 附件预算                                           │
│  ────────────────────────────────────────                    │
│  • 单个记忆文件: MAX_MEMORY_BYTES = 4096                     │
│  • 每轮记忆: 最多 5 个文件 × 4KB = 20KB                      │
│  • 会话记忆: MAX_SESSION_BYTES = 60KB                        │
│  • IDE 选区: maxSelectionLength = 2000                       │
│  • 附件收集超时: 1 秒                                        │
├─────────────────────────────────────────────────────────────┤
│  Layer 4: 消息预算                                           │
│  ────────────────────────────────────────                    │
│  • 工具结果预算: applyToolResultBudget()                     │
│  • 历史裁剪: snipCompactIfNeeded()                           │
│  • 微压缩: microcompact()                                    │
│  • 上下文折叠: contextCollapse.applyCollapsesIfNeeded()      │
│  • 自动压缩: autocompact()                                   │
└─────────────────────────────────────────────────────────────┘
```

每一层都有独立的预算控制，确保最终的 API 请求不会超过模型的上下文窗口。

### 11.22 路径处理的工程细节

Context 系统中大量的路径处理逻辑展示了工程实践的细节：

```typescript
// 路径规范化
get(key: string): FileState | undefined {
  return this.cache.get(normalize(key))  // /foo/../bar → /bar
}

// 路径展开
const normalizedPath = expandPath(filePath)  // ~ → /home/user

// 相对路径计算
const displayPath = relative(getCwd(), filename)  // /home/user/project/src/utils.ts → src/utils.ts

// 目录遍历
let currentDir = targetDir
while (currentDir !== originalCwd && currentDir !== parse(currentDir).root) {
  if (currentDir.startsWith(originalCwd)) {
    nestedDirs.push(currentDir)
  }
  currentDir = dirname(currentDir)
}
```

**路径处理的陷阱：**
- 不同操作系统使用不同的路径分隔符（/ vs \）
- 符号链接可能导致路径不一致
- 相对路径和绝对路径的混合使用
- 路径中可能包含空格或特殊字符

`FileStateCache` 通过 `normalize()` 统一处理这些问题，确保缓存的一致性。

### 11.23 错误处理的防御性设计

附件系统的错误处理体现了防御性编程：

```typescript
// 单个附件收集器的错误不影响整体
async function maybe<A>(label: string, f: () => Promise<A[]>): Promise<A[]> {
  try {
    return await f()
  } catch (e) {
    logError(e)
    return []  // 失败时返回空数组
  }
}

// 文件删除时的缓存清理
try {
  const mtime = await getFileModificationTimeAsync(normalizedPath)
  // ...
} catch (err) {
  // 仅在文件真正删除时才清除缓存
  if (isENOENT(err)) {
    toolUseContext.readFileState.delete(filePath)
  }
  return null  // 其他错误不清除缓存
}

// PDF 读取失败时的降级
try {
  const data = await readImageWithTokenBudget(normalizedPath)
  return { type: 'edited_image_file', filename: normalizedPath, content: data }
} catch (compressionError) {
  logError(compressionError)
  return null  // 压缩失败时返回 null
}
```

### 11.24 分析事件与可观测性

附件系统内置了丰富的分析事件，用于监控和调试：

```typescript
// 附件收集器的性能监控
logEvent('tengu_attachment_compute_duration', {
  label,           // 收集器名称
  duration_ms,     // 执行时间
  attachment_size_bytes,  // 附件大小
  attachment_count,       // 附件数量
})

// 附件类型统计
logEvent('tengu_attachments', {
  attachment_types: attachments.map(_ => _.type),
})

// PDF 引用统计
logEvent('tengu_pdf_reference_attachment', {
  pageCount,
  fileSize,
  hadPdfinfo: pageCount !== null,
})
```

这些事件帮助团队了解：
- 哪些附件收集器最慢
- 附件的平均大小和数量
- 哪些功能被使用（PDF 引用、技能发现等）

### 11.25 测试策略

附件系统的测试策略展示了复杂异步系统的测试方法：

```typescript
// getDateChangeAttachments 被导出用于测试
// src/utils/attachments.ts L1415
export function getDateChangeAttachments(messages: Message[] | undefined): Attachment[] {
  // ... 注释明确说明 "Exported for testing — regression guard for the cache-clear removal."
}

// memoryFilesToAttachments 也被导出用于测试
// src/utils/attachments.ts L1710
export function memoryFilesToAttachments(memoryFiles, toolUseContext, triggerFilePath): Attachment[] {
  // ... 注释说明 "Exported for testing — regression guard for LRU-eviction re-injection."
}
```

**测试关注点：**
- 日期变更时不清除 User Context 缓存（回归测试）
- LRU 缓存淘汰后的重新注入不会发生（回归测试）
- 路径规范化的一致性
- 错误场景的降级行为

---

> **下一章预告**：我们将深入分析 Claude Code 的 **工具系统（Tool System）**，探索 30+ 种内置工具的注册、权限控制和执行机制。

---

## 附录 A：完整附件类型清单

以下是 `src/utils/attachments.ts` 中定义的所有附件类型及其用途：

| 附件类型 | 触发条件 | 注入方式 |
|---------|---------|---------|
| `file` | 用户 @-提及文件 | 模拟 FileReadTool 调用 |
| `compact_file_reference` | 压缩后引用已读文件 | isMeta UserMessage |
| `pdf_reference` | 大 PDF 文件（超过阈值页数） | isMeta UserMessage（指令性） |
| `already_read_file` | 已读过的文件再次 @-提及 | 模拟 FileReadTool 调用 |
| `edited_text_file` | 文件被修改（mtime 变化） | isMeta UserMessage（diff） |
| `edited_image_file` | 图片文件被修改 | 模拟 FileReadTool 调用 |
| `directory` | @-提及目录 | 模拟 BashTool ls 调用 |
| `selected_lines_in_ide` | IDE 中选中代码行 | isMeta UserMessage |
| `opened_file_in_ide` | IDE 中打开文件 | isMeta UserMessage |
| `todo_reminder` | 10 轮未使用 TodoWrite | isMeta UserMessage |
| `task_reminder` | 10 轮未使用 TaskUpdate | isMeta UserMessage |
| `nested_memory` | @-提及文件路径上的 CLAUDE.md | isMeta UserMessage |
| `relevant_memories` | 语义相关的记忆文件（异步预取） | isMeta UserMessage |
| `dynamic_skill` | 动态发现的技能 | isMeta UserMessage |
| `skill_listing` | 技能列表（增量） | isMeta UserMessage |
| `skill_discovery` | 技能发现结果 | isMeta UserMessage |
| `queued_command` | 用户在模型执行期间发送的消息 | UserMessage |
| `date_change` | 日期变更（跨午夜） | isMeta UserMessage |
| `ultrathink_effort` | 用户使用 ultrathink 关键词 | 标记附件 |
| `deferred_tools_delta` | 延迟工具的增量变化 | isMeta UserMessage |
| `agent_listing_delta` | 代理列表的增量变化 | isMeta UserMessage |
| `mcp_instructions_delta` | MCP 指令的增量变化 | isMeta UserMessage |
| `companion_intro` | 伙伴（Buddy）介绍 | isMeta UserMessage |
| `plan_mode` / `plan_mode_exit` | 进入/退出计划模式 | isMeta UserMessage |
| `auto_mode` / `auto_mode_exit` | 进入/退出自动模式 | isMeta UserMessage |
| `agent_pending_message` | 子代理待处理消息 | UserMessage |
| `teammate_mailbox` | 团队成员邮箱消息 | isMeta UserMessage |
| `team_context` | 团队上下文信息 | isMeta UserMessage |
| `task_status` | 后台任务状态 | isMeta UserMessage |
| `async_hook_response` | 异步钩子响应 | isMeta UserMessage |
| `critical_system_reminder` | 关键系统提醒 | isMeta UserMessage |
| `compaction_reminder` | 压缩提醒 | isMeta UserMessage |
| `context_efficiency` | 上下文效率提示 | isMeta UserMessage |
| `verify_plan_reminder` | 验证计划提醒 | isMeta UserMessage |
| `token_usage` | Token 使用量 | isMeta UserMessage |
| `budget_usd` | 预算使用量 | isMeta UserMessage |
| `output_token_usage` | 输出 Token 使用量 | isMeta UserMessage |
| `hook_*` | 各种 Hook 事件 | isMeta UserMessage |

---

## 附录 B：关键配置常量

```typescript
// src/context.ts
const MAX_STATUS_CHARS = 2000                    // git status 最大字符数

// src/utils/claudemd.ts
export const MAX_MEMORY_CHARACTER_COUNT = 40000  // CLAUDE.md 最大字符数

// src/utils/attachments.ts
const MAX_MEMORY_LINES = 200                     // 记忆文件最大行数
const MAX_MEMORY_BYTES = 4096                    // 记忆文件最大字节数
export const RELEVANT_MEMORIES_CONFIG = {
  MAX_SESSION_BYTES: 60 * 1024,                  // 会话级记忆字节上限 (60KB)
}
export const TODO_REMINDER_CONFIG = {
  TURNS_SINCE_WRITE: 10,                         // 未使用 TodoWrite 的轮次
  TURNS_BETWEEN_REMINDERS: 10,                   // 提醒间隔轮次
}
export const PLAN_MODE_ATTACHMENT_CONFIG = {
  TURNS_BETWEEN_ATTACHMENTS: 5,                  // 计划模式附件间隔
  FULL_REMINDER_EVERY_N_ATTACHMENTS: 5,          // 完整提醒频率
}
const FILTERED_LISTING_MAX = 30                  // 技能列表最大数量

// src/utils/fileStateCache.ts
export const READ_FILE_STATE_CACHE_SIZE = 100    // 文件状态缓存最大条目
const DEFAULT_MAX_CACHE_SIZE_BYTES = 25 * 1024 * 1024  // 缓存最大字节数 (25MB)

// src/tools/FileReadTool/prompt.ts
export const MAX_LINES_TO_READ = ...             // 文件读取最大行数

// src/constants/apiLimits.ts
export const PDF_AT_MENTION_INLINE_THRESHOLD = ...  // PDF 内联阈值页数
```

---

## 附录 C：Context 系统的完整数据流图

```
                          ┌──────────────────────────────────────────┐
                          │           用户输入                        │
                          │  "帮我修复 @src/utils.ts 中的 bug"        │
                          └──────────────┬───────────────────────────┘
                                         │
                    ┌────────────────────┼────────────────────┐
                    │                    │                    │
                    ▼                    ▼                    ▼
          ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
          │ getUserContext() │  │ getSystemContext()│  │ getAttachments()│
          │ (memoized)      │  │ (memoized)      │  │ (per-turn)      │
          └────────┬────────┘  └────────┬────────┘  └────────┬────────┘
                   │                    │                    │
                   ▼                    ▼                    ▼
          ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
          │ CLAUDE.md 文件  │  │ git status      │  │ @-提及文件      │
          │ 当前日期        │  │ git log         │  │ 变更检测        │
          │                 │  │ git branch      │  │ 嵌套记忆        │
          │                 │  │ git user        │  │ 相关记忆        │
          │                 │  │                 │  │ TODO 提醒       │
          │                 │  │                 │  │ 日期变更        │
          │                 │  │                 │  │ 技能列表        │
          │                 │  │                 │  │ ... (30+ 种)    │
          └────────┬────────┘  └────────┬────────┘  └────────┬────────┘
                   │                    │                    │
                   ▼                    ▼                    ▼
          ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
          │ prependUser     │  │ appendSystem    │  │ getAttachment   │
          │ Context()       │  │ Context()       │  │ Messages()      │
          │                 │  │                 │  │ (AsyncGenerator)│
          │ → messages[0]   │  │ → system prompt │  │ → AttachmentMsg │
          │   (isMeta)      │  │   末尾          │  │   []            │
          └────────┬────────┘  └────────┬────────┘  └────────┬────────┘
                   │                    │                    │
                   └────────────────────┼────────────────────┘
                                        │
                                        ▼
                          ┌──────────────────────────────────────────┐
                          │           query() 主循环                  │
                          │                                          │
                          │  messagesWithContext = [                  │
                          │    UserMessage(CLAUDE.md, isMeta),       │
                          │    ...历史消息,                           │
                          │    AttachmentMsg(文件),                   │
                          │    AttachmentMsg(记忆),                   │
                          │    UserMessage(用户输入),                 │
                          │  ]                                       │
                          │                                          │
                          │  fullSystemPrompt = [                    │
                          │    系统提示词,                            │
                          │    gitStatus: ...,                       │
                          │  ]                                       │
                          └──────────────────┬───────────────────────┘
                                             │
                                             ▼
                          ┌──────────────────────────────────────────┐
                          │           Anthropic Messages API          │
                          │                                          │
                          │  {                                       │
                          │    system: fullSystemPrompt,             │
                          │    messages: messagesWithContext,        │
                          │    tools: [...],                         │
                          │    model: "claude-sonnet-4-20250514",   │
                          │  }                                       │
                          └──────────────────────────────────────────┘
```

---

## 附录 D：Context 系统的性能特征

### D.1 延迟分布

| 操作 | 典型延迟 | 瓶颈 |
|------|---------|------|
| `getUserContext()` (首次) | 50-200ms | 文件 I/O（CLAUDE.md 读取） |
| `getUserContext()` (缓存) | <1ms | 内存查找 |
| `getSystemContext()` (首次) | 100-500ms | 5 个并行 git 命令 |
| `getSystemContext()` (缓存) | <1ms | 内存查找 |
| `getAttachments()` (完整) | 200-1000ms | 受 1 秒超时保护 |
| `processAtMentionedFiles()` | 10-100ms | 文件读取 |
| `getChangedFiles()` | 50-300ms | mtime 查询 + diff 生成 |
| `startRelevantMemoryPrefetch()` | 100-500ms | 异步，不阻塞主循环 |
| `appendSystemContext()` | <1ms | 纯字符串操作 |
| `prependUserContext()` | <1ms | 纯数组操作 |

### D.2 内存使用

| 组件 | 典型内存 | 上限 |
|------|---------|------|
| `FileStateCache` | 5-50MB | 25MB (LRU) |
| `getUserContext` 缓存 | 10-40KB | 40KB (CLAUDE.md) |
| `getSystemContext` 缓存 | 1-2KB | 2KB (git status) |
| `loadedNestedMemoryPaths` | 1-10KB | 无上限（Set） |
| `sentSkillNames` | 1-5KB | 无上限（Map） |
| 附件对象（每轮） | 10-100KB | 受超时限制 |

### D.3 I/O 模式

```
启动阶段:
  ├─ getSystemContext() ──── 5 个 git 命令（并行）
  └─ getUserContext() ────── 1-N 个文件读取（串行遍历）

每轮:
  ├─ getAttachments()
  │   ├─ @-提及文件 ──────── 1 个文件读取
  │   ├─ getChangedFiles() ── N 个 stat 调用 + M 个文件读取
  │   ├─ 嵌套记忆 ────────── N 个目录遍历 + M 个文件读取
  │   └─ 记忆预取 ────────── 异步，不阻塞
  └─ API 调用
```

---

## 附录 E：Context 系统的扩展点

### E.1 添加新的附件类型

1. 在 `Attachment` 联合类型中添加新类型
2. 在 `getAttachments()` 中添加收集器（使用 `maybe()` 包装）
3. 在 `messages.ts` 的 `switch (attachment.type)` 中添加渲染逻辑
4. 在分析事件中添加类型标签

### E.2 修改 CLAUDE.md 加载逻辑

1. `src/utils/claudemd.ts` 中的 `getMemoryFiles()` 是入口点
2. `processMemoryFile()` 处理单个文件
3. `processMdRules()` 处理条件规则
4. `getClaudeMds()` 合并多个文件

### E.3 自定义上下文注入

1. 通过 `appendSystemPrompt` 参数追加自定义系统提示
2. 通过 `customSystemPrompt` 参数完全替换系统提示
3. 通过 `setSystemPromptInjection()` 注入缓存破坏器（仅内部）
4. 通过 Hook 系统注入附加上下文

### E.4 修改记忆检索策略

1. `findRelevantMemories()` 是记忆检索的核心
2. `readMemoriesForSurfacing()` 读取选中的记忆文件
3. `RELEVANT_MEMORIES_CONFIG` 控制预算
4. `collectSurfacedMemories()` 追踪已出现的记忆
