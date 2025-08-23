import asyncio
import time
import sqlite3
import pytesseract
from PIL import Image
import io

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command

TOKEN = "7919356847:AAHHdCT180UMA4cNpwOWNFPwILIRFDLu2E0"  # вставь свой токен сюда
bot = Bot(token=TOKEN)
dp = Dispatcher()

# два админа (замени на свои id из Telegram)
ADMINS = [6194786755, 987654321]

# Подключение к базе
conn = sqlite3.connect("stats.db")
cursor = conn.cursor()

# Таблица для статистики
cursor.execute("""
CREATE TABLE IF NOT EXISTS stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    views INTEGER,
    price INTEGER
)
""")
conn.commit()


# Проверка админа
def is_admin(user_id: int) -> bool:
    return user_id in ADMINS


# Цены по тарифам
def get_price(views: int) -> int:
    if 100 <= views <= 200:
        return 55
    elif 250 <= views <= 500:
        return 175
    elif 500 <= views <= 2000:
        return 300
    return 0


# Добавление статистики
def add_stat(views: int, price: int):
    cursor.execute("INSERT INTO stats (views, price) VALUES (?, ?)", (views, price))
    conn.commit()


# Получение статистики
def get_stats():
    cursor.execute("SELECT views, price FROM stats")
    rows = cursor.fetchall()
    total_views = sum(r[0] for r in rows)
    total_rub = sum(r[1] for r in rows)
    return rows, total_views, total_rub


# Сброс статистики
def reset_stats():
    cursor.execute("DELETE FROM stats")
    conn.commit()


# ========== Команды ==========

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    text = (
        "👋 Привет! Я бот для подсчёта стоимости просмотров.\n\n"
        "📊 Тарифы:\n"
        "100–200 просмотров = 55 руб\n"
        "250–500 просмотров = 175 руб\n"
        "500–2000 просмотров = 300 руб\n\n"
        "⚡ Команды:\n"
        "/ping — проверить бота\n"
        "/stat — статистика просмотров и рублей\n"
        "/reset — сбросить статистику\n\n"
        "💡 Просто отправь число просмотров или скриншот!"
    )
    await message.answer(text)


@dp.message(Command("ping"))
async def ping_cmd(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    start = time.time()
    await message.answer("🏓 Пинг...")
    end = time.time()
    ms = round((end - start) * 1000, 2)
    await message.answer(f"🟢 Бот работает\n⏱ Задержка: {ms} мс")


@dp.message(Command("stat"))
async def stat_cmd(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    rows, total_views, total_rub = get_stats()
    if not rows:
        await message.answer("❌ Статистика пуста.")
        return

    lines = [f"{idx}. {views} просмотров = {price} руб" for idx, (views, price) in enumerate(rows, 1)]
    text = "\n".join(lines)
    text += f"\n\n📊 ИТОГО:\n👁 Просмотров: {total_views}\n💰 Сумма: {total_rub} руб"
    await message.answer(text)


@dp.message(Command("reset"))
async def reset_cmd(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    reset_stats()
    await message.answer("♻️ Статистика сброшена.")


# ========== Добавление просмотров ==========

@dp.message()
async def process_views_or_screenshot(message: types.Message):
    if not is_admin(message.from_user.id):
        return  

    # 📸 Если пришло фото
    if message.photo:
        file = await bot.get_file(message.photo[-1].file_id)
        file_bytes = await bot.download_file(file.file_path)
        image = Image.open(io.BytesIO(file_bytes.read()))

        # OCR → текст
        text = pytesseract.image_to_string(image, lang="eng+rus")

        # Вытаскиваем все числа
        numbers = [int(s) for s in text.replace(",", "").split() if s.isdigit()]
        # Отбираем только адекватные (просмотры)
        views_list = [n for n in numbers if 50 <= n <= 5_000_000]

        if not views_list:
            await message.answer("❌ Не нашёл просмотры на скриншоте.")
            return

        added = []
        for views in views_list:
            price = get_price(views)
            if price > 0:
                add_stat(views, price)
                added.append(f"{views} = {price} руб")

        if added:
            text = "✅ Добавлены просмотры:\n" + "\n".join(added)
        else:
            text = "⚠️ Числа нашёл, но они не попали в тарифы."
        await message.answer(text)
        return

    # 📝 Если прислали текстом число
    try:
        views = int(message.text.strip())
    except (ValueError, AttributeError):
        return

    price = get_price(views)
    if price == 0:
        await message.answer("⚠️ Число просмотров не попадает в тарифы.")
        return

    add_stat(views, price)
    await message.answer(f"✅ Добавлено: {views} просмотров = {price} руб")


# ========== Запуск ==========

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
