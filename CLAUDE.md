# CLAUDE.md

Энэ файл нь Claude Code-д зориулсан project context. Кодыг өөрчлөхөөс өмнө
энэ файлыг уншиж, доорх дүрэм, агентын зан төлөвийг баримтал.

---

## Төслийн товч (Project Overview)

**Юу вэ:** Facebook Messenger дээр ажилладаг борлуулалтын AI агент.
**Юу зардаг:** Нуруу сунгалтын тавцан (back-stretching device).
**Дэлгүүр:** "тэгри_"
**Зорилго:** Үйлчлүүлэгчтэй найрсаг харьцаж, бүтээгдэхүүний мэдээлэл өгч,
сонирхсон хүнээс **захиалга (хаяг + утас)** бүрэн авах. Захиалга бүрдмэгц
**Telegram руу notification** автоматаар явуулна.

Бүх бизнес логик (agent prompt, order parsing, dedup, Messenger/Telegram
дуудлага) хоёр файлд багтсан жижиг, stateless бус (in-memory) FastAPI
үйлчилгээ юм — фреймворк, ORM, миграци гэх мэт нэмэлт давхарга байхгүй.

---

## Repository бүтэц

```
main.py           FastAPI app: webhook endpoints, system prompt, order
                   parsing, dedup, in-memory conversation history, logging
telegram.py        send_lead_notification() — захиалгын мэдээллийг Telegram
                   рүү Markdown хэлбэрээр илгээнэ
requirements.txt   fastapi, uvicorn, httpx, openai (DeepSeek-ийн OpenAI-compatible
                   endpoint дуудахад ашиглана), python-dotenv
render.yaml        Render.com дээрх deploy тохиргоо (service name, build/start
                   command, env var жагсаалт)
.env.example       Локал хөгжүүлэлтэд шаардлагатай орчны хувьсагчдын загвар
.gitignore         .env, __pycache__/, *.pyc, .DS_Store
```

Тест, миграци, тусдаа `config.py`/`models.py` гэх мэт файл байхгүй — бүх
зүйл `main.py`, `telegram.py` дотор шууд бичигдсэн. Шинэ файл нэмэхээсээ
өмнө энэ хэмжээнд тохирох эсэхийг бод (over-engineering хийхгүй байх).

---

## Tech stack

- **Backend:** Python 3.11 + FastAPI (async endpoints, `uvicorn` ажиллуулна)
- **LLM:** DeepSeek `deepseek-chat` — OpenAI Python SDK-г
  `base_url="https://api.deepseek.com"`, `api_key=DEEPSEEK_API_KEY`-тэйгээр
  ашигладаг (`main.py:26-29`). ⚠️ Хуучин design нь OpenAI `gpt-4o-mini`
  ашиглах байсан ч `254a943` commit-ээр DeepSeek рүү шилжсэн — код нь одоо
  зөвхөн DeepSeek дэмждэг гэдгийг санаарай.
- **Messaging:** Facebook Messenger (Meta Graph API `v19.0` webhook)
- **Notification:** Telegram bot (`telegram.py` доторх `send_lead_notification`)
- **Deploy:** Render.com (`messenger-agent-y9j0.onrender.com`,
  тохиргоо `render.yaml`-д)
- **State:** Бүх зүйл процессын санах ойд (`dict`/`OrderedDict`) хадгалагддаг —
  доорх "In-memory төлөв ба хязгаарлалт" хэсгийг үз.

---

## Development workflow

Локал ажиллуулах:

```bash
pip install -r requirements.txt
cp .env.example .env        # утга бөглөнө (доор тайлбарласан)
uvicorn main:app --reload --port 8000
```

Шаардлагатай орчны хувьсагч (`.env`, `.env.example`-д жагсаасан):

| Хувьсагч | Зориулалт |
|---|---|
| `VERIFY_TOKEN` | Meta webhook GET verify (`hub.verify_token`-тэй тааруулна) |
| `PAGE_ACCESS_TOKEN` | Messenger руу мессеж илгээхэд ашиглах Page access token |
| `DEEPSEEK_API_KEY` | DeepSeek `deepseek-chat` дуудлагад |
| `TELEGRAM_BOT_TOKEN` | Lead notification bot-ын token |
| `TELEGRAM_CHAT_ID` | Notification очих chat/group ID |

Webhook-ийг локалоор турших бол ngrok/cloudflared зэргээр public URL гарган
Meta App Dashboard дээр webhook subscription-д бүртгэ (эсвэл `curl`-аар
`/webhook` POST руу Messenger-ийн жишиг payload илгээж чадна).

**Deploy:** `main` (эсвэл feature branch) руу push хийхэд Render дээр
autodeploy тохируулагдсан бол шинэчлэгдэнэ. `render.yaml`-д `buildCommand`
(`pip install -r requirements.txt`) болон `startCommand`
(`uvicorn main:app --host 0.0.0.0 --port $PORT`) тодорхойлогдсон. Орчны
хувьсагчдыг Render dashboard дээр `sync: false` учир гараар тохируулна.

**Тест:** Одоогоор автомат тест байхгүй (`pytest` гэх мэт framework
нэмээгүй). Өөрчлөлт хийсний дараа дор хаяж:
1. `python -c "import main"` — syntax/import алдаагүй эсэхийг шалга.
2. Логик өөрчлөлт бол `extract_order()`-ийг жишээ текстээр гараар турш.

---

## Кодын дүрэм (Conventions)

- HTTP дуудлага бүрт timeout тавь. `httpx.ReadTimeout`/бусад network алдаа
  дахин гаргуулж болохгүй — `send_messenger_reply` (`main.py:155`) болон
  `send_lead_notification` (`telegram.py:8`) хоёулаа алдааг барьж (`except
  httpx.ReadTimeout, httpx.ConnectTimeout, httpx.RequestError`) чимээгүй
  унана; шинэ HTTP дуудлага нэмэхдээ энэ pattern-ыг дага.
- Бүх хэрэглэгчид рүү гарах текст **монгол хэлээр**.
- Нууц утга (token, API key) `.env`-ээс унш (`os.getenv`). Кодонд hardcode
  хийж болохгүй.
- Meta app одоогоор Development mode — `pages_messaging` App Review хийгдэх
  хүртэл зөвхөн admin/developer/tester role-той хүмүүст хариулна гэдгийг
  санаж бай.
- **Logging:** `logging.basicConfig(level=logging.INFO, ...)` тохируулагдсан
  (`main.py:17-18`, logger нэр `"tegri"`). Ирсэн мессеж, давхардал алгасалт,
  агентын хариулт, захиалга илэрсэн эсэхийг `log.info(...)`-оор бүртгэнэ;
  `call_openai` дотор алдаа гарвал `log.exception(...)`. Шинэ салбар
  нэмэхдээ адил түвшний structured logging-ийг үргэлжлүүл.
- **Дахин боловсруулалтаас сэргийлэх (dedup):** Meta webhook нь мессежийг
  давхардуулж (retry) илгээж болдог тул `already_seen(mid)` (`main.py:40`)
  функцээр `message.mid`-ийг шалгана. Аль хэдийн үзсэн бол алгасна. `OrderedDict`
  дээр хамгийн эрт орсон mid-ийг хасаж хамгийн ихдээ `_SEEN_CAP = 1000`
  бичлэг хадгална. Энэ бол **single-process, in-memory** dedup — доорх
  хязгаарлалтыг үз.
- **Async:** Webhook handler-ууд `async def` бөгөөд OpenAI/DeepSeek дуудлага
  (blocking) хийхдээ `asyncio.to_thread(call_openai, history)` ашиглан event
  loop-ыг бүү блоклож бай.
- Шинэ endpoint/функц нэмэхдээ одоо байгаа файлын бүтцэд нийцүүлж (`main.py`
  дотор webhook/orchestration, `telegram.py` дотор Telegram-тай холбоотой л
  зүйл) — тусдаа модуль/давхарга шаардлагагүй бол бүү нэм.

---

## In-memory төлөв ба хязгаарлалт

`main.py` дотор хоёр процессын санах ойд хадгалагдах бүтэц бий:

- `conversation_histories: dict[str, list]` — sender_id тус бүрийн харилцан
  ярианы түүх (`role`/`content`), OpenAI-compatible chat history формат.
- `_seen_message_ids: OrderedDict[str, None]` — дахин боловсруулалтаас
  сэргийлэх dedup cache (дээрх conventions-ийг үз).

Эдгээр нь **process restart, deploy, эсвэл олон worker/instance ажиллуулах
үед хадгалагдахгүй/synchronize хийгдэхгүй**. Одоогийн deploy (нэг Render
web service, нэг instance) дор энэ бэрхшээлгүй ч, хэрэв дараа нь horizontal
scaling эсвэл олон worker (`uvicorn --workers N`) руу шилжвэл эдгээрийг
Redis/DB зэрэг shared store руу шилжүүлэх шаардлагатай болно гэдгийг санаж
бай — энэ бол одоогийн scope-оос гадуур тул урьдчилж дизайн хийхгүй байя.

---

## Агентын system prompt

Энэ prompt нь `main.py:48-122` дотор `SYSTEM_PROMPT` хувьсагч болон бодитоор
байрладаг бөгөөд DeepSeek дуудлагын (`call_openai`, `main.py:146`) `system`
message болгон ашиглагдаж байна. **Доорх блок кодтой яг таарч байх ёстой —
аль нэгийг нь өөрчлөхөд нөгөөг нь мөн шинэчил.**

```text
# ҮҮРЭГ
Чи бол "тэгри_" дэлгүүрийн борлуулалтын туслах. Нуруу сунгалтын тавцан зардаг.
Зорилго: үйлчлүүлэгчтэй найрсаг харьцаж, бүтээгдэхүүний талаар мэдээлэл өгч,
сонирхсон хүнээс ЗАХИАЛГА (хаяг + утасны дугаар) бүрэн авах.

# ХАРИЛЦАХ ЗАРЧИМ
- Зөвхөн монгол хэлээр, эелдэг, ойлгомжтой, товч бич.
- Эмойжи хэт их бүү хэрэглэ (хааяа 1-2 хүртэл боломжтой).
- Мэдэхгүй зүйлээ зохиож бүү хэл. Эргэлзвэл 99194217 руу холбогдохыг санал болго.
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

Захиалга бүрэн биш бол энэ JSON-ийг бүү гарга.
```

Үнэ (`450000`) нь `bed7c42` → `160756c` (өөрчилсөн) → `370c021` ("Revert
product price to 450,000₮") commit-үүдийн дараа одоогийн жинхэнэ үнэ болж
тогтсон. Prompt дэх `price`/`delivery`/`assembly` тоо болон `telegram.py`
доторх default утгууд (`telegram.py:13-15`) хоёулаа `450000`/`10000`/`10000`
байгаа эсэхийг үнэ өөрчлөх бүрд хамт шинэчил.

---

## Захиалга илрүүлэх + Telegram notification логик (бодитоор хэрэгжсэн)

Энэ логик аль хэдийн `main.py` дотор бүрэн хэрэгжсэн (доор тайлбар нь TODO
биш, одоогийн кодын баримтжуулалт):

1. **`extract_order(reply_text)`** (`main.py:127-143`) — `<<<ORDER>>>...
   <<<ORDER>>>` regex-ээр (`ORDER_RE`, `main.py:124`) блокийг олж, дотор нь
   JSON parse хийнэ.
   - Блок олдоогүй бол `(reply_text.strip(), None)` буцаана.
   - JSON parse алдаатай бол текстийг цэвэрлэсэн хэвээр, `order=None`
     буцаана (захиалга алдахгүйн тулд).
   - `phone` эсвэл `address` дутуу бол мөн `order=None` буцаана.
   - Амжилттай бол `(clean_text, order_dict)` буцаана.
2. **`handle_webhook`** (`main.py:180-224`) — Messenger event бүрийг:
   давхардал шалгаж (`already_seen`) → conversation history-д хэрэглэгчийн
   мессежийг нэмж → `asyncio.to_thread(call_openai, history)`-оор DeepSeek
   дуудаж → `extract_order`-оор задалж → цэвэр текстийг `send_messenger_reply`-
   ээр Messenger рүү → `order` байвал `send_lead_notification(order)`-оор
   Telegram рүү явуулна.
3. **`send_lead_notification(order)`** (`telegram.py:8-37`) — order dict-ийг
   Markdown-оор форматлаад (`parse_mode: "Markdown"`), Telegram Bot API-гийн
   `sendMessage`-ээр илгээнэ. Дэлгүүрийн нэрэн доtorh `тэгри\_`-ийн зурвар
   зураас (`\_`) нь Telegram Markdown дотор `_` тусгай тэмдэгт байдаг тул
   escape хийсэн — шинэ Markdown текст нэмэхдээ `_`, `*`, `` ` ``, `[` зэрэг
   тусгай тэмдэгтийг мөн escape хийхээ бүү мартаарай.

---

## ⚠️ Анхаар

- `extract_order` JSON-ийг хэрэглэгчид **хэзээ ч** харуулахгүй байх ёстой —
  заавал `clean_text`-ийг л Messenger руу буцаа (`main.py`-д аль хэдийн
  ингэж хэрэгжсэн, зохион бүтээлтгүйгээр өөрчлөхгүй байх).
- Захиалгын JSON хэт эрт (хаяг/утас дутуу) ирвэл notification явуулахгүй —
  `extract_order` дотор аль хэдийн шалгасан.
- Эмнэлзүйн зөвлөгөө шаардсан эсвэл маргаантай асуулт гарвал агент 99194217
  руу холбогдохыг л санал болгоно. Энэ нь эмчилгээний хариуцлагаас зайлсхийх ёс.
- `call_openai` алдаа гаргавал (`main.py:208-210`) тухайн мессежийг
  алгасаад дараагийнх руу үргэлжилнэ — хэрэглэгчид алдааны мессеж
  буцаадаггүй тул retry/fallback логик нэмэх шаардлагатай эсэхийг ажиглаж
  бай.
- `conversation_histories`/`_seen_message_ids` нь process restart-аар
  устана — deploy болгонд шинэ хэрэглэгчийн харилцан яриа "шинээр эхэлдэг"
  гэдгийг тооцоолж бай (дээрх "In-memory төлөв" хэсгийг үз).
