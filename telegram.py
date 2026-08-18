import httpx
import os

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def send_lead_notification(order: dict) -> None:
    name = order.get("name") or "—"
    phone = order.get("phone", "")
    address = order.get("address", "")
    note = order.get("note") or ""
    price = order.get("price", 450000)
    delivery = order.get("delivery", 10000)
    assembly = order.get("assembly", 10000)

    text = (
        f"🛒 *Шинэ захиалга — тэгри\\_*\n\n"
        f"👤 Нэр: {name}\n"
        f"📞 Утас: {phone}\n"
        f"📍 Хаяг: {address}\n"
        f"💰 Бүтээгдэхүүн: {price:,}₮\n"
        f"🚚 Хүргэлт: {delivery:,}₮\n"
        f"🔧 Угсралт: {assembly:,}₮\n"
        f"📝 Тэмдэглэл: {note or '—'}"
    )

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        with httpx.Client(timeout=10.0) as client:
            client.post(url, json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "Markdown",
            })
    except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.RequestError):
        pass
