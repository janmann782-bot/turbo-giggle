#!/usr/bin/env python3
"""
Бот для расчёта площади / длины по красной метке на карте.
Карта должна быть без сжатия, красное пятно/линия — чисто красные (или почти).
"""

import os
import sys
from pathlib import Path
from typing import Tuple, List, Optional

import numpy as np
from PIL import Image
from dotenv import load_dotenv

load_dotenv()

# ====================== НАСТРОЙКИ ПОЯСОВ ======================
# (y_min, y_max) — включительно, север и юг симметричны
BELTS = [
    {
        "name": "Центральный (0°–15°)",
        "y_ranges": [(1836, 2484)],
        "km_per_px": 4.64,      # дороги/границы
        "km2_per_px2": 21.5,    # площади
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

# Допуск по цвету (0 = только идеальный красный 255,0,0)
# Можно задать в .env: RED_TOLERANCE=15
RED_TOLERANCE = int(os.getenv("RED_TOLERANCE", "10"))


def is_red(r: int, g: int, b: int, tol: int = RED_TOLERANCE) -> bool:
    """Чисто красный или почти (с допуском)."""
    return r >= 255 - tol and g <= tol and b <= tol


def find_red_pixels(img: Image.Image) -> np.ndarray:
    """Возвращает массив координат (y, x) всех красных пикселей."""
    arr = np.array(img.convert("RGB"))
    h, w, _ = arr.shape
    mask = (
        (arr[:, :, 0] >= 255 - RED_TOLERANCE)
        & (arr[:, :, 1] <= RED_TOLERANCE)
        & (arr[:, :, 2] <= RED_TOLERANCE)
    )
    ys, xs = np.where(mask)
    return np.column_stack((ys, xs))  # (y, x)


def get_belt(y: float) -> Optional[dict]:
    """Определяет пояс по Y-координате."""
    y = int(round(y))
    for belt in BELTS:
        for y_min, y_max in belt["y_ranges"]:
            if y_min <= y <= y_max:
                return belt
    return None


def calculate_area(red_pixels: np.ndarray, belt: dict) -> float:
    """Площадь = количество пикселей × км²/пикс²."""
    count = len(red_pixels)
    return count * belt["km2_per_px2"]


def calculate_length(red_pixels: np.ndarray, belt: dict) -> float:
    """
    Длина линии.
    Простой и надёжный способ для тонких линий:
    считаем количество «шагов» по 8-связности (приближение длины скелета).
    Более точный вариант — skeletonize + count, но требует scipy.
    Здесь используем эвристику: длина ≈ N * коэффициент (для 1-px линии).
    """
    if len(red_pixels) < 2:
        return 0.0

    # Сортируем по Y, потом по X — грубая оценка длины для линии
    # Лучше: строим граф соседей и считаем длину пути
    from collections import defaultdict

    # Превращаем в set для быстрого поиска
    points = set((int(y), int(x)) for y, x in red_pixels)

    # 8-соседи
    neighbors = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]

    # Считаем количество «рёбер» (каждая пара соседних пикселей)
    edge_count = 0
    visited = set()
    for y, x in points:
        for dy, dx in neighbors:
            ny, nx = y + dy, x + dx
            if (ny, nx) in points:
                # Чтобы не считать дважды
                edge = tuple(sorted([(y, x), (ny, nx)]))
                if edge not in visited:
                    visited.add(edge)
                    # Длина одного шага: 1 или √2
                    dist = 1.0 if dy == 0 or dx == 0 else 1.41421356237
                    edge_count += dist

    # Для тонкой линии edge_count примерно равен длине в пикселях
    # (каждое соединение добавляет ~1)
    pixel_length = edge_count
    return pixel_length * belt["km_per_px"]


def process_image(image_path: str, mode: str = "area") -> dict:
    """
    mode: "area" или "length"
    """
    img = Image.open(image_path)
    w, h = img.size
    print(f"Размер карты: {w}×{h} px")

    red = find_red_pixels(img)
    if len(red) == 0:
        raise ValueError(
            f"Красных пикселей не найдено (допуск={RED_TOLERANCE}). "
            "Убедись, что пятно/линия чисто красные (R≈255, G≈0, B≈0)."
        )

    mean_y = float(np.mean(red[:, 0]))
    belt = get_belt(mean_y)
    if belt is None:
        raise ValueError(
            f"Средний Y красных пикселей = {mean_y:.1f} — не попал ни в один пояс. "
            "Проверь координаты поясов или положение метки."
        )

    result = {
        "pixels": len(red),
        "mean_y": mean_y,
        "belt": belt["name"],
        "mode": mode,
    }

    if mode == "area":
        area_km2 = calculate_area(red, belt)
        result["value"] = area_km2
        result["unit"] = "км²"
        result["formula"] = f"{len(red)} px² × {belt['km2_per_px2']} км²/px²"
    else:
        length_km = calculate_length(red, belt)
        result["value"] = length_km
        result["unit"] = "км"
        result["formula"] = f"длина линии × {belt['km_per_px']} км/px"

    return result


def main():
    if len(sys.argv) < 2:
        print("Использование:")
        print("  python bot.py <карта.png> [area|length]")
        print()
        print("  area   — посчитать площадь красного пятна (по умолчанию)")
        print("  length — посчитать длину красной линии (дорога/граница)")
        print()
        print("Пример:")
        print("  python bot.py map.png area")
        print("  python bot.py map.png length")
        sys.exit(1)

    path = sys.argv[1]
    mode = sys.argv[2].lower() if len(sys.argv) > 2 else "area"

    if mode not in ("area", "length"):
        print("Режим должен быть 'area' или 'length'")
        sys.exit(1)

    if not Path(path).exists():
        print(f"Файл не найден: {path}")
        sys.exit(1)

    try:
        res = process_image(path, mode)
        print()
        print("=" * 50)
        print(f"Пояс:          {res['belt']}")
        print(f"Средний Y:     {res['mean_y']:.1f}")
        print(f"Красных px:    {res['pixels']}")
        print(f"Режим:         {res['mode']}")
        print(f"Результат:     {res['value']:.2f} {res['unit']}")
        print(f"Формула:       {res['formula']}")
        print("=" * 50)
    except Exception as e:
        print(f"Ошибка: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
