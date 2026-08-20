
import os
import telebot
from telebot import types
from PIL import Image
import io
from dotenv import load_dotenv

# Загружаем переменные из .env файла
load_dotenv()
TOKEN = os.getenv('BOT_TOKEN')
bot = telebot.TeleBot(TOKEN)

# Временное хранилище для отправленных карт
user_data = {}

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "Господин Президент, картографический вычислитель Аурелии готов\n\n"
                          "Отправьте мне карту **файлом (без сжатия)**, на которой нужный объект "
                          "закрашен АБСОЛЮТНО красным цветом (RGB: 255, 0, 0)")

# Ловим документы (картинки без сжатия)
@bot.message_handler(content_types=['document'])
def handle_docs(message):
    try:
        # Проверяем, что это картинка
        if message.document.mime_type.startswith('image/'):
            bot.send_message(message.chat.id, "Загружаю карту в Канцелярию ⏳")
            file_info = bot.get_file(message.document.file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            
            # Сохраняем файл временно
            user_data[message.chat.id] = {'file': downloaded_file}
            
            # Создаем кнопки
            markup = types.InlineKeyboardMarkup()
            btn1 = types.InlineKeyboardButton("Считать Площадь", callback_data="calc_area")
            btn2 = types.InlineKeyboardButton("Считать Расстояние", callback_data="calc_dist")
            markup.add(btn1, btn2)
            
            bot.send_message(message.chat.id, "Карта получена Что будем вычислять?", reply_markup=markup)
        else:
            bot.reply_to(message, "Пожалуйста, отправьте именно изображение (PNG/JPG) как файл")
    except Exception as e:
        bot.reply_to(message, f"Произошла ошибка при загрузке: {e}")

# Обработка нажатия кнопок
@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    chat_id = call.message.chat.id
    
    if chat_id not in user_data:
        bot.send_message(chat_id, "Файл потерялся Пожалуйста, отправьте карту заново")
        return
        
    calc_type = "area" if call.data == "calc_area" else "dist"
    image_bytes = user_data[chat_id]['file']
    
    # Удаляем кнопки после нажатия
    bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=None)
    bot.send_message(chat_id, "Анализирую красные пиксели и климатические пояса ⚙️")
    
    try:
        # Открываем изображение через PIL
        image = Image.open(io.BytesIO(image_bytes))
        image = image.convert('RGB')
        pixels = image.load()
        width, height = image.size
        
        total_value = 0.0
        red_pixels_count = 0
        
        # Сканируем каждый пиксель
        for y in range(height):
            for x in range(width):
                # Ищем абсолютно красный цвет (255, 0, 0)
                if pixels[x, y] == (255, 0, 0):
                    red_pixels_count += 1
                    
                    # 1 Пояс Центральный
                    if 1836 <= y <= 2484:
                        total_value += 21.5 if calc_type == "area" else 4.64
                    # 2 Пояс Тропический
                    elif (1489 <= y < 1836) or (2484 < y <= 2831):
                        total_value += 18.7 if calc_type == "area" else 4.32
                    # 3 Пояс Умеренный
                    elif (1083 <= y < 1489) or (2831 < y <= 3237):
                        total_value += 13.7 if calc_type == "area" else 3.70
                    # 4 Пояс Субполярный
                    elif (550 <= y < 1083) or (3237 < y <= 3770):
                        total_value += 8.0 if calc_type == "area" else 2.82
                    # 5 Пояс Полярный
                    elif (39 <= y < 550) or (3770 < y <= 4281):
                        total_value += 3.9 if calc_type == "area" else 1.96

        if red_pixels_count == 0:
            bot.send_message(chat_id, "❌ Абсолютно красных пикселей (RGB: 255, 0, 0) не найдено\n"
                                      "Убедитесь, что рисуете чистым красным цветом без сглаживания кисти")
        else:
            unit = "км²" if calc_type == "area" else "км"
            calc_name = "Площадь" if calc_type == "area" else "Расстояние"
            
            result_message = (
                f"📊 **Отчет Картографа:**\n"
                f"Тип: {calc_name}\n"
                f"Найдено красных пикселей: {red_pixels_count}\n"
                f"Итог: **{total_value:,.2f} {unit}**"
            )
            bot.send_message(chat_id, result_message, parse_mode='Markdown')
            
    except Exception as e:
        bot.send_message(chat_id, f"Произошла ошибка при анализе карты: {e}")
        
    # Очищаем память
    if chat_id in user_data:
        del user_data[chat_id]

# Предупреждение, если кидают фото сжатым форматом
@bot.message_handler(content_types=['photo'])
def handle_photos(message):
    bot.reply_to(message, "Внимание! Вы отправили картинку как **ФОТО**, Telegram сжал её, "
                          "и чистый красный цвет мог превратиться в грязные оттенки\n\n"
                          "Отправьте изображение как **ФАЙЛ (Документ)**")

if __name__ == '__main__':
    print("Бот Аурелии запущен и готов к расчетам")
    bot.polling(none_stop=True)
