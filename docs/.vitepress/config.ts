import { defineConfig } from 'vitepress'
import { withMermaid } from 'vitepress-plugin-mermaid'

export default withMermaid(
  defineConfig({
    title: 'Claude Code 源码剖析',
    description: 'Claude Code 源码深度解析 — 从架构到实现的全面剖析',
    lang: 'zh-CN',
    base: '/claude-code-analysis/',

    lastUpdated: true,
    cleanUrls: true,

    head: [
      ['link', { rel: 'icon', type: 'image/svg+xml', href: '/claude-code-analysis/logo.svg' }],
      ['meta', { name: 'theme-color', content: '#0E0E0E' }],
      ['meta', { property: 'og:type', content: 'website' }],
      ['meta', { property: 'og:title', content: 'Claude Code 源码剖析' }],
      ['meta', { property: 'og:description', content: 'Claude Code 源码深度解析 — 从架构到实现的全面剖析' }],
      ['meta', { name: 'twitter:card', content: 'summary' }],
      ['meta', { name: 'twitter:title', content: 'Claude Code 源码剖析' }],
    ],

    transformHead: ({ pageData }) => {
      const path = pageData?.relativePath?.replace(/index\.md$/, '').replace(/\.md$/, '') ?? ''
      const canonical = `https://frozenyears.github.io/claude-code-analysis${path}`
      return [
        ['link', { rel: 'canonical', href: canonical }],
      ]
    },

    sitemap: {
      hostname: 'https://frozenyears.github.io',
    },

    markdown: {
      theme: {
        light: 'github-light',
        dark: 'github-dark',
      },
      lineNumbers: false,
    },

    themeConfig: {
      logo: '/logo.svg',

      nav: [
        { text: '首页', link: '/' },
        { text: '阅读路线', link: '/reading-roadmap' },
        { text: '章节', link: '/chapters/01-architecture/' },
        {
          text: '相关资源',
          items: [
            { text: 'Claude Code 官方文档', link: 'https://docs.anthropic.com/en/docs/claude-code' },
            { text: 'GitHub 仓库', link: 'https://github.com/FrozenYears/claude-code-analysis' },
          ]
        }
      ],

      sidebar: {
        '/chapters/': [
          {
            text: '入门',
            collapsed: false,
            items: [
              { text: '阅读路线图', link: '/reading-roadmap' },
            ]
          },
          {
            text: '核心架构',
            collapsed: false,
            items: [
              { text: '01 整体架构', link: '/chapters/01-architecture/' },
              { text: '02 CLI 入口与启动流程', link: '/chapters/02-cli-entry/' },
              { text: '03 QueryEngine 核心', link: '/chapters/03-query-engine/' },
            ]
          },
          {
            text: '核心子系统',
            collapsed: false,
            items: [
              { text: '04 Agent 系统', link: '/chapters/04-agent-system/' },
              { text: '05 Tool 系统', link: '/chapters/05-tool-system/' },
              { text: '06 Prompt 系统', link: '/chapters/06-prompt-system/' },
              { text: '07 Context 系统', link: '/chapters/07-context-system/' },
            ]
          },
          {
            text: '高级机制',
            collapsed: false,
            items: [
              { text: '08 Session 与状态管理', link: '/chapters/08-session-state/' },
              { text: '09 Streaming 与 API 通信', link: '/chapters/09-streaming-api/' },
              { text: '10 多 Agent 协作', link: '/chapters/10-multi-agent/' },
            ]
          },
          {
            text: '支撑系统',
            collapsed: false,
            items: [
              { text: '11 命令系统', link: '/chapters/11-commands/' },
              { text: '12 UI 组件系统', link: '/chapters/12-ui-components/' },
              { text: '13 插件与扩展', link: '/chapters/13-plugins/' },
              { text: '14 安全与权限', link: '/chapters/14-security/' },
            ]
          }
        ]
      },

      socialLinks: [
        { icon: 'github', link: 'https://github.com/FrozenYears/claude-code-analysis' }
      ],

      footer: {
        message: '基于 Claude Code 源码的深度逆向工程分析',
        copyright: '© 2025-2026 Claude Code Analysis Project'
      },

      outline: {
        level: [2, 3],
        label: '本章目录'
      },

      lastUpdated: {
        text: '最后更新于'
      },

      docFooter: {
        prev: '上一章',
        next: '下一章'
      },

      returnToTopLabel: '回到顶部',
      sidebarMenuLabel: '菜单',
      darkModeSwitchLabel: '主题',

      search: {
        provider: 'local',
        options: {
          translations: {
            button: {
              buttonText: '搜索文档',
              buttonAriaLabel: '搜索文档'
            },
            modal: {
              noResultsText: '无法找到相关结果',
              resetButtonTitle: '清除查询条件',
              footer: {
                selectText: '选择',
                navigateText: '切换',
                closeText: '关闭'
              }
            }
          }
        }
      },

      editLink: {
        pattern: 'https://github.com/FrozenYears/claude-code-analysis/edit/main/docs/:path',
        text: '在 GitHub 上编辑此页面'
      }
    },

    mermaid: {
      theme: 'default'
    },

    mermaidPlugin: {
      class: 'mermaid'
    },

    vite: {
      build: {
        chunkSizeWarningLimit: 2000
      }
    }
  })
)
