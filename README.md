# Telegram 入群验证机器人

这是一个能直接丢到云服务器上长期跑的 Telegram 机器人项目。

它做的事很简单：

- 新人进群，先自动禁言
- 群里提示他先私聊机器人
- 机器人私聊发一段动态文字题
- 用户按要求手动改完再发回来
- 验证对了，自动解除禁言
- 超时没验证，默认踢出群

技术栈：

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
  config.py      配置读取
  db.py          建表
  main.py        启动入口
scripts/
  setup_debian.sh   首次部署脚本
  update_debian.sh  更新脚本
tests/              基础测试
```

## 配置项

程序会自动读取项目根目录的 `.env` 文件。

主要配置：

- `BOT_TOKEN`
- `DB_PATH`
- `VERIFY_TIMEOUT_SECONDS`
- `EXPIRE_ACTION`
- `LOG_LEVEL`
- `GROUP_MESSAGE_AUTO_DELETE_SECONDS`
- `SCHEDULER_INTERVAL_SECONDS`

示例文件在 [`.env.example`](/C:/Users/Administrator/Desktop/%E7%BE%A4%E7%AE%A1%E7%90%86%E6%9C%BA%E5%99%A8%E4%BA%BA/.env.example)。

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

下面这段是最省事的方式。你只需要准备好一台 Debian/Ubuntu 服务器，和一个 bot token。

### 第一步：登录服务器

```bash
ssh 用户名@你的服务器IP
```

### 第二步：拉代码

直接复制这条：

```bash
git clone https://github.com/koajsj/tgadmin2.git && cd tgadmin2
```

### 第三步：一键部署

把下面命令里的 `你的BotToken` 换成你自己的，然后直接回车：

```bash
BOT_TOKEN='你的BotToken' bash scripts/setup_debian.sh
```

这条命令会自动帮你做这些事：

1. 安装 Python、git、venv
2. 创建虚拟环境
3. 安装依赖
4. 自动写好 `.env`
5. 自动创建 systemd 服务
6. 自动启动机器人
7. 设置开机自启

你除了 bot token 以外，不需要再手动输入别的配置。

### 第四步：看机器人是不是跑起来了

```bash
sudo systemctl status tgadmin2
```

看日志：

```bash
sudo journalctl -u tgadmin2 -f
```

## 以后怎么更新

以后你改完 GitHub 上的代码，只需要在服务器里进项目目录，然后执行：

```bash
bash scripts/update_debian.sh
```

它会自动：

1. `git pull`
2. 更新依赖
3. 重启机器人

## 如果你想手动改配置

配置文件就在项目根目录：

```bash
nano .env
```

改完后重启：

```bash
sudo systemctl restart tgadmin2
```

## 机器人需要什么管理员权限

把机器人拉进群以后，至少给它这些权限：

- Restrict Members
- Ban Users

如果你后面想删提示消息，再额外给：

- Delete Messages

## 管理命令

这些命令只能群管理员用：

- `/status`
- `/enable`
- `/disable`
- `/set_timeout <seconds>`
- `/resend <user_id>`
- `/help`

`/resend` 也可以直接回复某个用户的消息后再发，不一定非要手填 user_id。

## Docker

如果你更喜欢 Docker，也可以用：

```bash
cp .env.example .env
docker compose up -d --build
```

不过默认还是推荐用上面的 `setup_debian.sh`，因为更直接。

## 数据库表

程序启动时会自动创建：

- `group_settings`
- `verification_challenges`
- `audit_logs`

默认数据库文件路径：

```text
./data/bot.sqlite3
```

## 验证流程

1. 新人加入群
2. 机器人立刻禁言
3. 群里发一个私聊验证链接
4. 用户点进私聊
5. 机器人发动态文字题
6. 用户手动编辑后发回
7. 验证成功后自动解禁
8. 超时未完成则默认踢出群

## 说明

- 不依赖 AI
- 不依赖第三方在线验证服务
- 所有逻辑都在本地完成
- 适合直接跑在 VPS 上长期运行
