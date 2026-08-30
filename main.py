import re
import json
import os
import hmac
import hashlib
import logging
import asyncio
from collections import OrderedDict

from dotenv import load_dotenv
import httpx

load_dotenv()
from fastapi import FastAPI, Request, Response
from openai import OpenAI

from telegram import send_lead_notification

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("tegri")

app = FastAPI()

# Мөнгөн дүнгийн эх сурвалж — SYSTEM_PROMPT доторх тоог өөрчлөхдөө эдгээрийг ч бас өөрчил.
PRICE = 450000
DELIVERY = 10000
ASSEMBLY = 10000

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
PAGE_ACCESS_TOKEN = os.getenv("PAGE_ACCESS_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
# Meta App Secret. Тохируулбал webhook бүрийн гарын үсгийг шалгана.
APP_SECRET = os.getenv("APP_SECRET")

# Алдаа гарсан үед үйлчлүүлэгчийг чимээгүй орхихгүйн тулд
FALLBACK_ERROR = (
    "Уучлаарай, түр зуурын алдаа гарлаа. Дахин бичихэд хариулъя, "
    "эсвэл 99194217 дугаар руу холбогдоно уу."
)
FALLBACK_ORDER_OK = (
    "Захиалгыг тань хүлээн авлаа. Манай ажилтан тантай удахгүй "
    "холбогдоно. Баярлалаа!"
)
FALLBACK_NON_TEXT = (
    "Уучлаарай, зураг болон хавсралтыг уншиж чадахгүй байна. "
    "Асуултаа бичиж илгээвэл хариулъя, эсвэл 99194217 руу холбогдоно уу."
)

openai_client = OpenAI(
    api_key=DEEPSEEK_API_KEY or "missing_key",
    base_url="https://api.deepseek.com",
)

# Бүх төлөв процессын санах ойд байгаа тул restart болгонд арчигдана.
# Тиймээс бүгд ХЯЗГААРТАЙ — удаан ажиллахад санах ой хязгааргүй өсөхөөс сэргийлнэ.

# Хэрэглэгч тус бүрийн яриа. Хамгийн эрт хэрэглэсэн хэрэглэгчийг эхэлж хасна.
conversation_histories: OrderedDict[str, list] = OrderedDict()
_MAX_USERS = 500
_MAX_HISTORY = 20  # 10 ээлж; үүнээс хэтэрвэл эхнээс нь тасална

# Meta webhook давхардуулж (retry) илгээсэн мессежийг дахин боловсруулахгүйн тулд
_seen_message_ids: OrderedDict[str, None] = OrderedDict()
_SEEN_CAP = 1000

# Нэг захиалгыг Telegram руу дахин дахин илгээхээс сэргийлнэ
_sent_orders: OrderedDict[str, None] = OrderedDict()
_ORDER_CAP = 500


def already_seen(mid: str) -> bool:
    if mid in _seen_message_ids:
        return True
    _seen_message_ids[mid] = None
    while len(_seen_message_ids) > _SEEN_CAP:
        _seen_message_ids.popitem(last=False)
    return False


def get_history(sender_id: str) -> list:
    """Хэрэглэгчийн ярианы түүхийг авна (LRU: хамгийн эрт хэрэглэснийг хасна)."""
    if sender_id in conversation_histories:
        conversation_histories.move_to_end(sender_id)
    else:
        conversation_histories[sender_id] = []
        while len(conversation_histories) > _MAX_USERS:
            conversation_histories.popitem(last=False)
    return conversation_histories[sender_id]


def trim_history(history: list) -> None:
    """Түүхийг таслана. Эхний мессеж үргэлж user байхаар үлдээнэ."""
    if len(history) > _MAX_HISTORY:
        del history[: len(history) - _MAX_HISTORY]
    if history and history[0]["role"] == "assistant":
        del history[0]


def _normalize_order_key(sender_id: str, order: dict) -> str:
    phone = re.sub(r"\D", "", str(order.get("phone", "")))
    if len(phone) == 11 and phone.startswith("976"):
        phone = phone[3:]
    address = " ".join(str(order.get("address", "")).lower().split())
    return f"{sender_id}|{phone}|{address}"


def is_order_already_sent(sender_id: str, order: dict) -> bool:
    """Ижил захиалга (утас + хаяг) амжилттай илгээгдсэн эсэхийг шалгана."""
    key = _normalize_order_key(sender_id, order)
    return key in _sent_orders


def mark_order_sent(sender_id: str, order: dict) -> None:
    """Захиалга Telegram руу амжилттай илгээгдсэний дараа бүртгэнэ."""
    key = _normalize_order_key(sender_id, order)
    _sent_orders[key] = None
    while len(_sent_orders) > _ORDER_CAP:
        _sent_orders.popitem(last=False)


def verify_signature(raw_body: bytes, header: str | None) -> bool:
    """Meta-гийн X-Hub-Signature-256 гарын үсгийг шалгана.

    APP_SECRET тохируулаагүй бол шалгахгүй өнгөрүүлнэ — ингэснээр
    env хувьсагч нэмэхээс өмнө ажиллаж байгаа bot зогсохгүй.
    """
    if not APP_SECRET:
        return True
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(APP_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[len("sha256="):])


SYSTEM_PROMPT = """# ҮҮРЭГ
Чи бол "тэгри_" дэлгүүрийн борлуулалтын туслах. Нуруу сунгалтын тавцан зардаг.
Зорилго: үйлчлүүлэгчтэй найрсаг харьцаж, бүтээгдэхүүний талаар мэдээлэл өгч,
сонирхсон хүнээс ЗАХИАЛГА (хаяг + утасны дугаар) бүрэн авах.

# ХАРИЛЦАХ ЗАРЧИМ
- Зөвхөн монгол хэлээр, эелдэг, ойлгомжтой, товч бич.
- Эмойжи хэт их бүү хэрэглэ (хааяа 1-2 хүртэл боломжтой).
- Мэдэхгүй зүйлээ зохиож бүү хэл. Эргэлзвэл 99194217 руу холбогдохыг санал болго.
- ТООН МЭДЭЭЛЛИЙГ (үнэ, хүргэлт, угсралт, даац, өндөр) доорх
  "БҮТЭЭГДЭХҮҮНИЙ МЭДЭЭЛЭЛ" хэсэгт бичсэн ЯГ тэр тоогоор давтан бич.
  Тоог хэзээ ч өөрчилж, дугуйлж, эсвэл өөрөө зохиож болохгүй.
  Үнэ асуувал заавал "450,000₮" гэж бич — өөр ямар ч тоо буруу.
- Эмчилгээний нарийн зөвлөгөө бүү өг — энэ нь эмчийн ажил. Эсрэг заалтыг л танилц.
- Хүн худалдан авах сонирхол гаргамагц шууд захиалга авах руу шилж.

# БҮТЭЭГДЭХҮҮНИЙ МЭДЭЭЛЭЛ
Нэр: Нуруу сунгалтын тавцан
Үнэ: 450,000₮
Хүргэлт: 10,000₮
Угсралт: 10,000₮
Холбоо барих утас: 99194217
Даац: 130-150кг
Өндөр: 1.30-1.90 хүртэлх

Угсралт ба хэрэглээ:
1-р алхам: Дэлгэх, боолтоо нүхэнд нь хийх, чангалах
2-р алхам: Нуруугаа сунгаж хэвтэх

Ач холбогдол:
- Нуруу өвддөг хүмүүст туслана
- Нурууны үений суулттай хүмүүст тохиромжтой
- Нурууны үе хоорондын мэдрэлийн судлууд дарагдсан нурууны мурийлттай хүмүүс хэрэглэвэл тохиромжтой

# ЭСРЭГ ЗААЛТ (заавал анхааруулах)
Дараах тохиолдолд тавцан дээрх сунгалтын эмчилгээ ХИЙХГҮЙ:
остеомиелит, хавдар, нурууны ясны хугарал, хүнд хэлбэрийн яс сийрэгжилт,
миелопати, артерийн даралт өндөр, өндөр настай, жирэмсэн.
Хэрэв үйлчлүүлэгч эдгээрийн аль нэгийг дурдвал — эелдэгээр анхааруулж,
эмчтэйгээ зөвлөхийг санал болго.

# ЯРИАНЫ УРСГАЛ
1. Мэндчилгээ — найрсаг угтаж, юу сонирхож байгааг асуу.
2. Тайлбар — асуултад нь тохирсон мэдээлэл өг (ач холбогдол, үнэ, хүргэлт).
3. Сонирхол — худалдан авах сонирхолтой бол захиалга руу шилж.
4. Захиалга авах — дараах 2 зүйлийг заавал асуу:
   - Хүлээн авах ХАЯГ (дүүрэг/хороо/байр/тоот)
   - Утасны ДУГААР
   Хэрэв нэр өгсөн бол нэрийг нь бас тэмдэглэ.
5. Баталгаажуулалт — авсан мэдээллээ давтаж хэлж зөв эсэхийг асуу.
6. Хаалт — баярлалаа гэж хэлж, "Манай ажилтан тантай удахгүй холбогдоно" гэж мэдэгд.

# АСУУЛТ ГАРВАЛ
Үнэ/хүргэлт/ач холбогдлын энгийн асуултад өөрөө хариул.
Эмнэлзүйн нарийн, эсвэл чиний мэдэхгүй асуулт гарвал:
"Энэ талаар дэлгэрэнгүй мэдээллийг 99194217 дугаараас авах боломжтой шүү" гэж санал болго.

# ЗАХИАЛГА БҮРТГЭХ
Хаяг БА утасны дугаар хоёулаа бүрэн авсан тохиолдолд л захиалгыг
баталгаажуулсанд тооц. Дутуу мэдээлэлтэй захиалгыг бүү илгээ.

# ДОТООД ТЭМДЭГЛЭЛ (үйлчлүүлэгчид харагдахгүй)
Захиалга бүрэн бүрдсэн (хаяг + утас) даруйд хариултынхаа АРД дараах
тэмдэгтээр хүрээлсэн JSON-ийг нэмж бич. Хэрэглэгчид энэ JSON харагдахгүй:

<<<ORDER>>>
{
  "name": "<нэр эсвэл null>",
  "phone": "<утасны дугаар>",
  "address": "<бүтэн хаяг>",
  "product": "Нуруу сунгалтын тавцан",
  "price": 450000,
  "delivery": 10000,
  "assembly": 10000,
  "note": "<нэмэлт тэмдэглэл байвал>"
}
<<<ORDER>>>

Захиалга бүрэн биш бол энэ JSON-ийг бүү гарга."""

ORDER_RE = re.compile(r"<<<ORDER>>>(.*?)(?:<<<ORDER>>>|$)", re.DOTALL | re.IGNORECASE)


def extract_order(reply_text: str) -> tuple[str, dict | None]:
    match = ORDER_RE.search(reply_text)
    if not match:
        return reply_text.strip(), None

    raw = match.group(1).strip()
    clean_text = ORDER_RE.sub("", reply_text).strip()
    # Текст дотор үлдсэн байж болзошгүй тагуудыг цэвэрлэнэ
    clean_text = re.sub(r"<<<ORDER>>>", "", clean_text, flags=re.IGNORECASE).strip()

    # Markdown code block (` ```json ... ``` `) орсон байвал тайлна
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\s*```$", "", raw).strip()

    order = None
    try:
        order = json.loads(raw)
    except json.JSONDecodeError:
        # Хэрэв LLM өөр тайлбар бичсэн бол {...} хэсгийг сугалж туршина
        brace_match = re.search(r"\{.*\}", raw, re.DOTALL)
        if brace_match:
            try:
                order = json.loads(brace_match.group(0))
            except json.JSONDecodeError:
                order = None

    if not isinstance(order, dict):
        return clean_text, None

    if not order.get("phone") or not order.get("address"):
        return clean_text, None

    # Мөнгөн дүн бол тогтмол утга — LLM-ийн бичсэнд бүү найд, дарж бич.
    order["price"] = PRICE
    order["delivery"] = DELIVERY
    order["assembly"] = ASSEMBLY

    return clean_text, order


def call_openai(history: list) -> str:
    if not DEEPSEEK_API_KEY:
        log.error("DEEPSEEK_API_KEY is missing in environment")
        raise RuntimeError("DEEPSEEK_API_KEY is not configured")
    response = openai_client.chat.completions.create(
        model="deepseek-chat",
        messages=[{"role": "system", "content": SYSTEM_PROMPT}] + history,
        # Үнэ зэрэг тоог өөрөө зохиож хазайхаас сэргийлж temperature-г бага барина
        temperature=0.2,
        timeout=30,
    )
    return response.choices[0].message.content


def send_messenger_reply(recipient_id: str, text: str) -> bool:
    """Messenger руу хариу илгээнэ. Амжилттай эсэхийг буцаана."""
    url = "https://graph.facebook.com/v19.0/me/messages"
    payload = {
        "recipient": {"id": recipient_id},
        "message": {"text": text},
    }
    params = {"access_token": PAGE_ACCESS_TOKEN}
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(url, json=payload, params=params)
    except httpx.RequestError as exc:
        # ReadTimeout, ConnectTimeout зэрэг нь RequestError-ийн удам
        log.error("messenger request failed recipient=%s err=%s", recipient_id, exc)
        return False

    if resp.status_code >= 400:
        log.error(
            "messenger rejected recipient=%s status=%s body=%s",
            recipient_id, resp.status_code, resp.text[:500],
        )
        return False
    return True


async def send_reply(recipient_id: str, text: str) -> bool:
    """Blocking HTTP дуудлагыг event loop-оос гаргана."""
    return await asyncio.to_thread(send_messenger_reply, recipient_id, text)


@app.get("/")
@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "tegri-agent"}


@app.get("/webhook")
async def verify_webhook(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    # VERIFY_TOKEN тохируулаагүй бол None == None болж өнгөрөхөөс сэргийлнэ
    if mode == "subscribe" and VERIFY_TOKEN and token == VERIFY_TOKEN:
        return Response(content=challenge, media_type="text/plain")
    return Response(status_code=403)


async def handle_event(event: dict) -> None:
    sender_id = event.get("sender", {}).get("id")
    if not sender_id:
        return

    message = event.get("message")
    postback = event.get("postback")

    if not message and not postback:
        return

    # Хуудас өөрөө илгээсэн мессежийн echo — үүнд хариулбал давталтад орно
    if message and message.get("is_echo"):
        return

    mid = message.get("mid") if message else None
    event_id = mid or f"pb_{sender_id}_{event.get('timestamp')}"
    if event_id and already_seen(event_id):
        log.info("skip duplicate mid/event=%s sender=%s", event_id, sender_id)
        return

    if postback:
        # "Get Started" эсвэл цэсний товчлуур дарагдсан үед
        text = postback.get("title") or postback.get("payload") or "Сайн байна уу"
    else:
        text = message.get("text")

    if not text:
        log.info("non-text message sender=%s mid=%s", sender_id, mid)
        await send_reply(sender_id, FALLBACK_NON_TEXT)
        return

    log.info("recv sender=%s mid=%s text=%r", sender_id, mid, text)

    history = get_history(sender_id)
    history.append({"role": "user", "content": text})
    trim_history(history)

    try:
        reply = await asyncio.to_thread(call_openai, history)
    except Exception:
        log.exception("call_openai failed sender=%s", sender_id)
        await send_reply(sender_id, FALLBACK_ERROR)
        return

    if not reply:
        log.error("empty LLM reply sender=%s", sender_id)
        await send_reply(sender_id, FALLBACK_ERROR)
        return

    try:
        user_text, order = extract_order(reply)
    except Exception:
        log.exception("extract_order failed sender=%s reply=%r", sender_id, reply)
        await send_reply(sender_id, FALLBACK_ERROR)
        return

    # Загвар зөвхөн ORDER JSON буцаавал хэрэглэгч хоосон мессеж авахгүй байх ёстой
    if not user_text:
        user_text = FALLBACK_ORDER_OK if order else FALLBACK_ERROR

    history.append({"role": "assistant", "content": user_text})
    trim_history(history)

    log.info("reply sender=%s order=%s text=%r", sender_id, bool(order), user_text)
    await send_reply(sender_id, user_text)

    if not order:
        return

    if is_order_already_sent(sender_id, order):
        log.info("skip duplicate order sender=%s phone=%s", sender_id, order.get("phone"))
        return

    log.info("order sender=%s phone=%s", sender_id, order.get("phone"))
    sent = await asyncio.to_thread(send_lead_notification, order)
    if sent:
        mark_order_sent(sender_id, order)
    else:
        log.error("LEAD NOT DELIVERED sender=%s order=%s", sender_id, order)


@app.post("/webhook")
async def handle_webhook(request: Request):
    raw = await request.body()

    if not verify_signature(raw, request.headers.get("X-Hub-Signature-256")):
        log.warning("rejected webhook with invalid signature")
        return Response(status_code=403)

    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("webhook body is not valid JSON")
        return Response(status_code=400)

    if body.get("object") != "page":
        return Response(status_code=404)

    for entry in body.get("entry", []):
        for event in entry.get("messaging", []):
            # Нэг event уналаа гээд бусдыг нь тасалдуулахгүй
            try:
                await handle_event(event)
            except Exception:
                log.exception("event handling failed: %s", event)

    return {"status": "ok"}
