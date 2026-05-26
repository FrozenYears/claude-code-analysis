---
title: 第09章：Streaming与API通信
---

# 第09章：Streaming与API通信

## 1. 本章目标

本章将深入剖析 Claude Code 如何与 Anthropic Messages API 进行通信，重点分析以下核心机制：

- **SSE 流式响应处理**：从 `query()` 到逐 chunk 解析的完整流程
- **Anthropic SDK 集成**：如何封装 `@anthropic-ai/sdk` 的 Stream API
- **错误重试机制**：`withRetry` 的指数退避、529 过载降级、OAuth token 刷新
- **流式降级策略**：当流式请求失败时自动回退到非流式请求的完整流程
- **Token 使用量统计**：`updateUsage` 如何累积流式事件中的 token 计数
- **成本追踪**：`calculateUSDCost` 与 `addToTotalSessionCost` 的费用计算链路

读完本章，你将理解 Claude Code 如何在保持实时流式输出的同时，处理各种网络异常、API 限流和认证失效，并将每一次 API 调用的成本精确追踪到毫美元级别。

## 2. 前置知识

在阅读本章之前，你需要了解：

- **SSE (Server-Sent Events)**：HTTP 服务器向客户端单向推送事件流的协议，数据以 `data: ` 前缀的文本行传输，以空行分隔事件
- **AsyncGenerator / AsyncIterable**：JavaScript 异步迭代器协议，允许通过 `for await...of` 逐步消费异步数据流
- **Anthropic Messages API**：Anthropic 的对话补全 API，支持 `stream: true` 参数启用流式响应
- **HTTP 状态码**：429（限流）、529（过载）、401（未认证）、403（未授权）
- **指数退避 (Exponential Backoff)**：重试策略，每次重试等待时间翻倍
- **Prompt Caching**：Anthropic 的服务端缓存机制，通过 `cache_control` 标记复用前缀相同的 KV 缓存

## 3. 宏观概览

Claude Code 的 API 通信层可以分为五个层次：

```
┌─────────────────────────────────────────────────┐
│                  query.ts                        │  ← 消费层：agentic loop
│  for await (message of callModel(...)) {         │
│    yield message  // 逐条产出给 UI               │
│  }                                               │
├─────────────────────────────────────────────────┤
│            claude.ts: queryModel()               │  ← 协调层：组装参数、流式解析
│  paramsFromContext → anthropic.beta.messages      │
│    .create({stream: true}) → for await (part)    │
├─────────────────────────────────────────────────┤
│            claude.ts: withRetry()                │  ← 重试层：指数退避、错误分类
│  for (attempt 1..N) {                            │
│    try { operation(client) } catch { retry }     │
│  }                                               │
├─────────────────────────────────────────────────┤
│            client.ts: getAnthropicClient()       │  ← 客户端层：认证、代理、SDK
│  new Anthropic | AnthropicBedrock | Vertex       │
├─────────────────────────────────────────────────┤
│            errors.ts: getAssistantMessageFromError│  ← 错误层：错误分类与用户提示
│  classifyAPIError → createAssistantAPIErrorMessage│
└─────────────────────────────────────────────────┘
```

**核心数据流**：

1. `query.ts` 中的 agentic loop 调用 `queryModelWithStreaming()`
2. `queryModelWithStreaming()` 通过 VCR 包装后调用 `queryModel()`
3. `queryModel()` 组装 API 参数，通过 `withRetry()` 创建重试生成器
4. 重试生成器调用 `anthropic.beta.messages.create({stream: true}).withResponse()`
5. 返回的 `Stream<BetaRawMessageStreamEvent>` 被 `for await...of` 逐步消费
6. 每个 SSE 事件（`message_start`、`content_block_delta` 等）被解析并累积到 `contentBlocks`
7. 每个 `content_block_stop` 事件触发一个 `AssistantMessage` 的产出（yield）
8. `query.ts` 收到 `AssistantMessage` 后提取 tool_use 块，执行工具，再进入下一轮

## 4. 源码入口定位

### 4.1 核心文件清单

| 文件 | 行数 | 职责 |
|------|------|------|
| `src/services/api/claude.ts` | ~3420 | API 调用核心：参数组装、流式解析、降级逻辑 |
| `src/services/api/withRetry.ts` | ~530 | 重试引擎：指数退避、529 降级、OAuth 刷新 |
| `src/services/api/errors.ts` | ~750 | 错误分类与用户消息生成 |
| `src/services/api/client.ts` | ~390 | Anthropic SDK 客户端创建（Direct/Bedrock/Vertex/Foundry） |
| `src/services/api/logging.ts` | ~400 | API 调用日志：查询、成功、错误的结构化记录 |
| `src/services/api/bootstrap.ts` | ~110 | 启动时引导数据获取 |
| `src/services/api/errorUtils.ts` | ~260 | SSL/连接错误详情提取 |
| `src/services/api/emptyUsage.ts` | ~20 | 零初始化的 usage 对象 |
| `src/services/vcr.ts` | ~410 | 测试用 VCR 录制/回放 |
| `src/query.ts` | ~1400 | Agentic loop：消费流式输出、执行工具、循环 |
| `src/utils/modelCost.ts` | ~250 | 模型定价与成本计算 |
| `src/cost-tracker.ts` | ~320 | 会话级成本累积 |

### 4.2 关键入口行号

**流式 API 调用入口**（`claude.ts`）：
- `queryModel()` 函数：约第 1301 行开始，是核心的 API 调用协调器
- `paramsFromContext()` 闭包：约第 1701 行，组装完整的 API 请求参数
- 流式解析循环：约第 1931 行 `for await (const part of stream)`
- `message_start` 事件处理：约第 1961 行
- `content_block_delta` 事件处理：约第 2001 行
- `message_delta` 事件处理：约第 2131 行
- 流式降级逻辑：约第 2371 行 catch 块

**重试引擎**（`withRetry.ts`）：
- `withRetry()` 函数：约第 120 行
- `shouldRetry()` 函数：约第 400 行
- `getRetryDelay()` 函数：约第 320 行

**客户端创建**（`client.ts`）：
- `getAnthropicClient()` 函数：约第 100 行

## 5. 调用链分析

### 5.1 从 query() 到 SSE 事件流的完整路径

```
query.ts: agentic loop
  │
  ├─ callModel({messages, systemPrompt, thinkingConfig, tools, signal, options})
  │   │
  │   ├─ claude.ts: queryModelWithStreaming()
  │   │   │
  │   │   ├─ withStreamingVCR(messages, async function*() {
  │   │   │     yield* queryModel(messages, systemPrompt, thinkingConfig, tools, signal, options)
  │   │   │   })
  │   │   │
  │   │   └─ claude.ts: queryModel()
  │   │       │
  │   │       ├─ 1. 检查 Opus off-switch（约 L1101）
  │   │       ├─ 2. 解析 previousRequestId（约 L1131）
  │   │       ├─ 3. 构建 betas 数组（约 L1141）
  │   │       ├─ 4. 过滤工具、构建 toolSchemas（约 L1201）
  │   │       ├─ 5. normalizeMessagesForAPI()（约 L1261）
  │   │       ├─ 6. 注入 system prompt（约 L1301）
  │   │       ├─ 7. paramsFromContext() 闭包定义（约 L1701）
  │   │       │
  │   │       ├─ 8. withRetry() 重试生成器（约 L1841）
  │   │       │   │
  │   │       │   ├─ getAnthropicClient() → Anthropic SDK 客户端
  │   │       │   ├─ paramsFromContext(context) → 完整 API 参数
  │   │       │   ├─ anthropic.beta.messages.create({stream: true}, {signal})
  │   │       │   │   .withResponse()
  │   │       │   └─ return Stream<BetaRawMessageStreamEvent>
  │   │       │
  │   │       ├─ 9. for await (const part of stream)（约 L1931）
  │   │       │   │
  │   │       │   ├─ message_start → partialMessage, ttftMs, usage
  │   │       │   ├─ content_block_start → contentBlocks[index] 初始化
  │   │       │   ├─ content_block_delta → 累积 text/thinking/input_json
  │   │       │   ├─ content_block_stop → yield AssistantMessage
  │   │       │   ├─ message_delta → 更新 usage, stopReason, costUSD
  │   │       │   └─ message_stop → 结束
  │   │       │
  │   │       └─ 10. logAPISuccessAndDuration()（约 L2881）
  │   │
  │   └─ 返回 AsyncGenerator<StreamEvent | AssistantMessage | SystemAPIErrorMessage>
  │
  ├─ for await (const message of callModel(...)) {
  │     // message.type === 'assistant' → 提取 tool_use 块
  │     // message.type === 'stream_event' → 转发给 UI 渲染
  │     yield message
  │   }
  │
  └─ 执行工具 → 下一轮循环
```

### 5.2 错误重试的调用链

```
withRetry() (withRetry.ts)
  │
  ├─ for (attempt = 1; attempt <= maxRetries + 1; attempt++)
  │   │
  │   ├─ try {
  │   │   ├─ getClient() → 新建/复用 Anthropic 客户端
  │   │   ├─ operation(client, attempt, retryContext)
  │   │   └─ return 结果
  │   │ }
  │   │
  │   └─ catch (error) {
  │       ├─ is529Error(error) → consecutive529Errors++
  │       ├─ shouldRetry(error) → 检查 x-should-retry 头
  │       ├─ getRetryDelay(attempt, retryAfter) → 指数退避
  │       ├─ yield createSystemAPIErrorMessage(error, delayMs, attempt)
  │       └─ await sleep(delayMs, signal)
  │     }
  │
  └─ throw CannotRetryError(lastError, retryContext)
```

### 5.3 流式降级的调用链

```
queryModel() 中的 try-catch:
  │
  ├─ try {
  │   ├─ stream = for await (part of stream) { ... }
  │   └─ 正常完成
  │ }
  │
  └─ catch (streamingError) {
      ├─ 清除 stream idle watchdog
      ├─ 判断是否禁用降级 → CLAUDE_CODE_DISABLE_NONSTREAMING_FALLBACK
      │
      └─ 降级路径:
          ├─ didFallBackToNonStreaming = true
          ├─ options.onStreamingFallback() → 通知上层
          ├─ executeNonStreamingRequest() → 非流式重试
          │   ├─ anthropic.beta.messages.create({stream: false}, {timeout})
          │   └─ return BetaMessage
          ├─ normalizeContentFromAPI(result.content)
          └─ yield AssistantMessage
    }
```

## 6. 核心源码解析

### 6.1 API 参数组装：paramsFromContext()

`paramsFromContext()` 是一个定义在 `queryModel()` 内部的闭包（`claude.ts` 约第 1701 行），它根据当前的重试上下文组装完整的 API 请求参数。这是一个精心设计的函数，因为每次重试时参数可能需要调整（例如 `maxTokensOverride` 因 context overflow 而变化）。

```typescript
// claude.ts 约第 1701 行
const paramsFromContext = (retryContext: RetryContext) => {
    const betasParams = [...betas]

    // 动态追加 1M 上下文 beta
    if (
      !betasParams.includes(CONTEXT_1M_BETA_HEADER) &&
      getSonnet1mExpTreatmentEnabled(retryContext.model)
    ) {
      betasParams.push(CONTEXT_1M_BETA_HEADER)
    }

    // Bedrock 需要额外的 beta 头放在 extraBodyParams
    const bedrockBetas =
      getAPIProvider() === 'bedrock'
        ? [
            ...getBedrockExtraBodyParamsBetas(retryContext.model),
            ...(toolSearchHeader ? [toolSearchHeader] : []),
          ]
        : []
    const extraBodyParams = getExtraBodyParams(bedrockBetas)

    // effort 配置
    const outputConfig: BetaOutputConfig = {
      ...((extraBodyParams.output_config as BetaOutputConfig) ?? {}),
    }
    configureEffortParams(effort, outputConfig, extraBodyParams, betasParams, options.model)

    // 重试上下文优先（因为 context overflow 会调整 max_tokens）
    const maxOutputTokens =
      retryContext?.maxTokensOverride ||
      options.maxOutputTokensOverride ||
      getMaxOutputTokensForModel(options.model)

    // thinking 配置
    let thinking: BetaMessageStreamParams['thinking'] | undefined = undefined
    if (hasThinking && modelSupportsThinking(options.model)) {
      if (modelSupportsAdaptiveThinking(options.model)) {
        thinking = { type: 'adaptive' }
      } else {
        let thinkingBudget = getMaxThinkingTokensForModel(options.model)
        thinkingBudget = Math.min(maxOutputTokens - 1, thinkingBudget)
        thinking = { budget_tokens: thinkingBudget, type: 'enabled' }
      }
    }

    // 快速模式
    let speed: BetaMessageStreamParams['speed']
    if (isFastModeForRetry) {
      speed = 'fast'
    }

    // temperature: thinking 启用时必须为 1
    const temperature = !hasThinking
      ? (options.temperatureOverride ?? 1)
      : undefined

    return {
      model: normalizeModelStringForAPI(options.model),
      messages: addCacheBreakpoints(messagesForAPI, enablePromptCaching, ...),
      system,
      tools: allTools,
      tool_choice: options.toolChoice,
      ...(useBetas && { betas: betasParams }),
      metadata: getAPIMetadata(),
      max_tokens: maxOutputTokens,
      thinking,
      ...(temperature !== undefined && { temperature }),
      ...extraBodyParams,
      ...(Object.keys(outputConfig).length > 0 && { output_config: outputConfig }),
      ...(speed !== undefined && { speed }),
    }
  }
```

**设计亮点**：
- `retryContext.maxTokensOverride` 优先于 `options.maxOutputTokensOverride`，这样当 API 返回 "input length and max_tokens exceed context limit" 错误时，重试引擎可以自动缩减 `max_tokens` 继续尝试
- `temperature` 在 thinking 启用时不发送，因为 API 要求 thinking 模式下 `temperature: 1`（已是默认值）
- `speed` 参数支持 fast mode，在限流时可以动态切换回标准速度

### 6.2 流式请求的发起

```typescript
// claude.ts 约第 1871 行 - withRetry 的 operation 回调
async (anthropic, attempt, context) => {
    attemptNumber = attempt
    isFastModeRequest = context.fastMode ?? false
    start = Date.now()
    attemptStartTimes.push(start)

    const params = paramsFromContext(context)
    captureAPIRequest(params, options.querySource)

    // 生成客户端请求 ID（仅第一方 API）
    clientRequestId =
      getAPIProvider() === 'firstParty' && isFirstPartyAnthropicBaseUrl()
        ? randomUUID()
        : undefined

    // 关键：使用 raw stream 而非 BetaMessageStream
    // BetaMessageStream 会对每个 input_json_delta 调用 partialParse()，导致 O(n²)
    // 我们自己处理 tool input 累积，不需要 SDK 的解析
    const result = await anthropic.beta.messages
      .create(
        { ...params, stream: true },
        {
          signal,
          ...(clientRequestId && {
            headers: { [CLIENT_REQUEST_ID_HEADER]: clientRequestId },
          }),
        },
      )
      .withResponse()

    streamRequestId = result.request_id
    streamResponse = result.response
    return result.data  // Stream<BetaRawMessageStreamEvent>
}
```

**关键决策：使用 raw stream 而非 BetaMessageStream**

这是一个重要的性能优化。Anthropic SDK 提供了两种流式消费方式：

1. **`BetaMessageStream`**：SDK 内置的高级流，自动解析 JSON、累积 content blocks、提供 `finalMessage()` 等便捷方法。但它会在每个 `input_json_delta` 事件上调用 `partialParse()`（增量 JSON 解析），对于大型 tool input 会导致 O(n²) 的性能问题。

2. **`Stream<BetaRawMessageStreamEvent>`**：底层 raw stream，直接返回 SSE 解析后的事件对象。Claude Code 选择这种方式，自行处理 content block 的累积。

`withResponse()` 方法返回一个 `{ data: Stream, request_id: string, response: Response }` 三元组，让 Claude Code 可以同时获取流数据、请求 ID 和原始 HTTP Response 对象（用于提取 headers）。

### 6.3 SSE 事件流的逐事件解析

流式解析是 `queryModel()` 中最核心的部分（`claude.ts` 约第 1931-2301 行）。它维护以下状态：

```typescript
// 状态初始化
const newMessages: AssistantMessage[] = []
let ttftMs = 0
let partialMessage: BetaMessage | undefined = undefined
const contentBlocks: (BetaContentBlock | ConnectorTextBlock)[] = []
let usage: NonNullableUsage = EMPTY_USAGE
let costUSD = 0
let stopReason: BetaStopReason | null = null
```

然后进入 `for await...of` 循环逐事件处理：

#### 6.3.1 message_start 事件

```typescript
case 'message_start': {
    partialMessage = part.message
    ttftMs = Date.now() - start  // 首 token 时间
    usage = updateUsage(usage, part.message?.usage)
    break
}
```

`message_start` 是流的第一个事件，携带模型名称、初始 usage（通常是 input_tokens）和一个空的 content 数组。`ttftMs`（Time To First Token）是衡量 API 响应速度的关键指标。

#### 6.3.2 content_block_start 事件

```typescript
case 'content_block_start':
    switch (part.content_block.type) {
      case 'tool_use':
        contentBlocks[part.index] = {
          ...part.content_block,
          input: '',  // 初始化为空字符串，后续通过 input_json_delta 累积
        }
        break
      case 'text':
        contentBlocks[part.index] = {
          ...part.content_block,
          text: '',  // SDK 可能会在 start 中携带部分文本，我们忽略它
        }
        break
      case 'thinking':
        contentBlocks[part.index] = {
          ...part.content_block,
          thinking: '',
          signature: '',  // 预初始化签名字段
        }
        break
      default:
        contentBlocks[part.index] = { ...part.content_block }
        break
    }
    break
```

**注意**：代码注释提到了一个 SDK 的行为怪癖——`content_block_start` 有时会携带部分文本内容，但随后 `content_block_delta` 又会重复发送这些文本。Claude Code 通过在 `start` 中将 `text` 初始化为空字符串来避免重复。

#### 6.3.3 content_block_delta 事件

这是最复杂的事件类型，需要根据 delta 的类型分发处理：

```typescript
case 'content_block_delta': {
    const contentBlock = contentBlocks[part.index]
    const delta = part.delta

    if (delta.type === 'connector_text_delta') {
        // MCP 连接器文本
        contentBlock.connector_text += delta.connector_text
    } else {
        switch (delta.type) {
            case 'input_json_delta':
                // Tool input 的 JSON 片段累积
                if (typeof contentBlock.input !== 'string') {
                    throw new Error('Content block input is not a string')
                }
                contentBlock.input += delta.partial_json
                break
            case 'text_delta':
                // 文本内容累积
                contentBlock.text += delta.text
                break
            case 'signature_delta':
                // Thinking 块的签名（用于验证 thinking 内容未被篡改）
                contentBlock.signature = delta.signature
                break
            case 'thinking_delta':
                // Thinking 内容累积
                contentBlock.thinking += delta.thinking
                break
        }
    }
    break
}
```

**Tool input 的处理策略**：Claude Code 将 `input_json_delta` 的 `partial_json` 片段直接拼接到 `contentBlock.input` 字符串上，而非使用 SDK 的 `partialParse()` 进行增量 JSON 解析。最终在 `content_block_stop` 时才通过 `normalizeContentFromAPI()` 进行一次完整的 JSON 解析。这避免了 O(n²) 的性能问题。

#### 6.3.4 content_block_stop 事件

```typescript
case 'content_block_stop': {
    const contentBlock = contentBlocks[part.index]
    if (!partialMessage) {
        throw new Error('Message not found')
    }
    const m: AssistantMessage = {
        message: {
            ...partialMessage,
            content: normalizeContentFromAPI(
                [contentBlock] as BetaContentBlock[],
                tools,
                options.agentId,
            ),
        },
        requestId: streamRequestId ?? undefined,
        type: 'assistant',
        uuid: randomUUID(),
        timestamp: new Date().toISOString(),
    }
    newMessages.push(m)
    yield m  // 产出给上层消费者
    break
}
```

**关键设计**：每个 `content_block_stop` 都会立即产出一个 `AssistantMessage`，而非等到整个 `message_stop` 才产出。这意味着：
- 文本块可以立即渲染到终端
- Tool use 块可以立即开始执行（实现并行流式工具执行）
- Thinking 块可以实时显示思考过程

#### 6.3.5 message_delta 事件

```typescript
case 'message_delta': {
    usage = updateUsage(usage, part.usage)
    stopReason = part.delta.stop_reason

    // 回写最终 usage 和 stop_reason 到已产出的消息
    const lastMsg = newMessages.at(-1)
    if (lastMsg) {
        lastMsg.message.usage = usage
        lastMsg.message.stop_reason = stopReason
    }

    // 计算并累积成本
    const costUSDForPart = calculateUSDCost(resolvedModel, usage)
    costUSD += addToTotalSessionCost(costUSDForPart, usage, options.model)

    // 处理 refusal（内容拒绝）
    const refusalMessage = getErrorMessageIfRefusal(
        part.delta.stop_reason,
        options.model,
    )
    if (refusalMessage) {
        yield refusalMessage
    }

    // 处理 max_tokens 和 context_window_exceeded
    if (stopReason === 'max_tokens') {
        yield createAssistantAPIErrorMessage({
            content: `API Error: Claude's response exceeded the ${maxOutputTokens} output token maximum.`,
            error: 'max_output_tokens',
        })
    }
    break
}
```

**重要细节**：`usage` 和 `stop_reason` 通过**直接属性修改**（mutation）回写到 `lastMsg.message`，而非创建新对象。这是因为 transcript 写入队列持有对 `message.message` 的引用，并以 100ms 的间隔懒序列化。如果使用对象替换（`{ ...lastMsg.message, usage }`），会断开队列中的引用，导致 transcript 无法捕获最终值。

### 6.4 流式空闲看门狗

Claude Code 实现了一个精巧的流式空闲超时机制（`claude.ts` 约第 1911 行），用于处理连接静默断开的情况：

```typescript
const STREAM_IDLE_TIMEOUT_MS =
    parseInt(process.env.CLAUDE_STREAM_IDLE_TIMEOUT_MS || '', 10) || 90_000

function resetStreamIdleTimer(): void {
    clearStreamIdleTimers()
    streamIdleWarningTimer = setTimeout(() => {
        logForDebugging(`Streaming idle warning: no chunks for ${warnMs/1000}s`)
    }, STREAM_IDLE_WARNING_MS)
    streamIdleTimer = setTimeout(() => {
        streamIdleAborted = true
        releaseStreamResources()  // 释放连接资源
    }, STREAM_IDLE_TIMEOUT_MS)
}

// 在 for await 循环中每次收到事件时重置
for await (const part of stream) {
    resetStreamIdleTimer()
    // ... 处理事件
}
```

**设计考量**：
- 默认 90 秒超时，可通过 `CLAUDE_STREAM_IDLE_TIMEOUT_MS` 环境变量配置
- 50% 时间点发出警告日志（45 秒）
- 超时后不是直接抛错，而是设置 `streamIdleAborted` 标志并释放资源
- 循环退出后检查标志，触发非流式降级而非直接报错
- 通过 `performance.now()` 测量看门狗触发到循环实际退出的延迟，区分"正常退出"和"卡死"

### 6.5 流式降级：从 Streaming 到 Non-Streaming

当流式请求失败时（网络中断、看门狗超时、529 过载等），Claude Code 会自动降级到非流式请求：

```typescript
// claude.ts 约第 2371 行 catch 块
catch (streamingError) {
    clearStreamIdleTimers()

    // 检查是否禁用降级
    const disableFallback =
        isEnvTruthy(process.env.CLAUDE_CODE_DISABLE_NONSTREAMING_FALLBACK) ||
        getFeatureValue_CACHED_MAY_BE_STALE('tengu_disable_streaming_to_non_streaming_fallback', false)

    if (disableFallback) {
        throw streamingError
    }

    didFallBackToNonStreaming = true
    options.onStreamingFallback?.()

    // 降级到非流式请求
    const result = yield* executeNonStreamingRequest(
        { model: options.model, source: options.querySource },
        {
            model: options.model,
            fallbackModel: options.fallbackModel,
            thinkingConfig,
            signal,
            initialConsecutive529Errors: is529Error(streamingError) ? 1 : 0,
            querySource: options.querySource,
        },
        paramsFromContext,
        (attempt, _startTime, tokens) => { ... },
        params => captureAPIRequest(params, options.querySource),
        streamRequestId,
    )

    const m: AssistantMessage = {
        message: {
            ...result,
            content: normalizeContentFromAPI(result.content, tools, options.agentId),
        },
        requestId: streamRequestId ?? undefined,
        type: 'assistant',
        uuid: randomUUID(),
        timestamp: new Date().toISOString(),
    }
    newMessages.push(m)
    fallbackMessage = m
    yield m
}
```

**`executeNonStreamingRequest()` 的实现**（`claude.ts` 约第 861 行）：

```typescript
async function* executeNonStreamingRequest(
    clientOptions, retryOptions, paramsFromContext, onAttempt, captureRequest, originatingRequestId
): AsyncGenerator<SystemAPIErrorMessage, BetaMessage> {
    const fallbackTimeoutMs = getNonstreamingFallbackTimeoutMs()  // 120s 或 300s

    const generator = withRetry(
        () => getAnthropicClient({ maxRetries: 0, model: clientOptions.model, ... }),
        async (anthropic, attempt, context) => {
            const retryParams = paramsFromContext(context)
            const adjustedParams = adjustParamsForNonStreaming(retryParams, MAX_NON_STREAMING_TOKENS)

            return await anthropic.beta.messages.create(
                { ...adjustedParams, model: normalizeModelStringForAPI(adjustedParams.model) },
                { signal: retryOptions.signal, timeout: fallbackTimeoutMs }
            )
        },
        retryOptions
    )

    // 消费重试生成器中的 system 消息
    let e
    do {
        e = await generator.next()
        if (!e.done && e.value.type === 'system') {
            yield e.value
        }
    } while (!e.done)

    return e.value as BetaMessage
}
```

**关键细节**：
- `adjustParamsForNonStreaming()` 会移除 `stream: true` 并限制 `max_tokens`
- 非流式请求有独立的超时（远程会话 120 秒，本地 300 秒）
- `initialConsecutive529Errors` 将流式阶段的 529 错误计入连续 529 计数，确保降级后的总 529 次数一致
- 404 错误也有专门的降级路径（某些网关对流式端点返回 404 但非流式正常）

### 6.6 重试引擎：withRetry()

`withRetry()`（`withRetry.ts` 约第 120 行）是整个 API 通信层的弹性核心。它是一个 `AsyncGenerator` 函数，允许在重试过程中 yield 系统消息（显示给用户）。

```typescript
export async function* withRetry<T>(
    getClient: () => Promise<Anthropic>,
    operation: (client: Anthropic, attempt: number, context: RetryContext) => Promise<T>,
    options: RetryOptions,
): AsyncGenerator<SystemAPIErrorMessage, T> {
    const maxRetries = getMaxRetries(options)  // 默认 10
    const retryContext: RetryContext = { model: options.model, thinkingConfig: options.thinkingConfig }
    let consecutive529Errors = options.initialConsecutive529Errors ?? 0

    for (let attempt = 1; attempt <= maxRetries + 1; attempt++) {
        if (options.signal?.aborted) throw new APIUserAbortError()

        try {
            // 需要新客户端的情况：首次、401、token revoked、ECONNRESET
            if (client === null || needsNewClient(lastError)) {
                if (isOAuth401(lastError)) {
                    await handleOAuth401Error(failedAccessToken)
                }
                client = await getClient()
            }
            return await operation(client, attempt, retryContext)
        } catch (error) {
            lastError = error

            // Fast mode 降级：429/529 时切换到标准速度
            if (wasFastModeActive && error.status === 429 || is529Error(error)) {
                const retryAfterMs = getRetryAfterMs(error)
                if (retryAfterMs !== null && retryAfterMs < 20_000) {
                    await sleep(retryAfterMs, signal)  // 短等待，保持 fast mode
                    continue
                }
                // 长等待，进入 cooldown
                triggerFastModeCooldown(Date.now() + cooldownMs, cooldownReason)
                retryContext.fastMode = false
                continue
            }

            // 529 连续计数与模型降级
            if (is529Error(error)) {
                consecutive529Errors++
                if (consecutive529Errors >= MAX_529_RETRIES) {
                    if (options.fallbackModel) {
                        throw new FallbackTriggeredError(options.model, options.fallbackModel)
                    }
                    throw new CannotRetryError(new Error(REPEATED_529_ERROR_MESSAGE), retryContext)
                }
            }

            // 检查是否应该重试
            if (!shouldRetry(error)) throw new CannotRetryError(error, retryContext)

            // 计算退避延迟
            const retryAfter = getRetryAfter(error)
            const delayMs = getRetryDelay(attempt, retryAfter)

            yield createSystemAPIErrorMessage(error, delayMs, attempt, maxRetries)
            await sleep(delayMs, signal)
        }
    }
}
```

#### 6.6.1 指数退避算法

```typescript
export function getRetryDelay(
    attempt: number,
    retryAfterHeader?: string | null,
    maxDelayMs = 32000,
): number {
    // 优先使用服务端的 Retry-After 头
    if (retryAfterHeader) {
        const seconds = parseInt(retryAfterHeader, 10)
        if (!isNaN(seconds)) return seconds * 1000
    }

    // 指数退避 + 随机抖动
    const baseDelay = Math.min(BASE_DELAY_MS * Math.pow(2, attempt - 1), maxDelayMs)
    // BASE_DELAY_MS = 500, 所以: 500, 1000, 2000, 4000, 8000, 16000, 32000
    const jitter = Math.random() * 0.25 * baseDelay  // ±12.5% 随机抖动
    return baseDelay + jitter
}
```

**退避序列**（毫秒）：~500 → ~1000 → ~2000 → ~4000 → ~8000 → ~16000 → ~32000 → ~32000...

抖动防止多个客户端同时重试导致"雷群效应"（thundering herd）。

#### 6.6.2 持久重试模式

对于无人值守的会话（`CLAUDE_CODE_UNATTENDED_RETRY`），重试引擎进入"持久模式"：

```typescript
const PERSISTENT_MAX_BACKOFF_MS = 5 * 60 * 1000   // 最大 5 分钟退避
const PERSISTENT_RESET_CAP_MS = 6 * 60 * 60 * 1000  // 最长 6 小时等待
const HEARTBEAT_INTERVAL_MS = 30_000  // 30 秒心跳

// 持久模式下，for 循环的 attempt 被钳制在 maxRetries+1
// 使用独立的 persistentAttempt 计数器
if (persistent) {
    let remaining = delayMs
    while (remaining > 0) {
        if (options.signal?.aborted) throw new APIUserAbortError()
        yield createSystemAPIErrorMessage(error, remaining, reportedAttempt, maxRetries)
        const chunk = Math.min(remaining, HEARTBEAT_INTERVAL_MS)
        await sleep(chunk, options.signal, { abortError })
        remaining -= chunk
    }
    // 钳制 attempt 使循环永不终止
    if (attempt >= maxRetries) attempt = maxRetries
}
```

**设计目的**：无人值守的 CLI 会话（如 CI/CD 管道）可能遇到长时间的 API 过载。持久模式会：
- 无限重试 429/529 错误
- 每 30 秒 yield 一次心跳消息，防止宿主环境因无输出而认为会话空闲
- 尊重服务端的 `anthropic-ratelimit-unified-reset` 头，等待到限额重置而非盲目轮询

#### 6.6.3 shouldRetry() 决策逻辑

```typescript
function shouldRetry(error: APIError): boolean {
    // Mock 错误不重试（/mock-limits 命令用）
    if (isMockRateLimitError(error)) return false

    // 持久模式：429/529 总是可重试
    if (isPersistentRetryEnabled() && isTransientCapacityError(error)) return true

    // CCR 模式：401/403 是瞬态错误（基础设施 JWT）
    if (isCCRMode() && (error.status === 401 || error.status === 403)) return true

    // 529 过载错误（SDK 有时在流式中不正确传递 529 状态码）
    if (error.message?.includes('"type":"overloaded_error"')) return true

    // max_tokens context overflow（可调整 max_tokens 重试）
    if (parseMaxTokensContextOverflowError(error)) return true

    // 遵循服务端 x-should-retry 头
    const shouldRetryHeader = error.headers?.get('x-should-retry')
    if (shouldRetryHeader === 'true' && (!isClaudeAISubscriber() || isEnterpriseSubscriber())) {
        return true
    }
    if (shouldRetryHeader === 'false') return false

    // 连接错误
    if (error instanceof APIConnectionError) return true

    // 标准 HTTP 状态码
    if (error.status === 408) return true  // 请求超时
    if (error.status === 409) return true  // 锁超时
    if (error.status === 429) return !isClaudeAISubscriber() || isEnterpriseSubscriber()
    if (error.status === 401) { clearApiKeyHelperCache(); return true }
    if (error.status >= 500) return true  // 服务端错误

    return false
}
```

**关键决策**：
- ClaudeAI 订阅用户的 429 不重试（因为他们有配额限制，重试无意义）
- 企业用户可以重试 429（因为他们通常使用 PAYG）
- 529 过载错误通过消息内容检测（SDK 的 bug：流式中有时不传递正确状态码）

### 6.7 错误分类与处理：errors.ts

`errors.ts` 是错误处理的"翻译层"，将各种 API 错误转换为用户友好的消息：

```typescript
export function getAssistantMessageFromError(error: unknown, model: string, options?): AssistantMessage {
    // 超时错误
    if (error instanceof APIConnectionTimeoutError) {
        return createAssistantAPIErrorMessage({ content: API_TIMEOUT_ERROR_MESSAGE, error: 'unknown' })
    }

    // 429 限流：提取详细的限额信息
    if (error instanceof APIError && error.status === 429) {
        const rateLimitType = error.headers?.get?.('anthropic-ratelimit-unified-representative-claim')
        const overageStatus = error.headers?.get?.('anthropic-ratelimit-unified-overage-status')

        if (rateLimitType || overageStatus) {
            // 构建限额对象，生成精确的错误消息
            const limits: ClaudeAILimits = { status: 'rejected', ... }
            const specificErrorMessage = getRateLimitErrorMessage(limits, model)
            if (specificErrorMessage) {
                return createAssistantAPIErrorMessage({ content: specificErrorMessage, error: 'rate_limit' })
            }
            // 如果返回 null，说明会静默降级（如 Opus → Sonnet）
            return createAssistantAPIErrorMessage({ content: NO_RESPONSE_REQUESTED, error: 'rate_limit' })
        }
    }

    // Prompt 太长
    if (error.message.toLowerCase().includes('prompt is too long')) {
        return createAssistantAPIErrorMessage({
            content: PROMPT_TOO_LONG_ERROR_MESSAGE,
            error: 'invalid_request',
            errorDetails: error.message,  // 包含 token 数量信息
        })
    }

    // Tool use/tool_result 不匹配
    if (error.status === 400 && error.message.includes('tool_use ids were found without tool_result')) {
        logToolUseToolResultMismatch(toolUseId, messages, messagesForAPI)
        return createAssistantAPIErrorMessage({
            content: 'API Error: 400 due to tool use concurrency issues.',
            error: 'invalid_request',
        })
    }

    // ... 更多错误类型
}
```

**`classifyAPIError()` 函数**：将错误分类为标准化的类型字符串，用于 Datadog 分析：

```typescript
export function classifyAPIError(error: unknown): string {
    if (error.message === 'Request was aborted.') return 'aborted'
    if (isTimeoutError(error)) return 'api_timeout'
    if (is529Error(error)) return 'server_overload'
    if (error.status === 429) return 'rate_limit'
    if (isPromptTooLong(error)) return 'prompt_too_long'
    if (isPDFError(error)) return 'pdf_too_large'
    if (isImageError(error)) return 'image_too_large'
    if (isToolUseError(error)) return 'tool_use_mismatch'
    if (isAuthError(error)) return 'auth_error'
    if (error.status >= 500) return 'server_error'
    if (error.status >= 400) return 'client_error'
    if (error instanceof APIConnectionError) {
        return extractConnectionErrorDetails(error)?.isSSLError ? 'ssl_cert_error' : 'connection_error'
    }
    return 'unknown'
}
```

### 6.8 Usage 更新与成本计算

#### 6.8.1 updateUsage()

Anthropic 的流式 API 提供**累积**的 usage 总量（非增量）。`updateUsage()` 需要小心处理：

```typescript
export function updateUsage(
    usage: Readonly<NonNullableUsage>,
    partUsage: BetaMessageDeltaUsage | undefined,
): NonNullableUsage {
    if (!partUsage) return { ...usage }

    return {
        // input 相关字段：只在非零非 null 时更新
        // message_start 设置了真实值，message_delta 可能发送显式 0
        input_tokens:
            partUsage.input_tokens !== null && partUsage.input_tokens > 0
                ? partUsage.input_tokens
                : usage.input_tokens,
        cache_creation_input_tokens: /* 同上逻辑 */,
        cache_read_input_tokens: /* 同上逻辑 */,

        // output 总是更新（累积值）
        output_tokens: partUsage.output_tokens ?? usage.output_tokens,

        server_tool_use: {
            web_search_requests: partUsage.server_tool_use?.web_search_requests ?? usage.server_tool_use.web_search_requests,
            web_fetch_requests: partUsage.server_tool_use?.web_fetch_requests ?? usage.server_tool_use.web_fetch_requests,
        },

        // 保留 message_start 的 inference_geo, service_tier
        inference_geo: usage.inference_geo,
        service_tier: usage.service_tier,
    }
}
```

**设计要点**：
- Input 相关 token（`input_tokens`、`cache_creation_input_tokens`、`cache_read_input_tokens`）只在 `message_start` 中设置一次
- `message_delta` 可能发送显式 0 值覆盖，所以必须检查 `> 0`
- Output tokens 是累积值，每次 `message_delta` 都更新

#### 6.8.2 成本计算

```typescript
// modelCost.ts
export function calculateUSDCost(resolvedModel: string, usage: Usage): number {
    const costs = getModelCosts(resolvedModel, usage)
    return (
        (usage.input_tokens / 1_000_000) * costs.inputTokens +
        (usage.output_tokens / 1_000_000) * costs.outputTokens +
        ((usage.cache_creation_input_tokens ?? 0) / 1_000_000) * costs.promptCacheWriteTokens +
        ((usage.cache_read_input_tokens ?? 0) / 1_000_000) * costs.promptCacheReadTokens +
        ((usage.server_tool_use?.web_search_requests ?? 0)) * costs.webSearchRequests
    )
}
```

**模型定价示例**（$/百万 token）：

| 模型 | Input | Output | Cache Write | Cache Read |
|------|-------|--------|-------------|------------|
| Sonnet 4 | $3 | $15 | $3.75 | $0.30 |
| Opus 4.1 | $15 | $75 | $18.75 | $1.50 |
| Opus 4.5 | $5 | $25 | $6.25 | $0.50 |
| Haiku 4.5 | $0.80 | $4 | $1.00 | $0.08 |

#### 6.8.3 会话级成本累积

```typescript
// cost-tracker.ts
export function addToTotalSessionCost(
    cost: number,
    usage: NonNullableUsage,
    model: string,
): number {
    const modelUsage = getOrCreateModelUsage(model)
    modelUsage.inputTokens += usage.input_tokens
    modelUsage.outputTokens += usage.output_tokens
    modelUsage.cacheReadTokens += usage.cache_read_input_tokens
    modelUsage.cacheCreationTokens += usage.cache_creation_input_tokens
    modelUsage.costUSD += cost

    totalSessionCost.costUSD += cost
    return cost
}
```

成本追踪是会话级别的，按模型分组统计 token 使用量和费用。`addToTotalSessionCost()` 返回传入的成本值，方便调用方累加到 `costUSD` 局部变量。

### 6.9 Prompt Cache 断点管理

Claude Code 的缓存策略是 API 通信中最精妙的部分之一：

```typescript
export function addCacheBreakpoints(
    messages: (UserMessage | AssistantMessage)[],
    enablePromptCaching: boolean,
    querySource?: QuerySource,
    useCachedMC = false,
    newCacheEdits?: CachedMCEditsBlock | null,
    pinnedEdits?: CachedMCPinnedEdits[],
    skipCacheWrite = false,
): MessageParam[] {
    // 每个请求只放一个 cache_control 标记
    // skipCacheWrite 时放在倒数第二条（fire-and-forget fork 不污染 KVCC）
    const markerIndex = skipCacheWrite ? messages.length - 2 : messages.length - 1

    const result = messages.map((msg, index) => {
        const addCache = index === markerIndex
        if (msg.type === 'user') {
            return userMessageToMessageParam(msg, addCache, enablePromptCaching, querySource)
        }
        return assistantMessageToMessageParam(msg, addCache, enablePromptCaching, querySource)
    })

    // Cache editing: 插入 cache_edits 块用于删除旧的 KV 缓存
    if (useCachedMC) {
        // 重新插入之前 pinned 的 cache_edits
        for (const pinned of pinnedEdits ?? []) { ... }
        // 插入新的 cache_edits
        if (newCacheEdits) { ... }
    }

    // 为 cached prefix 中的 tool_result 块添加 cache_reference
    if (enablePromptCaching) { ... }

    return result
}
```

**缓存层级**：
1. **System prompt**：通过 `buildSystemPromptBlocks()` 分割为多个块，支持 `global` scope
2. **消息历史**：在最后一条消息前添加 `cache_control: { type: 'ephemeral' }`
3. **1 小时 TTL**：通过 `should1hCacheTTL()` 判断是否使用 `ttl: '1h'`
4. **Cache editing**：通过 `cache_edits` 块主动删除不需要的 KV 缓存页

### 6.10 Anthropic 客户端创建

`getAnthropicClient()`（`client.ts` 约第 100 行）根据环境变量创建不同类型的 SDK 客户端：

```typescript
export async function getAnthropicClient({
    apiKey, maxRetries, model, fetchOverride, source
}: { ... }): Promise<Anthropic> {
    // 通用配置
    const ARGS = {
        defaultHeaders: {
            'x-app': 'cli',
            'User-Agent': getUserAgent(),
            'X-Claude-Code-Session-Id': getSessionId(),
            ...customHeaders,
        },
        maxRetries,
        timeout: parseInt(process.env.API_TIMEOUT_MS || String(600 * 1000), 10),
        dangerouslyAllowBrowser: true,
        fetchOptions: getProxyFetchOptions({ forAnthropicAPI: true }),
        ...(resolvedFetch && { fetch: resolvedFetch }),
    }

    // Bedrock
    if (isEnvTruthy(process.env.CLAUDE_CODE_USE_BEDROCK)) {
        const { AnthropicBedrock } = await import('@anthropic-ai/bedrock-sdk')
        return new AnthropicBedrock({ ...ARGS, awsRegion, ... }) as unknown as Anthropic
    }

    // Azure Foundry
    if (isEnvTruthy(process.env.CLAUDE_CODE_USE_FOUNDRY)) {
        const { AnthropicFoundry } = await import('@anthropic-ai/foundry-sdk')
        return new AnthropicFoundry({ ...ARGS, azureADTokenProvider, ... }) as unknown as Anthropic
    }

    // Vertex AI
    if (isEnvTruthy(process.env.CLAUDE_CODE_USE_VERTEX)) {
        const [{ AnthropicVertex }, { GoogleAuth }] = await Promise.all([
            import('@anthropic-ai/vertex-sdk'),
            import('google-auth-library'),
        ])
        return new AnthropicVertex({ ...ARGS, region, googleAuth, ... }) as unknown as Anthropic
    }

    // Direct API（默认）
    return new Anthropic({
        apiKey: isClaudeAISubscriber() ? null : apiKey || getAnthropicApiKey(),
        authToken: isClaudeAISubscriber() ? getClaudeAIOAuthTokens()?.accessToken : undefined,
        ...ARGS,
    })
}
```

**关键设计**：
- 所有返回类型都被 `as unknown as Anthropic` 强制转换，因为 Bedrock/Vertex/Foundry SDK 的类型不完全兼容
- `maxRetries: 0` 用于流式请求（Claude Code 自己管理重试逻辑）
- `buildFetch()` 包装了 `fetch`，注入 `x-client-request-id` 头用于超时场景的服务端日志关联
- 600 秒（10 分钟）的默认超时，与 API 的非流式边界对齐

## 7. 架构设计思想

### 7.1 生成器驱动的流式架构

Claude Code 的 API 通信层大量使用 `AsyncGenerator`，这不是偶然的选择：

**优势 1：背压控制**。`for await...of` 天然支持背压——如果消费者（如终端渲染）处理速度慢于生产者（API 流），JavaScript 运行时会自动暂停流的消费。

**优势 2：组合性**。生成器可以嵌套组合：
```
queryModel() → withStreamingVCR() → queryModelWithStreaming() → callModel()
```
每一层都可以拦截、转换或缓存事件，而不需要回调地狱。

**优势 3：可中断性**。通过 `generator.return()` 可以在任意点终止流，触发 `finally` 块进行资源清理（如 `releaseStreamResources()`）。

### 7.2 分层重试策略

Claude Code 实现了三层重试：

1. **SDK 层重试**（已禁用）：`maxRetries: 0`，不使用 SDK 内置重试
2. **withRetry 重试**：最多 10 次，指数退避，处理 429/529/5xx
3. **流式降级重试**：流式失败后降级到非流式，独立的超时和重试

这种分层设计确保了：
- 流式和非流式使用独立的重试预算
- 529 连续计数跨流式和非流式保持一致
- OAuth token 刷新在重试循环中自动处理

### 7.3 Beta Header 的 Latch 机制

Claude Code 使用"latch"（锁存）策略管理动态 beta 头：

```typescript
// 一旦首次发送，后续所有请求都包含该头
let fastModeHeaderLatched = getFastModeHeaderLatched() === true
if (!fastModeHeaderLatched && isFastMode) {
    fastModeHeaderLatched = true
    setFastModeHeaderLatched(true)
}
```

**为什么需要 latch？** 服务端的 prompt cache key 包含 beta 头集合。如果某个请求不包含之前发送过的 beta 头，会导致服务端缓存失效（~50-70K tokens 的缓存丢失）。通过 latch，一旦某个 beta 头被激活，后续所有请求都包含它，保持缓存 key 稳定。

### 7.4 Raw Stream vs BetaMessageStream

这是一个深思熟虑的架构决策。Claude Code 选择使用 `anthropic.beta.messages.create({stream: true}).withResponse()` 返回的 raw `Stream`，而非 SDK 提供的 `BetaMessageStream`：

**BetaMessageStream 的问题**：
- 每个 `input_json_delta` 事件都调用 `partialParse()`（增量 JSON 解析）
- 对于大型 tool input（如长文件内容），这导致 O(n²) 的性能
- SDK 内部的 `partialMessage` 对象会被 mutation，Claude Code 需要不可变的状态

**Raw Stream 的优势**：
- 直接返回 `BetaRawMessageStreamEvent`，无额外解析
- Claude Code 自行累积 `contentBlocks[]` 数组
- Tool input 以字符串形式累积，最终一次性 `JSON.parse()`
- `content_block_stop` 时创建不可变的 `AssistantMessage`

## 8. 工程实践细节

### 8.1 资源泄漏防护

流式连接涉及原生 TLS/socket 缓冲区，位于 V8 堆外。Claude Code 在多个层面防止泄漏：

```typescript
// 1. finally 块中释放
finally {
    stopSessionActivity('api_call')
    releaseStreamResources()
}

// 2. releaseStreamResources 函数
function releaseStreamResources(): void {
    cleanupStream(stream)         // 中止 stream controller
    stream = undefined
    if (streamResponse) {
        streamResponse.body?.cancel().catch(() => {})  // 释放 HTTP body
        streamResponse = undefined
    }
}

// 3. cleanupStream 函数
export function cleanupStream(stream): void {
    if (stream && !stream.controller.signal.aborted) {
        stream.controller.abort()
    }
}
```

代码注释引用了 GH #32920 作为这个设计的背景——一个真实的内存泄漏 bug。

### 8.2 流式 Stall 检测

```typescript
let lastEventTime: number | null = null
const STALL_THRESHOLD_MS = 30_000  // 30 秒
let totalStallTime = 0
let stallCount = 0

for await (const part of stream) {
    const now = Date.now()
    if (lastEventTime !== null) {
        const timeSinceLastEvent = now - lastEventTime
        if (timeSinceLastEvent > STALL_THRESHOLD_MS) {
            stallCount++
            totalStallTime += timeSinceLastEvent
            logEvent('tengu_streaming_stall', { stall_duration_ms: timeSinceLastEvent, ... })
        }
    }
    lastEventTime = now
    // ...
}
```

Stall 检测与看门狗互补：看门狗处理"完全无事件"的情况，stall 检测处理"事件间隔过大"的情况。

### 8.3 Gateway 检测

Claude Code 会自动检测请求是否经过 AI 网关：

```typescript
const GATEWAY_FINGERPRINTS = {
    litellm: { prefixes: ['x-litellm-'] },
    helicone: { prefixes: ['helicone-'] },
    portkey: { prefixes: ['x-portkey-'] },
    'cloudflare-ai-gateway': { prefixes: ['cf-aig-'] },
    kong: { prefixes: ['x-kong-'] },
    braintrust: { prefixes: ['x-bt-'] },
}

const GATEWAY_HOST_SUFFIXES = {
    databricks: ['.cloud.databricks.com', '.azuredatabricks.net', '.gcp.databricks.com'],
}
```

检测结果用于日志和分析，帮助诊断"为什么我的 API 调用变慢了"——可能是因为经过了额外的网关层。

### 8.4 Client Request ID

```typescript
// client.ts
function buildFetch(fetchOverride, source): ClientOptions['fetch'] {
    const injectClientRequestId =
        getAPIProvider() === 'firstParty' && isFirstPartyAnthropicBaseUrl()

    return (input, init) => {
        const headers = new Headers(init?.headers)
        if (injectClientRequestId && !headers.has(CLIENT_REQUEST_ID_HEADER)) {
            headers.set(CLIENT_REQUEST_ID_HEADER, randomUUID())
        }
        return inner(input, { ...init, headers })
    }
}
```

`x-client-request-id` 头解决了一个实际问题：当请求超时时，服务端不会返回 `request_id`，但 Claude Code 需要将客户端的超时与服务端日志关联。通过在客户端生成 UUID 并发送到服务端，技术支持可以通过这个 ID 在服务端日志中查找对应的请求。

### 8.5 Fast Mode 的精细控制

Fast mode（快速模式）是一个有趣的功能，它使用更快但可能更贵的模型变体：

```typescript
// Fast mode 降级策略
if (wasFastModeActive && is429or529) {
    const overageReason = error.headers?.get('anthropic-ratelimit-unified-overage-disabled-reason')
    if (overageReason) {
        handleFastModeOverageRejection(overageReason)  // 永久禁用
        retryContext.fastMode = false
        continue
    }

    const retryAfterMs = getRetryAfterMs(error)
    if (retryAfterMs !== null && retryAfterMs < 20_000) {
        await sleep(retryAfterMs, signal)  // 短等待，保持 fast mode
        continue
    }

    // 长等待：进入 cooldown（切换到标准速度模型）
    const cooldownMs = Math.max(retryAfterMs ?? 30 * 60 * 1000, 10 * 60 * 1000)
    triggerFastModeCooldown(Date.now() + cooldownMs, cooldownReason)
    retryContext.fastMode = false
    continue
}
```

**Fast mode 的三层降级**：
1. 短限流（<20 秒）：等待后重试，保持 fast mode
2. 长限流：进入 10-30 分钟 cooldown，切换到标准速度
3. Overage 拒绝：永久禁用 fast mode

## 9. 初学者易错点

### 9.1 误解流式事件的"累积"语义

**易错**：认为每个 `content_block_delta` 的 `text` 字段是完整的文本。

**正确**：`text` 只是增量片段，需要手动拼接到 `contentBlocks[index].text` 上。最终的完整文本是所有 delta 的拼接结果。

### 9.2 忽略 message_delta 的 usage 回写

**易错**：在 `content_block_stop` 时就认为 usage 已经完整。

**正确**：`content_block_stop` 时的 `partialMessage.usage` 是 `message_start` 时的值（只有 input tokens）。`output_tokens` 和 `stop_reason` 要到 `message_delta` 才能获取。Claude Code 通过直接 mutation `lastMsg.message.usage` 来回写。

### 9.3 混淆 `withRetry` 的 yield 语义

**易错**：认为 `withRetry` yield 的值是 API 响应。

**正确**：`withRetry` 是一个 `AsyncGenerator<SystemAPIErrorMessage, T>`。它 yield 的是 `SystemAPIErrorMessage`（重试状态消息），最终 return 的才是 API 响应 `T`。消费时需要区分：

```typescript
let e
do {
    e = await generator.next()
    if (!e.done && e.value.type === 'system') {
        yield e.value  // 重试状态消息，转发展示
    }
} while (!e.done)
// e.value 是最终的 BetaMessage
```

### 9.4 误解 cache_control 标记的位置

**易错**：在每条消息上都添加 `cache_control`。

**正确**：Anthropic API 要求每个请求只有一个 `cache_control` 标记（在最后一条需要缓存的消息上）。多个标记会导致 400 错误。Claude Code 通过 `markerIndex` 确保只有一个标记。

### 9.5 忽略 AbortSignal 的传播

**易错**：只在初始请求上传递 `signal`，忘记在重试和降级路径上传递。

**正确**：`signal` 需要在以下所有路径上传递：
- `anthropic.beta.messages.create({ signal })`
- `await sleep(delayMs, signal, { abortError })`
- `anthropic.beta.messages.create({ signal, timeout })`

Claude Code 通过在 `withRetry` 的每次迭代开始检查 `signal.aborted`，以及在 `sleep` 中监听 abort 事件来确保用户可以随时取消。

### 9.6 误解 streamIdleAborted 的处理

**易错**：在看门狗超时后直接抛出错误。

**正确**：Claude Code 设置 `streamIdleAborted = true` 并释放资源，但不立即抛错。循环自然退出后检查标志，触发非流式降级。这是因为连接可能只是暂时卡住，非流式请求可能成功。

## 10. 本章总结

Claude Code 的 Streaming 与 API 通信层展示了一个生产级 LLM 客户端的完整工程实践：

**核心架构**：
- 生成器驱动的流式架构，支持背压、组合和中断
- 分层重试策略（SDK 层禁用、withRetry 层手动、流式降级层）
- Raw stream 而非 BetaMessageStream，避免 O(n²) 的 JSON 解析

**弹性设计**：
- 指数退避 + 随机抖动的重试策略
- 529 连续计数与模型降级（Opus → Sonnet）
- 流式 → 非流式的自动降级
- Fast mode 的三层降级策略
- 持久重试模式（无人值守会话）
- OAuth token 自动刷新

**性能优化**：
- Tool input 以字符串累积，最终一次性 JSON 解析
- Beta header latch 保持服务端缓存稳定
- Prompt cache 断点的精确控制
- Cache editing 机制主动删除无用的 KV 缓存页

**可观测性**：
- 结构化日志（tengu_api_query、tengu_api_success、tengu_api_error）
- Gateway 检测
- Stall 检测与看门狗
- Client request ID 用于超时场景的日志关联
- 成本追踪精确到毫美元

**资源管理**：
- 显式释放 TLS/socket 缓冲区
- `finally` 块确保清理
- `AbortSignal` 在所有异步路径上传播

## 11. 延伸思考

### 11.1 SSE vs WebSocket vs gRPC Streaming

Claude Code 使用 SSE（Server-Sent Events）作为流式传输协议。SSE 的优势是简单（基于 HTTP/1.1 的文本协议）、兼容性好（所有 HTTP 客户端都支持）。但它的局限性也很明显：

- 单向通信（服务端 → 客户端）
- 文本协议有解析开销
- HTTP/1.1 的连接数限制

如果未来需要支持双向流（如客户端实时调整参数），可能需要迁移到 WebSocket 或 gRPC streaming。Claude Code 的分层架构（raw stream 抽象在 `Stream<BetaRawMessageStreamEvent>` 之后）使得这种迁移相对容易。

### 11.2 自适应重试策略

当前的重试策略是"一刀切"的指数退避。更智能的策略可以：

- 根据历史 529 频率动态调整 `MAX_529_RETRIES`
- 使用服务端的 `anthropic-ratelimit-unified-reset` 头精确等待
- 根据当前会话的 token 使用速率预判限流
- 在多个 API provider 之间自动切换（Direct → Bedrock → Vertex）

### 11.3 流式工具执行的精确一次语义

当前的流式降级存在一个已知问题（代码中引用了 inc-4258）：

> 流式执行工具时，部分流已经启动了工具执行，然后非流式重试产生了相同的 tool_use，导致工具被执行两次。

Claude Code 通过 `CLAUDE_CODE_DISABLE_NONSTREAMING_FALLBACK` 标志提供了一个缓解方案，但这意味着放弃降级能力。真正的解决方案需要：
- 流式工具执行的幂等性保证
- 或者在降级前等待当前工具执行完成
- 或者使用 tool_use_id 进行精确去重

### 11.4 缓存编辑的未来

`cache_edits` 是一个前沿特性，允许客户端主动删除服务端 KV 缓存中的特定页面。这比被动等待缓存过期更精确。未来可能扩展到：

- 缓存优先级控制（哪些页面更重要）
- 跨会话缓存共享（团队共享的 system prompt）
- 缓存预热（提前发送高频使用的前缀）

### 11.5 多模型并行请求

当前的架构是单模型串行请求。如果未来支持"同时向多个模型发送请求，取最快响应"的场景，需要：

- 并行的流式消费
- 竞态条件处理（第一个完成的胜出，其余取消）
- 成本只计入实际使用的响应
- `streamRequestId` 的并行管理

这些都是 Claude Code 当前架构已经预留了扩展空间的方向——生成器的组合性和 AbortSignal 的传播使得并行化成为可能。
