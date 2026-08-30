import html
import logging
import os

import httpx

log = logging.getLogger("tegri.telegram")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def _esc(value) -> str:
    """Хэрэглэгчээс ирсэн текстийг HTML-д аюулгүй болгоно."""
    text = str(value).strip() if value is not None else ""
    return html.escape(text) if text else "—"


def _money(value, default: int) -> int:
    """LLM тоог мөрөөр бичсэн ч format алдаа өгөхгүй байлгана."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def send_lead_notification(order: dict) -> bool:
    """Захиалгыг Telegram руу илгээнэ. Амжилттай эсэхийг буцаана.

    Бүтэлгүйтвэл захиалгыг бүтнээр нь log-д бичнэ — ингэснээр лид
    Telegram хүрээгүй ч Render-ийн log-оос сэргээх боломжтой хэвээр байна.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.error("telegram credentials missing, LEAD DROPPED: %s", order)
        return False

    price = _money(order.get("price"), 450000)
    delivery = _money(order.get("delivery"), 10000)
    assembly = _money(order.get("assembly"), 10000)

    # parse_mode=HTML: зөвхөн < > & escape хийхэд хангалттай тул хаяг доторх
    # _ * [ ` зэрэг тэмдэгт Markdown шиг entity алдаа өгөхгүй.
    text = (
        "🛒 <b>Шинэ захиалга — тэгри_</b>\n\n"
        f"👤 Нэр: {_esc(order.get('name'))}\n"
        f"📞 Утас: {_esc(order.get('phone'))}\n"
        f"📍 Хаяг: {_esc(order.get('address'))}\n"
        f"💰 Бүтээгдэхүүн: {price:,}₮\n"
        f"🚚 Хүргэлт: {delivery:,}₮\n"
        f"🔧 Угсралт: {assembly:,}₮\n"
        f"📝 Тэмдэглэл: {_esc(order.get('note'))}"
    )

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(url, json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
            })
    except httpx.RequestError as exc:
        log.error("telegram request failed (%s), LEAD DROPPED: %s", exc, order)
        return False

    if resp.status_code >= 400:
        log.error(
            "telegram rejected status=%s body=%s, LEAD DROPPED: %s",
            resp.status_code, resp.text[:500], order,
        )
        return False

    log.info("telegram lead sent phone=%s", order.get("phone"))
    return True
