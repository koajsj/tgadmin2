# Telegram 入群验证机器人

这是一个跑在 Telegram 里的入群验证机器人。

它的作用很简单：

- 新人进群后先被限制发言
- 机器人发一条私聊验证链接
- 用户去私聊机器人完成验证
- 验证通过后自动恢复发言
- OWNER 可以私聊机器人做运维和查看状态

---

## 先说你最关心的

如果你是第一次在云服务器上部署，记住下面两条就行：

1. 先把代码拉到 Debian 服务器上
2. 然后执行 `bash scripts/setup_debian.sh`

如果以后要更新，就执行：

```bash
bash scripts/update_debian.sh
```

---

## 1. 服务器要求

建议直接用 Debian 11 / 12。

你需要准备：

- 一台 Debian 云服务器
- 一个 Telegram Bot Token
- 机器人加到群里，并且给足权限

机器人至少需要这些群权限：

- 限制成员
- 封禁成员
- 删除消息

如果你想让机器人自动解除限制，最好也给它完整的管理权限。

---

## 2. 从 0 部署

下面是最省事的部署方式，顺序就是：

1. 先从 GitHub 拉代码
2. 再在服务器上部署

### 第一步：登录服务器

先 SSH 到你的 Debian 服务器。

```bash
ssh 用户名@服务器IP
```

### 第二步：从 GitHub 拉代码

```bash
git clone https://github.com/koajsj/tgadmin2.git
cd tgadmin2
```

### 第三步：直接一键部署

```bash
bash scripts/setup_debian.sh
```

脚本会帮你做这些事：

- 安装 `git`、`python3`、`python3-venv`
- 创建虚拟环境
- 安装 Python 依赖
- 生成 `.env`
- 创建 systemd 服务
- 启动机器人

执行的时候，它会让你输入 `BOT_TOKEN`。

### 第四步：确认机器人在跑

```bash
sudo systemctl status tgadmin2
```

看日志：

```bash
sudo journalctl -u tgadmin2 -f
```

如果状态里显示 `active (running)`，说明机器人已经起来了。

---

## 3. `.env` 怎么配

脚本会自动生成 `.env`，你也可以自己改。

关键配置如下：

```text
BOT_TOKEN=你的机器人Token
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

你最需要知道的只有几个：

- `BOT_TOKEN`：机器人 Token
- `OWNER_ID`：超级 OWNER，固定是 `1095020773`
- `DB_PATH`：数据库文件路径
- `SYSTEMD_SERVICE_NAME`：systemd 服务名，默认 `tgadmin2`

`REDIS_URL` 不填也可以。

---

## 4. 平时怎么更新

你以后更新代码，不需要重新手工装一遍。

直接在服务器项目目录里执行：

```bash
bash scripts/update_debian.sh
```

它会做这些事：

1. 拉取最新代码
2. 更新 Python 依赖
3. 重启机器人服务

如果你平时是用 systemd 跑的，这个脚本最适合。

---

## 5. 机器人里怎么更新

OWNER 也可以直接在 Telegram 私聊机器人更新。

命令是：

```text
/update 更新机器人
```

流程是这样的：

1. 先发一次 `/update 更新机器人`
2. 机器人会回一个确认码
3. 你再发一次 `/update 确认码`

这个设计是为了防止误操作。

---

## 6. OWNER 私聊能做什么

OWNER 不用进群，也不用靠群管理员权限，直接私聊机器人就能用。

常用命令：

- `/panel 仪表盘`
- `/status 状态页`
- `/groups 群列表`
- `/group 群ID`
- `/update 更新机器人`

如果你只是想看整体状态，用 `/panel` 就够了。

---

## 7. 机器人正常怎么工作

机器人进群后会这样跑：

- 新人进群
- 机器人先限制发言
- 机器人发验证链接
- 用户私聊机器人完成验证
- 验证通过后自动放行

如果用户一直没过验证，机器人会按配置处理。

---

## 8. 常见问题

### 机器人为什么没反应？

先看这几个地方：

- `BOT_TOKEN` 有没有写对
- 机器人有没有在群里
- 机器人有没有管理权限
- systemd 服务是不是已经启动

### `/start` 没反应怎么办？

要从群里发出来的验证链接进入私聊，不能直接在私聊里乱输 `/start`。

### 为什么看不到最新代码？

服务器上执行：

```bash
bash scripts/update_debian.sh
```

或者用 OWNER 私聊里的 `/update`。

---

## 9. 本地调试

如果你不是在服务器上，而是在本地试运行：

```bash
python -m bot.main
```

不过这个项目更推荐直接在 Debian 云服务器上跑。

---

## 10. 总结

你只要记住这两个命令就行：

### 第一次部署

```bash
bash scripts/setup_debian.sh
```

### 以后更新

```bash
bash scripts/update_debian.sh
```

如果你想用 Telegram 私聊更新，就用：

```text
/update 更新机器人
```
