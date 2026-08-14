# 系统架构

## 总览

```text
Browser
  |
  |-- Vite/React SPA (frontend, :5173)
  |      |-- Django Ninja API (:8000/api/v1)
  |      `-- OpenDigger public datasets
  |
  `-- OAuth provider callbacks -> Django -> SPA social callback

Django (backend, :8000)
  |-- relational database (SQLite locally, PostgreSQL in production)
  |-- django-tasks database worker
  |-- Redis cache (optional)
  |-- ClickHouse analytics (optional)
  |-- S3-compatible media storage (optional)
  `-- Shenbianyun and mail providers (optional integrations)
```

前后端是两个独立仓库，通过 `/api/v1` 的 HTTP 契约协作。前端不应直接依赖后端 ORM 概念，后端也不应依赖具体页面布局。

## 前端边界

- `src/app/router.tsx`：公开、半公开和受保护路由的总入口。
- `src/contexts/auth-context.tsx`：当前用户和登录状态。
- `src/lib/api.ts`：后端 Axios 客户端、Bearer Token 注入和刷新重试。
- `src/pages/`：路由级页面。目前部分页面同时承担请求、状态和展示职责，新功能应优先拆出 API/domain hook 和可复用组件。
- `src/pages/insight/`：相对独立的洞察模块，包含 API、domain、components 和 types 分层。
- `src/i18n/locales/`：中英文文案。新增用户可见文本应同时更新两种语言。

## 后端边界

- `config/`：settings、URL、Ninja API 聚合及部署入口。
- `accounts/`：用户、资料、地址、组织、OAuth、JWT 和账户合并。
- `points/`：积分池、积分流水、分配、提现和相关管理命令。
- `shop/`：商品、兑换、库存、优惠码和退款协作。
- `talent_reach/`：触达草稿、预览、发送、奖励和活动历史。
- `messages/`：站内消息、已读状态及消息触发的奖励领取。
- `homepage/`：公共主页和搜索接口。
- `contributions/`：贡献数据同步与缓存。
- `chdb/`：ClickHouse 查询边界；调用者必须考虑服务不可用或数据缺失。
- `shenbianyun/`：外部提现/支付服务集成。

后端业务写操作应优先放在 `services.py` 或明确的领域服务中，由 API、Admin 和管理命令复用。涉及余额、库存、Token 轮换或重复请求的写操作必须明确事务和幂等策略。

## 关键数据流

### 社交登录与 API 鉴权

1. SPA 发起 OAuth 登录，Django/social-auth 处理供应商回调。
2. Django 生成短期交换码，SPA 在 `/social-callback` 换取 access/refresh token。
3. SPA 请求拦截器附加 access token。
4. access token 过期后，SPA 只发出一个共享刷新请求；等待中的原请求使用新 token 重放。
5. 后端轮换 refresh token，旧 token 立即失效，因此前端不得并发刷新同一个 token。

### 积分与兑换

1. 可用余额来自积分流水和积分池规则，前端展示值不能作为授权依据。
2. 分配、兑换、提现等写操作必须由后端重新校验余额、标签、权限和状态。
3. 任何余额变化都应有可审计记录；失败操作不得留下部分写入。

### 数据洞察

Insight 页面可能同时读取后端公开搜索接口和 OpenDigger 静态/公开数据。外部数据源失败不应破坏账户、积分等核心业务，UI 应提供独立的加载与错误状态。

## 演进方向

- 以 Django Ninja OpenAPI 作为前后端契约源，生成或校验 TypeScript 类型。
- 把超大前端页面逐步拆成 API hook、领域状态和展示组件。
- 对认证刷新、积分分配、商城兑换、账户合并和 Talent Reach 增加跨模块回归测试。
