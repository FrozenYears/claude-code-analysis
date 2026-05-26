# Claude Code 源码深度剖析 — 完整撰写规划

## 总体结构

14 章，每章 11 节固定结构，预计总字数 15-20 万字。

## 章节依赖关系

```
00-阅读路线（独立）
01-整体架构（独立，全局鸟瞰）
  ↓
02-CLI入口与启动流程（依赖01）
03-QueryEngine核心（依赖02）
  ↓
04-Tool系统（依赖03）
05-Prompt系统（依赖03）
06-Context系统（依赖05）
  ↓
07-Agent系统（依赖04）
08-Session与状态管理（依赖03）
  ↓
09-Streaming与API通信（依赖03）
10-多Agent协作（依赖07）
  ↓
11-命令系统（依赖04）
12-UI组件系统（依赖02）
13-插件与扩展（依赖04）
14-安全与权限（依赖04）
```

## 每章 11 节模板

1. 本章目标
2. 前置知识
3. 宏观概览
4. 源码入口定位
5. 调用链分析
6. 核心源码解析
7. 架构设计思想
8. 工程实践细节
9. 初学者易错点
10. 本章总结
11. 延伸思考

## 撰写顺序

按依赖关系：
- 第一批：00, 01（已完成，需重写01）
- 第二批：02, 03, 05
- 第三批：04, 06, 07, 08, 09
- 第四批：10, 11, 12, 13, 14

## 关键源码文件清单

| 章节 | 核心文件 | 行数 |
|------|----------|------|
| 02-CLI | entrypoints/cli.tsx, main.tsx, setup.ts | 4684+478 |
| 03-QueryEngine | QueryEngine.ts, query.ts, query/*.ts | 1297+1730 |
| 04-Tool | Tool.ts, tools.ts, tools/*.ts | 794+390 |
| 05-Prompt | context.ts, constants/prompts.ts | 190+? |
| 06-Context | context/, utils/attachments.ts, utils/queryContext.ts | 3998 |
| 07-Agent | tools/AgentTool/, tasks/LocalAgentTask/ | 多文件 |
| 08-Session | state/, utils/sessionStorage.ts | 5106 |
| 09-Streaming | services/api/claude.ts, services/api/*.ts | 3420 |
| 10-多Agent | coordinator/, tools/AgentTool/forkSubagent.ts | 多文件 |
| 11-命令 | commands/, commands.ts | 754+101目录 |
| 12-UI | components/, screens/, ink/ | 144目录 |
| 13-插件 | plugins/, skills/, services/mcp/ | 3349 |
| 14-安全 | hooks/toolPermission/, utils/permissions/ | 多文件 |
