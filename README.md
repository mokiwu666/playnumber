# Play 号码监控与 Telegram 控制

这个脚本持续查询 Play 的号码接口，按 `PATTERNS` 中的规则筛选号码，并将命中的号码发送到指定 Telegram 聊天。号码不会自动预订；需要通过 `/add` 手动提交预订请求。

## 环境要求

- Python 3.10 或更新版本
- 能访问 `sklep.play.pl` 和 Telegram Bot API 的网络环境
- 一个 Telegram Bot，以及用于接收消息的 Chat ID

安装依赖：

```bash
python3 -m pip install aiohttp yarl
```

## 配置

将脚本保存为 `play.py`，修改文件开头和 `main()` 中的配置：

| 配置项 | 用途 |
| --- | --- |
| `User_Agent` | 访问 Play 时使用的浏览器 User-Agent |
| `OFFER_ITEM_ID` | 预订号码时提交的商品 ID |
| `cookie` | Play 会话的 Cookie 字符串 |
| `bot_token` | Telegram Bot Token |
| `chat_id` | 接收通知和发送指令的聊天 ID |
| `PATTERNS` | 用于匹配号码的正则表达式 |
| `CONCURRENCY` | 并发查询 worker 数，默认 5 |
| `REQUESTS_PER_SECOND` | 所有查询 worker 共用的请求速率，默认每秒 2 次 |
| `BACKOFF_SECONDS` | 查询收到 401、403 或 429 后暂停的秒数，默认 60 秒 |

脚本只处理来自所配置 `chat_id`、且在本次启动后收到的 Telegram 消息。首次使用 Bot 时，先在 Telegram 中向它发送一条消息，确保它能向该聊天回复。

### 当前粘贴版本需要修正的代码

在 `headers` 字典中，`User_Agent` 后面缺少逗号。改成：

```python
"User-Agent": User_Agent,
"Accept": "application/json",
```

在 `send_cookies()` 开头，`if not cookie:` 之前补上：

```python
cookie = current_cookies(play_session)
```

文件开头还有一个 `COOKIES` 占位变量，但实际请求使用的是 `main()` 中的 `cookie`。请修改真正被使用的 `cookie`，或自行统一这两个变量；只填写 `COOKIES` 不会改变请求 Cookie。

## 运行

```bash
python3 play.py
```

启动后立即开始找号。终端会打印查询错误、找到的号码以及手动预订的结果。按 `Ctrl+C` 结束整个程序。

## Telegram 指令

| 指令 | 作用 |
| --- | --- |
| `/find` | 开始或恢复找号 |
| `/stop` | 暂停新的找号查询；Bot 仍运行，其他指令仍可使用 |
| `/cookies` | 将当前配置的 Cookie 与会话更新的 Cookie 发送到配置的聊天；过长时分多条发送 |
| `/add 48530564441` | 向 Play 提交该 11 位号码的预订请求，并返回 HTTP 状态和响应摘要 |

`/stop` 后，已经发出的查询或已经排队的通知可能仍会完成。`/find` 恢复时，如果此前触发了 401、403 或 429 的限速等待，查询要等该等待结束。`/add` 不受找号暂停状态影响。

同一个号码在一次程序运行中只通知一次；重启后会重新统计。Cookie 和 Bot Token 可用于访问账户或 Bot，请勿将配置后的脚本提交到公开仓库，也不要在公开聊天中使用 `/cookies`。
