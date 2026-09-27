import asyncio
import re
from datetime import datetime, timezone

import aiohttp
from yarl import URL as YarlURL


URL = "https://sklep.play.pl/api/cart/msisdns"  #默认
User_Agent = "填写你的User-Agent"
OFFER_ITEM_ID = "填写你的OFFER_ITEM_ID"
COOKIES = r'''填写你的COOKIES'''
CONCURRENCY = 5
REQUESTS_PER_SECOND = 2
BACKOFF_SECONDS = 60
TIMEOUT = aiohttp.ClientTimeout(total=15)
TG_TIMEOUT = aiohttp.ClientTimeout(total=40)

BOT_TOKEN = "你的机器人Token"
CHAT_ID = "你的TG id"

PATTERNS = {
    # 豹子号
    "4位以上重复号": r"(\d)\1{3,}",

    # 四位组合
    "AAAA": r"(\d)\1{3}",

    # 六位组合
    "ABCABC": r"(\d)(\d)(\d)\1\2\3",
    "AAABBB": r"(\d)\1{2}((?!\1)\d)\2{2}",
    "AABBCC": r"(\d)\1((?!\1)\d)\2((?!\1|\2)\d)\3",

    # 循环号
    "AB循环": r"(\d)((?!\1)\d)(?:\1\2){2,}",
    "ABC循环": r"(\d)(\d)(\d)(?:\1\2\3)+",

    # 回文/对称
    "6位对称ABCCBA": r"(\d)(\d)(\d)\3\2\1",
    "8位对称ABCDDCBA": r"(\d)(\d)(\d)(\d)\4\3\2\1",

    # 顺子
    "4位以上顺子": r"(?:(?:0(?=1)|1(?=2)|2(?=3)|3(?=4)|4(?=5)|5(?=6)|6(?=7)|7(?=8)|8(?=9)){3,}\d)",
    "4位以上倒顺子": r"(?:(?:9(?=8)|8(?=7)|7(?=6)|6(?=5)|5(?=4)|4(?=3)|3(?=2)|2(?=1)|1(?=0)){3,}\d)",

    # 特殊寓意
    "寓意号": r"(?:5201314|5211314|1314520|1314521)",
}


def now() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


async def notify(
    session: aiohttp.ClientSession,
    token: str,
    chat_id: str,
    message: str,
    *,
    sensitive: bool = False,
) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for attempt in range(3):
        try:
            async with session.post(url, data={"chat_id": chat_id, "text": message}) as response:
                if response.status == 429:
                    data = await response.json(content_type=None)
                    delay = data.get("parameters", {}).get("retry_after", 3)
                    await asyncio.sleep(min(int(delay), 60))
                    continue
                response.raise_for_status()
                return
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            if attempt == 2:
                print(f"Telegram 发送失败：{type(exc).__name__}")
            else:
                await asyncio.sleep(2 ** attempt)
    print("Telegram 敏感消息未送达" if sensitive else f"Telegram 消息未送达：{message}")


def current_cookies(session: aiohttp.ClientSession) -> str:
    """Return the configured Cookie header with newer session cookies applied."""
    configured = session.headers.get("Cookie", "")
    cookies = dict(
        item.split("=", 1)
        for item in configured.split("; ")
        if "=" in item
    )
    for name, morsel in session.cookie_jar.filter_cookies(YarlURL(URL)).items():
        cookies[name] = morsel.value
    return "; ".join(f"{name}={value}" for name, value in cookies.items())


async def send_cookies(
    play_session: aiohttp.ClientSession,
    tg_session: aiohttp.ClientSession,
    bot_token: str,
    chat_id: str,
) -> None:
    cookie = current_cookies(play_session)
    if not cookie:
        await notify(tg_session, bot_token, chat_id, "当前没有 Cookie。")
        return
    # Telegram sendMessage has a 4096-character text limit.
    parts = [cookie[i:i + 3500] for i in range(0, len(cookie), 3500)]
    for index, part in enumerate(parts, 1):
        await notify(
            tg_session, bot_token, chat_id,
            f"当前 Cookie（{index}/{len(parts)}）：\n{part}",
            sensitive=True,
        )


async def fetch_numbers(session: aiohttp.ClientSession) -> tuple[list[str], int]:
    try:
        async with session.get(URL) as response:
            if response.status in (401, 403, 429):
                return [], response.status
            response.raise_for_status()
            data = await response.json(content_type=None)
            numbers = data.get("reservedNumbers", [])
            if not isinstance(numbers, list):
                raise ValueError("reservedNumbers 不是列表")
            return [str(number) for number in numbers], response.status
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        print(f"{now()} 查询失败：{exc}")
        return [], 0


async def reserve(session: aiohttp.ClientSession, number: str) -> tuple[int, str]:
    try:
        async with session.post(URL, json={"msisdn": number, "offerItemId": OFFER_ITEM_ID}) as response:
            body = await response.text()
            return response.status, body
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        return 0, f"请求异常：{type(exc).__name__}: {exc}"


def matching_patterns(number: str, patterns: dict[str, re.Pattern[str]]) -> list[tuple[str, str]]:
    return [
        (name, match.group())
        for name, pattern in patterns.items()
        if (match := pattern.search(number)) is not None
    ]


class RequestPacer:
    def __init__(self, requests_per_second: int) -> None:
        self.interval = 1 / requests_per_second
        self.next_at = 0.0
        self.pause_until = 0.0
        self.lock = asyncio.Lock()

    async def wait_turn(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            async with self.lock:
                scheduled = max(loop.time(), self.next_at, self.pause_until)
                self.next_at = scheduled + self.interval
            await asyncio.sleep(max(0, scheduled - loop.time()))
            if loop.time() >= self.pause_until:
                return

    async def pause(self, seconds: float) -> None:
        loop = asyncio.get_running_loop()
        async with self.lock:
            self.pause_until = max(self.pause_until, loop.time() + seconds)
            self.next_at = max(self.next_at, self.pause_until)


async def monitor(
    play_session: aiohttp.ClientSession,
    tg_session: aiohttp.ClientSession,
    bot_token: str,
    chat_id: str,
    patterns: dict[str, re.Pattern[str]],
    finding_enabled: asyncio.Event,
) -> None:
    pacer = RequestPacer(REQUESTS_PER_SECOND)
    notices: asyncio.Queue[str] = asyncio.Queue(maxsize=500)
    notified_numbers: set[str] = set()
    total_gets = 0

    async def query_worker() -> None:
        nonlocal total_gets
        while True:
            await finding_enabled.wait()
            await pacer.wait_turn()
            if not finding_enabled.is_set():
                continue
            numbers, status = await fetch_numbers(play_session)
            total_gets += 1
            if status in (401, 403, 429):
                await pacer.pause(BACKOFF_SECONDS)
            if not finding_enabled.is_set():
                continue
            for number in dict.fromkeys(numbers):
                found = matching_patterns(number, patterns)
                if not found or number in notified_numbers:
                    continue
                notified_numbers.add(number)
                descriptions = ", ".join(f"{name}({fragment})" for name, fragment in found)
                message = (
                    f"找到号码：{number}\n命中规则：{descriptions}\n"
                    f"时间：{now()}\n累计查询：{total_gets} 次\n"
                    f"手动锁定：/add {number}"
                )
                print(message)
                await notices.put(message)

    async def send_notices() -> None:
        while True:
            message = await notices.get()
            try:
                await notify(tg_session, bot_token, chat_id, message)
            finally:
                notices.task_done()

    await asyncio.gather(
        *(query_worker() for _ in range(CONCURRENCY)),
        send_notices(),
    )


async def telegram_commands(
    play_session: aiohttp.ClientSession,
    tg_session: aiohttp.ClientSession,
    bot_token: str,
    chat_id: str,
    finding_enabled: asyncio.Event,
) -> None:
    url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
    started_at = int(datetime.now(timezone.utc).timestamp())
    offset = None
    post_lock = asyncio.Lock()

    while True:
        params = {"timeout": 25, "allowed_updates": '["message"]'}
        if offset is not None:
            params["offset"] = offset
        try:
            async with tg_session.get(url, params=params) as response:
                response.raise_for_status()
                payload = await response.json(content_type=None)
            if not payload.get("ok"):
                raise ValueError(f"Telegram 返回错误：{payload.get('description', 'unknown')}")
            for update in payload.get("result", []):
                offset = update["update_id"] + 1
                message = update.get("message", {})
                if str(message.get("chat", {}).get("id")) != chat_id:
                    continue
                if message.get("date", 0) < started_at:
                    continue
                text = message.get("text", "").strip()
                if re.fullmatch(r"/stop(?:@\w+)?", text):
                    finding_enabled.clear()
                    await notify(tg_session, bot_token, chat_id, "已停止找号。")
                    continue
                if re.fullmatch(r"/find(?:@\w+)?", text):
                    finding_enabled.set()
                    await notify(tg_session, bot_token, chat_id, "已开始找号。")
                    continue
                if re.fullmatch(r"/cookies(?:@\w+)?", text):
                    await send_cookies(play_session, tg_session, bot_token, chat_id)
                    continue
                if not text.startswith("/add"):
                    continue
                match = re.fullmatch(r"/add(?:@\w+)?\s+\*{0,2}(\d{11})\*{0,2}", text)
                if not match:
                    await notify(tg_session, bot_token, chat_id, "格式：/add 48530564441")
                    continue
                number = match.group(1)
                async with post_lock:
                    status, body = await reserve(play_session, number)
                result = (
                    f"/add {number} 操作结果\n时间：{now()}\n"
                    f"HTTP 状态：{status if status else '请求失败'}\n"
                    f"响应：{body[:3500]}"
                )
                print(result)
                await notify(tg_session, bot_token, chat_id, result)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, KeyError) as exc:
            print(f"{now()} Telegram 指令轮询失败：{type(exc).__name__}: {exc}")
            await asyncio.sleep(5)


async def main() -> None:
    patterns = {name: re.compile(expression) for name, expression in PATTERNS.items()}
    bot_token = BOT_TOKEN
    chat_id = CHAT_ID
    cookie = COOKIES
    if not patterns:
        raise ValueError("PATTERNS 至少需要一条正则规则")
    if "填写你的" in bot_token or "填写你的" in chat_id:
        raise ValueError("请在 main() 中填写 Bot Token 和 Chat ID")

    headers = {
        "User-Agent": User_Agent
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": "https://sklep.play.pl",
        "Referer": f"https://sklep.play.pl/items/{OFFER_ITEM_ID}/msisdn",
        "Cookie": cookie,
    }
    connector = aiohttp.TCPConnector(limit_per_host=CONCURRENCY + 2)
    finding_enabled = asyncio.Event()
    finding_enabled.set()

    async with aiohttp.ClientSession(headers=headers, timeout=TIMEOUT, connector=connector) as play_session:
        async with aiohttp.ClientSession(timeout=TG_TIMEOUT) as tg_session:
            await asyncio.gather(
                monitor(play_session, tg_session, bot_token, chat_id, patterns, finding_enabled),
                telegram_commands(play_session, tg_session, bot_token, chat_id, finding_enabled),
            )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("已手动停止。")
