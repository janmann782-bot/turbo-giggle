#!/usr/bin/env python3
"""
Telegram-бот для расчёта площади / длины по красной метке на карте.
Карта без сжатия, красное пятно/линия — чисто красные (или почти).
"""

import os
import io
import logging
from typing import Optional

import numpy as np
from PIL import Image
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

load_dotenv()

# ====================== НАСТРОЙКИ ======================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
RED_TOLERANCE = int(os.getenv("RED_TOLERANCE", "10"))

if not BOT_TOKEN or BOT_TOKEN.startswith("123456"):
    raise SystemExit(
        "❌ BOT_TOKEN не задан!\n"
        "Открой .env и вставь токен от @BotFather:\n"
        "BOT_TOKEN=твой_токен_сюда"
    )

# ====================== ПОЯСА ======================
BELTS = [
    {
        "name": "Центральный (0°–15°)",
        "y_ranges": [(1836, 2484)],
        "km_per_px": 4.64,
        "km2_per_px2": 21.5,
    },
    {
        "name": "Тропический (15°–30°)",
        "y_ranges": [(1489, 1836), (2484, 2831)],
        "km_per_px": 4.32,
        "km2_per_px2": 18.7,
    },
    {
        "name": "Умеренный (30°–45°)",
        "y_ranges": [(1083, 1489), (2831, 3237)],
        "km_per_px": 3.70,
        "km2_per_px2": 13.7,
    },
    {
        "name": "Субполярный (45°–60°)",
        "y_ranges": [(550, 1083), (3237, 3770)],
        "km_per_px": 2.82,
        "km2_per_px2": 8.0,
    },
    {
        "name": "Полярный (60°–70°)",
        "y_ranges": [(39, 550), (3770, 4281)],
        "km_per_px": 1.96,
        "km2_per_px2": 3.9,
    },
]

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ====================== ЛОГИКА РАСЧЁТА ======================
def find_red_pixels(img: Image.Image) -> np.ndarray:
    arr = np.array(img.convert("RGB"))
    mask = (
        (arr[:, :, 0] >= 255 - RED_TOLERANCE)
        & (arr[:, :, 1] <= RED_TOLERANCE)
        & (arr[:, :, 2] <= RED_TOLERANCE)
    )
    ys, xs = np.where(mask)
    return np.column_stack((ys, xs))


def get_belt(y: float) -> Optional[dict]:
    y = int(round(y))
    for belt in BELTS:
        for y_min, y_max in belt["y_ranges"]:
            if y_min <= y <= y_max:
                return belt
    return None


def calculate_area(red_pixels: np.ndarray, belt: dict) -> float:
    return len(red_pixels) * belt["km2_per_px2"]


def calculate_length(red_pixels: np.ndarray, belt: dict) -> float:
    if len(red_pixels) < 2:
        return 0.0

    points = set((int(y), int(x)) for y, x in red_pixels)
    neighbors = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
    visited = set()
    total = 0.0

    for y, x in points:
        for dy, dx in neighbors:
            ny, nx = y + dy, x + dx
            if (ny, nx) in points:
                edge = tuple(sorted([(y, x), (ny, nx)]))
                if edge not in visited:
                    visited.add(edge)
                    dist = 1.0 if dy == 0 or dx == 0 else 1.41421356237
                    total += dist

    return total * belt["km_per_px"]


def process_image(image_bytes: bytes, mode: str) -> dict:
    img = Image.open(io.BytesIO(image_bytes))
    w, h = img.size

    red = find_red_pixels(img)
    if len(red) == 0:
        raise ValueError(
            f"Красных пикселей не найдено (допуск={RED_TOLERANCE}).\n"
            "Убедись, что пятно/линия чисто красные (R≈255, G≈0, B≈0)."
        )

    mean_y = float(np.mean(red[:, 0]))
    belt = get_belt(mean_y)
    if belt is None:
        raise ValueError(
            f"Средний Y = {mean_y:.1f} — не попал ни в один пояс.\n"
            "Проверь положение метки на карте."
        )

    result = {
        "pixels": len(red),
        "mean_y": mean_y,
        "belt": belt["name"],
        "size": f"{w}×{h}",
        "mode": mode,
    }

    if mode == "area":
        value = calculate_area(red, belt)
        result["value"] = value
        result["unit"] = "км²"
        result["formula"] = f"{len(red)} px² × {belt['km2_per_px2']}"
    else:
        value = calculate_length(red, belt)
        result["value"] = value
        result["unit"] = "км"
        result["formula"] = f"длина линии × {belt['km_per_px']}"

    return result


def format_result(res: dict) -> str:
    return (
        f"✅ <b>Результат</b>\n\n"
        f"📍 Пояс: <b>{res['belt']}</b>\n"
        f"📐 Размер карты: {res['size']}\n"
        f"🔴 Красных пикселей: {res['pixels']}\n"
        f"📊 Средний Y: {res['mean_y']:.1f}\n\n"
        f"🎯 <b>{res['value']:.2f} {res['unit']}</b>\n"
        f"<i>({res['formula']})</i>"
    )


# ====================== TELEGRAM HANDLERS ======================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я бот для расчёта площади/длины по карте.\n\n"
        "1️⃣ Пришли карту <b>без сжатия</b> (PNG лучше)\n"
        "2️⃣ На карте должно быть <b>чисто красное</b> пятно или линия\n"
        "3️⃣ Выбери, что считать: площадь или длину\n\n"
        "Команды:\n"
        "/start — это сообщение\n"
        "/help — подробная помощь",
        parse_mode="HTML",
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 <b>Как пользоваться</b>\n\n"
        "1. Открой карту в любом редакторе\n"
        "2. Нарисуй <b>чисто красным</b> (R=255 G=0 B=0):\n"
        "   • пятно/заливку — для площади\n"
        "   • тонкую линию — для длины (дорога/граница)\n"
        "3. Сохрани <b>без сжатия</b> (PNG)\n"
        "4. Пришли фото сюда\n"
        "5. Нажми кнопку «Площадь» или «Длина»\n\n"
        "⚠️ Если карта в JPEG — красный может «поплыть». "
        "Тогда увеличь RED_TOLERANCE в .env",
        parse_mode="HTML",
    )


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сохраняем фото и показываем кнопки выбора режима."""
    photo = update.message.photo[-1]  # самое большое
    file = await context.bot.get_file(photo.file_id)
    image_bytes = await file.download_as_bytearray()

    # Сохраняем в context пользователя
    context.user_data["image"] = bytes(image_bytes)

    keyboard = [
        [
            InlineKeyboardButton("📐 Площадь", callback_data="mode_area"),
            InlineKeyboardButton("📏 Длина", callback_data="mode_length"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "Карта получена ✅\nЧто считаем?",
        reply_markup=reply_markup,
    )


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Принимаем документ (PNG без сжатия Telegram'ом)."""
    doc = update.message.document
    if not doc.mime_type or not doc.mime_type.startswith("image/"):
        await update.message.reply_text("Пришли изображение (PNG/JPG).")
        return

    file = await context.bot.get_file(doc.file_id)
    image_bytes = await file.download_as_bytearray()
    context.user_data["image"] = bytes(image_bytes)

    keyboard = [
        [
            InlineKeyboardButton("📐 Площадь", callback_data="mode_area"),
            InlineKeyboardButton("📏 Длина", callback_data="mode_length"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "Карта получена (как файл) ✅\nЧто считаем?",
        reply_markup=reply_markup,
    )


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    mode = "area" if query.data == "mode_area" else "length"
    image_bytes = context.user_data.get("image")

    if not image_bytes:
        await query.edit_message_text("Сначала пришли карту 🖼")
        return

    await query.edit_message_text("⏳ Считаю...")

    try:
        res = process_image(image_bytes, mode)
        text = format_result(res)
        await query.edit_message_text(text, parse_mode="HTML")
    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка:\n{e}")


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.IMAGE, handle_document))
    app.add_handler(CallbackQueryHandler(button_callback))

    logger.info("Бот запущен...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
