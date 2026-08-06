# Token 管理页面

在现有 Calton React 前端中新增一个 API Token 管理页面（`/tokens`），支持列出、创建和删除 API Token。后端完全不需要改动。

## 路由与入口

| 项目 | 内容 |
|---|---|
| 路由 | `/tokens`，在 `routes.tsx` 中注册 |
| 页面组件 | `TokensPage`，放在 `routes/TokensPage.tsx` |
| 侧边栏 | `Sidebar.tsx` 的 `NAV` 数组新增一项，`labelKey: 'user.apiTokens.title'`，图标 `KeyRound`（lucide-react） |
| 权限 | 走 `RequireAuth`（JWT 认证），与所有业务页面一致 |

## API 层（新增 `web-react/src/api/tokens.ts`）

三个接口方法，直接调用 `apiClient`（复用 `client.ts` 的 Bearer 注入和错误处理）：

| 操作 | 方法 | 端点 |
|---|---|---|
| 列表 | `getTokens(params)` → `client.get<APIToken[]>('/tokens', { query })` | `GET /tokens` |
| 创建 | `createToken(body)` → `client.put<APIToken>('/tokens', body)` | `PUT /tokens` |
| 删除 | `deleteToken(id)` → `client.delete('/tokens/{tokenID}', { tokenID: id })` | `DELETE /tokens/{tokenID}` |

返回的 `APIToken` 类型直接引用 `generated.ts` 已有的 `components["schemas"]["models.APIToken"]`。

**关键：创建接口的响应包含 `token` 字段（明文），列表接口不包含。请求体需要 `title`、`permissions`、`expires_at` 三个必填字段。**

## Query Hooks（新增 `web-react/src/features/tokens/queries.ts`）

```typescript
useTokens()         → useQuery   GET /tokens
useCreateToken()    → useMutation PUT /tokens, 成功后 invalidate useTokens
useDeleteToken()    → useMutation DELETE /tokens/{id}, 成功后 invalidate useTokens
```

## 页面文件清单

### 新增文件

| 文件 | 内容 |
|---|---|
| `web-react/src/api/tokens.ts` | API 调用封装 |
| `web-react/src/features/tokens/queries.ts` | React Query hooks |
| `web-react/src/routes/TokensPage.tsx` | 主页面组件 |
| `web-react/src/routes/TokensPage.test.tsx` | 页面测试 |
| `web-react/src/components/tokens/CreateTokenDialog.tsx` | 创建 Token 弹窗（表单） |
| `web-react/src/components/tokens/TokenCreatedDialog.tsx` | 创建成功后展示明文 |
| `web-react/src/components/tokens/DeleteTokenDialog.tsx` | 删除确认弹窗 |
| `web-react/src/components/tokens/PermissionPicker.tsx` | 权限组选择器 |

### 修改文件

| 文件 | 改动 |
|---|---|
| `web-react/src/app/routes.tsx` | 导入 `TokensPage`，添加 `{ path: '/tokens', element: <TokensPage /> }` |
| `web-react/src/components/layout/Sidebar.tsx` | 导入 `KeyRound`，NAV 新增 `{ to: '/tokens', labelKey: 'user.apiTokens.title', icon: KeyRound }` |
| `web-react/src/test/handlers.ts` | 新增 `/tokens` 默认 handler，避免无关测试报错 |

## 页面结构

### TokensPage（主页面）

```
section.p-6
├── div.flex.items-center.justify-between
│   ├── h1 "API Token"
│   │   └── p "API 令牌允许您在无需用户凭据的情况下使用 Calton 的 API。"
│   └── Button [新建 Token]
│
├── pending → "加载中…"
├── error   → error message（红色）
├── 空态    → KeyRound 图标 + "还没有 API Token" + 引导文案 + 快捷创建按钮
└── ul.token-list
    └── li（每行）
        ├── KeyRound icon
        ├── div 左侧：标题 / 权限摘要 / 创建时间
        ├── div 中间：过期状态标签
        │   ├── 未过期 → "N 天后过期"（绿色）
        │   ├── 7天内  → "N 天后过期"（橙色）
        │   ├── 已过期 → "已过期 N 天"（红色）
        │   └── 永不过期 → "永不过期"（灰色）
        └── button [删除]（红色悬停）
```

### CreateTokenDialog

```
Dialog
├── h2 "新建 API Token"
├── form
│   ├── Field label="标题" → Input (required, upstream key user.apiTokens.titleRequired 校验)
│   ├── Field label="过期时间" → Button group (30天 / 60天 / 90天 / 自定义) + date input
│   ├── Field label="权限" → PermissionPicker
│   └── div.flex.justify-end.gap-2
│       ├── Button variant="outline" [取消]
│       └── Button type="submit" [创建] (disabled when pending)
```

### TokenCreatedDialog（创建成功后弹出）

```
Dialog（禁止外部关闭，禁止 Esc 关闭）
├── div.text-center
│   ├── KeyRound icon（大号，蓝色）
│   └── h2 "这是您的令牌: {token}"
├── ⚠️ "将其存储在一个安全的位置，你不会再看到它了！"（橙色）
├── div.flex.gap-2
│   ├── Input (readOnly, value=token明文, font-mono, 点击全选)
│   └── Button [复制] → 成功后变"已复制"（绿色），2s 恢复
├── "请立即复制并妥善保管。关闭此窗口后将无法再次查看完整的 Token。"
└── Button [我已复制，关闭]
```

### DeleteTokenDialog

```
Dialog
├── h2 "删除该令牌"
├── p "你确定要删除令牌 {token} 吗？"
├── p "这将撤销使用它的所有应用程序或集成的访问权。您不能回退此操作。"
└── div.flex.justify-end.gap-2
    ├── Button variant="outline" [取消]
    └── Button variant="destructive" [删除]
```

### PermissionPicker

```
div.space-y-2
├── div 每组（9 个 group：tasks / projects / labels / buckets / teams / comments / notifications / saved_filters / relations）
│   ├── button 组名（点击展开/折叠，显示已选 action 数）
│   └── div.flex.flex-wrap.gap-3（展开时显示）
│       └── label.checkbox + action 名（如 read_all / create / update / delete）
└── (error 提示)
```

默认展开 `tasks` 和 `projects` 两组。

权限组的来源：目前硬编码 9 个已知的 permission group。选择至少一个 action 才能提交。

## 注意事项

1. **Token 明文只出现一次** — `TokenCreatedDialog` 创建后必须弹，禁止外部点击关闭，禁止 Esc 关闭，用户必须主动点"我已复制"才能关闭
2. **权限至少选一个** — 创建表单校验要求至少勾选一个权限组的一个 action，否则提交不可用
3. **过期时间验证** — 自定义日期不能是过去的时间
4. **删除不可恢复** — 删除确认文案需明确提示后果
5. **i18n 文案** — 直接引用上游已有的 `user.apiTokens.*` key 体系（en/zh-CN 均有翻译）
