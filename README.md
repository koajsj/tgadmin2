# Telegram 入群验证机器人

这是一个可以直接部署到云服务器长期运行的 Telegram 群验证机器人。

核心流程：

- 新用户进群后先自动禁言
- 机器人在群里发送私聊验证链接
- 用户在私聊里完成动态文字验证
- 验证通过后自动解除禁言
- 超时未完成则默认踢出群
- 连续输错超过阈值也会被移出群

项目使用：

- Python
- aiogram
- SQLite
- systemd

## 目录结构

```text
bot/
  handlers/      群事件、私聊验证、管理命令
  services/      验证逻辑、成员权限、超时扫描、审计
  storage/       SQLite 读写
  utils/         通用工具
  config.py      配置读取
  db.py          数据库初始化
  main.py        启动入口
scripts/
  setup_debian.sh   首次部署
  update_debian.sh  更新部署
tests/              基础测试
```

## 配置

程序会自动读取项目根目录的 `.env` 文件。

支持的环境变量：

- `BOT_TOKEN`
- `DB_PATH`
- `VERIFY_TIMEOUT_SECONDS`
- `EXPIRE_ACTION`
- `MAX_FAILED_ATTEMPTS`
- `LOG_LEVEL`
- `GROUP_MESSAGE_AUTO_DELETE_SECONDS`
- `SCHEDULER_INTERVAL_SECONDS`

说明：

- `GROUP_MESSAGE_AUTO_DELETE_SECONDS` 是新群的默认自动删消息时间
- 每个群都可以再用 `/set_autodelete <seconds>` 单独覆盖
- `MAX_FAILED_ATTEMPTS` 是单个验证任务允许的最大错误次数
- 设为 `0` 的只有自动删除时间，失败次数最小值是 `1`

示例配置见 [.env.example](/C:/Users/Administrator/Desktop/%E7%BE%A4%E7%AE%A1%E7%90%86%E6%9C%BA%E5%99%A8%E4%BA%BA/.env.example)。

## 本地运行

```powershell
copy .env.example .env
notepad .env
py -m venv .venv
.venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt
py -m bot.main
```

## 服务器部署

你只需要准备：

- 一台 Debian 或 Ubuntu 服务器
- 一个 Telegram bot token

### 1. 登录服务器

```bash
ssh 用户名@服务器IP
```

### 2. 拉取代码

```bash
git clone https://github.com/koajsj/tgadmin2.git && cd tgadmin2
```

### 3. 一键部署

直接执行：

```bash
bash scripts/setup_debian.sh
```

脚本运行后只会让你输入一次 `BOT_TOKEN`，其他内容都会自动处理。

这条命令会自动完成：

1. 安装 Python、git、venv
2. 创建虚拟环境
3. 安装依赖
4. 自动写入 `.env`
5. 自动创建 systemd 服务
6. 自动启动机器人
7. 自动设置开机自启

### 4. 查看运行状态

```bash
sudo systemctl status tgadmin2
```

查看日志：

```bash
sudo journalctl -u tgadmin2 -f
```

## 以后更新

服务器进入项目目录后执行：

```bash
bash scripts/update_debian.sh
```

它会自动：

1. 拉取最新代码
2. 更新 Python 依赖
3. 重启机器人服务

## 机器人需要的群权限

至少给这些管理员权限：

- `Restrict Members`
- `Ban Users`

如果你想让机器人删除自己在群里的提示消息，再额外给：

- `Delete Messages`

## 管理命令

这些命令只能群管理员使用：

- `/status`
- `/enable`
- `/disable`
- `/set_timeout <seconds>`
- `/set_autodelete <seconds>`
- `/resend <user_id>`
- `/help`

说明：

- `/set_autodelete 0` 表示关闭群内机器人消息自动删除
- `/set_autodelete 30` 表示机器人在群里发出的消息 30 秒后自动删除
- `/resend` 也可以直接回复目标用户消息后使用

## Docker

如果你更喜欢 Docker，也可以这样启动：

```bash
cp .env.example .env
docker compose up -d --build
```

默认仍然推荐 `scripts/setup_debian.sh`，部署更直接。

## 数据库

程序启动时会自动创建这些表：

- `group_settings`
- `verification_challenges`
- `audit_logs`

默认数据库路径：

```text
./data/bot.sqlite3
```

## 常见问题

### 机器人为什么不私聊我

Telegram 机器人不能主动先私聊用户。你必须先点击群里的验证链接进入私聊。

### 我输入了 `/start` 但没反应

现在机器人会明确提示你：请从群里的验证链接进入。单独私聊 `/start` 不知道要验证哪个群。

### 为什么验证通过了还是不能发言

通常是机器人没有足够的管理员权限，或者群本身权限设置比较特殊。先检查是否给了 `Restrict Members` 和 `Ban Users`。

### 自动删消息为什么没生效

先确认机器人有 `Delete Messages` 权限，再确认当前群没有把 `/set_autodelete` 设成 `0`。

## 说明

- 不依赖 AI
- 不依赖第三方在线验证服务
- 所有逻辑都在本地完成
- 适合直接部署到 VPS 长期运行
