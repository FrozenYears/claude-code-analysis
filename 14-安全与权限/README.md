# 第14章：安全与权限

## 1. 本章目标

本章将深入剖析 Claude Code 的安全与权限系统——这是整个代码库中最关键、最复杂的子系统之一。通过对源码的逐层解读，读者将理解：

- **权限模式体系**：Claude Code 如何通过 `default`、`plan`、`acceptEdits`、`bypassPermissions`、`dontAsk`、`auto` 六种权限模式实现分级安全控制
- **工具权限检查流程**：每次工具调用如何经过多层权限校验，从规则匹配到分类器决策再到用户确认
- **Bash 安全机制**：`bashSecurity.ts`（约 2593 行）中 23 个安全验证器如何防御命令注入、解析差异和各种绕过攻击
- **YOLO 分类器**：auto 模式下如何利用 LLM 二次分类器（两阶段 XML 分类器）实现智能安全决策
- **纵深防御架构**：从模式层、规则层、语义层到分类器层的四层防御体系如何协同工作

## 2. 前置知识

在学习本章之前，读者应具备以下基础知识：

- **Shell 安全基础**：了解 bash/zsh 的命令替换（`$()`）、进程替换（`<()`）、重定向（`>`/`<`）、管道（`|`）等概念
- **引号语义**：理解单引号（强引用）、双引号（弱引用）和转义字符在 shell 中的不同行为
- **TypeScript 类型系统**：了解联合类型、类型守卫、泛型约束等高级特性
- **正则表达式**：熟悉前瞻/后顾断言、非贪婪匹配、字符类等高级正则语法
- **AST 解析概念**：了解抽象语法树的基本概念，理解 tree-sitter 的作用
- **API 分类器概念**：理解使用 LLM 进行安全分类的基本思路

## 3. 宏观概览

Claude Code 的安全与权限系统是一个**纵深防御（Defense in Depth）**架构，由四个核心层次构成：

```
┌─────────────────────────────────────────────────┐
│              权限模式层 (PermissionMode)          │
│   default / plan / acceptEdits / auto / bypass   │
├─────────────────────────────────────────────────┤
│              权限规则层 (PermissionRules)          │
│   allow / deny / ask 规则匹配 + 路径约束         │
├─────────────────────────────────────────────────┤
│              语义安全层 (BashSecurity)             │
│   23 个验证器 + tree-sitter AST 分析              │
├─────────────────────────────────────────────────┤
│              分类器层 (YOLO Classifier)            │
│   LLM 二次分类 + 两阶段 XML 决策                  │
└─────────────────────────────────────────────────┘
```

**核心文件一览：**

| 文件 | 行数 | 职责 |
|------|------|------|
| `utils/permissions/PermissionMode.ts` | ~100 | 权限模式定义与配置 |
| `utils/permissions/permissionSetup.ts` | ~1200 | 权限初始化、模式转换、危险规则检测 |
| `utils/permissions/permissions.ts` | ~1500 | 权限规则匹配核心逻辑 |
| `utils/permissions/yoloClassifier.ts` | ~800 | YOLO/自动模式分类器 |
| `utils/permissions/denialTracking.ts` | ~40 | 拒绝追踪与回退机制 |
| `utils/permissions/classifierDecision.ts` | ~100 | 分类器决策安全白名单 |
| `utils/permissions/dangerousPatterns.ts` | ~70 | 危险命令模式定义 |
| `tools/BashTool/bashSecurity.ts` | ~2593 | 23 个 Bash 安全验证器 |
| `tools/BashTool/bashPermissions.ts` | ~2622 | Bash 权限检查主流程 |
| `hooks/toolPermission/PermissionContext.ts` | ~300 | 权限上下文与决策构建 |
| `services/policyLimits/index.ts` | ~600 | 组织级策略限制 |
| `utils/secureStorage/` | ~200 | 安全存储（密钥链） |

## 4. 源码入口定位

### 4.1 权限模式定义

权限模式是整个安全系统的顶层抽象。每种模式决定了工具调用是否需要用户确认。

**文件：`src/types/permissions.ts`（第 30-51 行）**

```typescript
export const EXTERNAL_PERMISSION_MODES = [
  'acceptEdits',
  'bypassPermissions',
  'default',
  'dontAsk',
  'plan',
] as const

export type ExternalPermissionMode = (typeof EXTERNAL_PERMISSION_MODES)[number]

export type InternalPermissionMode = ExternalPermissionMode | 'auto' | 'bubble'
export type PermissionMode = InternalPermissionMode

export const INTERNAL_PERMISSION_MODES = [
  ...EXTERNAL_PERMISSION_MODES,
  ...(feature('TRANSCRIPT_CLASSIFIER') ? (['auto'] as const) : ([] as const)),
] as const satisfies readonly PermissionMode[]
```

这里有三个关键设计决策：

1. **外部模式 vs 内部模式**：`ExternalPermissionMode` 是面向用户的5种模式，`auto` 和 `bubble` 是内部模式，通过 `feature('TRANSCRIPT_CLASSIFIER')` 编译时条件控制
2. **类型安全**：使用 `as const` 和 `satisfies` 确保编译时的类型完备性
3. **功能门控**：`auto` 模式仅在 `TRANSCRIPT_CLASSIFIER` feature flag 启用时可用

**文件：`src/utils/permissions/PermissionMode.ts`（第 58-108 行）**

```typescript
const PERMISSION_MODE_CONFIG: Partial<Record<PermissionMode, PermissionModeConfig>> = {
  default: {
    title: 'Default',
    shortTitle: 'Default',
    symbol: '',
    color: 'text',
    external: 'default',
  },
  plan: {
    title: 'Plan Mode',
    shortTitle: 'Plan',
    symbol: PAUSE_ICON,
    color: 'planMode',
    external: 'plan',
  },
  acceptEdits: {
    title: 'Accept edits',
    shortTitle: 'Accept',
    symbol: '⏵⏵',
    color: 'autoAccept',
    external: 'acceptEdits',
  },
  bypassPermissions: {
    title: 'Bypass Permissions',
    shortTitle: 'Bypass',
    symbol: '⏵⏵',
    color: 'error',
    external: 'bypassPermissions',
  },
  // ...
}
```

每种模式都有独立的 UI 配置（标题、符号、颜色），并映射到一个外部可见的模式名。

### 4.2 权限上下文初始化

**文件：`src/utils/permissions/permissionSetup.ts`（第 ~900 行）**

```typescript
export async function initializeToolPermissionContext({
  allowedToolsCli,
  disallowedToolsCli,
  baseToolsCli,
  permissionMode,
  allowDangerouslySkipPermissions,
  addDirs,
}: { ... }): Promise<{
  toolPermissionContext: ToolPermissionContext
  warnings: string[]
  dangerousPermissions: DangerousPermissionInfo[]
  overlyBroadBashPermissions: DangerousPermissionInfo[]
}>
```

这是权限系统的启动入口。它完成以下工作：
1. 解析 CLI 传入的 `--allowed-tools` 和 `--disallowed-tools`
2. 从磁盘加载所有权限规则（`loadAllPermissionRulesFromDisk`）
3. 检测危险的 auto 模式权限（`findDangerousClassifierPermissions`）
4. 检测过于宽泛的 Bash 权限（`findOverlyBroadBashPermissions`）
5. 验证额外工作目录
6. 返回完整的 `ToolPermissionContext`

### 4.3 工具权限检查入口

**文件：`src/utils/permissions/permissions.ts`（第 ~100-250 行）**

权限检查的核心函数 `canUseTool` 位于此文件中（约 1500 行）。它协调了整个权限检查流程：

```typescript
// 核心流程概要（简化）:
// 1. 检查权限模式 → bypassPermissions 直接放行
// 2. 检查 deny 规则 → 命中则拒绝
// 3. 检查 allow 规则 → 命中则放行
// 4. 对 Bash 工具执行特殊安全检查
// 5. 运行 auto 模式分类器（如果启用）
// 6. 回退到用户确认
```

## 5. 调用链分析

### 5.1 完整的工具权限检查调用链

当 Claude 的模型输出一个工具调用时，权限检查的完整调用链如下：

```
模型输出 tool_use
    │
    ▼
PermissionContext.runHooks()           ← 第1层：Hook 检查
    │
    ▼
permissions.canUseTool()               ← 第2层：核心权限逻辑
    ├── checkPermissionMode()          ← 模式级检查
    │   ├── bypassPermissions → 直接 allow
    │   ├── plan → 拒绝写操作
    │   └── dontAsk → 拒绝所有
    │
    ├── getDenyRules() + 规则匹配      ← 第3层：deny 规则
    │
    ├── getAllowRules() + 规则匹配     ← 第3层：allow 规则
    │
    ├── bashToolHasPermission()        ← 第4层：Bash 专项检查
    │   ├── bashCommandIsSafe()        ← 23个安全验证器
    │   ├── splitCommand()             ← 命令拆分
    │   ├── checkPathConstraints()     ← 路径约束
    │   └── classifyBashCommand()      ← Bash 分类器
    │
    ├── classifyYoloAction()           ← 第5层：YOLO 分类器
    │   ├── buildYoloSystemPrompt()
    │   ├── buildTranscriptForClassifier()
    │   └── classifyYoloActionXml()    ← 两阶段 XML 分类
    │
    └── 用户确认                        ← 第6层：回退到人工
```

### 5.2 Bash 安全验证器调用链

`bashCommandIsSafe()` 函数内部按顺序调用 23 个验证器：

```
bashCommandIsSafe_DEPRECATED(command)
    │
    ├── validateEmpty()                    ← 空命令检查
    ├── validateIncompleteCommands()       ← 不完整命令检查
    ├── validateSafeCommandSubstitution()  ← 安全 heredoc 检查（快速放行）
    ├── validateGitCommit()                ← git commit 特殊处理
    ├── validateJqCommand()                ← jq 安全检查
    │
    ├── [构建 ValidationContext]
    │   ├── extractQuotedContent()         ← 提取引号内容
    │   └── stripSafeRedirections()        ← 剥离安全重定向
    │
    ├── validateShellMetacharacters()      ← shell 元字符检查
    ├── validateDangerousVariables()       ← 危险变量检查
    ├── validateDangerousPatterns()        ← 命令替换模式检查
    ├── validateRedirections()             ← 重定向检查
    ├── validateNewlines()                 ← 换行符检查
    ├── validateCarriageReturn()           ← 回车符检查
    ├── validateIFSInjection()             ← IFS 注入检查
    ├── validateProcEnvironAccess()        ← /proc 环境变量访问检查
    ├── validateMalformedTokenInjection()  ← 畸形 token 注入检查
    ├── validateObfuscatedFlags()          ← 混淆标志检查
    ├── validateBackslashEscapedWhitespace() ← 反斜杠转义空白检查
    ├── validateBackslashEscapedOperators()  ← 反斜杠转义操作符检查
    ├── validateBraceExpansion()           ← 花括号展开检查
    ├── validateUnicodeWhitespace()        ← Unicode 空白检查
    ├── validateMidWordHash()              ← 词中 # 检查
    ├── validateCommentQuoteDesync()       ← 注释引号去同步检查
    ├── validateQuotedNewline()            ← 引号内换行检查
    └── validateZshDangerousCommands()     ← Zsh 危险命令检查
```

### 5.3 模式转换调用链

当用户切换权限模式时：

```
用户 Shift+Tab / SDK set_permission_mode
    │
    ▼
transitionPermissionMode(fromMode, toMode, context)
    │
    ├── handlePlanModeTransition()         ← Plan 模式附件处理
    ├── handleAutoModeTransition()         ← Auto 模式附件处理
    │
    ├── [进入 auto 模式]
    │   ├── isAutoModeGateEnabled()        ← 门控检查
    │   ├── setAutoModeActive(true)
    │   └── stripDangerousPermissionsForAutoMode()  ← 剥离危险规则
    │
    └── [离开 auto 模式]
        ├── setAutoModeActive(false)
        ├── setNeedsAutoModeExitAttachment(true)
        └── restoreDangerousPermissions()  ← 恢复危险规则
```

## 6. 核心源码解析

### 6.1 权限模式体系

Claude Code 定义了 6 种权限模式（其中 `auto` 是内部模式），每种模式对应不同的安全等级：

| 模式 | 行为 | 安全等级 |
|------|------|---------|
| `default` | 所有写操作和 Bash 命令需用户确认 | 最高 |
| `plan` | 只允许读操作，拒绝所有写操作 | 高 |
| `acceptEdits` | 自动批准工作目录内的文件编辑 | 中 |
| `auto` | 由 LLM 分类器自动决策 | 中-低 |
| `dontAsk` | 拒绝所有需要权限的操作 | 特殊 |
| `bypassPermissions` | 跳过所有权限检查 | 最低 |

**关键源码：`permissionSetup.ts` 的 `initialPermissionModeFromCLI` 函数**

```typescript
export function initialPermissionModeFromCLI({
  permissionModeCli,
  dangerouslySkipPermissions,
}: {
  permissionModeCli: string | undefined
  dangerouslySkipPermissions: boolean | undefined
}): { mode: PermissionMode; notification?: string } {
  const settings = getSettings_DEPRECATED() || {}

  // GrowthBook 门控检查 — 最高优先级
  const growthBookDisableBypassPermissionsMode =
    checkStatsigFeatureGate_CACHED_MAY_BE_STALE(
      'tengu_disable_bypass_permissions_mode',
    )

  // 设置文件检查 — 较低优先级
  const settingsDisableBypassPermissionsMode =
    settings.permissions?.disableBypassPermissionsMode === 'disable'

  // 模式优先级顺序
  const orderedModes: PermissionMode[] = []
  if (dangerouslySkipPermissions) orderedModes.push('bypassPermissions')
  if (permissionModeCli) orderedModes.push(parsedMode)
  if (settings.permissions?.defaultMode) orderedModes.push(settingsMode)
  // ...
}
```

这个函数展示了**优先级链**的设计：CLI 参数 > 设置文件 > 默认值，同时 GrowthBook 门控可以覆盖一切。

### 6.2 危险权限检测

进入 auto 模式前，系统必须剥离所有"危险"的 allow 规则。这些规则会让特定命令绕过分类器的安全评估。

**文件：`permissionSetup.ts`（第 ~100-200 行）**

```typescript
export function isDangerousBashPermission(
  toolName: string,
  ruleContent: string | undefined,
): boolean {
  if (toolName !== BASH_TOOL_NAME) return false

  // 工具级 allow（Bash 无内容）— 允许所有命令
  if (ruleContent === undefined || ruleContent === '') return true

  const content = ruleContent.trim().toLowerCase()
  if (content === '*') return true

  // 检查危险模式
  for (const pattern of DANGEROUS_BASH_PATTERNS) {
    const lowerPattern = pattern.toLowerCase()
    if (content === lowerPattern) return true
    if (content === `${lowerPattern}:*`) return true
    if (content === `${lowerPattern}*`) return true
    if (content === `${lowerPattern} *`) return true
    if (content.startsWith(`${lowerPattern} -`) && content.endsWith('*')) return true
  }
  return false
}
```

**危险模式列表（`dangerousPatterns.ts`）：**

```typescript
export const DANGEROUS_BASH_PATTERNS: readonly string[] = [
  // 解释器
  'python', 'python3', 'python2', 'node', 'deno', 'tsx',
  'ruby', 'perl', 'php', 'lua',
  // 包管理器
  'npx', 'bunx', 'npm run', 'yarn run', 'pnpm run', 'bun run',
  // Shell
  'bash', 'sh', 'zsh', 'fish',
  // 执行器
  'eval', 'exec', 'env', 'xargs', 'sudo',
  // SSH
  'ssh',
  // Ant-only（内部额外添加的）
  ...(process.env.USER_TYPE === 'ant' ? [
    'fa run', 'coo', 'gh', 'gh api', 'curl', 'wget',
    'git', 'kubectl', 'aws', 'gcloud', 'gsutil',
  ] : []),
]
```

这些模式之所以危险，是因为 `Bash(python:*)` 这样的规则会允许任意 Python 代码执行，完全绕过分类器。

### 6.3 Bash 安全验证器详解

`bashSecurity.ts` 是整个安全系统的核心，包含 23 个验证器。以下逐一解析最关键的几个：

#### 6.3.1 validateEmpty — 空命令检查

```typescript
function validateEmpty(context: ValidationContext): PermissionResult {
  if (!context.originalCommand.trim()) {
    return {
      behavior: 'allow',
      updatedInput: { command: context.originalCommand },
      decisionReason: { type: 'other', reason: 'Empty command is safe' },
    }
  }
  return { behavior: 'passthrough', message: 'Command is not empty' }
}
```

最简单的验证器：空命令直接放行。

#### 6.3.2 validateIncompleteCommands — 不完整命令检查

```typescript
function validateIncompleteCommands(context: ValidationContext): PermissionResult {
  const { originalCommand } = context
  const trimmed = originalCommand.trim()

  // 以 tab 开头 → 可能是不完整的片段
  if (/^\s*\t/.test(originalCommand)) {
    return { behavior: 'ask', message: 'Command appears to be an incomplete fragment' }
  }

  // 以 flag 开头 → 缺少命令名
  if (trimmed.startsWith('-')) {
    return { behavior: 'ask', message: 'Command appears to be an incomplete fragment' }
  }

  // 以操作符开头 → 续行
  if (/^\s*(&&|\|\||;|>>?|<)/.test(originalCommand)) {
    return { behavior: 'ask', message: 'Command appears to be a continuation line' }
  }
  return { behavior: 'passthrough', message: 'Command appears complete' }
}
```

防御模型生成的不完整命令片段。

#### 6.3.3 validateGitCommit — Git Commit 特殊处理

```typescript
function validateGitCommit(context: ValidationContext): PermissionResult {
  const { originalCommand, baseCommand } = context
  if (baseCommand !== 'git' || !/^git\s+commit\s+/.test(originalCommand)) {
    return { behavior: 'passthrough', message: 'Not a git commit' }
  }

  // 反斜杠可能导致引号边界误判
  if (originalCommand.includes('\\')) {
    return { behavior: 'passthrough', message: 'Git commit contains backslash' }
  }

  const messageMatch = originalCommand.match(
    /^git[ \t]+commit[ \t]+[^;&|`$<>()\n\r]*?-m[ \t]+(["'])([\s\S]*?)\1(.*)$/,
  )

  if (messageMatch) {
    const [, quote, messageContent, remainder] = messageMatch

    // 双引号中的命令替换检查
    if (quote === '"' && messageContent && /\$\(|`|\$\{/.test(messageContent)) {
      return { behavior: 'ask', message: 'Git commit message contains command substitution' }
    }

    // 检查 remainder 中的 shell 操作符
    if (remainder && /[;|&()`]|\$\(|\$\{/.test(remainder)) {
      return { behavior: 'passthrough', message: 'Git commit remainder contains shell metacharacters' }
    }

    // 检查 remainder 中的重定向操作符
    if (remainder) {
      let unquoted = ''
      let inSQ = false, inDQ = false
      for (let i = 0; i < remainder.length; i++) {
        const c = remainder[i]
        if (c === "'" && !inDQ) { inSQ = !inSQ; continue }
        if (c === '"' && !inSQ) { inDQ = !inDQ; continue }
        if (!inSQ && !inDQ) unquoted += c
      }
      if (/[<>]/.test(unquoted)) {
        return { behavior: 'passthrough', message: 'Git commit remainder contains unquoted redirect' }
      }
    }

    return {
      behavior: 'allow',
      updatedInput: { command: originalCommand },
      decisionReason: { type: 'other', reason: 'Git commit with simple quoted message is allowed' },
    }
  }
  return { behavior: 'passthrough', message: 'Git commit needs validation' }
}
```

这是一个**早期放行**验证器。`git commit -m "message"` 是如此常见的操作，所以专门做了安全快路径。但注释中详细记录了之前的安全漏洞：`.*?` 正则曾匹配 shell 操作符，导致 `git commit ; curl evil.com -m 'x'` 被错误放行。

#### 6.3.4 validateObfuscatedFlags — 混淆标志检测

这是最复杂的验证器之一（约 450 行），防御各种标志混淆攻击：

```typescript
function validateObfuscatedFlags(context: ValidationContext): PermissionResult {
  const { originalCommand, baseCommand } = context

  // 1. ANSI-C 引号 ($'...') — 可编码任意字符
  if (/\$'[^']*'/.test(originalCommand)) {
    return { behavior: 'ask', message: 'Command contains ANSI-C quoting' }
  }

  // 2. Locale 引号 ($"...") — 也可使用转义序列
  if (/\$"[^"]*"/.test(originalCommand)) {
    return { behavior: 'ask', message: 'Command contains locale quoting' }
  }

  // 3. 空 ANSI-C 或 locale 引号后跟连字符
  if (/\$['"]{2}\s*-/.test(originalCommand)) {
    return { behavior: 'ask', message: 'Empty special quotes before dash' }
  }

  // 4. 任意空引号序列后跟连字符
  if (/(?:^|\s)(?:''|"")+\s*-/.test(originalCommand)) {
    return { behavior: 'ask', message: 'Empty quotes before dash' }
  }

  // 4b. 同质空引号对紧邻引号连字符（如 """-"f"）
  if (/(?:""|'')+['"]-/.test(originalCommand)) {
    return { behavior: 'ask', message: 'Empty quote pair adjacent to quoted dash' }
  }

  // 4c. 3+ 连续引号在词首
  if (/(?:^|\s)['"]{3,}/.test(originalCommand)) {
    return { behavior: 'ask', message: 'Consecutive quotes at word start' }
  }

  // 引号状态追踪的标志检测...
  // （省略约 300 行引号状态追踪逻辑）
}
```

**攻击示例：**
- `grep $'-exec' file` — ANSI-C 引号编码 `-exec`
- `find . -""exec` — 空引号拼接
- `"""-"exec` — 多重引号混淆

#### 6.3.5 validateBraceExpansion — 花括号展开检测

```typescript
function validateBraceExpansion(context: ValidationContext): PermissionResult {
  const content = context.fullyUnquotedPreStrip

  // 检查不匹配的花括号计数
  let unescapedOpenBraces = 0
  let unescapedCloseBraces = 0
  for (let i = 0; i < content.length; i++) {
    if (content[i] === '{' && !isEscapedAtPosition(content, i)) unescapedOpenBraces++
    else if (content[i] === '}' && !isEscapedAtPosition(content, i)) unescapedCloseBraces++
  }

  // 关闭花括号多于开启花括号 → 可能是花括号展开混淆
  if (unescapedOpenBraces > 0 && unescapedCloseBraces > unescapedOpenBraces) {
    return { behavior: 'ask', message: 'Excess closing braces after quote stripping' }
  }

  // 扫描花括号展开模式
  for (let i = 0; i < content.length; i++) {
    if (content[i] !== '{' || isEscapedAtPosition(content, i)) continue

    // 找匹配的 }
    let depth = 1
    let matchingClose = -1
    for (let j = i + 1; j < content.length; j++) {
      if (content[j] === '{' && !isEscapedAtPosition(content, j)) depth++
      else if (content[j] === '}' && !isEscapedAtPosition(content, j)) {
        depth--
        if (depth === 0) { matchingClose = j; break }
      }
    }

    if (matchingClose === -1) continue

    // 检查最外层的 , 或 ..
    let innerDepth = 0
    for (let k = i + 1; k < matchingClose; k++) {
      if (content[k] === '{' && !isEscapedAtPosition(content, k)) innerDepth++
      else if (content[k] === '}' && !isEscapedAtPosition(content, k)) innerDepth--
      else if (innerDepth === 0) {
        if (content[k] === ',' || (content[k] === '.' && content[k + 1] === '.')) {
          return { behavior: 'ask', message: 'Brace expansion detected' }
        }
      }
    }
  }
  return { behavior: 'passthrough', message: 'No brace expansion detected' }
}
```

**攻击示例：**
```
git ls-remote {--upload-pack="touch /tmp/test",test}
```
解析器看到一个字面参数，但 bash 会展开为两个参数。

#### 6.3.6 validateQuotedNewline — 引号内换行检测

```typescript
function validateQuotedNewline(context: ValidationContext): PermissionResult {
  const { originalCommand } = context
  if (!originalCommand.includes('\n') || !originalCommand.includes('#')) {
    return { behavior: 'passthrough', message: 'No newline or no hash' }
  }

  // 追踪引号状态
  let inSingleQuote = false, inDoubleQuote = false, escaped = false

  for (let i = 0; i < originalCommand.length; i++) {
    const char = originalCommand[i]
    if (escaped) { escaped = false; continue }
    if (char === '\\' && !inSingleQuote) { escaped = true; continue }
    if (char === "'" && !inDoubleQuote) { inSingleQuote = !inSingleQuote; continue }
    if (char === '"' && !inSingleQuote) { inDoubleQuote = !inDoubleQuote; continue }

    // 引号内的换行：下一行如果以 # 开头会被 stripCommentLines 剥离
    if (char === '\n' && (inSingleQuote || inDoubleQuote)) {
      const nextLine = originalCommand.slice(i + 1, originalCommand.indexOf('\n', i + 1))
      if (nextLine.trim().startsWith('#')) {
        return { behavior: 'ask', message: 'Quoted newline followed by #-prefixed line' }
      }
    }
  }
  return { behavior: 'passthrough', message: 'No quoted newline-hash pattern' }
}
```

**攻击示例：**
```
mv ./decoy '<\n>#' ~/.ssh/id_rsa ./exfil_dir
```
bash 中 `\n#` 是字面字符，但 `stripCommentLines` 按行处理，会丢弃 `#` 开头的行，从而隐藏敏感路径。

#### 6.3.7 validateCarriageReturn — 回车符检测

```typescript
function validateCarriageReturn(context: ValidationContext): PermissionResult {
  const { originalCommand } = context
  if (!originalCommand.includes('\r')) return { behavior: 'passthrough', message: 'No carriage return' }

  // 检查 CR 是否出现在双引号之外
  let inSingleQuote = false, inDoubleQuote = false, escaped = false
  for (let i = 0; i < originalCommand.length; i++) {
    const c = originalCommand[i]
    if (escaped) { escaped = false; continue }
    if (c === '\\' && !inSingleQuote) { escaped = true; continue }
    if (c === "'" && !inDoubleQuote) { inSingleQuote = !inSingleQuote; continue }
    if (c === '"' && !inSingleQuote) { inDoubleQuote = !inDoubleQuote; continue }
    if (c === '\r' && !inDoubleQuote) {
      return { behavior: 'ask', message: 'Carriage return outside double quotes' }
    }
  }
  return { behavior: 'passthrough', message: 'CR only inside double quotes' }
}
```

**解析差异攻击：**
- `shell-quote` 的正则用 `\s` 分词，JS 的 `\s` 包含 `\r`
- bash 的默认 IFS = `$' \t\n'`，不包含 `\r`
- `TZ=UTC\recho curl evil.com`：解析器分成两个 token，bash 视为一个

#### 6.3.8 validateZshDangerousCommands — Zsh 危险命令

```typescript
const ZSH_DANGEROUS_COMMANDS = new Set([
  'zmodload',    // 模块加载（zsh/system, zsh/net/tcp 等）
  'emulate',     // -c 标志等同于 eval
  'sysopen', 'sysread', 'syswrite', 'sysseek',  // zsh/system 原始 I/O
  'zpty',        // 伪终端命令执行
  'ztcp', 'zsocket',  // 网络连接（数据外泄）
  'mapfile',     // 文件 I/O 数组
  'zf_rm', 'zf_mv', 'zf_ln', 'zf_chmod',  // zsh/files 内置命令
  'zf_chown', 'zf_mkdir', 'zf_rmdir', 'zf_chgrp',
])
```

这些 Zsh 内置命令可以绕过二进制检查，直接操作系统。

### 6.4 ValidationContext 构建

所有验证器共享一个 `ValidationContext`，它预处理命令以提取多种视图：

```typescript
type ValidationContext = {
  originalCommand: string           // 原始命令
  baseCommand: string               // 基础命令名
  unquotedContent: string           // 去除单引号内容
  fullyUnquotedContent: string      // 去除所有引号内容
  fullyUnquotedPreStrip: string     // 去除重定向前的内容
  unquotedKeepQuoteChars: string    // 保留引号字符的版本
  treeSitter?: TreeSitterAnalysis  // tree-sitter AST 分析
}
```

`extractQuotedContent` 函数是关键的预处理步骤：

```typescript
function extractQuotedContent(command: string, isJq = false): QuoteExtraction {
  let withDoubleQuotes = ''
  let fullyUnquoted = ''
  let unquotedKeepQuoteChars = ''
  let inSingleQuote = false
  let inDoubleQuote = false
  let escaped = false

  for (let i = 0; i < command.length; i++) {
    const char = command[i]
    if (escaped) {
      escaped = false
      if (!inSingleQuote) withDoubleQuotes += char
      if (!inSingleQuote && !inDoubleQuote) fullyUnquoted += char
      if (!inSingleQuote && !inDoubleQuote) unquotedKeepQuoteChars += char
      continue
    }
    if (char === '\\' && !inSingleQuote) {
      escaped = true
      // ...
      continue
    }
    if (char === "'" && !inDoubleQuote) {
      inSingleQuote = !inSingleQuote
      unquotedKeepQuoteChars += char
      continue
    }
    if (char === '"' && !inSingleQuote) {
      inDoubleQuote = !inDoubleQuote
      unquotedKeepQuoteChars += char
      if (!isJq) continue
    }
    if (!inSingleQuote) withDoubleQuotes += char
    if (!inSingleQuote && !inDoubleQuote) fullyUnquoted += char
    if (!inSingleQuote && !inDoubleQuote) unquotedKeepQuoteChars += char
  }
  return { withDoubleQuotes, fullyUnquoted, unquotedKeepQuoteChars }
}
```

这个函数正确处理了引号嵌套和转义，是安全验证器的基础。

### 6.5 YOLO 分类器（Auto 模式）

当 auto 模式启用时，系统使用 LLM 作为二次分类器来决定是否允许操作。

**文件：`utils/permissions/yoloClassifier.ts`**

#### 6.5.1 分类器架构

```typescript
export async function classifyYoloAction(
  messages: Message[],
  action: TranscriptEntry,
  tools: Tools,
  context: ToolPermissionContext,
  signal: AbortSignal,
): Promise<YoloClassifierResult> {
  // 1. 构建紧凑的会话记录
  const transcriptEntries = buildTranscriptEntries(messages)

  // 2. 构建系统提示
  const systemPrompt = await buildYoloSystemPrompt(context)

  // 3. 构建 CLAUDE.md 前缀消息
  const claudeMdMessage = buildClaudeMdMessage()

  // 4. 选择分类器模型
  const model = getClassifierModel()

  // 5. 根据配置选择分类器类型
  if (isTwoStageClassifierEnabled()) {
    return classifyYoloActionXml(...)  // 两阶段 XML 分类器
  }
  // 否则使用传统 tool_use 分类器
}
```

#### 6.5.2 两阶段 XML 分类器

这是最先进的分类器实现，支持三种模式：

```typescript
type TwoStageMode = 'both' | 'fast' | 'thinking'

// 'both'（默认）：Stage 1 快速判断，被阻止时升级到 Stage 2
// 'fast'：仅 Stage 1，max_tokens=256
// 'thinking'：仅 Stage 2，带思维链推理
```

**Stage 1（快速阶段）：**
```typescript
const stage1Content = [
  ...wrappedContent,
  { type: 'text', text: XML_S1_SUFFIX },  // "Err on the side of blocking. <block> immediately."
]
const stage1Opts = {
  model,
  max_tokens: 64 + thinkingPadding,
  temperature: 0,
  stop_sequences: ['</block>'],  // 在 </block> 处停止
  // ...
}
```

**Stage 2（思考阶段）：**
```typescript
const stage2Content = [
  ...wrappedContent,
  { type: 'text', text: XML_S2_SUFFIX },  // 要求思维链推理
]
const stage2Opts = {
  model,
  max_tokens: 4096 + thinkingPadding,
  temperature: 0,
  // 无 stop_sequences，允许完整推理
}
```

**响应解析：**
```typescript
function parseXmlBlock(text: string): boolean | null {
  const matches = [
    ...stripThinking(text).matchAll(/<block>(yes|no)\b(<\/block>)?/gi),
  ]
  if (matches.length === 0) return null
  return matches[0]![1]!.toLowerCase() === 'yes'
}
```

#### 6.5.3 安全白名单

`classifierDecision.ts` 定义了 auto 模式下的安全工具白名单：

```typescript
const SAFE_YOLO_ALLOWLISTED_TOOLS = new Set([
  // 只读文件操作
  FILE_READ_TOOL_NAME,
  // 搜索
  GREP_TOOL_NAME, GLOB_TOOL_NAME, LSP_TOOL_NAME,
  // 任务管理（仅元数据）
  TODO_WRITE_TOOL_NAME, TASK_CREATE_TOOL_NAME, TASK_GET_TOOL_NAME,
  // Plan 模式 / UI
  ASK_USER_QUESTION_TOOL_NAME, ENTER_PLAN_MODE_TOOL_NAME, EXIT_PLAN_MODE_TOOL_NAME,
  // 杂项安全
  SLEEP_TOOL_NAME,
  // ...
])
```

这些工具在 auto 模式下不需要分类器检查，直接放行。

### 6.6 拒绝追踪与回退机制

**文件：`utils/permissions/denialTracking.ts`**

```typescript
export const DENIAL_LIMITS = {
  maxConsecutive: 3,   // 连续拒绝 3 次
  maxTotal: 20,        // 总共拒绝 20 次
} as const

export function shouldFallbackToPrompting(state: DenialTrackingState): boolean {
  return (
    state.consecutiveDenials >= DENIAL_LIMITS.maxConsecutive ||
    state.totalDenials >= DENIAL_LIMITS.maxTotal
  )
}
```

当分类器连续拒绝 3 次或累计拒绝 20 次时，系统会回退到用户确认模式。这防止了分类器过于保守导致用户体验下降。

### 6.7 安全存储

**文件：`utils/secureStorage/index.ts`**

```typescript
export function getSecureStorage(): SecureStorage {
  if (process.platform === 'darwin') {
    return createFallbackStorage(macOsKeychainStorage, plainTextStorage)
  }
  return plainTextStorage
}
```

macOS 上优先使用系统钥匙链（Keychain），失败时回退到明文存储。其他平台使用明文存储（TODO: Linux 的 libsecret 支持）。

### 6.8 策略限制服务

**文件：`services/policyLimits/index.ts`**

```typescript
// 关键常量
const FETCH_TIMEOUT_MS = 10000           // 10 秒超时
const DEFAULT_MAX_RETRIES = 5            // 最多重试 5 次
const POLLING_INTERVAL_MS = 60 * 60 * 1000  // 每小时轮询

// 会话级缓存
let sessionCache: PolicyLimitsResponse['restrictions'] | null = null
```

策略限制服务从 API 获取组织级限制，遵循"失败开放（fail open）"策略：如果获取失败，继续不限制。这确保了网络问题不会阻断用户工作。

### 6.9 权限上下文与决策构建

**文件：`hooks/toolPermission/PermissionContext.ts`**

`createPermissionContext` 函数创建了一个冻结的权限上下文对象，提供了完整的决策构建能力：

```typescript
function createPermissionContext(
  tool: ToolType,
  input: Record<string, unknown>,
  toolUseContext: ToolUseContext,
  assistantMessage: AssistantMessage,
  toolUseID: string,
  setToolPermissionContext: (context: ToolPermissionContext) => void,
  queueOps?: PermissionQueueOps,
) {
  return Object.freeze({
    // 分类器尝试
    async tryClassifier(pendingClassifierCheck, updatedInput) {
      if (tool.name !== BASH_TOOL_NAME || !pendingClassifierCheck) return null
      const classifierDecision = await awaitClassifierAutoApproval(
        pendingClassifierCheck,
        toolUseContext.abortController.signal,
        toolUseContext.options.isNonInteractiveSession,
      )
      // ...
    },

    // Hook 执行
    async runHooks(permissionMode, suggestions, updatedInput, permissionPromptStartTimeMs) {
      for await (const hookResult of executePermissionRequestHooks(...)) {
        if (hookResult.permissionRequestResult) {
          const decision = hookResult.permissionRequestResult
          if (decision.behavior === 'allow') {
            return await this.handleHookAllow(...)
          } else if (decision.behavior === 'deny') {
            return this.buildDeny(...)
          }
        }
      }
      return null
    },

    // 构建 allow/deny 决策
    buildAllow(updatedInput, opts) { ... },
    buildDeny(message, decisionReason) { ... },

    // 用户 allow 处理
    async handleUserAllow(updatedInput, permissionUpdates, feedback, ...) {
      const acceptedPermanentUpdates = await this.persistPermissions(permissionUpdates)
      // ...
    },
  })
}
```

`createResolveOnce` 工具函数解决了一个竞态条件问题：

```typescript
function createResolveOnce<T>(resolve: (value: T) => void): ResolveOnce<T> {
  let claimed = false
  let delivered = false
  return {
    resolve(value: T) {
      if (delivered) return
      delivered = true
      claimed = true
      resolve(value)
    },
    isResolved() { return claimed },
    claim() {
      if (claimed) return false
      claimed = true
      return true
    },
  }
}
```

`claim()` 方法提供原子性的"检查并标记"操作，防止异步回调之间的竞态。

### 6.10 safe wrappers 剥离

`stripSafeWrappers` 函数是权限规则匹配的关键预处理步骤：

```typescript
export function stripSafeWrappers(command: string): string {
  const SAFE_WRAPPER_PATTERNS = [
    /^timeout[ \t]+(?:(?:--(?:foreground|preserve-status|verbose)|...)...).../,
    /^time[ \t]+(?:--[ \t]+)?/,
    /^nice(?:[ \t]+-n[ \t]+-?\d+|[ \t]+-\d+)?[ \t]+(?:--[ \t]+)?/,
    /^stdbuf(?:[ \t]+-[ioe][LN0-9]+)+[ \t]+(?:--[ \t]+)?/,
    /^nohup[ \t]+(?:--[ \t]+)?/,
  ] as const

  const ENV_VAR_PATTERN = /^([A-Za-z_][A-Za-z0-9_]*)=([A-Za-z0-9_./:-]+)[ \t]+/

  // 阶段 1：剥离环境变量和注释
  // 阶段 2：剥离包装命令
  // ...
}
```

**安全约束：**
- 环境变量值只允许安全字符 `[A-Za-z0-9_./:-]`
- 空白必须是 `[ \t]`（水平制表符），不能是 `\s`（包含换行符）
- 包装命令中的 `timeout` 标志值使用白名单 `[A-Za-z0-9_.+-]`

### 6.11 Compound 命令的权限匹配

`bashPermissions.ts` 中的 `filterRulesByContentsMatchingInput` 函数处理复合命令的权限匹配：

```typescript
function filterRulesByContentsMatchingInput(
  input: z.infer<typeof BashTool.inputSchema>,
  rules: Map<string, PermissionRule>,
  matchMode: 'exact' | 'prefix',
  { stripAllEnvVars = false, skipCompoundCheck = false },
): PermissionRule[] {
  const command = input.command.trim()

  // 剥离输出重定向
  const commandWithoutRedirections =
    extractOutputRedirections(command).commandWithoutRedirections

  // 剥离安全包装命令
  const commandsToTry = commandsForMatching.flatMap(cmd => {
    const strippedCommand = stripSafeWrappers(cmd)
    return strippedCommand !== cmd ? [cmd, strippedCommand] : [cmd]
  })

  // 对于 deny/ask 规则，还尝试剥离所有环境变量
  if (stripAllEnvVars) {
    const seen = new Set(commandsToTry)
    let startIdx = 0
    while (startIdx < commandsToTry.length) {
      // 不动点迭代：直到不再产生新候选
      for (let i = startIdx; i < endIdx; i++) {
        const envStripped = stripAllLeadingEnvVars(cmd)
        if (!seen.has(envStripped)) {
          commandsToTry.push(envStripped)
          seen.add(envStripped)
        }
        const wrapperStripped = stripSafeWrappers(cmd)
        if (!seen.has(wrapperStripped)) {
          commandsToTry.push(wrapperStripped)
          seen.add(wrapperStripped)
        }
      }
      startIdx = endIdx
    }
  }
}
```

**关键安全规则：**
- 对于 allow 规则，只剥离安全环境变量（白名单）
- 对于 deny/ask 规则，剥离所有环境变量（防止 `FOO=bar denied_command` 绕过）
- 复合命令不匹配前缀规则（防止 `cd: *` 匹配 `cd /path && python3 evil.py`）

## 7. 架构设计思想

### 7.1 纵深防御（Defense in Depth）

Claude Code 的安全系统采用了经典的纵深防御策略，没有任何单一层次能完全阻止所有攻击：

1. **模式层**：`plan` 模式直接拒绝写操作，`default` 模式要求所有写操作确认
2. **规则层**：`deny` 规则可以永久阻止特定命令，`allow` 规则可以跳过确认
3. **语义层**：23 个验证器检测命令级别的安全问题
4. **分类器层**：LLM 分类器理解命令的意图和上下文

每一层都可以独立工作，即使某一层被绕过，其他层仍然提供保护。

### 7.2 失败关闭（Fail Closed）

安全系统在不确定时默认拒绝：

```typescript
// 分类器解析失败 → 阻止
if (stage2Block === null) {
  return {
    shouldBlock: true,
    reason: 'Classifier stage 2 unparseable - blocking for safety',
  }
}

// 分类器 API 错误 → 阻止
return {
  shouldBlock: true,
  reason: 'Classifier unavailable - blocking for safety',
  unavailable: true,
}
```

### 7.3 解析差异防御

许多验证器专门防御 shell-quote（JavaScript 的 shell 解析库）和 bash 之间的解析差异。这是 Claude Code 安全设计中最独特的方面：

- **`\r` 差异**：JS `\s` 包含 `\r`，bash IFS 不包含
- **花括号展开**：shell-quote 视为字面量，bash 展开
- **`#` 注释**：shell-quote 和 bash 在边界条件下行为不同
- **反斜杠转义**：`\;` 在不同解析器中可能有不同含义

### 7.4 编译时功能门控

使用 `feature()` 宏实现编译时的代码消除：

```typescript
const autoModeStateModule = feature('TRANSCRIPT_CLASSIFIER')
  ? (require('./autoModeState.js') as typeof import('./autoModeState.js'))
  : null
```

这确保了外部构建不包含内部功能的代码，减小了攻击面。

### 7.5 权限规则的多源聚合

权限规则来自多个来源，按优先级合并：

```
CLI 参数 (--allowed-tools)
    ↓
策略限制 (policySettings)
    ↓
用户设置 (userSettings)
    ↓
项目设置 (projectSettings)
    ↓
本地设置 (localSettings)
    ↓
会话规则 (session)
```

每个来源独立管理，通过 `applyPermissionRulesToPermissionContext` 合并到统一的上下文中。

## 8. 工程实践细节

### 8.1 性能优化

**子命令数量上限：**
```typescript
export const MAX_SUBCOMMANDS_FOR_SECURITY_CHECK = 50
```

复杂复合命令可能导致 `splitCommand` 产生指数级子命令，每个子命令都要运行 tree-sitter 解析和 23 个验证器。超过 50 个子命令时直接回退到 `ask`。

**建议规则数量上限：**
```typescript
export const MAX_SUGGESTED_RULES_FOR_COMPOUND = 5
```

避免一次保存过多规则产生噪音。

**分类器缓存控制：**
```typescript
const cacheControl = getCacheControl({ querySource: 'auto_mode' })
userContentBlocks.push({
  type: 'text',
  text: actionCompact,
  cache_control: cacheControl,
})
```

系统提示和会话记录使用 API 的 prompt caching（1 小时 TTL），大幅降低分类器 API 成本。

### 8.2 遥测与分析

每个安全检查触发时都会记录事件：

```typescript
logEvent('tengu_bash_security_check_triggered', {
  checkId: BASH_SECURITY_CHECK_IDS.OBFUSCATED_FLAGS,
  subId: 5,
})
```

使用数字 ID 而非字符串来避免日志中的字符串泄露。

**分类器结果分析（仅 Ant 内部）：**
```typescript
function logClassifierResultForAnts(command, behavior, descriptions, result) {
  if (process.env.USER_TYPE !== 'ant') return
  logEvent('tengu_internal_bash_classifier_result', {
    behavior,
    descriptions: jsonStringify(descriptions),
    matches: result.matches,
    matchedDescription: result.matchedDescription ?? '',
    confidence: result.confidence,
    reason: result.reason,
    command,  // 仅内部版本记录命令内容
  })
}
```

### 8.3 安全环境变量白名单

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
  // API
  'ANTHROPIC_API_KEY',
  // Locale
  'LANG', 'LANGUAGE', 'LC_ALL', 'LC_CTYPE', 'LC_TIME', 'CHARSET',
  // Terminal
  'TERM', 'COLORTERM', 'NO_COLOR', 'FORCE_COLOR', 'TZ',
  // Colors
  'LS_COLORS', 'LSCOLORS', 'GREP_COLOR', 'GREP_COLORS', 'GCC_COLORS',
  // ...
])
```

**安全约束注释：**
```
// SECURITY: These must NEVER be added to the whitelist:
// - PATH, LD_PRELOAD, LD_LIBRARY_PATH, DYLD_* (execution/library loading)
// - PYTHONPATH, NODE_PATH, CLASSPATH, RUBYLIB (module loading)
// - GOFLAGS, RUSTFLAGS, NODE_OPTIONS (can contain code execution flags)
// - HOME, TMPDIR, SHELL, BASH_ENV (affect system behavior)
```

### 8.4 Tree-sitter 集成

验证器支持 tree-sitter AST 分析作为更精确的替代方案：

```typescript
// Tree-sitter path: if tree-sitter confirms no actual operator nodes exist
// in the AST, then any \; is just an escaped character in a word argument
if (context.treeSitter && !context.treeSitter.hasActualOperatorNodes) {
  return { behavior: 'passthrough', message: 'No operator nodes in AST' }
}
```

当 tree-sitter 可用时，它提供了比正则表达式更准确的命令解析。但正则作为后备方案始终存在。

### 8.5 错误转储与调试

分类器错误时自动转储诊断信息：

```typescript
async function dumpErrorPrompts(systemPrompt, userPrompt, error, contextInfo) {
  const path = getAutoModeClassifierErrorDumpPath()
  const content =
    `=== ERROR ===\n${errorMessage(error)}\n\n` +
    `=== CONTEXT COMPARISON ===\n` +
    `mainLoopTokens: ${contextInfo.mainLoopTokens}\n` +
    `classifierChars: ${contextInfo.classifierChars}\n` +
    `classifierTokensEst: ${contextInfo.classifierTokensEst}\n` +
    `delta (classifierEst - mainLoop): ${contextInfo.classifierTokensEst - contextInfo.mainLoopTokens}\n\n` +
    `=== SYSTEM PROMPT ===\n${systemPrompt}\n\n` +
    `=== USER PROMPT (transcript) ===\n${userPrompt}\n`
  await writeFile(path, content, 'utf-8')
}
```

## 9. 初学者易错点

### 9.1 误解引号语义

**易错点：** 认为 `"hello"` 和 `'hello'` 在 shell 中完全等价。

**正确理解：**
- 单引号：强引用，所有字符都是字面量
- 双引号：弱引用，`$`、`` ` ``、`\` 仍然有特殊含义
- 这就是为什么 `validateGitCommit` 要区分引号类型

### 9.2 忽视解析差异

**易错点：** 认为 JavaScript 的 shell 解析库和 bash 行为完全一致。

**正确理解：**
- `\r` 在 JS `\s` 中是空白，在 bash IFS 中不是
- 花括号 `{a,b}` 在 bash 中展开，在 JS 解析器中可能是字面量
- `#` 在 bash 中开始注释，但在某些边界条件下解析器行为不同

### 9.3 低估正则表达式的复杂性

**易错点：** 认为简单的正则就能覆盖所有安全场景。

**正确理解：**
- 正则不能处理嵌套结构（如嵌套花括号）
- 正则的贪婪/非贪婪匹配可能产生意外结果
- 引号状态追踪需要逐字符处理，不能用简单正则

### 9.4 混淆 allow 和 deny 规则的安全语义

**易错点：** 认为 allow 和 deny 规则的环境变量处理方式相同。

**正确理解：**
- allow 规则只剥离安全环境变量（白名单）
- deny 规则剥离所有环境变量（防止绕过）
- 这是有意的安全设计：`FOO=bar denied_command` 必须被阻止

### 9.5 忽视"失败关闭"原则

**易错点：** 认为分类器出错时应该放行操作。

**正确理解：**
- 分类器 API 错误 → 阻止操作
- 分类器响应解析失败 → 阻止操作
- 分类器会话过长 → 阻止操作
- 所有不确定情况都默认阻止

### 9.6 误解 auto 模式的"危险规则"剥离

**易错点：** 认为进入 auto 模式后所有用户规则都保留。

**正确理解：**
- `Bash(*)`、`Bash(python:*)`、`Agent(*)` 等规则会在进入 auto 模式时被剥离
- 离开 auto 模式时会恢复这些规则
- 这防止了用户规则绕过分类器的安全评估

## 10. 本章总结

Claude Code 的安全与权限系统是一个精心设计的多层防御体系，核心设计原则包括：

1. **纵深防御**：模式层、规则层、语义层、分类器层四层协同
2. **失败关闭**：任何不确定情况都默认阻止
3. **解析差异防御**：专门针对 JS shell 解析器与 bash 的差异
4. **编译时功能门控**：通过 `feature()` 宏控制内部功能的代码消除
5. **权限规则多源聚合**：支持 CLI、设置文件、策略限制等多个来源
6. **智能分类器回退**：拒绝追踪机制防止分类器过于保守

**关键数据：**
- 23 个 Bash 安全验证器
- 6 种权限模式
- 20+ 种危险命令模式
- 两阶段 XML 分类器（fast + thinking）
- 最多 50 个子命令的安全检查上限
- 连续 3 次或累计 20 次拒绝后回退到用户确认

这个系统展示了在 AI 编码助手中实现安全性的最佳实践：不是简单地阻止危险操作，而是通过多层智能决策在安全性和用户体验之间找到平衡。

## 11. 延伸思考

### 11.1 LLM 分类器的可信度边界

YOLO 分类器使用 LLM 来判断命令是否安全。但 LLM 本身可能被提示注入攻击。Claude Code 如何防御这一点？

- 系统提示使用 `<user_claude_md>` 标签包裹用户配置
- 会话记录中的 assistant text 被排除（防止模型自导自演）
- 分类器的 `temperature: 0` 确保确定性输出
- 两阶段设计：Stage 1 快速判断，Stage 2 深度推理

**思考：** 如果攻击者在 CLAUDE.md 中注入恶意指令，分类器会如何响应？系统是否检测到了这种攻击向量？

### 11.2 安全与可用性的永恒张力

23 个验证器中每一个都可能产生误报（false positive）。例如：
- `validateBraceExpansion` 会阻止合法的 `git ls-remote {--upload-pack="...",test}`
- `validateObfuscatedFlags` 会阻止合法的 `echo "---"`
- `validateBackslashEscapedOperators` 会阻止 `find . -exec cmd {} \;`

系统通过以下机制缓解：
- 早期放行验证器（如 `validateGitCommit`）减少常见操作的摩擦
- 用户始终可以手动批准被阻止的操作
- 拒绝追踪确保分类器不会过于保守

**思考：** 在你的项目中，如何量化误报率？如何建立反馈循环来持续改进验证器？

### 11.3 跨平台安全挑战

Claude Code 支持 macOS、Linux 和 Windows（通过 PowerShell）。不同平台的安全模型差异巨大：

- **路径分隔符**：`/` vs `\`
- **Shell 语法**：bash vs zsh vs PowerShell
- **权限模型**：Unix 权限 vs Windows ACL
- **环境变量**：`$VAR` vs `%VAR%` vs `$env:VAR`

系统通过 `isDangerousPowerShellPermission` 和 PowerShell 特定的 deny 指引来处理这些差异。

**思考：** PowerShell 的 `Invoke-Expression` 和 bash 的 `eval` 在安全模型上有何异同？系统是否充分覆盖了 PowerShell 的攻击面？

### 11.4 未来安全挑战

随着 AI 能力的增强，安全模型也需要演进：

1. **多模态攻击**：图像中嵌入的恶意指令如何处理？
2. **供应链攻击**：恶意 CLAUDE.md 文件如何检测？
3. **上下文窗口溢出**：分类器的上下文窗口有限，超长会话如何处理？
4. **模型更新兼容性**：当底层 LLM 更新时，分类器的行为是否可预测？

**思考：** Claude Code 的安全模型能否扩展到支持完全自主的 AI Agent？需要哪些额外的安全机制？

---

*本章基于 Claude Code 源码中 `src/utils/permissions/`、`src/tools/BashTool/`、`src/hooks/toolPermission/`、`src/services/policyLimits/` 和 `src/utils/secureStorage/` 目录的深度分析。*
