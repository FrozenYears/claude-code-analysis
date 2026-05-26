---
title: 第13章：插件与扩展
---

# 第13章：插件与扩展

## 1. 本章目标

Claude Code 的插件与扩展系统是其作为"AI 原生开发平台"而非"封闭工具"的核心标志。本章将深入剖析三大扩展机制——**MCP（Model Context Protocol）协议实现**、**插件加载系统**、**Skills 系统**——的完整源码，帮助读者理解：

1. MCP 客户端如何通过多种传输协议（stdio、SSE、HTTP、WebSocket）连接外部服务
2. 插件如何从市场发现、下载、缓存到最终加载运行
3. Skills 系统如何将 Markdown 文件转化为可执行的 AI 指令
4. 企业级策略（allowlist/denylist）如何在配置层与连接层同时生效
5. 插件去重、认证缓存、自动重连等工程细节

## 2. 前置知识

- **JSON-RPC 2.0**：MCP 协议基于 JSON-RPC，需了解 request/response/notification 三种消息类型
- **OAuth 2.0**：MCP 远程服务器的认证基于 OAuth 2.0 授权码流程
- **传输层协议**：stdio（子进程）、SSE（Server-Sent Events）、HTTP（Streamable HTTP）、WebSocket
- **Zod Schema**：Claude Code 大量使用 Zod v4 进行运行时类型校验
- **React/Ink**：CLI UI 基于 React + Ink，MCPConnectionManager 是一个 React Context Provider
- **Git sparse-checkout**：插件市场支持从 monorepo 子目录稀疏检出插件

## 3. 宏观概览

Claude Code 的插件与扩展系统由四个层次组成：

```
┌─────────────────────────────────────────────────────────────┐
│                     用户交互层                               │
│  /plugin 命令  │  /mcp 命令  │  SkillTool  │  MCPTool       │
├─────────────────────────────────────────────────────────────┤
│                    编排与管理层                              │
│  MCPConnectionManager (React Context)                       │
│  useManageMCPConnections (连接状态管理)                      │
│  pluginLoader (插件发现与加载)                               │
│  marketplaceManager (市场管理)                               │
├─────────────────────────────────────────────────────────────┤
│                    协议与传输层                              │
│  MCP Client (@modelcontextprotocol/sdk)                     │
│  Transport: Stdio | SSE | HTTP | WebSocket | InProcess      │
│  ClaudeAuthProvider (OAuth 认证)                            │
├─────────────────────────────────────────────────────────────┤
│                    配置与存储层                              │
│  config.ts (多作用域配置合并)                               │
│  schemas.ts (Zod 类型定义)                                  │
│  installed_plugins.json / known_marketplaces.json           │
│  .mcp.json (项目级 MCP 配置)                                │
└─────────────────────────────────────────────────────────────┘
```

**核心数据流**：

1. 启动时，`config.ts` 从 7 个作用域（enterprise → managed → local → project → user → plugin → dynamic）合并 MCP 服务器配置
2. `pluginLoader.ts` 从缓存/市场加载插件，提取其中的 MCP 服务器定义
3. `useManageMCPConnections.ts` 驱动并行连接，每个服务器创建 `Client` 实例并选择合适的 `Transport`
4. 连接成功后，工具（MCPTool）、命令（Skills）、资源（Resources）被注册到 `AppState`
5. 模型通过 `SkillTool` 或 `MCPTool` 调用这些扩展能力

## 4. 源码入口定位

### 4.1 MCP 协议核心

| 文件 | 行数 | 职责 |
|------|------|------|
| `src/services/mcp/client.ts` | 3349 | MCP 客户端核心：连接、工具调用、资源读取 |
| `src/services/mcp/auth.ts` | 2466 | OAuth 认证流程、token 刷新、XAA 跨应用访问 |
| `src/services/mcp/types.ts` | ~250 | MCP 配置类型定义（Zod Schema + TypeScript 类型） |
| `src/services/mcp/config.ts` | ~800 | 多作用域配置合并、环境变量展开、策略过滤 |
| `src/services/mcp/MCPConnectionManager.tsx` | ~80 | React Context Provider，暴露 reconnect/toggle |
| `src/services/mcp/useManageMCPConnections.ts` | ~1100 | 连接生命周期管理 Hook |
| `src/services/mcp/InProcessTransport.ts` | ~65 | 进程内双向传输对 |

### 4.2 插件系统

| 文件 | 行数 | 职责 |
|------|------|------|
| `src/utils/plugins/pluginLoader.ts` | 3303 | 插件发现、缓存、加载核心 |
| `src/utils/plugins/marketplaceManager.ts` | 2644 | 市场注册、缓存、更新 |
| `src/utils/plugins/schemas.ts` | ~900 | 插件/市场/安装 全套 Zod Schema |
| `src/utils/plugins/mcpPluginIntegration.ts` | ~600 | 从插件提取 MCP 服务器配置 |
| `src/plugins/builtinPlugins.ts` | ~150 | 内置插件注册表 |

### 4.3 Skills 系统

| 文件 | 行数 | 职责 |
|------|------|------|
| `src/skills/loadSkillsDir.ts` | ~1100 | 从目录加载 Skill（Markdown → Command） |
| `src/skills/bundledSkills.ts` | ~220 | 内置 Skill 注册表 |
| `src/skills/mcpSkillBuilders.ts` | ~40 | MCP Skill 构建器注册（打破循环依赖） |
| `src/tools/SkillTool/SkillTool.ts` | ~1100 | SkillTool 实现：fork 子 Agent 执行 Skill |
| `src/tools/MCPTool/MCPTool.ts` | ~90 | MCPTool 定义（具体实现在 client.ts） |

## 5. 调用链分析

### 5.1 MCP 服务器连接全流程

```
CLI 启动
  ↓
getAllMcpConfigs()                          [config.ts]
  ├─ getMcpConfigsByScope('enterprise')     // 企业级独占
  ├─ getMcpConfigsByScope('user')           // ~/.claude/settings.json
  ├─ getMcpConfigsByScope('project')        // .mcp.json（向上遍历）
  ├─ getMcpConfigsByScope('local')          // 本地覆盖
  ├─ loadAllPluginsCacheOnly()              // 插件 MCP 服务器
  ├─ fetchClaudeAIMcpConfigsIfEligible()    // claude.ai 连接器
  └─ filterMcpServersByPolicy()             // allowlist/denylist 过滤
  ↓
MCPConnectionManager (React Provider)
  ↓
useManageMCPConnections()                   [useManageMCPConnections.ts]
  ↓ 并行连接每个服务器
reconnectMcpServerImpl()                    [client.ts]
  ├─ 创建 Transport（按 type 选择）
  │   ├─ stdio → StdioClientTransport（子进程）
  │   ├─ sse → SSEClientTransport（长轮询）
  │   ├─ http → StreamableHTTPClientTransport（流式 HTTP）
  │   ├─ ws → WebSocketTransport（WebSocket）
  │   └─ sdk → SdkControlClientTransport（SDK 控制）
  ├─ new Client() → client.connect(transport)
  ├─ fetchToolsForClient() → 注册 MCPTool
  ├─ fetchCommandsForClient() → 注册 MCP Skills
  └─ fetchResourcesForClient() → 注册 Resources
  ↓
AppState.mcp 更新（tools, commands, resources）
```

### 5.2 MCP 工具调用链

```
模型决定调用 MCP 工具
  ↓
MCPTool.call(input, context)               [MCPTool.ts → client.ts]
  ↓
client.callTool({ name, arguments })        [@modelcontextprotocol/sdk]
  ↓ Transport 层发送 JSON-RPC request
  ↓
MCP 服务器处理 → 返回结果
  ↓
结果处理链：
  ├─ 二进制内容 → persistBinaryContent() → 文件路径
  ├─ 文本内容 → truncateMcpContentIfNeeded()
  ├─ 图片 → maybeResizeAndDownsampleImageBuffer()
  └─ 超大输出 → getLargeOutputInstructions()
  ↓
返回 ToolResult 给模型
```

### 5.3 插件加载全流程

```
getClaudeCodeMcpConfigs()
  ↓
loadAllPluginsCacheOnly()                   [pluginLoader.ts]
  ├─ getBuiltinPlugins()                    // 内置插件
  ├─ loadKnownMarketplacesConfigSafe()      // 读取 known_marketplaces.json
  ├─ getSettings_DEPRECATED()               // 读取 settings.plugins
  └─ 对每个启用的插件：
      ├─ resolvePluginPath() → 版本化缓存路径
      ├─ validatePluginManifest() → plugin.json 校验
      ├─ loadPluginCommands() → commands/*.md → Command[]
      ├─ loadPluginHooks() → hooks/hooks.json → HooksSettings
      └─ getPluginMcpServers()              [mcpPluginIntegration.ts]
          ├─ 读取 .mcp.json
          ├─ 加载 MCPB/DXT 包
          ├─ substitutePluginVariables()    // ${user_config.KEY} 替换
          └─ addPluginScopeToServers()      // 命名空间 plugin:name:server
  ↓
dedupPluginMcpServers()                     [config.ts]
  ↓ 签名去重：手动配置 > 先加载的插件
  ↓
merge → Object.assign(plugin, user, project, local)
  ↓
filterMcpServersByPolicy()                  // allowlist/denylist
```

### 5.4 Skill 执行链

```
模型决定使用 Skill（或用户输入 /command）
  ↓
SkillTool.call({ command, args })           [SkillTool.ts]
  ↓
getAllCommands(context) → 查找匹配的 Command
  ↓
executeForkedSkill()
  ├─ createAgentId() → 创建子 Agent
  ├─ getPromptForCommand(args, context) → 生成 prompt 内容
  │   ├─ 内置 Skill → 编译时注册的函数
  │   ├─ 文件 Skill → 读取 Markdown + frontmatter
  │   └─ MCP Skill → fetchMcpSkillsForClient()
  ├─ runAgent() → 在隔离的 token 预算内执行
  └─ extractResultText() → 返回结果
```

## 6. 核心源码解析

### 6.1 MCP 类型系统 (`types.ts`)

MCP 类型系统定义了服务器配置的所有可能形态。核心是一个 Zod discriminated union：

```typescript
// services/mcp/types.ts — 传输类型枚举
export const TransportSchema = lazySchema(() =>
  z.enum(['stdio', 'sse', 'sse-ide', 'http', 'ws', 'sdk']),
)
```

每种传输类型有对应的配置 Schema。以 stdio 为例：

```typescript
export const McpStdioServerConfigSchema = lazySchema(() =>
  z.object({
    type: z.literal('stdio').optional(),  // 向后兼容：不填默认 stdio
    command: z.string().min(1, 'Command cannot be empty'),
    args: z.array(z.string()).default([]),
    env: z.record(z.string(), z.string()).optional(),
  }),
)
```

远程服务器（SSE/HTTP/WS）共享 OAuth 配置结构：

```typescript
const McpOAuthConfigSchema = lazySchema(() =>
  z.object({
    clientId: z.string().optional(),
    callbackPort: z.number().int().positive().optional(),
    authServerMetadataUrl: z.string().url()
      .startsWith('https://', { message: 'must use https://' })
      .optional(),
    xaa: McpXaaConfigSchema().optional(),  // 跨应用访问
  }),
)
```

服务器连接状态是一个 5 元联合类型，这是理解整个 MCP 状态机的关键：

```typescript
export type MCPServerConnection =
  | ConnectedMCPServer    // type: 'connected' + client + capabilities
  | FailedMCPServer       // type: 'failed' + error
  | NeedsAuthMCPServer    // type: 'needs-auth'（等待 OAuth）
  | PendingMCPServer      // type: 'pending' + reconnectAttempt
  | DisabledMCPServer     // type: 'disabled'（用户关闭）
```

**设计要点**：`ScopedMcpServerConfig` 在 `McpServerConfig` 基础上附加了 `scope` 字段（`'local' | 'user' | 'project' | 'dynamic' | 'enterprise' | 'claudeai' | 'managed'`），这让配置在合并时可以追溯来源，也使得企业策略过滤能精确作用于特定作用域。

### 6.2 MCP 配置合并 (`config.ts`)

`getAllMcpConfigs()` 是配置合并的顶层入口，实现了 Claude Code 最复杂的配置优先级系统：

```typescript
// services/mcp/config.ts
export async function getAllMcpConfigs(): Promise<{
  servers: Record<string, ScopedMcpServerConfig>
  errors: PluginError[]
}> {
  // 企业模式独占：有 enterprise MCP 配置时忽略其他所有来源
  if (doesEnterpriseMcpConfigExist()) {
    return getClaudeCodeMcpConfigs()
  }
  // claude.ai 连接器与插件加载并行
  const claudeaiPromise = fetchClaudeAIMcpConfigsIfEligible()
  const { servers: claudeCodeServers, errors } = await getClaudeCodeMcpConfigs(
    {},
    claudeaiPromise,
  )
  const { allowed: claudeaiMcpServers } = filterMcpServersByPolicy(
    await claudeaiPromise,
  )
  // 去重：claude.ai 连接器与手动配置的签名比对
  const { servers: dedupedClaudeAi } = dedupClaudeAiMcpServers(
    claudeaiMcpServers,
    claudeCodeServers,
  )
  // claude.ai 优先级最低
  const servers = Object.assign({}, dedupedClaudeAi, claudeCodeServers)
  return { servers, errors }
}
```

配置合并的核心在 `getClaudeCodeMcpConfigs()` 中，采用 `Object.assign` 的覆盖语义：

```typescript
// 合并优先级：plugin < user < project < local
const configs = Object.assign(
  {},
  dedupedPluginServers,   // 最低：插件提供的
  userServers,            // 用户全局
  approvedProjectServers, // 项目共享（需审批）
  localServers,           // 最高：本地覆盖
)
```

**签名去重机制**——防止同一服务器被多个来源重复连接：

```typescript
export function getMcpServerSignature(config: McpServerConfig): string | null {
  const cmd = getServerCommandArray(config)
  if (cmd) return `stdio:${jsonStringify(cmd)}`
  const url = getServerUrl(config)
  if (url) return `url:${unwrapCcrProxyUrl(url)}`
  return null
}
```

去重逻辑确保：手动配置优先于插件配置，先加载的插件优先于后加载的。被去重的服务器不会消失——它们仍出现在 `/mcp` 列表中（标记为 suppressed），但不会实际建立连接。

### 6.3 MCP 客户端连接 (`client.ts`)

`client.ts` 是整个 MCP 系统的核心，3349 行代码涵盖了连接管理、工具执行、认证处理等全部逻辑。

**传输层选择**是连接的第一步。根据 `config.type` 创建不同的 Transport：

```typescript
// 伪代码，实际逻辑在 reconnectMcpServerImpl 中
switch (config.type) {
  case 'stdio':
    transport = new StdioClientTransport({
      command: config.command,
      args: config.args,
      env: subprocessEnv(config.env),
    })
    break
  case 'sse':
    transport = new SSEClientTransport(config.url, {
      headers: getMcpServerHeaders(config),
      fetch: createClaudeAiProxyFetch(globalThis.fetch),
    })
    break
  case 'http':
    transport = new StreamableHTTPClientTransport(config.url, {
      headers: getMcpServerHeaders(config),
      fetch: wrapFetchWithTimeout(globalThis.fetch),
    })
    break
  case 'ws':
    const ws = await createNodeWsClient(config.url, {
      headers: getMcpServerHeaders(config),
      ...getWebSocketTLSOptions(),
    })
    transport = new WebSocketTransport(ws)
    break
}
```

**超时处理**是 MCP 网络层的重要工程细节：

```typescript
// services/mcp/client.ts — wrapFetchWithTimeout
export function wrapFetchWithTimeout(baseFetch: FetchLike): FetchLike {
  return async (url: string | URL, init?: RequestInit) => {
    const method = (init?.method ?? 'GET').toUpperCase()
    // GET 请求不超时——它们是长轮询 SSE 流
    if (method === 'GET') return baseFetch(url, init)
    // 使用 setTimeout 而非 AbortSignal.timeout() 以避免内存泄漏
    const controller = new AbortController()
    const timer = setTimeout(
      c => c.abort(new DOMException('The operation timed out.', 'TimeoutError')),
      MCP_REQUEST_TIMEOUT_MS,  // 60 秒
      controller,
    )
    timer.unref?.()
    // ... 信号传播和清理逻辑
  }
}
```

**认证缓存**避免对需要 OAuth 的服务器反复触发认证流程：

```typescript
const MCP_AUTH_CACHE_TTL_MS = 15 * 60 * 1000 // 15 分钟

async function isMcpAuthCached(serverId: string): Promise<boolean> {
  const cache = await getMcpAuthCache()
  const entry = cache[serverId]
  if (!entry) return false
  return Date.now() - entry.timestamp < MCP_AUTH_CACHE_TTL_MS
}
```

缓存文件存储在 `~/.claude/mcp-needs-auth-cache.json`，写入通过 Promise 链串行化以防止并发竞态：

```typescript
let writeChain = Promise.resolve()
function setMcpAuthCacheEntry(serverId: string): void {
  writeChain = writeChain.then(async () => {
    const cache = await getMcpAuthCache()
    cache[serverId] = { timestamp: Date.now() }
    await writeFile(getMcpAuthCachePath(), jsonStringify(cache))
    authCachePromise = null  // 失效读缓存
  }).catch(() => {})
}
```

### 6.4 MCP OAuth 认证 (`auth.ts`)

`auth.ts` 实现了完整的 OAuth 2.0 授权码流程，2466 行代码覆盖了从服务器元数据发现到 token 刷新的全链路。

核心类 `ClaudeAuthProvider` 实现了 MCP SDK 的 `OAuthClientProvider` 接口：

```typescript
// services/mcp/auth.ts（概念简化）
export class ClaudeAuthProvider implements OAuthClientProvider {
  // 1. 保存/读取 client 信息（注册后持久化）
  async saveClientInformation(info: OAuthClientInformationFull): Promise<void>
  async getClientInformation(): Promise<OAuthClientInformation | undefined>
  
  // 2. 保存/读取 tokens
  async saveTokens(tokens: OAuthTokens): Promise<void>
  async getTokens(): Promise<OAuthTokens | undefined>
  
  // 3. 核心：重定向到浏览器进行授权
  async redirectToAuthorization(url: URL): Promise<void>
  // 实现：启动本地 HTTP 服务器监听回调 → 打开浏览器 → 等待授权码
}
```

认证流程的精妙之处在于**本地回调服务器**的实现：

```typescript
// 在随机可用端口启动 HTTP 服务器接收 OAuth 回调
const server = createServer(async (req, res) => {
  const { query } = parse(req.url ?? '', true)
  if (query.error) {
    // 用户拒绝授权
    res.writeHead(400)
    res.end(`Authorization denied: ${query.error}`)
    return
  }
  // 验证 state 参数防 CSRF
  if (query.state !== expectedState) {
    res.writeHead(400)
    res.end('State mismatch')
    return
  }
  // 用授权码换取 token
  const tokens = await exchangeCodeForToken(query.code)
  // ...
})
server.listen(randomPort)
openBrowser(authorizationUrl)  // 打开系统浏览器
```

**XAA（Cross-App Access）**是 Claude Code 特有的扩展——允许 CLI 通过已登录的 claude.ai 身份访问 MCP 服务器，避免用户重复登录：

```typescript
// services/mcp/xaa.ts — 概念
export async function performCrossAppAccess(
  serverConfig: McpSSEServerConfig | McpHTTPServerConfig,
): Promise<OAuthTokens | null> {
  if (!isXaaEnabled(serverConfig)) return null
  // 1. 从 IdP 获取 id_token
  const idToken = await acquireIdpIdToken()
  // 2. 用 id_token 交换 MCP 服务器的 access_token
  const tokens = await exchangeToken(idToken, serverConfig.oauth)
  return tokens
}
```

### 6.5 插件加载器 (`pluginLoader.ts`)

`pluginLoader.ts` 是插件系统的核心，3303 行代码处理从磁盘发现到运行时加载的全部流程。

**插件标识符**格式为 `name@marketplace`，例如 `code-formatter@anthropic-tools`。解析逻辑在 `pluginIdentifier.ts`：

```typescript
export function parsePluginIdentifier(id: string): {
  name: string
  marketplace: string
} {
  const atIndex = id.lastIndexOf('@')
  if (atIndex <= 0) return { name: id, marketplace: '' }
  return {
    name: id.substring(0, atIndex),
    marketplace: id.substring(atIndex + 1),
  }
}
```

**版本化缓存**是插件系统的关键设计。每个插件版本有独立的缓存目录：

```typescript
// 缓存路径格式：~/.claude/plugins/cache/{marketplace}/{plugin}/{version}/
export function getVersionedCachePath(pluginId: string, version: string): string {
  const { name: pluginName, marketplace } = parsePluginIdentifier(pluginId)
  const sanitizedMarketplace = (marketplace || 'unknown').replace(/[^a-zA-Z0-9\-_]/g, '-')
  const sanitizedPlugin = (pluginName || pluginId).replace(/[^a-zA-Z0-9\-_]/g, '-')
  const sanitizedVersion = version.replace(/[^a-zA-Z0-9\-_.]/g, '-')
  return join(getPluginsDirectory(), 'cache', sanitizedMarketplace, sanitizedPlugin, sanitizedVersion)
}
```

版本化设计解决了两个关键问题：（1）不同项目可以使用同一插件的不同版本；（2）更新可以原子切换，失败时可回退。

**Seed Cache** 是一个优化——预置的插件缓存目录，避免首次启动时的网络下载：

```typescript
async function probeSeedCache(pluginId: string, version: string): Promise<string | null> {
  for (const seedDir of getPluginSeedDirs()) {
    const seedPath = getVersionedCachePathIn(seedDir, pluginId, version)
    try {
      const entries = await readdir(seedPath)
      if (entries.length > 0) return seedPath  // 命中！
    } catch { /* 尝试下一个 seed */ }
  }
  return null
}
```

**插件验证**通过 Zod Schema 链式校验。`PluginManifestSchema` 是所有插件清单的统一入口：

```typescript
// utils/plugins/schemas.ts — plugin.json 的完整 Schema
export const PluginManifestSchema = lazySchema(() =>
  z.object({
    ...PluginManifestMetadataSchema().shape,     // name, version, description, author
    ...PluginManifestHooksSchema().partial().shape,     // hooks 配置
    ...PluginManifestCommandsSchema().partial().shape,  // 额外命令
    ...PluginManifestAgentsSchema().partial().shape,    // 自定义 Agent
    ...PluginManifestSkillsSchema().partial().shape,    // Skill 目录
    ...PluginManifestOutputStylesSchema().partial().shape,
    ...PluginManifestChannelsSchema().partial().shape,  // 通道声明
    ...PluginManifestMcpServerSchema().partial().shape, // MCP 服务器
    ...PluginManifestLspServerSchema().partial().shape, // LSP 服务器
    ...PluginManifestSettingsSchema().partial().shape,  // 设置合并
    ...PluginManifestUserConfigSchema().partial().shape, // 用户配置
  }),
)
```

注意 `z.object` 默认会 **strip unknown keys**（静默移除未知字段），而非拒绝。这是一个刻意的设计决策：

> "Unknown top-level fields are silently stripped rather than rejected. This keeps plugin loading resilient to custom/future top-level fields that plugin authors may add."

### 6.6 插件市场管理 (`marketplaceManager.ts`)

市场管理器负责插件的发现和分发。每个市场是一个 `marketplace.json` 文件，包含插件列表和源信息。

**市场来源类型**多样：

```typescript
// utils/plugins/schemas.ts — MarketplaceSourceSchema
z.discriminatedUnion('source', [
  z.object({ source: z.literal('url'), url: z.string().url() }),
  z.object({ source: z.literal('github'), repo: z.string(), ref: z.string().optional() }),
  z.object({ source: z.literal('git'), url: z.string() }),
  z.object({ source: z.literal('npm'), package: z.string() }),
  z.object({ source: z.literal('file'), path: z.string() }),
  z.object({ source: z.literal('directory'), path: z.string() }),
  z.object({ source: z.literal('settings'), name: z.string(), plugins: z.array(...) }),
  // ...
])
```

**官方市场名称保护**防止钓鱼攻击：

```typescript
export const ALLOWED_OFFICIAL_MARKETPLACE_NAMES = new Set([
  'claude-code-marketplace',
  'claude-code-plugins',
  'claude-plugins-official',
  'anthropic-marketplace',
  'anthropic-plugins',
  'agent-skills',
  // ...
])

// 阻止仿冒名称
export const BLOCKED_OFFICIAL_NAME_PATTERN =
  /(?:official[^a-z0-9]*(anthropic|claude)|(?:anthropic|claude)[^a-z0-9]*official)/i

// 阻止非 ASCII 同形攻击（如西里尔字母 'а' 替代拉丁字母 'a'）
const NON_ASCII_PATTERN = /[^\u0020-\u007E]/
```

**市场缓存**使用 `known_marketplaces.json` 跟踪所有已注册市场：

```typescript
// ~/.claude/plugins/known_marketplaces.json
{
  "anthropic-tools": {
    "source": { "source": "github", "repo": "anthropics/claude-plugins" },
    "installLocation": "~/.claude/plugins/cached/marketplaces/anthropic-tools",
    "lastUpdated": "2024-01-15T10:30:00Z",
    "autoUpdate": true
  }
}
```

自动更新逻辑优先检查 GCS（Google Cloud Storage）缓存，失败时回退到 GitHub：

```typescript
// officialMarketplace.ts + officialMarketplaceGcs.ts
export async function fetchOfficialMarketplace(): Promise<PluginMarketplace> {
  try {
    // 先尝试 GCS CDN（更快、更稳定）
    return await fetchOfficialMarketplaceFromGcs()
  } catch {
    // 回退到 GitHub
    return await fetchOfficialMarketplaceFromGithub()
  }
}
```

### 6.7 Skills 系统 (`loadSkillsDir.ts`)

Skills 系统将 Markdown 文件转化为可执行的 AI 指令，是 Claude Code 最优雅的设计之一。

**目录扫描**从多个位置收集 Skill 文件：

```typescript
// skills/loadSkillsDir.ts
// 扫描位置（按优先级）：
// 1. 政策管理目录（managed/.claude/skills/）
// 2. 用户目录（~/.claude/skills/）
// 3. 项目目录（.claude/skills/）
// 4. 插件提供的 skills
// 5. 内置 bundled skills
```

**Frontmatter 解析**是 Skill 的核心——Markdown 文件头部的 YAML 块定义了 Skill 的元数据：

```typescript
// 一个典型的 Skill 文件：
// ---
// name: my-skill
// description: 做某件事
// whenToUse: 当用户需要...时使用
// allowedTools: [Read, Write, Bash]
// model: claude-sonnet-4-20250514
// ---
// 
// 你的任务是...
```

解析时，`loadSkillsDir.ts` 会提取 frontmatter 并构建 `Command` 对象：

```typescript
export function createSkillCommand(
  filePath: string,
  content: string,
  frontmatter: FrontmatterData,
  source: SettingSource | 'plugin' | 'bundled',
): Command {
  return {
    type: 'prompt',
    name: frontmatter.name ?? basename(filePath, '.md'),
    description: frontmatter.description ?? '',
    whenToUse: frontmatter.whenToUse,
    allowedTools: parseSlashCommandToolsFromFrontmatter(frontmatter),
    model: parseUserSpecifiedModel(frontmatter.model),
    getPromptForCommand: async (args, context) => {
      // 读取文件内容 → 替换参数 → 返回 ContentBlockParam[]
      const raw = await readFile(filePath, 'utf-8')
      const { content } = parseFrontmatter(raw)
      return [createUserMessage(substituteArguments(content, args))]
    },
  }
}
```

**Skill 执行模式**分为两种：`inline`（在当前 Agent 中执行）和 `fork`（在隔离的子 Agent 中执行）：

```typescript
// SkillTool.ts — executeForkedSkill
async function executeForkedSkill(command, commandName, args, context, ...) {
  const agentId = createAgentId()
  // 在隔离的 token 预算内执行
  const result = await runAgent({
    agentId,
    prompt: await command.getPromptForCommand(args, context),
    model: resolveSkillModelOverride(command.model),
    maxTokens: ...,  // 子 Agent 有独立的 token 限制
    tools: command.allowedTools,
  })
  return extractResultText(result)
}
```

### 6.8 MCP 与插件的集成 (`mcpPluginIntegration.ts`)

插件可以声明自己的 MCP 服务器，这些服务器在插件加载时被提取并注册：

```typescript
// utils/plugins/mcpPluginIntegration.ts
export async function getPluginMcpServers(
  plugin: LoadedPlugin,
  errors: PluginError[],
): Promise<Record<string, ScopedMcpServerConfig> | null> {
  const mcpServers: Record<string, McpServerConfig> = {}
  
  // 1. 从 .mcp.json 文件加载
  const mcpJsonPath = join(plugin.path, '.mcp.json')
  if (await pathExists(mcpJsonPath)) {
    const config = jsonParse(await readFile(mcpJsonPath, 'utf-8'))
    Object.assign(mcpServers, config.mcpServers)
  }
  
  // 2. 从 plugin.json 的 mcpServers 字段加载
  if (plugin.manifest.mcpServers) {
    // 可以是：内联配置、JSON 文件路径、MCPB 包路径
    for (const source of plugin.manifest.mcpServers) {
      if (isMcpbSource(source)) {
        // MCPB/DXT 格式：下载 → 解压 → 转换
        const result = await loadMcpServersFromMcpb(plugin, source, errors)
        if (result) Object.assign(mcpServers, result)
      } else if (typeof source === 'string') {
        // JSON 文件路径
        const config = jsonParse(await readFile(join(plugin.path, source), 'utf-8'))
        Object.assign(mcpServers, config.mcpServers)
      } else {
        // 内联配置
        Object.assign(mcpServers, source)
      }
    }
  }
  
  // 3. 变量替换（${user_config.KEY}）
  for (const [name, config] of Object.entries(mcpServers)) {
    substitutePluginVariables(config, plugin)
    substituteUserConfigVariables(config, plugin)
  }
  
  // 4. 命名空间化：防止插件间冲突
  return addPluginScopeToServers(mcpServers, plugin)
}
```

### 6.9 内置插件系统 (`builtinPlugins.ts`)

内置插件是随 CLI 发布、可通过 `/plugin` UI 开关的功能模块：

```typescript
// plugins/builtinPlugins.ts
export function getBuiltinPlugins(): {
  enabled: LoadedPlugin[]
  disabled: LoadedPlugin[]
} {
  const settings = getSettings_DEPRECATED()
  for (const [name, definition] of BUILTIN_PLUGINS) {
    // 可用性检查
    if (definition.isAvailable && !definition.isAvailable()) continue
    const pluginId = `${name}@${BUILTIN_MARKETPLACE_NAME}`
    // 用户设置 > 插件默认值 > true
    const isEnabled = settings?.enabledPlugins?.[pluginId] !== undefined
      ? settings.enabledPlugins[pluginId] === true
      : (definition.defaultEnabled ?? true)
    // ...
  }
}
```

内置插件与 bundled skills 的区别在于：内置插件出现在 `/plugin` UI 中，用户可以显式开关；bundled skills 始终可用。

### 6.10 MCPConnectionManager 的 React 架构

MCP 连接管理使用 React Context 模式，将连接状态注入组件树：

```typescript
// services/mcp/MCPConnectionManager.tsx
export function MCPConnectionManager({ children, dynamicMcpConfig, isStrictMcpConfig }) {
  const { reconnectMcpServer, toggleMcpServer } = useManageMCPConnections(
    dynamicMcpConfig, isStrictMcpConfig
  )
  const value = useMemo(() => ({
    reconnectMcpServer,
    toggleMcpServer
  }), [reconnectMcpServer, toggleMcpServer])
  
  return (
    <MCPConnectionContext.Provider value={value}>
      {children}
    </MCPConnectionContext.Provider>
  )
}
```

`useManageMCPConnections` Hook 负责实际的连接生命周期：

```typescript
// services/mcp/useManageMCPConnections.ts（概念简化）
export function useManageMCPConnections(dynamicMcpConfig, isStrictMcpConfig) {
  // 1. 启动时获取所有 MCP 配置
  useEffect(() => {
    const configs = await getAllMcpConfigs()
    setMcpConfigs(configs)
  }, [])
  
  // 2. 对每个配置并行建立连接
  useEffect(() => {
    for (const [name, config] of Object.entries(mcpConfigs)) {
      if (!isMcpServerDisabled(name)) {
        connectWithBackoff(name, config)  // 指数退避重连
      }
    }
  }, [mcpConfigs])
  
  // 3. 监听服务器通知（工具/命令/资源变更）
  // 4. 处理通道消息（Telegram/Slack/Discord）
  // 5. 注册 elicitation 处理器（服务器主动询问用户）
}
```

重连策略使用指数退避：

```typescript
const MAX_RECONNECT_ATTEMPTS = 5
const INITIAL_BACKOFF_MS = 1000
const MAX_BACKOFF_MS = 30000

function getBackoffMs(attempt: number): number {
  return Math.min(INITIAL_BACKOFF_MS * Math.pow(2, attempt), MAX_BACKOFF_MS)
}
```

## 7. 架构设计思想

### 7.1 分层解耦

插件系统严格分为四层：**配置层**（`config.ts` + `schemas.ts`）→ **加载层**（`pluginLoader.ts` + `marketplaceManager.ts`）→ **协议层**（`client.ts` + `auth.ts`）→ **执行层**（`MCPTool` + `SkillTool`）。每层只依赖下层，上层变更不影响下层。

### 7.2 策略与机制分离

企业策略（allowlist/denylist）在配置层实现，不侵入连接层代码。`filterMcpServersByPolicy()` 是一个纯函数，接受配置记录返回过滤结果。连接层无需知道策略的存在——它只处理已通过过滤的配置。

### 7.3 签名去重而非名称去重

传统做法是按名称去重，但 Claude Code 使用**内容签名**（`stdio:command+args` 或 `url:baseurl`）。这解决了同一服务器被多个来源（手动 + 插件 + claude.ai）重复声明的场景，避免重复连接浪费资源。

### 7.4 乐观加载与并行化

`getAllMcpConfigs()` 中，`fetchClaudeAIMcpConfigsIfEligible()` 和 `loadAllPluginsCacheOnly()` 是并行执行的。去重操作在两者都完成后才进行。这种"乐观并行"模式在整个系统中广泛使用。

### 7.5 渐进式 Schema 演进

Schema 设计使用 `lazySchema()` 包装器实现惰性求值，支持循环引用。`InstalledPluginsFileSchema` 同时接受 V1 和 V2 格式，实现了无迁移的 Schema 升级：

```typescript
export const InstalledPluginsFileSchema = lazySchema(() =>
  z.union([InstalledPluginsFileSchemaV1(), InstalledPluginsFileSchemaV2()]),
)
```

### 7.6 MCP 作为统一扩展协议

Claude Code 通过 MCP 将工具、命令、资源统一到一个协议下。插件声明的 MCP 服务器、claude.ai 连接器、用户手动配置的服务器——所有外部能力都通过同一个 `MCPServerConnection` 状态机管理。这避免了为每种扩展类型实现独立的生命周期管理。

## 8. 工程实践细节

### 8.1 安全：路径遍历防护

插件系统对所有用户输入的路径进行严格校验：

```typescript
// schemas.ts — 插件名称不能包含路径分隔符
.refine(name => !name.includes(' '), { ... })
.refine(name => !name.includes('/') && !name.includes('\\') && !name.includes('..'), { ... })

// pluginLoader.ts — 版本号中的特殊字符被替换
const sanitizedVersion = version.replace(/[^a-zA-Z0-9\-_.]/g, '-')

// pluginInstallationHelpers.ts — 验证路径在基础目录内
export function validatePathWithinBase(path: string, base: string): boolean
```

### 8.2 安全：OAuth 重定向 XSS 防护

`auth.ts` 使用 `xss` 库对 OAuth 回调参数进行转义：

```typescript
import xss from 'xss'
// 在渲染 OAuth 回调页面时
res.end(`Authorization ${xss(query.error_description || 'denied')}`)
```

### 8.3 安全：企业级 MCP 策略

```typescript
// config.ts — 三层策略
// 1. deniedMcpServers：绝对禁止（名称/命令/URL 匹配）
// 2. allowedMcpServers：仅允许列表中的服务器
// 3. allowManagedMcpServersOnly：仅管理策略可配置 allowlist

// denylist 优先于 allowlist
if (isMcpServerDenied(serverName, config)) return false
// SDK 类型服务器豁免（它们是 IDE 扩展的传输占位符）
if (c.type === 'sdk') return true
```

### 8.4 性能：Memoize 与惰性加载

```typescript
// 避免重复解析企业配置
export const doesEnterpriseMcpConfigExist = memoize((): boolean => { ... })

// Skill 工具提示词预算控制
export const SKILL_BUDGET_CONTEXT_PERCENT = 0.01  // 上下文窗口的 1%
export const DEFAULT_CHAR_BUDGET = 8_000           // 200k × 4 × 1%

// MCP 描述长度上限
const MAX_MCP_DESCRIPTION_LENGTH = 2048
```

### 8.5 性能：并行插件处理

```typescript
// config.ts — 并行加载插件 MCP 服务器
const pluginServerResults = await Promise.all(
  pluginResult.enabled.map(plugin => getPluginMcpServers(plugin, mcpErrors)),
)
```

### 8.6 可靠性：原子文件写入

```typescript
// config.ts — writeMcpjsonFile
// 1. 写入临时文件
const tempPath = `${mcpJsonPath}.tmp.${process.pid}.${Date.now()}`
const handle = await open(tempPath, 'w', existingMode ?? 0o644)
await handle.writeFile(content, { encoding: 'utf8' })
await handle.datasync()  // 确保刷盘
// 2. 恢复权限
if (existingMode !== undefined) await chmod(tempPath, existingMode)
// 3. 原子重命名
await rename(tempPath, mcpJsonPath)
```

### 8.7 可靠性：Session 过期检测

```typescript
// client.ts — 检测 MCP 会话过期（HTTP 404 + JSON-RPC -32001）
export function isMcpSessionExpiredError(error: Error): boolean {
  const httpStatus = 'code' in error ? (error as any).code : undefined
  if (httpStatus !== 404) return false
  return error.message.includes('"code":-32001') ||
         error.message.includes('"code": -32001')
}
```

### 8.8 可靠性：InProcess 传输

```typescript
// InProcessTransport.ts — 进程内双向传输
// 用于 SDK 模式：MCP 服务器和客户端在同一进程中运行
async send(message: JSONRPCMessage): Promise<void> {
  // 异步投递避免栈溢出
  queueMicrotask(() => {
    this.peer?.onmessage?.(message)
  })
}
```

## 9. 初学者易错点

### 9.1 混淆 `.mcp.json` 的作用域

`.mcp.json` 是**项目级**配置，沿目录树向上查找。靠近 CWD 的文件覆盖父目录的同名配置。不要与 `~/.claude/settings.json` 中的 `mcpServers`（用户级）混淆。

### 9.2 忘记环境变量展开

MCP 配置中的 `${VAR_NAME}` 会在加载时自动展开。如果环境变量缺失，不会报错但会在日志中记录 warning。远程服务器的 `headers` 字段同样支持展开。

### 9.3 插件 ID 的格式

插件 ID 是 `name@marketplace`，`@` 分隔符使用 `lastIndexOf('@')` 解析。这意味着插件名称本身可以包含 `@`（虽然不推荐）。例如 `my-plugin@v2@marketplace` 会被解析为 name=`my-plugin@v2`，marketplace=`marketplace`。

### 9.4 插件 MCP 服务器的命名空间

插件声明的 MCP 服务器会被自动添加 `plugin:插件名:` 前缀。例如插件 `my-plugin` 的服务器 `db-server` 实际注册为 `plugin:my-plugin:db-server`。手动配置同名服务器不会冲突，但签名去重会抑制插件版本。

### 9.5 OAuth token 的缓存行为

OAuth token 通过 memoize 缓存，`handleOAuth401Error` 会清除缓存并强制刷新。但 15 分钟的 `needs-auth` 缓存意味着：如果服务器需要认证但用户未完成 OAuth 流程，后续 15 分钟内的连接尝试会直接返回 `needs-auth` 状态而不重新触发认证。

### 9.6 Skill 的 `inline` vs `fork` 模式

`inline` 模式在当前 Agent 上下文中执行 Skill，共享 token 预算；`fork` 模式创建子 Agent，有独立的 token 限制。默认使用 `fork`，只有显式设置 `context: 'inline'` 才会内联执行。对于需要访问父 Agent 上下文的 Skill，应使用 `inline`。

### 9.7 企业配置的独占性

当 `managed-mcp.json` 存在时，它**完全替代**其他所有 MCP 配置来源。这不是"追加"而是"替换"。企业管理员必须在一个文件中声明所有需要的服务器。

### 9.8 MCPB/DXT 包的用户配置

MCPB 包可能需要用户配置（API key 等）。未配置的包会返回 `needs-config` 状态，而非错误。用户可以通过 `/plugin → Manage → Configure` 进行配置。敏感配置存储在系统 keychain 而非 settings.json。

## 10. 本章总结

Claude Code 的插件与扩展系统是一个精心设计的多层架构：

**MCP 协议层**提供了统一的外部能力接入标准。通过支持 5 种传输协议（stdio、SSE、HTTP、WebSocket、InProcess），它既能连接本地子进程也能连接远程服务。OAuth 认证链覆盖了标准 OAuth、企业 XAA、claude.ai 代理三种场景。

**插件系统**实现了从市场发现到运行时加载的完整生命周期管理。版本化缓存、签名去重、原子写入等工程实践确保了系统的可靠性。企业级策略（allowlist/denylist/managed-only）在配置层优雅地叠加，不侵入核心逻辑。

**Skills 系统**将 Markdown 文件转化为可执行的 AI 指令，通过 frontmatter 定义元数据、通过 fork 模式实现隔离执行。这是"约定优于配置"理念在 AI 工具中的绝佳实践。

三者的协同工作使得 Claude Code 从一个封闭的 CLI 工具进化为一个可扩展的 AI 开发平台——用户可以通过 MCP 连接任意外部服务，通过插件分发和复用配置，通过 Skills 封装领域知识。

## 11. 延伸思考

1. **MCP 协议的演进**：当前 MCP 支持 5 种传输协议，未来是否会统一到 Streamable HTTP？stdio 的"子进程即服务"模式在容器化环境中是否面临挑战？

2. **插件安全边界**：当前插件运行在 CLI 进程内，其 MCP 服务器通过 stdio 可执行任意命令。未来是否需要沙箱隔离（如 WASM）？插件的 `allowedTools` 限制是否足够？

3. **Skills 与 Agent 的融合**：Skills 本质上是"结构化的 prompt + 工具限制"。当 Agent 框架成熟后，Skills 是否会进化为完整的 Agent 定义（包含记忆、规划、反思）？

4. **去中心化市场**：当前市场是中心化的 GitHub 仓库。基于 IPFS 或 Git 的去中心化市场是否可行？如何在去中心化环境中实现恶意插件检测？

5. **跨 IDE 的 MCP 统一**：MCP 已被 VS Code、Cursor、Windsurf 等 IDE 采纳。Claude Code 的 `SdkControlTransport` 和 `sse-ide` 类型暗示了 IDE 深度集成的方向。未来 MCP 是否会成为 AI 工具的"USB 协议"？

6. **插件依赖管理**：`dependencies` 字段支持跨市场依赖（通过 `allowCrossMarketplaceDependenciesOn`），但目前没有版本冲突解决机制。当插件生态复杂后，是否需要类似 npm 的 lockfile？

7. **热更新与无感重连**：当前重连需要指数退避（最长 30 秒）。对于高频变更的开发环境，是否需要 WebSocket 长连接 + 心跳保活？`PromptListChangedNotification` 等通知机制暗示了这个方向。
