import asyncio
import logging
import os
import re
import sqlite3
import time
from io import BytesIO

from aiogram import Bot, Dispatcher, F, types
from aiogram.enums import ParseMode
from aiogram.filters import Command
from PIL import Image
import pytesseract

# =================== НАСТРОЙКИ ===================
API_TOKEN = "7919356847:AAHHdCT180UMA4cNpwOWNFPwILIRFDLu2E0"          # <— вставь свой токен
ADMINS = [6194786755, 8183369219]          # <— два user_id админов (только им бот отвечает)

# Для Termux путь указывать не нужно, но на всякий случай жёстко укажем бинарник
pytesseract.pytesseract.tesseract_cmd = "tesseract"

logging.basicConfig(level=logging.INFO)
bot = Bot(API_TOKEN, parse_mode=ParseMode.HTML)
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
    # Таблица статистики
    cur.execute("""
        CREATE TABLE IF NOT EXISTS stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            views INTEGER NOT NULL,
            price INTEGER NOT NULL,
            ts   DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Таблица тарифов
    cur.execute("""
        CREATE TABLE IF NOT EXISTS tariffs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            min_views INTEGER NOT NULL,
            max_views INTEGER NOT NULL,
            price INTEGER NOT NULL,
            UNIQUE(min_views, max_views)
        )
    """)
    # Вставляем дефолтные тарифы, если их нет
    defaults = [
        (100, 200, 55),
        (250, 500, 175),
        (500, 2000, 300),
    ]
    for mn, mx, pr in defaults:
        cur.execute("""
            INSERT OR IGNORE INTO tariffs (min_views, max_views, price)
            VALUES (?, ?, ?)
        """, (mn, mx, pr))
    conn.commit()
    conn.close()

def tariffs_all():
    conn = db_connect()
    rows = conn.execute("SELECT min_views, max_views, price FROM tariffs ORDER BY min_views ASC").fetchall()
    conn.close()
    return rows

def get_price_for_views(views: int) -> int:
    conn = db_connect()
    row = conn.execute(
        "SELECT price FROM tariffs WHERE ? BETWEEN min_views AND max_views LIMIT 1",
        (views,)
    ).fetchone()
    conn.close()
    return row["price"] if row else 0

def set_price_for_range(mn: int, mx: int, price: int):
    conn = db_connect()
    cur = conn.cursor()
    # пробуем обновить; если нет — вставим
    cur.execute("""
        UPDATE tariffs SET price=? WHERE min_views=? AND max_views=?
    """, (price, mn, mx))
    if cur.rowcount == 0:
        cur.execute("""
            INSERT INTO tariffs (min_views, max_views, price) VALUES (?, ?, ?)
        """, (mn, mx, price))
    conn.commit()
    conn.close()

def add_stat_row(views: int, price: int):
    conn = db_connect()
    conn.execute("INSERT INTO stats (views, price) VALUES (?, ?)", (views, price))
    conn.commit()
    conn.close()

def get_all_stats():
    conn = db_connect()
    rows = conn.execute("SELECT views, price, ts FROM stats ORDER BY id ASC").fetchall()
    totals = conn.execute("SELECT COALESCE(SUM(views),0) AS tv, COALESCE(SUM(price),0) AS tr FROM stats").fetchone()
    conn.close()
    return rows, totals["tv"], totals["tr"]

def reset_all_stats():
    conn = db_connect()
    conn.execute("DELETE FROM stats")
    conn.commit()
    conn.close()


# =================== УТИЛИТЫ ===================
def is_admin(user_id: int) -> bool:
    return user_id in ADMINS

def extract_views_from_text(text: str):
    """
    Достаём ВСЕ потенциальные количества просмотров из текста.
    Поддержка форматов:
      - 1 234 / 1,234 / 1234
      - 2.3k / 2,3k / 2.3K / 2,3К / 2к / 2К (тыс)
      - 1.2m / 1,2m / 1.2M / 1,2М / 1м / 1М (млн)
    Возвращаем список int.
    """
    if not text:
        return []

    # нормализуем пробелы и запятые
    t = text.replace("\u00A0", " ").lower()

    results = []

    # k / m с дробями
    for m in re.findall(r"(\d+(?:[.,]\d+)?)\s*([kmкм])", t, flags=re.IGNORECASE):
        num_str, suffix = m
        num = float(num_str.replace(",", "."))
        if suffix in ("k", "к"):
            val = int(round(num * 1000))
        else:  # m / м
            val = int(round(num * 1_000_000))
        results.append(val)

    # числа с разделителями тысяч: 1 234, 1,234
    for m in re.findall(r"\b\d{1,3}(?:[ .,]\d{3})+\b", t):
        val = int(re.sub(r"[ .,]", "", m))
        results.append(val)

    # обычные целые
    for m in re.findall(r"\b\d+\b", t):
        results.append(int(m))

    # фильтр "похоже на просмотры"
    filtered = [n for n in results if 50 <= n <= 5_000_000]

    # можно не уникализировать, т.к. на скрине у каждого видео свои цифры
    return filtered

async def process_views_and_reply(message: types.Message, views_list: list[int]):
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

    if added_lines:
        chunks = []
        # чтобы не упереться в лимиты, разобьём при необходимости
        header = "✅ Добавлено:\n"
        body = "\n".join(added_lines)
        summary = f"\n\n📊 Итого за добавление:\n👁 {total_views} просмотров\n💰 {total_rub} руб"
        text = header + body + summary
        # Telegram ограничение ~4096 символов
        for i in range(0, len(text), 3500):
            chunks.append(text[i:i+3500])
        for idx, ch in enumerate(chunks):
            await message.answer(ch if idx == 0 else ch)
    else:
        await message.answer("⚠️ Нашёл числа, но ни одно не попало в тарифы.")


# =================== ХЕНДЛЕРЫ ===================
@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    text = (
        "👋 Привет! Я бот для подсчёта стоимости просмотров.\n\n"
        "📊 Текущие тарифы (за одно видео):\n" +
        "\n".join([f"{mn}-{mx} просмотров = {pr} руб" for mn, mx, pr in tariffs_all()]) +
        "\n\n⚡ Команды:\n"
        "/ping — проверить задержку\n"
        "/stat — показать всю статистику\n"
        "/reset — очистить статистику\n"
        "/prices — показать тарифы\n"
        "/setprice <min>-<max> <price> — изменить цену диапазона\n\n"
        "💡 Отправь мне число просмотров (можно несколько через запятую/пробел)\n"
        "или пришли скрин — я всё распознаю и посчитаю."
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

@dp.message(Command("stat"))
async def stat_cmd(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    rows, total_views, total_rub = get_all_stats()
    if not rows:
        await message.answer("📊 Статистика пуста.")
        return
    lines = []
    for idx, r in enumerate(rows, 1):
        lines.append(f"{idx}. {r['views']} просмотров = {r['price']} руб  ({r['ts']})")
    text = "\n".join(lines)
    text += f"\n\n📊 ИТОГО:\n👁 Просмотров: {total_views}\n💰 Сумма: {total_rub} руб"
    # разбиение по 3500 символов
    for i in range(0, len(text), 3500):
        await message.answer(text[i:i+3500])

@dp.message(Command("reset"))
async def reset_cmd(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    reset_all_stats()
    await message.answer("♻️ Статистика полностью очищена.")

@dp.message(Command("prices"))
async def prices_cmd(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    t = "\n".join([f"{mn}-{mx} просмотров = {pr} руб" for mn, mx, pr in tariffs_all()])
    await message.answer("📋 Текущие тарифы:\n" + (t or "нет тарифов"))

@dp.message(Command("setprice"))
async def setprice_cmd(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    # Ожидаем формат: /setprice 100-200 60  ИЛИ  /setprice 100 200 60
    try:
        args = message.text.split(maxsplit=1)[1]
    except Exception:
        await message.answer("Использование: <code>/setprice 100-200 60</code> или <code>/setprice 100 200 60</code>")
        return

    mn = mx = price = None
    # попытка как "100-200 60"
    m = re.match(r"\s*(\d+)\s*-\s*(\d+)\s+(\d+)\s*$", args)
    if m:
        mn, mx, price = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        # попытка как "100 200 60"
        m2 = re.match(r"\s*(\d+)\s+(\d+)\s+(\d+)\s*$", args)
        if m2:
            mn, mx, price = int(m2.group(1)), int(m2.group(2)), int(m2.group(3))

    if not all([mn, mx, price]) or mn >= mx:
        await message.answer("❌ Неверные параметры. Пример: <code>/setprice 250-500 180</code>")
        return

    set_price_for_range(mn, mx, price)
    await message.answer(f"✅ Обновил тариф: {mn}-{mx} просмотров = {price} руб")

# ---------- Ручной ввод чисел (одно или много) ----------
@dp.message(F.text)
async def handle_text_numbers(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    # достаём все возможные числа из текста (поддержка 1.2k и т.п.)
    views_list = extract_views_from_text(message.text)
    if not views_list:
        # если ничего не нашли — молчим, чтобы не мешать
        return
    await process_views_and_reply(message, views_list)

# ---------- Обработка скринов ----------
@dp.message(F.photo)
async def handle_photo(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    try:
        # скачиваем фото лучшего качества
        file = await bot.get_file(message.photo[-1].file_id)
        downloaded = await bot.download_file(file.file_path)

        # читаем как PIL.Image
        image = Image.open(BytesIO(downloaded.read()))

        # OCR
        text = pytesseract.image_to_string(image, lang="eng+rus")
        views_list = extract_views_from_text(text)

        if not views_list:
            await message.answer("⚠️ Не нашёл подходящие числа на скрине.")
            return

        await process_views_and_reply(message, views_list)

    except Exception as e:
        await message.answer(f"❌ Ошибка обработки фото: {e}")

# =================== ЗАПУСК ===================
async def main():
    db_init()
    print("🤖 Бот запущен")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
