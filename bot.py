import asyncio
import logging
import re
import sqlite3
import time
from io import BytesIO

from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from PIL import Image
import pytesseract

# =================== НАСТРОЙКИ ===================
API_TOKEN = "8112953231:AAHe0aRWs7fUfoUqaTXdc5bwBBqP0JZnUOE"
ADMINS = [6194786755]  # ← два Telegram user_id, которым доступен бот

# Для Termux путь указывать не нужно
pytesseract.pytesseract.tesseract_cmd = "tesseract"

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

DB_PATH = "bot.db"

# =================== БАЗА ДАННЫХ ===================
def db_connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def db_init():
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            views INTEGER NOT NULL,
            price INTEGER NOT NULL,
            ts DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS tariffs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            min_views INTEGER NOT NULL,
            max_views INTEGER NOT NULL,
            price INTEGER NOT NULL,
            UNIQUE(min_views, max_views)
        )
    """)
    defaults = [(1, 249, 55),(250, 499, 175),(500, 20000, 300)]
    for mn, mx, pr in defaults:
        cur.execute("INSERT OR IGNORE INTO tariffs (min_views, max_views, price) VALUES (?, ?, ?)", (mn, mx, pr))
    conn.commit()
    conn.close()

def tariffs_all():
    conn = db_connect()
    rows = conn.execute("SELECT min_views, max_views, price FROM tariffs ORDER BY min_views ASC").fetchall()
    conn.close()
    return rows

def get_price_for_views(views: int) -> int:
    conn = db_connect()
    row = conn.execute("SELECT price FROM tariffs WHERE ? BETWEEN min_views AND max_views LIMIT 1",(views,)).fetchone()
    conn.close()
    return int(row["price"]) if row else 0

def set_price_for_range(mn: int, mx: int, price: int):
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("UPDATE tariffs SET price=? WHERE min_views=? AND max_views=?", (price, mn, mx))
    if cur.rowcount == 0:
        cur.execute("INSERT INTO tariffs (min_views, max_views, price) VALUES (?, ?, ?)", (mn, mx, price))
    conn.commit()
    conn.close()

def add_stat_row(views: int, price: int):
    conn = db_connect()
    conn.execute("INSERT INTO stats (views, price) VALUES (?, ?)", (views, price))
    conn.commit()
    conn.close()

def get_all_stats():
    conn = db_connect()
    rows = conn.execute("SELECT id, views, price, ts FROM stats ORDER BY id ASC").fetchall()
    totals = conn.execute("SELECT COALESCE(SUM(views),0) AS tv, COALESCE(SUM(price),0) AS tr FROM stats").fetchone()
    conn.close()
    return rows, int(totals["tv"]), int(totals["tr"])

def reset_all_stats():
    conn = db_connect()
    conn.execute("DELETE FROM stats")
    conn.commit()
    conn.close()

def update_stat_row(row_id: int, new_views: int) -> bool:
    new_price = get_price_for_views(new_views)
    if new_price == 0:
        return False
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("UPDATE stats SET views=?, price=? WHERE id=?", (new_views, new_price, row_id))
    conn.commit()
    changed = cur.rowcount > 0
    conn.close()
    return changed

# =================== УТИЛИТЫ ===================
def is_admin(uid: int) -> bool:
    return uid in ADMINS

def extract_views_from_text(text: str):
    if not text:
        return []

    t = text.replace("\u00A0", " ").lower()
    results = []

    # k/m с дробями
    for num_str, suffix in re.findall(r"(\d+(?:[.,]\d+)?)\s*([kmкм])", t, flags=re.IGNORECASE):
        num = float(num_str.replace(",", "."))
        if suffix in ("k", "к"): results.append(int(round(num * 1000)))
        else: results.append(int(round(num * 1_000_000)))

    # числа с разделителями тысяч
    for m in re.findall(r"\b\d{1,3}(?:[ .,]\d{3})+\b", t):
        results.append(int(re.sub(r"[ .,]", "", m)))

    # обычные целые
    for m in re.findall(r"\b\d+\b", t):
        results.append(int(m))

    # фильтр "похоже на просмотры"
    return [n for n in results if 50 <= n <= 5_000_000]

async def process_views_and_reply(message: types.Message, views_list):
    added_lines = []
    total_views = 0
    total_rub = 0
    for v in views_list:
        price = get_price_for_views(v)
        if price > 0:
            add_stat_row(v, price)
            added_lines.append(f"{v} просмотров = {price} руб")
            total_views += v
            total_rub += price
        else:
            added_lines.append(f"⚠️ {v} — нет тарифа")
    if added_lines:
        text = "✅ Добавлено:\n" + "\n".join(added_lines)
        text += f"\n\n📊 Итого: {total_views} просмотров = {total_rub} руб"
        for i in range(0, len(text), 3500):
            await message.answer(text[i:i+3500])
    else:
        await message.answer("⚠️ Нашёл числа, но ни одно не попало в тарифы.")

# =================== ХЕНДЛЕРЫ ===================
@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    kb = InlineKeyboardBuilder()
    kb.button(text="📊 Статистика", callback_data="show_stats")
    kb.button(text="ℹ️ Помощь", callback_data="show_help")
    kb.button(text="💰 Тарифы", callback_data="show_prices")
    kb.adjust(1)
    photo_url = "https://img.freepik.com/premium-vector/doodle-cosmos-lettering-childish-style-hand-drawn-abstract-space-text-hello-world-black-white_637178-1441.jpg"
    tariffs_text = "\n".join([f"{mn}-{mx} просмотров = {pr} руб" for mn, mx, pr in tariffs_all()])
    await message.answer_photo(photo=photo_url, caption=(
        "👋 <b>Привет!</b> Я бот для подсчёта стоимости просмотров.\n\n"
        "⚡ <b>Команды:</b>\n"
        "/ping — проверить пинг\n"
        "/stat, /stats — показать статистику\n"
        "/reset — очистить статистику\n"
        "/prices — тарифы\n"    
        "/setprice — изменить тариф\n"
        "/editstat — изменить запись\n"
        "/calc — калькулятор\n\n"
        f"📊 <b>Текущие тарифы:</b>\n{tariffs_text or '— (тарифов нет)'}"
    ), reply_markup=kb.as_markup())

@dp.message(Command("help"))
async def help_cmd(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    text = (
        "📖 <b>Справка по командам:</b>\n\n"
        "• /ping — показать задержку.\n"
        "• /stat или /stats — вся статистика добавлений.\n"
        "• /reset — очистить статистику.\n"
        "• /prices — список тарифов.\n"
        "• /setprice <min>-<max> <price> — изменить/создать тариф.\n"
        "• /editstat <id> <views> — изменить число просмотров в записи; цена пересчитается по тарифам.\n"
        "• /calc <views> — калькулятор (узнать цену, без записи в статистику).\n\n"
        "💡 Отправь число просмотров (можно несколько в одном сообщении) или пришли скрин — я распознаю и посчитаю."
    )
    await message.answer(text)

@dp.message(Command("ping"))
async def ping_cmd(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    t0 = time.perf_counter()
    tmp = await message.answer("🏓 Пинг…")
    t1 = time.perf_counter()
    ms = round((t1 - t0) * 1000, 2)
    await tmp.edit_text(f"🟢 Бот работает. Задержка: <b>{ms} мс</b>")

@dp.message(Command("prices"))
async def prices_cmd(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    t = "\n".join([f"{mn}-{mx} просмотров = {pr} руб" for mn, mx, pr in tariffs_all()])
    await message.answer("📋 Текущие тарифы:\n" + (t or "— (нет тарифов)"))

@dp.message(Command("setprice"))
async def setprice_cmd(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    try:
        args = message.text.split(maxsplit=1)[1]
    except Exception:
        await message.answer("Использование: <code>/setprice 100-200 60</code>")
        return
    mn = mx = price = None
    m = re.match(r"\s*(\d+)\s*-\s*(\d+)\s+(\d+)\s*$", args)
    if m: mn, mx, price = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m2 = re.match(r"\s*(\d+)\s+(\d+)\s+(\d+)\s*$", args)
        if m2: mn, mx, price = int(m2.group(1)), int(m2.group(2)), int(m2.group(3))
    if not all([mn, mx, price]) or mn >= mx:
        await message.answer("❌ Неверные параметры. Пример: <code>/setprice 250-500 180</code>")
        return
    set_price_for_range(mn, mx, price)
    await message.answer(f"✅ Тариф обновлён: {mn}-{mx} просмотров = {price} руб")

@dp.message(Command("stat"))
@dp.message(Command("stats"))
async def stat_cmd(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    rows, total_views, total_rub = get_all_stats()
    if not rows:
        await message.answer("📊 Статистика пуста.")
        return
    lines = [f"#{r['id']}. {r['views']} просмотров = {r['price']} руб  ({r['ts']})" for r in rows]
    text = "\n".join(lines) + f"\n\n📊 ИТОГО:\n👁 Просмотров: {total_views}\n💰 Сумма: {total_rub} руб"
    for i in range(0, len(text), 3500):
        await message.answer(text[i:i+3500])

@dp.message(Command("reset"))
async def reset_cmd(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    reset_all_stats()
    await message.answer("♻️ Статистика очищена.")

# ===== Исправленный editstat =====
@dp.message(Command("editstat"))
async def editstat_cmd(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    args = message.text.split()
    if len(args) != 3:
        await message.answer("Использование: <code>/editstat &lt;id&gt; &lt;views&gt;</code>", parse_mode="HTML")
        return
    try:
        row_id = int(args[1])
        new_views = int(args[2])
    except ValueError:
        await message.answer("❌ ID и просмотры должны быть числами.")
        return
    ok = update_stat_row(row_id, new_views)
    if ok:
        await message.answer(f"✏️ Запись #{row_id} обновлена на {new_views} просмотров (цена пересчитана).")
    else:
        await message.answer("❌ Ошибка: записи с таким ID нет или нет тарифа для этих просмотров.")

@dp.message(Command("calc"))
async def calc_cmd(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    try:
        views = int(message.text.split()[1])
    except Exception:
        await message.answer("Использование: <code>/calc 1234</code>")
        return
    price = get_price_for_views(views)
    if price > 0:
        await message.answer(f"🔢 {views} просмотров = {price} руб (по тарифам)")
    else:
        await message.answer("⚠️ Нет подходящего тарифа для такого числа просмотров.")

# ---------- Ручной ввод чисел ----------
@dp.message(F.text)
async def handle_text_numbers(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    views_list = extract_views_from_text(message.text)
    if not views_list:
        return
    await process_views_and_reply(message, views_list)

# ---------- Обработка скринов ----------
@dp.message(F.photo)
async def handle_photo(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    try:
        file = await bot.get_file(message.photo[-1].file_id)
        downloaded = await bot.download_file(file.file_path)
        image = Image.open(BytesIO(downloaded.read()))
        try:
            text = pytesseract.image_to_string(image, lang="eng+rus")
        except Exception:
            text = pytesseract.image_to_string(image, lang="eng")
        views_list = extract_views_from_text(text)
        if not views_list:
            await message.answer("⚠️ Не нашёл подходящие числа на скрине.")
            return
        await process_views_and_reply(message, views_list)
    except Exception as e:
        await message.answer(f"❌ Ошибка обработки фото: {e}")

# ---------- CALLBACK-КНОПКИ ----------
@dp.callback_query(F.data == "show_stats")
async def cb_stats(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return
    rows, total_views, total_rub = get_all_stats()
    if not rows: await callback.message.answer("📊 Статистика пуста.")
    else:
        lines = [f"#{r['id']}. {r['views']} просмотров = {r['price']} руб  ({r['ts']})" for r in rows]
        text = "\n".join(lines) + f"\n\n📊 ИТОГО:\n👁 Просмотров: {total_views}\n💰 Сумма: {total_rub} руб"
        for i in range(0, len(text), 3500):
            await callback.message.answer(text[i:i+3500])
    await callback.answer()

@dp.callback_query(F.data == "show_help")
async def cb_help(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return
    text = (
        "📖 <b>Справка:</b>\n\n"
        "• /ping — задержка.\n"
        "• /stat — вся статистика.\n"
        "• /reset — очистить статистику.\n"
        "• /prices — тарифы.\n"
        "• /setprice — создать/изменить тариф.\n"
        "• /editstat — изменить запись.\n"
        "• /calc — калькулятор.\n\n"
        "💡 Можно кидать просто число или скрин."
    )
    await callback.message.answer(text)
    await callback.answer()

@dp.callback_query(F.data == "show_prices")
async def cb_prices(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return
    t = "\n".join([f"{mn}-{mx} просмотров = {pr} руб" for mn, mx, pr in tariffs_all()])
    await callback.message.answer("📋 Текущие тарифы:\n" + (t or "— (нет тарифов)"))
    await callback.answer()

# =================== ЗАПУСК ===================
async def main():
    db_init()
    print("🤖 Бот запущен")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
