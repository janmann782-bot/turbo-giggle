#!/usr/bin/env python3
"""
Telegram-бот для расчёта площади / длины по красной метке на карте
Плавный масштаб: каждому Y своё значение км/пикс
"""

import os
import io
import logging
from typing import List, Tuple

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

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
RED_TOLERANCE = int(os.getenv("RED_TOLERANCE", "10"))

if not BOT_TOKEN or BOT_TOKEN.startswith("123456"):
    raise SystemExit(
        "BOT_TOKEN не задан\n"
        "Открой .env и вставь токен от @BotFather:\n"
        "BOT_TOKEN=твой_токен_сюда"
    )

# ====================== ОПОРНЫЕ ТОЧКИ МАСШТАБА ======================
# (Y, км/пикс) - середины поясов + зеркало на юг
# Между ними линейная интерполяция
SCALE_POINTS = [
    (294, 1.96),    # полярный север ~60-70
    (816, 2.82),    # субполярный север
    (1286, 3.70),   # умеренный север
    (1662, 4.32),   # тропический север
    (2160, 4.64),   # центральный (экватор)
    (2658, 4.32),   # тропический юг
    (3034, 3.70),   # умеренный юг
    (3504, 2.82),   # субполярный юг
    (4026, 1.96),   # полярный юг
]

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def km_per_px(y: float) -> float:
    """Плавный масштаб: линейная интерполяция по Y"""
    y = float(y)
    points = SCALE_POINTS

    if y <= points[0][0]:
        return points[0][1]
    if y >= points[-1][0]:
        return points[-1][1]

    for i in range(len(points) - 1):
        y1, v1 = points[i]
        y2, v2 = points[i + 1]
        if y1 <= y <= y2:
            t = (y - y1) / (y2 - y1)
            return v1 + t * (v2 - v1)

    return points[-1][1]


def km2_per_px2(y: float) -> float:
    """Площадь = квадрат линейного масштаба"""
    k = km_per_px(y)
    return k * k


def find_red_pixels(img: Image.Image) -> np.ndarray:
    arr = np.array(img.convert("RGB"))
    mask = (
        (arr[:, :, 0] >= 255 - RED_TOLERANCE)
        & (arr[:, :, 1] <= RED_TOLERANCE)
        & (arr[:, :, 2] <= RED_TOLERANCE)
    )
    ys, xs = np.where(mask)
    return np.column_stack((ys, xs))


def calculate_area(red_pixels: np.ndarray) -> Tuple[float, float, float]:
    """
    Площадь: каждый пиксель со своим км²/пикс²
    Возвращает (сумма км², средний коэффициент, мин коэффициент)
    """
    if len(red_pixels) == 0:
        return 0.0, 0.0, 0.0

    total = 0.0
    coeffs = []
    for y, x in red_pixels:
        c = km2_per_px2(float(y))
        total += c
        coeffs.append(c)

    return total, float(np.mean(coeffs)), float(np.min(coeffs))


def calculate_length(red_pixels: np.ndarray) -> Tuple[float, float]:
    """
    Длина: рёбра между соседними пикселями
    Каждый сегмент берёт средний масштаб двух концов
    """
    if len(red_pixels) < 2:
        return 0.0, 0.0

    points = set((int(y), int(x)) for y, x in red_pixels)
    neighbors = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
    visited = set()
    total_km = 0.0
    coeffs = []

    for y, x in points:
        for dy, dx in neighbors:
            ny, nx = y + dy, x + dx
            if (ny, nx) in points:
                edge = tuple(sorted([(y, x), (ny, nx)]))
                if edge not in visited:
                    visited.add(edge)
                    dist_px = 1.0 if dy == 0 or dx == 0 else 1.41421356237
                    # масштаб - среднее двух точек
                    k = (km_per_px(y) + km_per_px(ny)) / 2.0
                    total_km += dist_px * k
                    coeffs.append(k)

    avg_k = float(np.mean(coeffs)) if coeffs else 0.0
    return total_km, avg_k


def process_image(image_bytes: bytes, mode: str) -> dict:
    img = Image.open(io.BytesIO(image_bytes))
    w, h = img.size

    red = find_red_pixels(img)
    if len(red) == 0:
        raise ValueError(
            f"Красных пикселей не нашёл (допуск={RED_TOLERANCE})\n"
            "Нарисуй чисто красным: R≈255, G≈0, B≈0"
        )

    mean_y = float(np.mean(red[:, 0]))
    min_y = float(np.min(red[:, 0]))
    max_y = float(np.max(red[:, 0]))

    result = {
        "pixels": len(red),
        "mean_y": mean_y,
        "min_y": min_y,
        "max_y": max_y,
        "size": f"{w}x{h}",
        "mode": mode,
    }

    if mode == "area":
        value, avg_c, min_c = calculate_area(red)
        result["value"] = value
        result["unit"] = "км²"
        result["avg_coeff"] = avg_c
        result["min_coeff"] = min_c
    else:
        value, avg_k = calculate_length(red)
        result["value"] = value
        result["unit"] = "км"
        result["avg_coeff"] = avg_k

    return result


def format_result(res: dict) -> str:
    if res["mode"] == "area":
        return (
            f"Готово\n\n"
            f"Карта: {res['size']}\n"
            f"Красных пикселей: {res['pixels']}\n"
            f"Y: {res['min_y']:.0f} - {res['max_y']:.0f} (средний {res['mean_y']:.0f})\n"
            f"Средний коэффициент: {res['avg_coeff']:.2f} км²/пикс²\n\n"
            f"Площадь: {res['value']:.1f} {res['unit']}"
        )
    else:
        return (
            f"Готово\n\n"
            f"Карта: {res['size']}\n"
            f"Красных пикселей: {res['pixels']}\n"
            f"Y: {res['min_y']:.0f} - {res['max_y']:.0f} (средний {res['mean_y']:.0f})\n"
            f"Средний коэффициент: {res['avg_coeff']:.2f} км/пикс\n\n"
            f"Длина: {res['value']:.1f} {res['unit']}"
        )


# ====================== TELEGRAM ======================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет\n\n"
        "Кидай карту с красной меткой - посчитаю площадь или длину\n\n"
        "Как лучше:\n"
        "1. Карта без сжатия (PNG)\n"
        "2. Чисто красный цвет (255, 0, 0)\n"
        "3. Присылай файлом, а не фото - так Telegram не сожмёт\n\n"
        "Масштаб плавный: у каждого Y своё значение, без скачков по поясам\n\n"
        "/help - если что-то непонятно"
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Как пользоваться\n\n"
        "1. Открой карту в редакторе\n"
        "2. Нарисуй чисто красным (R=255 G=0 B=0):\n"
        "   - пятно - если нужна площадь\n"
        "   - тонкую линию - если нужна длина\n"
        "3. Сохрани PNG без сжатия\n"
        "4. Пришли сюда документом\n"
        "5. Выбери что считать\n\n"
        "Если карта в JPEG и красный размазался - "
        "увеличь RED_TOLERANCE в .env"
    )


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    image_bytes = await file.download_as_bytearray()
    context.user_data["image"] = bytes(image_bytes)

    keyboard = [[
        InlineKeyboardButton("Площадь", callback_data="mode_area"),
        InlineKeyboardButton("Длина", callback_data="mode_length"),
    ]]
    await update.message.reply_text(
        "Карту получил (как фото - Telegram мог поджать качество)\n"
        "Лучше кидай файлом\n\n"
        "Что считаем?",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc.mime_type or not doc.mime_type.startswith("image/"):
        await update.message.reply_text("Пришли картинку (PNG или JPG)")
        return

    file = await context.bot.get_file(doc.file_id)
    image_bytes = await file.download_as_bytearray()
    context.user_data["image"] = bytes(image_bytes)

    keyboard = [[
        InlineKeyboardButton("Площадь", callback_data="mode_area"),
        InlineKeyboardButton("Длина", callback_data="mode_length"),
    ]]
    await update.message.reply_text(
        "Карту получил\nЧто считаем?",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    mode = "area" if query.data == "mode_area" else "length"
    image_bytes = context.user_data.get("image")

    if not image_bytes:
        await query.edit_message_text("Сначала кинь карту")
        return

    await query.edit_message_text("Считаю...")

    try:
        res = process_image(image_bytes, mode)
        text = format_result(res)
        await query.edit_message_text(text)
    except Exception as e:
        await query.edit_message_text(f"Не вышло:\n{e}")


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.IMAGE, handle_document))
    app.add_handler(CallbackQueryHandler(button_callback))

    logger.info("Бот запущен")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
