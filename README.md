# 会议纪要任务单系统

会议纪要、历史文件导入、任务派发与验证、统计导出，以及企业微信登录、应用消息和原生待办集成。

技术栈：Vue 3 / TypeScript / Element Plus / Vite + FastAPI / SQLAlchemy / PostgreSQL 16 / Celery / Redis 7。PDF 内嵌 Noto Sans SC 字体，许可证见 `backend/app/assets/fonts/OFL.txt`。

## 交接与开发进度

接手开发先读 [最新源码同步说明](docs/源码同步-2026-11-23.md)，再参考 [开发维护交接](docs/开发维护交接-2026-10-23.md) 中的代码导航、配置分工、开发启动和生产维护说明。

本次源码同步日期为 **2026-11-23**，最近已部署版本为 **`review-task-drag-20261123`**，当前源码数据库迁移头为 `20261110_custom_roles`。本次仅同步 Git 仓库；历史文档中的数量、开关和验收结论仅代表各自记录日期。

- [业务基线](开发计划.md)
- [开发进度与发布记录](docs/开发进度.md)
- [使用与运维](docs/使用与运维.md)
- [最近九项修复与验证](docs/Bug修复-2026-10-20.md)
- [需求验收矩阵](docs/需求验收矩阵.md)
- [企业微信长期接入方案](docs/企业微信待办长期接入方案-2026-10-13.md)

## 功能

- 会议系列、会议模板、草稿自动保存、预览、发布修订、上期回顾和接续新建。
- Excel / Word 历史导入、人员纠错、AI 辅助建议、人工确认入库与原件追溯。
- 直接派发、岗位与部门权限、多人共同任务、完成申请、验证、移交、撤回和周期任务。
- 会议编辑申请、版本追溯、上期任务拖动排序、PDF / Excel 导出与鉴权附件。
- 绩效统计、会议纪要统计（含中高层会议）和直接派发任务统计。
- 自定义角色、员工权限、操作日志、任务单导入与多人任务结果评定。
- 密码修改、企业微信工作台自动登录、浏览器扫码登录、通讯录同步及应用消息。
- 派发人确认创建企微原生待办、关联恢复、事务队列、独立监控、告警与备份恢复。

功能验证范围以最新源码同步说明和各日期发布记录为准；旧交接文档中的待验收事项需结合后续记录核对。

## Docker 启动

需要 Docker 和 Compose v2。在新环境复制配置；已有环境不覆盖 `.env`。

```bash
cp .env.example .env
mkdir -p storage monitor-state backups native-todo
```

本地 Docker 的 `.env` 设置 `PUBLIC_URL=http://localhost:8086`。Linux 需将持久目录交给容器用户（默认 UID/GID 10001）。生产另设独立 JWT、数据库密码及实际入口。然后运行：

```bash
docker compose -f docker-compose.yml -f docker-compose.app.yml up -d --build
# 仅首次创建管理员；随机密码写入 storage/local-admin-credentials.txt
docker compose -f docker-compose.yml -f docker-compose.app.yml exec backend \
  python -m app.cli bootstrap --generate-password
curl -f http://localhost:8086/api/health
```

访问 <http://localhost:8086>。首次管理员账号为 `admin`，密码为初始化时生成的随机值。七个服务为 backend、worker、beat、monitor、frontend、postgres、redis。同一环境只运行一个 beat。

## 本地开发

需要 Python 3.12+、uv、Node.js 22.12+（建议 24）及 npm。开发 `.env` 使用本地连接并保持外部服务关闭。

```bash
docker compose up -d postgres redis
cd backend
uv sync --frozen --python 3.12
uv run alembic upgrade head
uv run python -m app.cli bootstrap --generate-password
uv run uvicorn app.main:app --host 127.0.0.1 --port 8016
```

另开终端执行 `cd frontend && npm ci && npm run dev`，访问 <http://localhost:5176>；API 文档为 <http://127.0.0.1:8016/docs>。worker、beat、检查命令和隔离测试配置见交接文档。

## 源码交接包

在已提交且工作区干净的仓库中运行：

```bash
python3 scripts/package-handoff.py
```

压缩包及 SHA-256 校验文件生成在 `交接包/`。ZIP 包含与提交一致的源码、迁移、测试、文档、字体及许可证，并附提交信息和逐文件校验清单。实际 `.env`、密码、授权缓存、数据库、附件、原始业务文件和依赖目录不进入仓库或交接源码包；接管现有生产环境所需的私有配置与一致性数据备份由负责人另行移交。

Linux 部署用 tar 包可通过 `bash scripts/package-deploy.sh /tmp/meeting-actions-deploy.tar.gz` 生成。部署维护前先阅读运维文档，保留旧镜像与备份。
