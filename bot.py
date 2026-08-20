#!/usr/bin/env python3
"""
Telegram-бот для расчёта площади / длины по красной метке на карте.
Карта без сжатия, красное пятно/линия — чисто красные (или почти).

Если красное пересекает несколько поясов — каждый пиксель считается
по своему поясу, потом всё суммируется.
"""

import os
import io
import logging
from typing import Optional, Dict, List, Tuple
from collections import defaultdict

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
    """(y, x) всех красных пикселей."""
    arr = np.array(img.convert("RGB"))
    mask = (
        (arr[:, :, 0] >= 255 - RED_TOLERANCE)
        & (arr[:, :, 1] <= RED_TOLERANCE)
        & (arr[:, :, 2] <= RED_TOLERANCE)
    )
    ys, xs = np.where(mask)
    return np.column_stack((ys, xs))


def get_belt_for_y(y: int) -> Optional[dict]:
    """Возвращает пояс, в который попадает Y, или None."""
    for belt in BELTS:
        for y_min, y_max in belt["y_ranges"]:
            if y_min <= y <= y_max:
                return belt
    return None


def group_pixels_by_belt(red_pixels: np.ndarray):
    """
    Разбивает красные пиксели по поясам.
    Ключ — имя пояса, значение — список (y, x).
    """
    groups = defaultdict(list)
    outside = 0

    for y, x in red_pixels:
        y_int = int(y)
        belt = get_belt_for_y(y_int)
        if belt is None:
            outside += 1
            continue
        groups[belt["name"]].append((y_int, int(x)))

    return dict(groups), outside


def calculate_area_by_belts(groups, belts_map):
    """Площадь: каждый пояс своим коэффициентом, потом сумма."""
    total = 0.0
    details = []

    for belt_name, pixels in groups.items():
        belt = belts_map[belt_name]
        count = len(pixels)
        area = count * belt["km2_per_px2"]
        total += area
        details.append(f"  • {belt_name}: {count} px × {belt['km2_per_px2']} = {area:.2f} км²")

    return total, details


def calculate_length_by_belts(groups, belts_map):
    """Длина: внутри каждого пояса отдельно, потом сумма."""
    total = 0.0
    details = []
    neighbors = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]

    for belt_name, pixels in groups.items():
        if len(pixels) < 2:
            details.append(f"  • {belt_name}: слишком мало пикселей")
            continue

        belt = belts_map[belt_name]
        points = set(pixels)
        visited = set()
        pixel_len = 0.0

        for y, x in points:
            for dy, dx in neighbors:
                ny, nx = y + dy, x + dx
                if (ny, nx) in points:
                    edge = tuple(sorted([(y, x), (ny, nx)]))
                    if edge not in visited:
                        visited.add(edge)
                        dist = 1.0 if dy == 0 or dx == 0 else 1.41421356237
                        pixel_len += dist

        length_km = pixel_len * belt["km_per_px"]
        total += length_km
        details.append(
            f"  • {belt_name}: {pixel_len:.1f} px × {belt['km_per_px']} = {length_km:.2f} км"
        )

    return total, details


def process_image(image_bytes: bytes, mode: str) -> dict:
    img = Image.open(io.BytesIO(image_bytes))
    w, h = img.size

    red = find_red_pixels(img)
    if len(red) == 0:
        raise ValueError(
            f"Красных пикселей не найдено (допуск={RED_TOLERANCE}).\n"
            "Убедись, что пятно/линия чисто красные (R≈255, G≈0, B≈0)."
        )

    groups, outside = group_pixels_by_belt(red)
    if not groups:
        raise ValueError(
            "Все красные пиксели оказались вне поясов.\n"
            "Проверь Y-координаты метки."
        )

    belts_map = {b["name"]: b for b in BELTS}

    if mode == "area":
        value, details = calculate_area_by_belts(groups, belts_map)
        unit = "км²"
    else:
        value, details = calculate_length_by_belts(groups, belts_map)
        unit = "км"

    return {
        "pixels": len(red),
        "outside": outside,
        "size": f"{w}×{h}",
        "mode": mode,
        "value": value,
        "unit": unit,
        "details": details,
        "belts_used": list(groups.keys()),
    }


def format_result(res: dict) -> str:
    belts_str = ", ".join(res["belts_used"])
    details_str = "\n".join(res["details"])

    text = (
        f"✅ <b>Результат</b>\n\n"
        f"📐 Размер карты: {res['size']}\n"
        f"🔴 Красных пикселей: {res['pixels']}\n"
        f"📍 Пояса: <b>{belts_str}</b>\n\n"
        f"Разбивка:\n{details_str}\n\n"
        f"🎯 <b>Итого: {res['value']:.2f} {res['unit']}</b>"
    )

    if res["outside"] > 0:
        text += f"\n\n⚠️ {res['outside']} пикселей вне поясов (проигнорированы)"

    return text


# ====================== TELEGRAM HANDLERS ======================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я бот для расчёта площади/длины по карте.\n\n"
        "1️⃣ Пришли карту <b>без сжатия</b> (лучше файлом PNG)\n"
        "2️⃣ На карте — <b>чисто красное</b> пятно или линия\n"
        "3️⃣ Выбери: площадь или длину\n\n"
        "Если красное пересекает несколько поясов — "
        "каждый кусок считается по своему коэффициенту.\n\n"
        "/help — подробная помощь",
        parse_mode="HTML",
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 <b>Как пользоваться</b>\n\n"
        "1. Открой карту в любом редакторе\n"
        "2. Нарисуй <b>чисто красным</b> (R=255 G=0 B=0):\n"
        "   • пятно/заливку — для площади\n"
        "   • тонкую линию — для длины\n"
        "3. Сохрани <b>без сжатия</b> (PNG)\n"
        "4. Пришли <b>документом</b> (не фото — иначе Telegram сожмёт)\n"
        "5. Нажми «Площадь» или «Длина»\n\n"
        "⚠️ JPEG может размазать красный → увеличь RED_TOLERANCE в .env",
        parse_mode="HTML",
    )


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    image_bytes = await file.download_as_bytearray()
    context.user_data["image"] = bytes(image_bytes)

    keyboard = [[
        InlineKeyboardButton("📐 Площадь", callback_data="mode_area"),
        InlineKeyboardButton("📏 Длина", callback_data="mode_length"),
    ]]
    await update.message.reply_text(
        "Карта получена (как фото — Telegram мог сжать).\n"
        "Лучше кидай <b>документом</b>.\n\nЧто считаем?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc.mime_type or not doc.mime_type.startswith("image/"):
        await update.message.reply_text("Пришли изображение (PNG/JPG).")
        return

    file = await context.bot.get_file(doc.file_id)
    image_bytes = await file.download_as_bytearray()
    context.user_data["image"] = bytes(image_bytes)

    keyboard = [[
        InlineKeyboardButton("📐 Площадь", callback_data="mode_area"),
        InlineKeyboardButton("📏 Длина", callback_data="mode_length"),
    ]]
    await update.message.reply_text(
        "Карта получена (файл) ✅\nЧто считаем?",
        reply_markup=InlineKeyboardMarkup(keyboard),
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
