import logging
import re
import pytesseract
from PIL import Image
from io import BytesIO
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
import asyncio

# === НАСТРОЙКИ ===
API_TOKEN = "7919356847:AAHHdCT180UMA4cNpwOWNFPwILIRFDLu2E0"

# Для Termux путь указывать НЕ нужно
# Для Windows было бы так:
# pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# Включаем логи
logging.basicConfig(level=logging.INFO)

# Инициализация бота
bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# "База данных" (память в рантайме)
stats = []  # [(просмотры, цена), ...]

# === Тарифы ===
def get_price(views: int) -> int:
    if 100 <= views <= 200:
        return 55
    elif 250 <= views <= 500:
        return 175
    elif 500 <= views <= 2000:
        return 300
    return 0

# === Команды ===
@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    text = (
        "👋 Привет! Я бот для подсчёта просмотров.\n\n"
        "📌 Команды:\n"
        "/start — стартовое меню\n"
        "/ping — проверить бота\n"
        "/stat — твоя статистика\n"
        "/reset — обнулить статистику\n\n"
        "➕ Просто отправь мне число (просмотры) или фото со скриншотом — я всё посчитаю!"
    )
    await message.answer(text)

@dp.message(Command("ping"))
async def ping_cmd(message: types.Message):
    await message.answer("🟢 Бот работает!")

@dp.message(Command("stat"))
async def stat_cmd(message: types.Message):
    if not stats:
        await message.answer("📊 Статистика пуста.")
        return

    text_lines = []
    total_views = 0
    total_rub = 0

    for i, (views, price) in enumerate(stats, start=1):
        text_lines.append(f"{i}. 👁 {views} просмотров = 💰 {price} руб")
        total_views += views
        total_rub += price

    text_lines.append("\n📊 ИТОГО:")
    text_lines.append(f"👁 Всего просмотров: {total_views}")
    text_lines.append(f"💰 Всего сумма: {total_rub} руб")

    await message.answer("\n".join(text_lines))

@dp.message(Command("reset"))
async def reset_cmd(message: types.Message):
    stats.clear()
    await message.answer("♻️ Статистика сброшена!")

# === Обработка чисел ===
@dp.message(lambda m: m.text and m.text.isdigit())
async def handle_number(message: types.Message):
    views = int(message.text)
    price = get_price(views)
    if price > 0:
        stats.append((views, price))
        await message.answer(f"✅ Добавлено: {views} просмотров = {price} руб")
    else:
        await message.answer("❌ Для этого количества просмотров нет тарифа.")

# === Обработка скринов ===
@dp.message(lambda m: m.photo)
async def handle_photo(message: types.Message):
    try:
        # Берем фото в наилучшем качестве
        photo = message.photo[-1]
        file = await bot.get_file(photo.file_id)
        file_path = file.file_path
        downloaded = await bot.download_file(file_path)

        # Открываем картинку
        image = Image.open(BytesIO(downloaded.read()))

        # OCR распознавание
        text = pytesseract.image_to_string(image, lang="eng+rus")

        # Ищем все числа в тексте
        numbers = re.findall(r"\d+", text)
        numbers = list(map(int, numbers))

        if not numbers:
            await message.answer("⚠️ На скриншоте не нашёл просмотров.")
            return

        total_added = 0
        added_lines = []
        for num in numbers:
            price = get_price(num)
            if price > 0:
                stats.append((num, price))
                added_lines.append(f"👁 {num} = 💰 {price} руб")
                total_added += price

        if added_lines:
            response = "✅ Добавлено со скрина:\n" + "\n".join(added_lines)
            response += f"\n\n💰 Всего начислено: {total_added} руб"
            await message.answer(response)
        else:
            await message.answer("⚠️ Нашёл числа, но ни одно не попало в тарифы.")
    except Exception as e:
        await message.answer(f"❌ Ошибка обработки фото: {e}")

# === Запуск ===
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

