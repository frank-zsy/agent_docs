# OpenShare Agent Docs

本目录保存 OpenShare 的跨仓库开发上下文，主要供开发者和编码代理快速理解项目。

OpenShare 当前由两个独立 Git 仓库组成：

- `../frontend`：Vite、React、TypeScript 单页应用。
- `../backend`：Django、Django Ninja API、后台任务与管理后台。

建议按以下顺序阅读：

1. [ARCHITECTURE.md](ARCHITECTURE.md)：系统边界、模块职责和关键数据流。
2. [DOMAIN_RULES.md](DOMAIN_RULES.md)：修改业务代码时必须保持的规则。
3. [DEVELOPMENT.md](DEVELOPMENT.md)：本地启动、验证和常见故障。
4. [API_CONTRACT.md](API_CONTRACT.md)：前后端接口契约及维护方式。

## 审计记录

- [2026-10-21 积分与兑换安全审计](audits/2026-10-21-points-security/report.txt)：9 组问题的修复前证据、复现脚本及日志。单人兑换上限合入后已实现修复；当前验证结果和上线边界见[修复记录](audits/2026-10-21-points-security/fix-status.txt)。

## 维护原则

- 代码、数据库约束和自动化测试是最终事实来源；文档冲突时先验证代码。
- 架构边界、业务不变量或开发命令发生变化时，在同一变更中更新本文档。
- 不在这里复制完整 API 字段或环境变量清单，避免与自动生成内容漂移。
- 重要且长期有效的设计选择应补充到 `adr/`，一项决策一个文件。

## 当前工程状态

- 后端拥有完整的 Django 测试体系和覆盖率门禁。
- 前端拥有 lint、typecheck、Vitest 和生产构建检查，但业务流程测试仍需逐步补齐。
- Insight 同时依赖后端 API 和 OpenDigger 公共数据源；ClickHouse 属于后端可选分析依赖。
- `agent_docs` 是独立 Git 仓库，不隶属于前端或后端仓库。
