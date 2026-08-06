# Calton Web (React)

Calton 的新 Web 前端。现有 `frontend/`（Vue）保持原样，本目录是 Phase 1 的新实现。

## 环境

Node ≥ 20（见 `.nvmrc`）。

```bash
npm install
npm run dev          # http://localhost:5173，/api 代理到 VITE_API_PROXY_TARGET（默认 localhost:3456）
npm run test         # vitest
npm run lint         # eslint
npm run format       # prettier --write（缩进跟随仓库根 .editorconfig：tab）
npm run build        # tsc --noEmit && vite build
```

## 技术栈

Vite 6 + TypeScript · React Router v7（data mode）· TanStack Query v5（服务端状态）·
Zustand（仅 UI 状态）· Tailwind 3 + shadcn/ui（Radix）。

## 目录

```
src/
  app/         providers（Query + 主题同步）、路由表、queryClient
  components/
    layout/    AppShell / Sidebar / TopBar / AuthLayout
    ui/        shadcn 生成的组件
  design/      设计 token（tailwind.config.ts 的唯一来源）
  lib/         cn() 等工具
  routes/      页面（F01 阶段为占位）
  store/       Zustand UI store
  test/        vitest setup 与 renderApp 帮手
```

## 设计 token

`src/design/tokens.ts` 是唯一 token 来源，`tailwind.config.ts` 直接消费它。
`tokens.nexus.snapshot.json` 是从团队 Nexus 项目抽出的基线快照，
`src/design/tokens.test.ts` 断言 `tailwind.config.ts` 的 `theme.extend`
（colors / borderRadius / fontFamily / maxWidth）与快照逐键逐值相等。

**改 token 必须同步改快照**，并在 commit message 里写清为什么偏离 Nexus。

主题切换把 `.dark` 挂在 `<html>` 上 —— Radix 的 Portal 渲染到 `document.body`，
挂在内层 div 上对它不生效（Nexus 已踩过这个坑）。

## 与后端的约定

- API 字段是 **snake_case，前端直吃**，不做 camelCase 转换层。
- 时间零值是 `"0001-01-01T00:00:00Z"` 而不是 `null` —— 用 `src/lib/datetime.ts`
  的 `parseApiTime` / `toApiTime`，不要直接 `new Date(task.due_date)`。
- v1 的动词是反的：**PUT 新建、POST 全量替换更新**。`client.put()` / `client.post()`
  的注释里标了，改数据前先看一眼。

### API 类型生成

```bash
npm run gen:api      # → src/api/generated.ts（入库，勿手改）
```

契约来源按优先级找：`CALTON_SWAGGER` 环境变量 → `contract/calton-v1-swagger.json`（T06 产出，
最终以它为准）→ `pkg/swagger/swagger.json`（Go 版现产出，T06 落地前的临时来源，脚本会打警告）。
上游是 Swagger 2.0，中间过一道 `swagger2openapi` 转 3.0 再交给 `openapi-typescript`。

### client.ts

所有请求走 `src/api/client.ts`，页面不要直接 `fetch`。它负责：

- Bearer 注入（`anonymous: true` 的请求不带，登录/注册用）
- **401 刷新一次后重试；重试仍 401 直接登出，不再刷新** —— 写错就是无限刷新循环，
  这条有专门用例，且做过变异验证（把重试改成允许再刷新，测试会挂死而不是通过）
- 并发 401 单飞：多个请求同时 401 只刷新一次
- 分页头 → `{items, resultCount, totalPages}`；**头缺失直接报 `ContractViolationError`**，
  不静默算成 NaN（最常见成因是后端漏了 `Access-Control-Expose-Headers`）
- 错误体 → `CaltonError`（保留 `code` / `i18n_params` / `invalid_fields`；
  纯字符串 echo 错误没有 `code`，不给它补）

单测用 MSW（`src/test/msw.ts`），`onUnhandledRequest: 'error'` —— 忘了 mock 的请求当场红。

## 当前进度

F01（脚手架 + 布局骨架）、F02（类型生成链 + client.ts）完成。
`src/routes/` 下多数页面是占位，组件里标了负责的任务号。
