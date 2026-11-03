"""
Bot Telegram tra tin cổ phiếu, chạy được trên GitHub Actions.
Gõ /hsg, /fpt, /vic ... -> bot trả về tin tức mới nhất của mã đó.

Mỗi lần chạy, script lắng nghe tin nhắn trong RUN_SECONDS giây (mặc định 0 = xử lý
tin đang chờ rồi thoát). Trên GitHub, workflow chạy mỗi 5 phút và đặt RUN_SECONDS=270
nên các lần chạy nối nhau, bot gần như luôn "thức".

Token lấy từ biến môi trường TELEGRAM_TOKEN và CHAT_ID (GitHub Secrets).
"""

import calendar
import html
import os
import re
import sys
import time
from urllib.parse import quote

import feedparser
import requests

TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")
RUN_SECONDS = int(os.environ.get("RUN_SECONDS", "0"))

MAX_RESULTS = 8  # số tin trả về cho mỗi lệnh

API = f"https://api.telegram.org/bot{TOKEN}"
HELP_TEXT = (
    "Gõ /mã_cổ_phiếu để xem tin, ví dụ:\n"
    "/hsg\n/fpt\n/vic\n\n"
    "Hoặc gõ: /tin HPG"
)


# ---------------------------------------------------------------- Telegram
def send(text, chat_id=None):
    try:
        r = requests.post(
            f"{API}/sendMessage",
            data={
                "chat_id": chat_id or CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=20,
        )
        if not r.ok:
            print("Lỗi gửi Telegram:", r.status_code, r.text)
        return r.ok
    except requests.RequestException as e:
        print("Lỗi mạng khi gửi:", e)
        return False


def get_updates(offset=None, timeout=0):
    params = {"timeout": timeout}
    if offset is not None:
        params["offset"] = offset
    r = requests.get(f"{API}/getUpdates", params=params, timeout=timeout + 15)
    r.raise_for_status()
    return r.json().get("result", [])


# ---------------------------------------------------------------- Tìm tin theo mã
def fetch_google_news(ticker, days):
    q = f'"{ticker}" cổ phiếu when:{days}d'
    url = "https://news.google.com/rss/search?q=" + quote(q) + "&hl=vi&gl=VN&ceid=VN:vi"
    feed = feedparser.parse(url)
    items = []
    for e in feed.entries:
        title = e.get("title", "").strip()
        link = e.get("link")
        if not title or not link:
            continue
        source = ""
        if " - " in title:
            title, source = title.rsplit(" - ", 1)
        t = e.get("published_parsed")
        ts = calendar.timegm(t) if t else 0
        items.append({"title": title, "source": source, "link": link, "ts": ts})
    return items


def news_for_ticker(ticker):
    items = fetch_google_news(ticker, 7)
    if len(items) < 3:
        items = fetch_google_news(ticker, 30)

    seen_titles, unique = set(), []
    for it in items:
        key = it["title"].lower()
        if key not in seen_titles:
            seen_titles.add(key)
            unique.append(it)

    pat = re.compile(rf"\b{re.escape(ticker)}\b", re.IGNORECASE)
    unique.sort(key=lambda x: (not pat.search(x["title"]), -x["ts"]))
    top = unique[:MAX_RESULTS]
    top.sort(key=lambda x: -x["ts"])
    return top


def format_news(ticker, items):
    if not items:
        return f"Không tìm thấy tin gần đây về <b>{html.escape(ticker)}</b>."
    lines = [f"📊 <b>Tin mới về {html.escape(ticker)}</b>\n"]
    for it in items:
        # giờ Việt Nam (UTC+7), không phụ thuộc múi giờ của máy chủ
        date = time.strftime("%d/%m", time.gmtime(it["ts"] + 7 * 3600)) if it["ts"] else ""
        src = f" · {html.escape(it['source'])}" if it["source"] else ""
        lines.append(
            f"• <a href=\"{html.escape(it['link'], quote=True)}\">{html.escape(it['title'])}</a>"
            f"\n   <i>{date}{src}</i>"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------- Xử lý lệnh
def handle_message(msg):
    chat_id = str(msg.get("chat", {}).get("id"))
    if chat_id != str(CHAT_ID):
        return  # chỉ phục vụ chủ bot
    text = (msg.get("text") or "").strip()
    if not text.startswith("/"):
        return

    parts = text.split()
    cmd = parts[0][1:].split("@")[0].lower()

    if cmd in ("start", "help"):
        send(HELP_TEXT, chat_id)
        return

    if cmd == "tin":
        if len(parts) < 2:
            send("Cú pháp: /tin HPG", chat_id)
            return
        ticker = parts[1].upper()
    else:
        ticker = cmd.upper()

    if not re.fullmatch(r"[A-Z0-9]{2,6}", ticker):
        send("Mã cổ phiếu không hợp lệ. Ví dụ: /hsg", chat_id)
        return

    send(f"🔎 Đang tìm tin về <b>{ticker}</b>...", chat_id)
    try:
        send(format_news(ticker, news_for_ticker(ticker)), chat_id)
    except Exception as e:
        print("Lỗi tìm tin:", e)
        send("Có lỗi khi tìm tin, thử lại sau nhé.", chat_id)


# ---------------------------------------------------------------- Chạy
def main():
    if not TOKEN or not CHAT_ID:
        print("Thiếu TELEGRAM_TOKEN hoặc CHAT_ID (biến môi trường / GitHub Secrets).")
        sys.exit(1)

    deadline = time.time() + RUN_SECONDS
    offset = None
    handled = 0

    while True:
        remaining = deadline - time.time()
        timeout = max(0, min(25, int(remaining)))
        try:
            updates = get_updates(offset, timeout)
        except Exception as e:
            print("Lỗi nhận tin:", e)
            updates = []
            time.sleep(3)

        for u in updates:
            offset = u["update_id"] + 1
            if "message" in u:
                try:
                    handle_message(u["message"])
                    handled += 1
                except Exception as e:
                    print("Lỗi xử lý tin nhắn:", e)

        if time.time() >= deadline:
            break

    # xác nhận với Telegram là đã xử lý xong, để lần chạy sau không trả lời lại
    if offset is not None:
        try:
            get_updates(offset, 0)
        except Exception as e:
            print("Lỗi xác nhận:", e)

    print(f"Xong. Đã xử lý {handled} tin nhắn.")


if __name__ == "__main__":
    main()
