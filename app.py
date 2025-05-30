import os
import sqlite3
import random
import string
import threading
from flask import Flask, request, jsonify
import telebot
from dotenv import load_dotenv
import xlsxwriter

# Загрузка переменных окружения
load_dotenv()

# Инициализация Flask
app = Flask(__name__)
app.config['DATABASE'] = 'database/bot.db'

# Путь к базе данных
DB_PATH = app.config['DATABASE']

# Папка для временных файлов
TEMP_DIR = "temp"
os.makedirs(TEMP_DIR, exist_ok=True)

# Инициализация бота
BOT_TOKEN = '7695860708:AAHDA-80C8Pn9rixjmhPSRwhXvEsi82WQ6w'
if not BOT_TOKEN:
    raise Exception("BOT_TOKEN не установлен в .env")
bot = telebot.TeleBot(BOT_TOKEN)

# Для защиты БД от блокировок
db_lock = threading.Lock()

# Словарь для отслеживания состояния пользователей
user_states = {}  # {chat_id: state}

# === Инициализация базы данных ===
def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id TEXT UNIQUE NOT NULL,
            chat_id TEXT NOT NULL,
            username TEXT,
            name TEXT,
            last_name TEXT,
            promo_code TEXT NOT NULL,
            referrals_count INTEGER DEFAULT 0,
            paid_referrals_count INTEGER DEFAULT 0,
            referral_income REAL DEFAULT 0,
            balance REAL DEFAULT 0,
            invited_by_username TEXT,
            role TEXT DEFAULT 'user',
            used_promo BOOLEAN DEFAULT 0
        )
    ''')
    cursor.execute("PRAGMA table_info(users)")
    columns = [col[1] for col in cursor.fetchall()]
    if 'paid_referrals_count' not in columns:
        cursor.execute('ALTER TABLE users ADD COLUMN paid_referrals_count INTEGER DEFAULT 0')
    if 'referral_income' not in columns:
        cursor.execute('ALTER TABLE users ADD COLUMN referral_income REAL DEFAULT 0')
    if 'balance' not in columns:
        cursor.execute('ALTER TABLE users ADD COLUMN balance REAL DEFAULT 0')
    if 'invited_by_username' not in columns:
        cursor.execute('ALTER TABLE users ADD COLUMN invited_by_username TEXT')
    conn.commit()
    conn.close()

# === Функции работы с БД ===
def generate_promo_code(length=8):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

def add_user_if_not_exists(telegram_id, chat_id, name, last_name, username=None, invited_by_username=None):
    with db_lock:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        try:
            promo_code = generate_promo_code()
            cursor.execute('''
                INSERT INTO users (
                    telegram_id, chat_id, name, last_name, username, promo_code, invited_by_username
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (str(telegram_id), str(chat_id), name, last_name, username, promo_code, invited_by_username))
            conn.commit()
            print(f"Пользователь {telegram_id} добавлен с промокодом {promo_code}")
        except sqlite3.IntegrityError:
            print(f"Пользователь {telegram_id} уже существует")
        finally:
            conn.close()

def is_valid_promo(promo_code):
    with db_lock:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('SELECT telegram_id, username FROM users WHERE promo_code = ?', (promo_code,))
        result = cursor.fetchone()
        conn.close()
        if result:
            return {'telegram_id': result[0], 'username': result[1]}
        return None

def has_used_promo(telegram_id):
    with db_lock:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('SELECT used_promo FROM users WHERE telegram_id = ?', (str(telegram_id),))
        result = cursor.fetchone()
        conn.close()
        return bool(result[0]) if result else False

def mark_promo_used(telegram_id):
    with db_lock:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('UPDATE users SET used_promo = 1 WHERE telegram_id = ?', (str(telegram_id),))
        conn.commit()
        conn.close()

def increase_referral(owner_telegram_id):
    with db_lock:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('UPDATE users SET referrals_count = referrals_count + 1 WHERE telegram_id = ?', (str(owner_telegram_id),))
        conn.commit()
        conn.close()

def update_referral_payment(inviter_id, amount):
    income = amount * 0.5  # 50%
    with db_lock:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE users 
            SET paid_referrals_count = paid_referrals_count + 1, 
                referral_income = referral_income + ?
            WHERE telegram_id = ?
        ''', (income, inviter_id))
        conn.commit()
        conn.close()

def get_all_users_data():
    with db_lock:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users")
        rows = cursor.fetchall()
        cursor.execute("PRAGMA table_info(users)")
        columns = [desc[1] for desc in cursor.description]
        conn.close()
    data = [dict(zip(columns, row)) for row in rows]
    return data

def get_referrals_count(telegram_id):
    with db_lock:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT referrals_count, paid_referrals_count, referral_income 
            FROM users 
            WHERE telegram_id = ?
        ''', (str(telegram_id),))
        result = cursor.fetchone()
        conn.close()
        return result or (0, 0, 0)

def send_broadcast_message(message_text):
    with db_lock:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('SELECT chat_id FROM users')
        rows = cursor.fetchall()
        conn.close()
    for row in rows:
        try:
            bot.send_message(row[0], message_text)
        except Exception as e:
            print(f"Не удалось отправить сообщение {row[0]}: {e}")

# === Flask маршруты ===
@app.route('/')
def index():
    return "Bot работает!", 200

@app.route('/add_user', methods=['POST'])
def add_user_to_db():
    data = request.get_json()
    telegram_id = data.get('telegram_id')
    chat_id = data.get('chat_id')
    name = data.get('name')
    last_name = data.get('last_name', "")
    username = data.get('username')

    if not telegram_id or not chat_id or not name:
        return jsonify({"error": "Missing required fields"}), 400

    try:
        with db_lock:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            promo_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
            cursor.execute('''
                INSERT INTO users (
                    telegram_id, chat_id, name, last_name, username, promo_code
                ) VALUES (?, ?, ?, ?, ?, ?)
            ''', (str(telegram_id), str(chat_id), name, last_name, username, promo_code))
            conn.commit()
            conn.close()
        return jsonify({
            "status": "success",
            "message": f"User {telegram_id} added with promo code {promo_code}"
        }), 201
    except sqlite3.IntegrityError:
        return jsonify({"error": "User already exists"}), 409

# === Telegram команды и логика ===
@bot.message_handler(commands=['start'])
def start(message):
    user = message.from_user
    telegram_id = user.id
    chat_id = message.chat.id
    name = user.first_name
    last_name = user.last_name or ""
    username = f"@{user.username}" if user.username else None
    add_user_if_not_exists(telegram_id, chat_id, name, last_name, username)
    keyboard = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(telebot.types.KeyboardButton("Пропустить"))
    try:
        with open('img/start_img.png', 'rb') as photo:
            bot.send_photo(
                chat_id=message.chat.id,
                photo=photo,
                caption="👋 Привет от команды ExamBot!\nВводи промокод и получай все свежие ответы за пару часов до экзамена, чтобы спокойно готовиться и уверенно идти на испытание!",
                reply_markup=keyboard
            )
    except Exception as e:
        bot.send_message(message.chat.id, "Ошибка загрузки фото...")
        bot.send_message(message.chat.id, "Введите промокод, если у вас есть, или нажмите «Пропустить»:", reply_markup=keyboard)
    user_states[message.chat.id] = 'awaiting_promo'

# --- Остальные обработчики ---
@bot.message_handler(func=lambda m: user_states.get(m.chat.id) == 'awaiting_promo')
def handle_promo_input(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    if message.text == "Пропустить":
        user_info, inline_kb, reply_kb = get_main_menu(user_id)
        bot.send_message(chat_id, user_info, reply_markup=inline_kb)
        bot.send_message(chat_id, "Главное меню:", reply_markup=reply_kb)
        user_states.pop(chat_id, None)
        return
    entered_promo = message.text.strip()
    owner_data = is_valid_promo(entered_promo)
    if not owner_data:
        keyboard = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
        keyboard.add(telebot.types.KeyboardButton("Попробовать снова"))
        keyboard.add(telebot.types.KeyboardButton("Перейти в меню"))
        bot.send_message(chat_id, "Неверный промокод. Хотите попробовать снова или перейти в меню?", reply_markup=keyboard)
        user_states[chat_id] = 'invalid_promo'
        return
    if has_used_promo(user_id):
        user_info, inline_kb, reply_kb = get_main_menu(user_id)
        bot.send_message(chat_id, user_info, reply_markup=inline_kb)
        bot.send_message(chat_id, "Вы уже использовали промокод ранее.", reply_markup=reply_kb)
        user_states.pop(chat_id, None)
        return
    increase_referral(owner_data['telegram_id'])
    mark_promo_used(user_id)
    with db_lock:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('UPDATE users SET invited_by_username = ? WHERE telegram_id = ?', (owner_data['username'], user_id))
        conn.commit()
        conn.close()
    user_info, inline_kb, reply_kb = get_main_menu(user_id)
    bot.send_message(chat_id, user_info, reply_markup=inline_kb)
    bot.send_message(chat_id, "Спасибо! Промокод принят. Вы перешли в главное меню.", reply_markup=reply_kb)
    user_states.pop(chat_id, None)

def get_main_menu(user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT name, last_name, promo_code, referrals_count
        FROM users
        WHERE telegram_id = ?
    ''', (str(user_id),))
    result = cursor.fetchone()
    conn.close()
    if not result:
        return "", telebot.types.ReplyKeyboardMarkup(), telebot.types.InlineKeyboardMarkup()
    name, last_name, promo_code, referrals_count = result
    user_info = (
        f"👤 Ваш профиль:\n"
        f"Имя: {name}\n"
        f"Фамилия: {last_name or 'Не указана'}\n"
        f"Промокод: {promo_code}\n"
        f"Приглашённые: {referrals_count}"
    )
    inline_keyboard = telebot.types.InlineKeyboardMarkup()
    inline_keyboard.add(
        telebot.types.InlineKeyboardButton("📋 Копировать промокод", callback_data=f"copy_promo:{promo_code}")
    )
    main_keyboard = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    if is_admin(user_id):
        main_keyboard.add(telebot.types.KeyboardButton("Посмотреть пользователей"))
        main_keyboard.add(telebot.types.KeyboardButton("Проверить количество людей"))
        main_keyboard.add(telebot.types.KeyboardButton("Сделать рассылку"))
    else:
        main_keyboard.add(telebot.types.KeyboardButton("1. Наша группа"))
        main_keyboard.add(telebot.types.KeyboardButton("2. Наши отзывы"))
        main_keyboard.add(telebot.types.KeyboardButton("3. О нас"))
        main_keyboard.add(telebot.types.KeyboardButton("4. Каталог"))
        main_keyboard.add(telebot.types.KeyboardButton("5. Устроиться к нам на работу"))
        main_keyboard.add(telebot.types.KeyboardButton("6. Контакт с менеджером"))
    return user_info, inline_keyboard, main_keyboard

def is_admin(telegram_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT role FROM users WHERE telegram_id = ?', (str(telegram_id),))
    result = cursor.fetchone()
    conn.close()
    return result and result[0] == 'admin'

@bot.callback_query_handler(func=lambda call: call.data.startswith("copy_promo:"))
def handle_copy_promo(call):
    promo_code = call.data.split(":")[1]
    bot.answer_callback_query(call.id, text=f"Промокод {promo_code} скопирован!", show_alert=False)
    bot.send_message(call.message.chat.id, f"`{promo_code}`", parse_mode="Markdown")

# === Админ-команды ===
@bot.message_handler(func=lambda m: is_admin(m.from_user.id) and m.text == "Посмотреть пользователей")
def send_users_excel(message):
    data = get_all_users_data()
    file_path = os.path.join(TEMP_DIR, "users.xlsx")
    workbook = xlsxwriter.Workbook(file_path)
    worksheet = workbook.add_worksheet()
    headers = data[0].keys() if data else []
    worksheet.write_row(0, 0, headers)
    for row_num, row_data in enumerate(data):
        worksheet.write_row(row_num + 1, 0, row_data.values())
    workbook.close()
    with open(file_path, "rb") as f:
        bot.send_document(message.chat.id, f)

@bot.message_handler(func=lambda m: is_admin(m.from_user.id) and m.text == "Проверить количество людей")
def request_user_id(message):
    bot.send_message(message.chat.id, "Введите Telegram ID пользователя:")
    user_states[message.chat.id] = 'awaiting_user_id_for_referrals'

@bot.message_handler(func=lambda m: user_states.get(m.chat.id) == 'awaiting_user_id_for_referrals')
def show_referrals_count(message):
    user_id = message.text.strip()
    rc, prc, ri = get_referrals_count(user_id)
    bot.send_message(message.chat.id, f"Приглашённые: {rc}")
    bot.send_message(message.chat.id, f"Оплатили: {prc}")
    bot.send_message(message.chat.id, f"Общий доход от рефералов: {round(ri, 2)} руб.")
    user_info, inline_kb, reply_kb = get_main_menu(message.from_user.id)
    bot.send_message(message.chat.id, user_info, reply_markup=inline_kb)
    bot.send_message(message.chat.id, "Главное меню:", reply_markup=reply_kb)
    user_states.pop(message.chat.id, None)

@bot.message_handler(func=lambda m: is_admin(m.from_user.id) and m.text == "Сделать рассылку")
def request_broadcast_message(message):
    bot.send_message(message.chat.id, "Введите текст для рассылки:")
    user_states[message.chat.id] = 'awaiting_broadcast_message'

@bot.message_handler(func=lambda m: user_states.get(m.chat.id) == 'awaiting_broadcast_message')
def do_broadcast(message):
    text = message.text
    bot.send_message(message.chat.id, "Рассылка начата...")
    send_broadcast_message(text)
    bot.send_message(message.chat.id, "Рассылка завершена. Возвращаюсь в главное меню.")
    user_info, inline_kb, reply_kb = get_main_menu(message.from_user.id)
    bot.send_message(message.chat.id, user_info, reply_markup=inline_kb)
    bot.send_message(message.chat.id, "Главное меню:", reply_markup=reply_kb)
    user_states.pop(message.chat.id, None)

# === Обработчики обычного меню ===
@bot.message_handler(func=lambda m: m.text == "1. Наша группа")
def our_group(message): bot.reply_to(message, "Ссылка на нашу группу: https://t.me/+phaj3N7gq6wxODQy") 

@bot.message_handler(func=lambda m: m.text == "2. Наши отзывы")
def reviews(message): bot.reply_to(message, "💬 Ознакомьтесь с отзывами наших выпускников в группе @otzivieoge — они уже оценили качество и надёжность сервиса")

@bot.message_handler(func=lambda m: m.text == "3. О нас")
def about_us(message): bot.reply_to(message, "Мы работаем с 2021 года и уже помогли БОЛЕЕ 500 ребятам поступить в вузы с отличными баллами\n\nПОЧЕМУ МЫ ЛУЧШЕ ‼️\n➖В отличие от «готовых ответов» от мошенников, мы получаем варианты КИМ одни из первых за 10-12 часов до экзамена;\n➖Наша команда репетиторов решает их в течение 2–3 часов и передаёт вам свежие, полностью проверенные решения;\n➖Мы полностью РУЧАЕМСЯ ЗА РЕЗУЛЬТАТ: если что-то пойдёт не так, вернём вам полную оплату без лишних вопросов;\nБоишься, что не сдашь? ПЕРЕСТРАХУЙСЯ С НАМИ! Мы понимаем насколько этот экзамен может быть важен для вас.")

@bot.message_handler(func=lambda m: m.text == "4. Каталог")
def catalog(message):
    markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(telebot.types.KeyboardButton("ОГЭ"), telebot.types.KeyboardButton("ЕГЭ"))
    markup.add(telebot.types.KeyboardButton("Назад"))
    bot.reply_to(message, "Выберите тип экзамена:", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text in ["ОГЭ", "ЕГЭ"])
def select_city(message):
    cities = [
        "Москва", "Санкт-Петербург", "Казань", "Екатеринбург",
        "Новосибирск", "Ростов-на-Дону", "Уфа", "Челябинск"
    ]
    markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    for city in cities:
        markup.add(telebot.types.KeyboardButton(city))
    markup.add(telebot.types.KeyboardButton("Назад"))
    bot.reply_to(message, "Выберите город:\n(если вашего нет — напишите менеджеру)", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text in ["математика", "русский язык", "физика", "информатика"])
def show_price(message):
    prices = {
        "математика": 3000,
        "русский язык": 2800,
        "физика": 3500,
        "информатика": 4000
    }
    price = prices.get(message.text.lower(), 0)
    bot.reply_to(message, f"Цена за {message.text}: {price} руб.\nВозможность оплатить со скидкой через менеджера.")

@bot.message_handler(func=lambda m: m.text == "5. Устроиться к нам на работу")
def job(message): bot.reply_to(message, "Хочешь работать у нас? Свяжитесь с менеджером: @Mikhal_l")

@bot.message_handler(func=lambda m: m.text == "6. Контакт с менеджером")
def contact_manager(message): bot.reply_to(message, "Связаться с менеджером: @Mikhal_l")

@bot.message_handler(func=lambda m: m.text == "Назад")
def go_back(message):
    user_id = message.from_user.id
    user_info, inline_kb, reply_kb = get_main_menu(user_id)
    bot.send_message(message.chat.id, user_info, reply_markup=inline_kb)
    bot.send_message(message.chat.id, "Главное меню:", reply_markup=reply_kb)

# === Команды для администратора ===
@bot.message_handler(commands=['setadmin'])
def set_admin(message):
    requester_id = str(message.from_user.id)
    if not is_admin(requester_id):
        bot.reply_to(message, "У вас нет прав.")
        return
    args = message.text.split()
    if len(args) != 2:
        bot.reply_to(message, "Используйте: /setadmin <telegram_id>")
        return
    target_id = args[1]
    with db_lock:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('UPDATE users SET role = "admin" WHERE telegram_id = ?', (target_id,))
        conn.commit()
        conn.close()
    bot.reply_to(message, f"Пользователь {target_id} назначен администратором.")

@bot.message_handler(commands=['setbalance'])
def set_balance(message):
    requester_id = str(message.from_user.id)
    if not is_admin(requester_id):
        bot.reply_to(message, "У вас нет прав.")
        return
    args = message.text.split()
    if len(args) != 3:
        bot.reply_to(message, "Используйте: /setbalance @username 3200")
        return
    username = args[1].lower().strip()
    try:
        amount = float(args[2])
    except ValueError:
        bot.reply_to(message, "Неверная сумма.")
        return
    with db_lock:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('SELECT telegram_id FROM users WHERE username = ?', (username,))
        result = cursor.fetchone()
        conn.close()
    if not result:
        bot.reply_to(message, "Пользователь не найден.")
        return
    telegram_id = result[0]
    with db_lock:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('UPDATE users SET balance = ? WHERE telegram_id = ?', (amount, telegram_id))
        conn.commit()
        conn.close()
    bot.reply_to(message, f"Баланс пользователя {username} установлен на {amount} руб.")

# === Экспорт пользователей в Excel ===
def create_users_excel_file(file_path):
    data = get_all_users_data()
    workbook = xlsxwriter.Workbook(file_path)
    worksheet = workbook.add_worksheet()
    if data:
        headers = data[0].keys()
        worksheet.write_row(0, 0, headers)
        for row_num, row_data in enumerate(data):
            worksheet.write_row(row_num + 1, 0, row_data.values())
    workbook.close()

# === Запуск приложения ===
if __name__ == '__main__':
    init_db()
    print("✅ База данных инициализирована")
    
    bot_thread = threading.Thread(target=bot.polling, kwargs={'none_stop': True})
    bot_thread.daemon = True
    bot_thread.start()
    print("🤖 Telegram бот запущен")

    port = int(os.getenv("PORT", 8080))
    print(f"🌐 Запускаю Flask на порту {port}")
    app.run(host='0.0.0.0', port=port)
