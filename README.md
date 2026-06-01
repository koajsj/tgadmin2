# Telegram 入群验证机器人

一个可直接部署到云服务器的 Telegram 入群验证机器人，支持群内验证、OWNER 私聊运维、群监控、用户统计、日志中心和在线更新。

## 核心能力

- 新成员自动限制并发送私聊验证链接
- OWNER 私聊控制全局功能
- `/panel 仪表盘` 查看全量运维概览
- `/status 状态页` 查看分页系统状态
- `/groups 群列表` 和 `/group 群详情` 查看群监控
- `/update 更新机器人` 自动拉取代码、安装依赖、执行数据库迁移并准备重启
- 安全审计、权限控制、数据库迁移、定时清理

## 环境变量

复制 `.env.example` 为 `.env` 后修改：

```text
BOT_TOKEN=你的机器人 Token
OWNER_ID=1095020773
DB_PATH=./data/bot.sqlite3
VERIFY_TIMEOUT_SECONDS=600
EXPIRE_ACTION=kick
MAX_FAILED_ATTEMPTS=3
LOG_LEVEL=INFO
GROUP_MESSAGE_AUTO_DELETE_SECONDS=0
SCHEDULER_INTERVAL_SECONDS=30
SYSTEMD_SERVICE_NAME=tgadmin2
PM2_PROCESS_NAME=tgadmin2
REDIS_URL=
```

说明：

- `OWNER_ID` 是最高权限 OWNER
- `REDIS_URL` 可选，不配置时面板会显示“未配置”
- `SYSTEMD_SERVICE_NAME` 和 `PM2_PROCESS_NAME` 用于 `/update` 后的重启检测

## 快速部署

```powershell
copy .env.example .env
notepad .env
py -m venv .venv
.venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt
py -m bot.main
```

## 一键部署

Ubuntu / Debian 推荐直接执行：

```bash
bash scripts/setup_debian.sh
```

脚本会：

1. 安装 `git`、`python3`、`python3-venv`
2. 创建虚拟环境
3. 安装 Python 依赖
4. 写入 `.env`
5. 创建并启用 systemd 服务

## 云服务器部署

### Ubuntu / Debian

推荐使用上面的 `scripts/setup_debian.sh`。

如果你要手动部署：

```bash
git clone https://github.com/koajsj/tgadmin2.git
cd tgadmin2
cp .env.example .env
vim .env
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m bot.main
```

### CentOS / Rocky / AlmaLinux

先安装 `git`、`python3`、`python3-venv`，再执行同样的手动部署流程。

如果你更偏向容器化，直接看下面的 Docker 部署。

## Docker 部署

```bash
cp .env.example .env
docker compose up -d --build
```

容器部署时，`/update` 会根据运行环境自动选择可用的重启方式。

## OWNER 命令

这些命令都要求在私聊中由 `OWNER_ID` 对应账号执行。

- `/panel 仪表盘`：显示机器人、服务器、数据库、Redis、群和用户的综合概览
- `/status 状态页`：分页查看服务器、机器人、验证、数据库和日志状态
- `/groups 群列表`：分页查看群监控列表
- `/group 群详情`：查看单个群的详细状态，参数为群 ID
- `/update 更新机器人`：先发送一次获取确认码，再重复发送同一命令完成更新

群内管理员命令：

- `/status 查看验证状态`
- `/enable 开启验证`
- `/disable 关闭验证`
- `/set_timeout 设置验证超时`
- `/set_autodelete 设置自动删消息`
- `/resend 重新发送验证链接`

## `/update` 更新流程

`/update` 会执行：

1. 拉取最新 Git 代码
2. 安装 `requirements.txt`
3. 执行数据库迁移
4. 检测 systemd / Docker / PM2 并准备重启

使用方式：

1. 在私聊里发送 `/update 更新机器人`
2. 复制机器人返回的确认码
3. 再发送一次 `/update <确认码>`

## 数据库与维护

- 程序启动时自动执行数据库迁移
- 调度器会定时清理过期验证记录和旧审计日志
- OWNER 面板会显示数据库健康检查结果

## 常见问题

### 为什么机器人不直接放行新成员？

这是验证流程的默认行为。新成员先被限制，完成私聊验证后自动解除限制。

### 为什么 `/start` 没反应？

请先从群里的验证链接进入私聊，再发送 `/start`。

### 为什么看不到 Redis 状态？

如果没有配置 `REDIS_URL`，面板会显示“未配置”。

## 本地运行

```bash
python -m bot.main
```

## 说明

- 验证、统计、日志和群监控都在本地 SQLite 中完成
- `/update` 是主要的远程运维入口
- OWNER 无需登录服务器即可完成大部分运维操作
