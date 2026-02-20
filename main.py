import os
import json
import random
import logging
from pathlib import Path
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

logging.basicConfig(level=logging.INFO)

DATA_PATH = Path("phrases.json")

def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip())
    except Exception:
        return default

TOKEN = os.getenv("BOT_TOKEN", "").strip()

# Админы: "123,456" (через запятую). Если пусто — админом станет первый, кто выполнит /claim
ADMIN_IDS_RAW = os.getenv("ADMIN_IDS", "").replace(" ", "").strip()
ADMIN_IDS = {int(x) for x in ADMIN_IDS_RAW.split(",") if x.isdigit()}

MIN_MINUTES_DEFAULT = env_int("MIN_MINUTES", 5)
MAX_MINUTES_DEFAULT = env_int("MAX_MINUTES", 180)

def load_data():
    if DATA_PATH.exists():
        try:
            return json.loads(DATA_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "chat_id": None,
        "enabled": False,
        "mode": "random",      # random | cycle
        "phrases": [],
        "cycle_index": 0,
        "last_phrase": None,   # чтобы не повторять подряд
        "min_minutes": MIN_MINUTES_DEFAULT,
        "max_minutes": MAX_MINUTES_DEFAULT,
    }

def save_data(data: dict):
    DATA_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def is_admin(update: Update) -> bool:
    uid = update.effective_user.id if update.effective_user else None
    print("DEBUG USER ID:", uid)
    print("ADMIN_IDS:", ADMIN_IDS)
    return uid in ADMIN_IDS
    uid = update.effective_user.id if update.effective_user else None
    return uid in ADMIN_IDS

async def deny(update: Update):
    await update.message.reply_text("⛔ Команды доступны только владельцу (ADMIN_IDS).")

def pick_phrase(data: dict) -> str | None:
    phrases = data.get("phrases", [])
    if not phrases:
        return None

    if data.get("mode") == "cycle":
        i = int(data.get("cycle_index", 0)) % len(phrases)
        phrase = phrases[i]
        data["cycle_index"] = (i + 1) % len(phrases)
        return phrase

    # random + не повторять подряд
    last = data.get("last_phrase")
    if len(phrases) == 1:
        return phrases[0]
    candidates = [p for p in phrases if p != last]
    return random.choice(candidates) if candidates else random.choice(phrases)

def random_delay_seconds(data: dict) -> int:
    mn = int(data.get("min_minutes", 5))
    mx = int(data.get("max_minutes", 180))
    if mx < mn:
        mn, mx = mx, mn
    return random.randint(max(1, mn), max(1, mx)) * 60

def schedule_next(context: ContextTypes.DEFAULT_TYPE, seconds_from_now: int):
    # удаляем старую задачу, чтобы не плодить таймеры
    for j in context.job_queue.get_jobs_by_name("next_phrase"):
        j.schedule_removal()
    context.job_queue.run_once(send_and_reschedule, when=seconds_from_now, name="next_phrase")

async def send_and_reschedule(context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    if not data.get("enabled"):
        return
    chat_id = data.get("chat_id")
    if not chat_id:
        return

    phrase = pick_phrase(data)
    if phrase:
        data["last_phrase"] = phrase
        save_data(data)
        try:
            await context.bot.send_message(chat_id=chat_id, text=phrase)
        except Exception:
            logging.exception("Failed to send message")

    delay = random_delay_seconds(data)
    schedule_next(context, seconds_from_now=delay)

# ----------------- команды -----------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Я бот для беседы: периодически вкидываю фразы в чат.\n\n"
        "Команды:\n"
        "/myid — показать твой user_id (доступно всем)\n"
        "/claim — назначить себя владельцем (если ADMIN_IDS пуст)\n\n"
        "Команды владельца:\n"
        "/setchat — закрепить этот чат\n"
        "/add <фраза> — добавить фразу\n"
        "/list — список фраз\n"
        "/del <номер> — удалить фразу\n"
        "/mode random|cycle — случайно/по кругу\n"
        "/range <мин> <макс> — пауза в минутах\n"
        "/on — включить\n"
        "/off — выключить\n"
        "/test — отправить одну фразу сейчас\n"
        "/status — показать настройки"
    )

# ✅ /myid доступен всем (исправление)
async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id if update.effective_user else None
    await update.message.reply_text(f"Твой user_id: {uid}")

# если ADMIN_IDS не задан, можно один раз "забрать" владение
async def claim(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global ADMIN_IDS
    uid = update.effective_user.id if update.effective_user else None
    if not uid:
        return
    if ADMIN_IDS:
        return await update.message.reply_text("Владелец уже задан через ADMIN_IDS.")
    ADMIN_IDS = {uid}
    await update.message.reply_text(
        f"✅ Готово. Ты владелец (ADMIN_IDS={uid}).\n"
        "Теперь добавь этот id в Secrets как ADMIN_IDS и перезапусти, чтобы закрепилось."
    )

async def setchat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return await deny(update)
    data = load_data()
    data["chat_id"] = update.effective_chat.id
    save_data(data)
    await update.message.reply_text("✅ Этот чат закреплён как целевой.")

async def add_phrase(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return await deny(update)
    parts = update.message.text.split(" ", 1)
    if len(parts) < 2 or not parts[1].strip():
        return await update.message.reply_text("Используй: /add <фраза>")
    data = load_data()
    data.setdefault("phrases", []).append(parts[1].strip())
    save_data(data)
    await update.message.reply_text(f"✅ Добавлено. Фраз: {len(data['phrases'])}")

async def list_phrases(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return await deny(update)
    data = load_data()
    phrases = data.get("phrases", [])
    if not phrases:
        return await update.message.reply_text("Список пуст. Добавь: /add <фраза>")
    msg = "\n".join([f"{i+1}. {p}" for i, p in enumerate(phrases)])
    await update.message.reply_text(msg)

async def del_phrase(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return await deny(update)
    parts = update.message.text.split(" ", 1)
    if len(parts) < 2:
        return await update.message.reply_text("Используй: /del <номер>")
    try:
        idx = int(parts[1].strip()) - 1
    except ValueError:
        return await update.message.reply_text("Номер должен быть числом.")
    data = load_data()
    phrases = data.get("phrases", [])
    if idx < 0 or idx >= len(phrases):
        return await update.message.reply_text("Нет такой фразы по номеру.")
    removed = phrases.pop(idx)
    data["phrases"] = phrases
    if data.get("last_phrase") == removed:
        data["last_phrase"] = None
    save_data(data)
    await update.message.reply_text(f"🗑️ Удалено: {removed}")

async def mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return await deny(update)
    parts = update.message.text.split(" ", 1)
    if len(parts) < 2:
        return await update.message.reply_text("Используй: /mode random|cycle")
    m = parts[1].strip().lower()
    if m not in ("random", "cycle"):
        return await update.message.reply_text("Доступно: random или cycle")
    data = load_data()
    data["mode"] = m
    save_data(data)
    await update.message.reply_text(f"✅ Режим: {m}")

async def range_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return await deny(update)
    parts = update.message.text.split()
    if len(parts) != 3:
        return await update.message.reply_text("Используй: /range <мин> <макс> (например /range 5 180)")
    try:
        mn = int(parts[1]); mx = int(parts[2])
        if mn < 1 or mx < 1:
            raise ValueError
    except ValueError:
        return await update.message.reply_text("Мин/макс должны быть целыми числами >= 1.")
    data = load_data()
    data["min_minutes"] = mn
    data["max_minutes"] = mx
    save_data(data)
    await update.message.reply_text(f"✅ Диапазон установлен: {mn}–{mx} минут")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return await deny(update)
    data = load_data()
    await update.message.reply_text(
        "⚙️ Статус:\n"
        f"- enabled: {data.get('enabled')}\n"
        f"- chat_id: {data.get('chat_id')}\n"
        f"- phrases: {len(data.get('phrases', []))}\n"
        f"- mode: {data.get('mode')}\n"
        f"- range: {data.get('min_minutes')}–{data.get('max_minutes')} минут"
    )

async def on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return await deny(update)
    data = load_data()
    if not data.get("chat_id"):
        return await update.message.reply_text("Сначала в нужном чате: /setchat")
    if not data.get("phrases"):
        return await update.message.reply_text("Сначала добавь фразы: /add <фраза>")

    data["enabled"] = True
    save_data(data)

    first_delay = random.randint(10, 60)  # стартанём быстро
    schedule_next(context, seconds_from_now=first_delay)
    await update.message.reply_text("✅ Включено. Фразы будут появляться с рандомной паузой 5–180 минут.")

async def off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return await deny(update)
    data = load_data()
    data["enabled"] = False
    save_data(data)
    for j in context.job_queue.get_jobs_by_name("next_phrase"):
        j.schedule_removal()
    await update.message.reply_text("❌ Выключено.")

async def test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return await deny(update)
    data = load_data()
    if not data.get("chat_id"):
        return await update.message.reply_text("Сначала /setchat в нужном чате.")
    phrase = pick_phrase(data)
    if not phrase:
        return await update.message.reply_text("Список фраз пуст: /add <фраза>")
    data["last_phrase"] = phrase
    save_data(data)
    await context.bot.send_message(chat_id=data["chat_id"], text=phrase)
    await update.message.reply_text("✅ Отправил одну фразу.")

def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN не задан в Secrets.")

    if not ADMIN_IDS:
        logging.warning("ADMIN_IDS пуст — используй /claim один раз, затем сохрани id в Secrets как ADMIN_IDS.")

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", myid))     # <-- всем доступно
    app.add_handler(CommandHandler("claim", claim))   # <-- всем, но работает только если ADMIN_IDS пуст

    # команды владельца
    app.add_handler(CommandHandler("setchat", setchat))
    app.add_handler(CommandHandler("add", add_phrase))
    app.add_handler(CommandHandler("list", list_phrases))
    app.add_handler(CommandHandler("del", del_phrase))
    app.add_handler(CommandHandler("mode", mode))
    app.add_handler(CommandHandler("range", range_cmd))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("on", on))
    app.add_handler(CommandHandler("off", off))
    app.add_handler(CommandHandler("test", test))

    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
