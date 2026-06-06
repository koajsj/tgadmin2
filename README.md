# Telegram 群管理机器人

这是一个运行在 Telegram 的群管理机器人，主要用于入群验证、群参数控制和 OWNER 私聊运维。

## 功能

- 新人入群后先限制发言
- 发送私聊验证链接完成验证
- 验证通过后自动恢复权限
- 支持自动删除群内消息
- 支持 OWNER 在私聊里查看运行状态、更新代码、管理群参数
- 支持在机器人还没进群时，先在私聊里预配置群参数

## 快速部署

推荐在 Debian 11 / Debian 12 上部署。

### 1. 准备环境

需要：

- 一台 Debian 服务器
- 一个 Telegram Bot Token
- 把机器人拉进目标群，并赋予足够权限

机器人在群里至少需要这些权限：

- 限制成员
- 封禁成员
- 删除消息

如果你想让机器人自动解除限制，最好也给它完整的管理权限。

### 2. 克隆并安装

```bash
git clone https://github.com/koajsj/tgadmin2.git
cd tgadmin2
bash scripts/setup_debian.sh
```

脚本会完成这些事：

- 安装 `git`、`python3`、`python3-venv`
- 创建虚拟环境
- 安装 Python 依赖
- 生成 `.env`
- 创建并启动 systemd 服务

执行时会提示输入 `BOT_TOKEN`。

### 3. 检查服务

```bash
sudo systemctl status tgadmin2
sudo journalctl -u tgadmin2 -f
```

如果状态显示 `active (running)`，说明机器人已经正常启动。

## 配置项

`.env` 会由安装脚本自动生成，你也可以手动调整：

```env
BOT_TOKEN=你的 Telegram Bot Token
OWNER_ID=1095020773
DB_PATH=./data/bot.sqlite3
VERIFY_TIMEOUT_SECONDS=600
EXPIRE_ACTION=kick
MAX_FAILED_ATTEMPTS=3
LOG_LEVEL=INFO
GROUP_MESSAGE_AUTO_DELETE_SECONDS=0
SCHEDULER_INTERVAL_SECONDS=30
SYSTEMD_SERVICE_NAME=tgadmin2
REDIS_URL=
```

重点配置：

- `BOT_TOKEN`：机器人令牌，必填
- `OWNER_ID`：OWNER 的 Telegram 用户 ID
- `DB_PATH`：SQLite 数据库路径
- `VERIFY_TIMEOUT_SECONDS`：验证超时时间
- `EXPIRE_ACTION`：验证超时后的处理方式，`kick` 或 `restrict`
- `GROUP_MESSAGE_AUTO_DELETE_SECONDS`：群消息自动删除时间
- `SYSTEMD_SERVICE_NAME`：systemd 服务名

`REDIS_URL` 为空也可以运行。

## 更新

### 服务器上更新

```bash
bash scripts/update_debian.sh
```

这个脚本会：

1. `git pull --ff-only`
2. 更新 Python 依赖
3. 重启 systemd 服务

### 在 Telegram 私聊里更新

OWNER 可以直接私聊机器人执行：

```text
/update
```

机器人会先给出确认码，再执行更新，避免误操作。

## OWNER 私聊控制

OWNER 不需要把机器人拉进群，也能在私聊里管理运行状态和群参数。

### 常用命令

- `/panel`：打开主面板
- `/status`：查看系统状态
- `/groups`：查看已接入群
- `/group <chat_id>`：打开指定群配置页
- `/config`：查看可配置群列表
- `/config <chat_id> [备注]`：直接预配置群参数，支持先录入群 ID
- `/update`：更新机器人
- `/help`：查看帮助
- `/cancel`：取消当前输入模式

### 私聊里能调什么

在群配置详情页里，可以直接调整：

- 验证开关
- 验证超时
- 到期动作
- 自动删消息
- 群备注

这意味着你可以先输入群 ID 和参数，等机器人之后进群时自动按这套配置运行。

## 群内管理命令

群里原有的管理命令也可用：

- `/status`：查看群验证状态
- `/enable`：开启验证
- `/disable`：关闭验证
- `/set_timeout <秒数>`：设置验证超时
- `/set_autodelete <秒数>`：设置群消息自动删除时间
- `/resend <user_id>`：重发某个用户的验证链接

如果你在私聊里发这些命令，机器人会先让你选目标群，再执行对应操作。

## 入群验证流程

机器人在群里的行为大致如下：

1. 新成员入群
2. 机器人先限制发言
3. 机器人给用户发送私聊验证链接
4. 用户去私聊完成验证
5. 验证通过后恢复发言权限

如果用户还没通过验证，机器人会按当前群配置处理超时和失败情况。

## 常见问题

### 机器人没有响应

优先检查这些项：

- `BOT_TOKEN` 是否正确
- 机器人是否已经在群里
- 机器人是否有管理权限
- systemd 服务是否已经启动

### 私聊里执行 `/start` 没反应

验证入口需要从群里的验证链接进入私聊，不是直接在私聊里手动输入普通 `/start`。

### 想先配置，但机器人还没进群

可以直接在 OWNER 私聊里使用：

```text
/config <chat_id> [备注]
```

这样会先把群配置落库，后续机器人进群后即可直接使用。

## 本地运行

如果你想在本地测试：

```bash
python -m bot.main
```

一般还是更推荐在 Debian 服务器上用 systemd 运行。

