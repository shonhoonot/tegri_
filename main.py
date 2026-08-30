import re
import json
import os
import logging
import asyncio
from collections import defaultdict, OrderedDict

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

openai_client = OpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url="https://api.deepseek.com",
)

# Хэрэглэгч тус бүрийн яриа хадгалдаг санах ой (in-memory)
conversation_histories: dict[str, list] = defaultdict(list)

# Meta webhook давхардуулж (retry) илгээсэн мессежийг дахин боловсруулахгүйн тулд.
# OrderedDict тул хамгийн эрт үзсэн mid-ийг хасаж, санах ойг хязгаарлана.
_seen_message_ids: OrderedDict[str, None] = OrderedDict()
_SEEN_CAP = 1000


def already_seen(mid: str) -> bool:
    if mid in _seen_message_ids:
        return True
    _seen_message_ids[mid] = None
    while len(_seen_message_ids) > _SEEN_CAP:
        _seen_message_ids.popitem(last=False)
    return False

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

ORDER_RE = re.compile(r"<<<ORDER>>>(.*?)<<<ORDER>>>", re.DOTALL)


def extract_order(reply_text: str) -> tuple[str, dict | None]:
    match = ORDER_RE.search(reply_text)
    if not match:
        return reply_text.strip(), None

    raw = match.group(1).strip()
    clean_text = ORDER_RE.sub("", reply_text).strip()

    try:
        order = json.loads(raw)
    except json.JSONDecodeError:
        return clean_text, None

    if not order.get("phone") or not order.get("address"):
        return clean_text, None

    # Мөнгөн дүн бол тогтмол утга — LLM-ийн бичсэнд бүү найд, дарж бич.
    order["price"] = PRICE
    order["delivery"] = DELIVERY
    order["assembly"] = ASSEMBLY

    return clean_text, order


def call_openai(history: list) -> str:
    response = openai_client.chat.completions.create(
        model="deepseek-chat",
        messages=[{"role": "system", "content": SYSTEM_PROMPT}] + history,
        # Үнэ зэрэг тоог өөрөө зохиож хазайхаас сэргийлж temperature-г бага барина
        temperature=0.2,
        timeout=30,
    )
    return response.choices[0].message.content


def send_messenger_reply(recipient_id: str, text: str) -> None:
    url = f"https://graph.facebook.com/v19.0/me/messages"
    payload = {
        "recipient": {"id": recipient_id},
        "message": {"text": text},
    }
    params = {"access_token": PAGE_ACCESS_TOKEN}
    try:
        with httpx.Client(timeout=10.0) as client:
            client.post(url, json=payload, params=params)
    except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.RequestError):
        pass


@app.get("/webhook")
async def verify_webhook(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        return Response(content=challenge, media_type="text/plain")
    return Response(status_code=403)


@app.post("/webhook")
async def handle_webhook(request: Request):
    body = await request.json()

    if body.get("object") != "page":
        return Response(status_code=404)

    for entry in body.get("entry", []):
        for event in entry.get("messaging", []):
            sender_id = event.get("sender", {}).get("id")
            message = event.get("message", {})
            text = message.get("text")
            mid = message.get("mid")

            if not sender_id or not text:
                continue

            if mid and already_seen(mid):
                log.info("skip duplicate mid=%s sender=%s", mid, sender_id)
                continue

            log.info("recv sender=%s mid=%s text=%r", sender_id, mid, text)

            history = conversation_histories[sender_id]
            history.append({"role": "user", "content": text})

            try:
                reply = await asyncio.to_thread(call_openai, history)
            except Exception:
                log.exception("call_openai failed sender=%s", sender_id)
                continue

            user_text, order = extract_order(reply)

            history.append({"role": "assistant", "content": user_text})

            log.info("reply sender=%s order=%s text=%r", sender_id, bool(order), user_text)

            send_messenger_reply(sender_id, user_text)

            if order:
                log.info("order sender=%s phone=%s", sender_id, order.get("phone"))
                send_lead_notification(order)

    return {"status": "ok"}
