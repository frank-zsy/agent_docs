# 本地开发

## 版本与目录

- 前端：Node.js 20.19+、22.13+ 或 24+，npm，目录 `../frontend`。
- 后端：Python 3.12+、uv、just，目录 `../backend`。
- 默认前端地址：`http://localhost:5173`。
- 默认后端地址：`http://127.0.0.1:8000`。
- 默认 API 地址：`http://localhost:8000/api/v1`。

前后端是独立 Git 仓库，提交、分支和工作区状态需要分别检查。

## 首次准备

后端：

```bash
cd ../backend
uv sync --frozen
cp .env.example .env
uv run manage.py migrate
```

前端：

```bash
cd ../frontend
nvm use
npm ci
```

`.env` 和 `.env.local` 不得提交。完整后端变量及说明以 `backend/.env.example` 为准。

## 启动

分别打开两个终端：

```bash
cd backend
just run
```

```bash
cd frontend
npm run dev
```

需要处理异步数据库任务时再启动：

```bash
cd backend
just worker
```

## 提交前验证

前端：

```bash
npm run lint
npm run typecheck
npm run test
npm run build
```

后端常规变更：

```bash
uv run ruff check .
uv run manage.py makemigrations --check --dry-run
uv run manage.py test --parallel
```

后端完整覆盖率门禁使用 `just test`，它还会执行 E2E/coverage 步骤，耗时明显更长。

## 常见故障

- ESLint 出现 `util.styleText is not a function`：当前 Node 版本太旧，执行 `nvm use` 后重试。
- Vitest/jsdom 出现 CommonJS/ESM 加载错误：确认 Node 满足 `package.json.engines`，然后使用 lockfile 重新安装依赖。
- 前端页面能打开但 API 失败：确认后端已启动，并检查 `VITE_API_BASE_URL`。
- OAuth 登录失败：本地供应商 key/secret、callback URL 和 `FRONTEND_APP_URL` 必须匹配。
- Insight 局部无数据：分别检查 OpenDigger 公共数据源和后端 ClickHouse 配置，不要先假定核心 API 故障。

## 变更归属

- 纯 UI、路由、交互：前端仓库。
- 数据约束、权限、余额、外部集成：后端仓库。
- API 字段变化：先更新后端 Schema/测试，再更新前端类型与调用，并同步契约说明。
- 跨端业务规则变化：两个仓库分别提交，同时更新本目录对应文档。
