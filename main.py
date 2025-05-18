import logging
import asyncpg
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.types import CallbackQuery
from aiogram import F
import json
import random


# Конфигурация
def load_config():
    with open('config.json') as f:
        return json.load(f)


config = load_config()
API_TOKEN = config['bot_token']
DB_URL = config['database_url']

# Инициализация
logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
dp = Dispatcher()
db_pool = None


# Вспомогательные функции
def generate_group_code():
    return f"{random.randint(0, 999999):06d}"


async def create_db_pool():
    global db_pool
    db_pool = await asyncpg.create_pool(DB_URL)


async def get_museums():
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT name FROM museums")
        return [row["name"] for row in rows]


async def show_museums_keyboard(message: types.Message):
    async with db_pool.acquire() as conn:
        museums = await conn.fetch("SELECT name FROM museums")
        keyboard = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text=museum['name'])] for museum in museums],
            resize_keyboard=True,
            one_time_keyboard=True
        )
        await message.answer("Выберите музей для начала игры:", reply_markup=keyboard)


async def show_leaderboard(message: types.Message, group_code=None):
    async with db_pool.acquire() as conn:
        # Показываем группы пользователя
        user_groups = await conn.fetch(
            "SELECT g.code, g.name FROM user_groups ug "
            "JOIN groups g ON ug.group_code = g.code "
            "WHERE ug.user_id = $1",
            message.from_user.id
        )

        if user_groups:
            groups_text = "\n".join([f"• {g['name']} (код: {g['code']})" for g in user_groups])
            await message.answer(f"📌 Ваши группы:\n{groups_text}")

        # Показываем рейтинг
        if group_code:
            # Рейтинг внутри группы
            top_users = await conn.fetch(
                "SELECT u.username, u.score FROM user_groups ug "
                "JOIN users u ON ug.user_id = u.id "
                "WHERE ug.group_code = $1 "
                "ORDER BY u.score DESC LIMIT 10",
                group_code
            )
            group_name = await conn.fetchval(
                "SELECT name FROM groups WHERE code = $1",
                group_code
            )
            leaderboard_text = f"🏆 Топ группы {group_name}:\n"
        else:
            # Общий рейтинг
            top_users = await conn.fetch(
                "SELECT username, score FROM users ORDER BY score DESC LIMIT 10"
            )
            leaderboard_text = "🏆 Общий топ игроков:\n"

        for i, user in enumerate(top_users, start=1):
            leaderboard_text += f"{i}. {user['username']}: {user['score']} очков\n"

        await message.answer(leaderboard_text)


# Обработчики команд
@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO users (id, username, score, current_task_id)
            VALUES ($1, $2, 0, 0)
            ON CONFLICT (id) DO UPDATE
            SET username = EXCLUDED.username
            """,
            message.from_user.id, message.from_user.username
        )

    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Создать группу"), KeyboardButton(text="Присоединиться")],
            [KeyboardButton(text="Общий рейтинг"), KeyboardButton(text="Рейтинг группы")],
            [KeyboardButton(text="Начать игру")]
        ],
        resize_keyboard=True
    )

    await message.answer(
        "🎮 Добро пожаловать в квиз-игру!\n"
        "Вы можете:\n"
        "- Создать группу\n"
        "- Присоединиться к существующей\n"
        "- Посмотреть общий рейтинг\n"
        "- Посмотреть рейтинг своей группы\n"
        "- Начать игру",
        reply_markup=keyboard
    )


@dp.message(Command("start_game"))
async def start_game_cmd(message: types.Message):
    await show_museums_keyboard(message)


@dp.message(F.text == "Начать игру")
async def start_game_button(message: types.Message):
    await start_game_cmd(message)


@dp.message(F.text == "Создать группу")
async def create_group(message: types.Message):
    async with db_pool.acquire() as conn:
        while True:
            code = generate_group_code()
            exists = await conn.fetchval("SELECT 1 FROM groups WHERE code=$1", code)
            if not exists:
                break

        await conn.execute(
            "INSERT INTO groups (code, name) VALUES ($1, $2)",
            code, f"Группа {message.from_user.username}"
        )

        await message.answer(
            f"🎉 Группа создана!\n"
            f"🔑 Код группы: <code>{code}</code>\n"
            f"Отправьте этот код участникам, чтобы они могли присоединиться.",
            parse_mode="HTML"
        )
    await start_game_cmd(message)


@dp.message(F.text == "Присоединиться")
async def join_group(message: types.Message):
    await message.answer("Введите 6-значный код группы:")


@dp.message(lambda message: message.text.isdigit() and len(message.text) == 6)
async def process_group_code(message: types.Message):
    code = message.text
    user_id = message.from_user.id

    async with db_pool.acquire() as conn:
        try:
            group_exists = await conn.fetchval(
                "SELECT 1 FROM groups WHERE code=$1",
                code
            )

            if not group_exists:
                await message.answer("❌ Группа с таким кодом не найдена.")
                return

            await conn.execute(
                "INSERT INTO user_groups (user_id, group_code) VALUES ($1, $2) "
                "ON CONFLICT (user_id, group_code) DO NOTHING",
                user_id, code
            )

            inserted = await conn.fetchval(
                "SELECT 1 FROM user_groups WHERE user_id=$1 AND group_code=$2",
                user_id, code
            )

            if inserted:
                await message.answer("✅ Вы успешно присоединились к группе!")
            else:
                await message.answer("ℹ️ Вы уже состоите в этой группе.")

            await start_game_cmd(message)

        except Exception as e:
            logging.error(f"Ошибка при присоединении: {e}")
            await message.answer("⚠️ Произошла ошибка. Попробуйте позже.")


@dp.message(F.text == "Общий рейтинг")
async def common_rating(message: types.Message):
    await show_leaderboard(message)


@dp.message(F.text == "Рейтинг группы")
async def group_rating(message: types.Message):
    async with db_pool.acquire() as conn:
        # Получаем все группы пользователя
        user_groups = await conn.fetch(
            "SELECT group_code FROM user_groups WHERE user_id = $1",
            message.from_user.id
        )

        if not user_groups:
            await message.answer("Вы не состоите ни в одной группе.")
            return

        if len(user_groups) == 1:
            # Если только одна группа - показываем ее рейтинг
            await show_leaderboard(message, user_groups[0]['group_code'])
        else:
            # Если несколько групп - предлагаем выбрать
            keyboard = ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text=group['group_code'])] for group in user_groups],
                resize_keyboard=True
            )
            await message.answer("Выберите группу для просмотра рейтинга:", reply_markup=keyboard)


@dp.message(lambda message: not message.text.startswith('/'))
async def handle_text(message: types.Message):
    # Обработка специальных команд
    if message.text in ["Начать игру", "Следующее задание"]:
        await start_game_cmd(message)
        return

    # Проверка на код группы для рейтинга
    async with db_pool.acquire() as conn:
        user_groups = await conn.fetch(
            "SELECT group_code FROM user_groups WHERE user_id = $1",
            message.from_user.id
        )

        if message.text in [group['group_code'] for group in user_groups]:
            await show_leaderboard(message, message.text)
            return

    # Проверка на название музея
    museums = await get_museums()
    if message.text in museums:
        async with db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET current_museum = "
                "(SELECT id FROM museums WHERE name = $1), "
                "current_task_id = 0 WHERE id = $2",
                message.text, message.from_user.id
            )
            await message.answer(f"Вы выбрали музей: {message.text}")
            await send_tasks(message)
        return

    # Если это не специальная команда и не музей - обрабатываем как ответ на вопрос
    await handle_quiz_answer(message)


async def handle_quiz_answer(message: types.Message):
    user_id = message.from_user.id
    answer = message.text.strip().lower()

    async with db_pool.acquire() as conn:
        # Получаем текущий музей и задание
        current_museum = await conn.fetchval(
            "SELECT current_museum FROM users WHERE id=$1",
            user_id
        )

        if current_museum is None:
            await message.answer("Сначала выберите музей.")
            return

        task = await conn.fetchrow(
            "SELECT id, correct_answer, options FROM tasks WHERE id = ("
            "  SELECT current_task_id FROM users WHERE id=$1"
            ") AND museum_id=$2",
            user_id, current_museum
        )

        if not task:
            await message.answer("Задание не найдено.")
            return

        # Проверяем, есть ли варианты ответов
        options = []
        if task.get('options'):
            try:
                options = json.loads(task['options'].strip())
            except:
                options = []

        # Если есть варианты ответов, но ответ не из вариантов
        if options and answer not in [opt.lower() for opt in options]:
            await message.answer("Пожалуйста, выберите один из предложенных вариантов.")
            return

        # Проверка правильности ответа
        if answer == task["correct_answer"].lower():
            # Обновляем счёт и задание
            await conn.execute(
                "UPDATE users SET score = score + 1, current_task_id = current_task_id + 1 WHERE id=$1",
                user_id
            )

            keyboard = ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="/next (Следующее задание)")]],
                resize_keyboard=True
            )
            await message.answer("✅ Правильный ответ!", reply_markup=keyboard)
        else:
            await message.answer("❌ Неправильный ответ. Попробуйте снова.")


@dp.callback_query(F.data.startswith("answer:"))
async def handle_inline_answer(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    selected = callback.data.split(":")[1].strip().lower()

    async with db_pool.acquire() as conn:
        current_museum = await conn.fetchval(
            "SELECT current_museum FROM users WHERE id=$1",
            user_id
        )

        if current_museum is None:
            await callback.message.answer("Сначала выберите музей.")
            return

        task = await conn.fetchrow(
            "SELECT id, correct_answer FROM tasks WHERE id=("
            " SELECT current_task_id FROM users WHERE id=$1"
            ") AND museum_id=$2",
            user_id, current_museum
        )

        if not task:
            await callback.message.answer("Задание не найдено.")
            return

        if selected == task["correct_answer"].strip().lower():
            await conn.execute(
                "UPDATE users SET score = score + 1, current_task_id = current_task_id + 1 WHERE id=$1",
                user_id
            )
            await callback.message.edit_reply_markup()  # Убираем кнопки
            await callback.message.answer("✅ Верно! /next — следующее задание")
        else:
            await callback.answer("❌ Неверно. Попробуй снова!", show_alert=True)

@dp.message(Command("tasks"))
async def send_tasks(message: types.Message):
    async with db_pool.acquire() as conn:
        user_id = message.from_user.id
        current_museum = await conn.fetchval("SELECT current_museum FROM users WHERE id=$1", user_id)

        if current_museum is None:
            await message.answer("Сначала выберите музей.")
            return

        current_task_id = await conn.fetchval(
            "SELECT current_task_id FROM users WHERE id=$1",
            user_id
        )

        if not current_task_id:  # Если текущего задания нет
            current_task_id = await conn.fetchval(
                "SELECT MIN(id) FROM tasks WHERE museum_id=$1", current_museum
            )

            if not current_task_id:
                await message.answer("Для выбранного музея пока нет заданий.")
                return

            await conn.execute(
                "UPDATE users SET current_task_id=$1 WHERE id=$2",
                current_task_id, user_id
            )

        task = await conn.fetchrow(
            "SELECT id, question, options FROM tasks WHERE id=$1 AND museum_id=$2",
            current_task_id, current_museum
        )

        if not task:
            await message.answer("Для выбранного музея пока нет заданий.")
            return

        # Получаем список опций, если они есть
        options = task.get('options')
        # print(options, 1)
        if options:
            try:
                # Убираем лишние пробелы и гарантируем правильный формат
                options = options.strip()

                # Проверяем, является ли строка валидным JSON
                if options.startswith('[') and options.endswith(']'):
                    options = json.loads(options)
                else:
                    options = []

            except json.JSONDecodeError:
                options = []  # Если не удается распарсить JSON, делаем options пустым

        # print(options)
        if options:
            # print(f"options перед созданием клавиатуры: {options}")

            # Создаем inline-кнопки
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text=opt, callback_data=f"answer:{opt}")]
                    for opt in options
                ]
            )

            await message.answer(
                f"📌 Вопрос: {task['question']}",
                reply_markup=keyboard
            )
        else:
            # Старый способ: просто текст
            await message.answer(
                f"📌 Вопрос: {task['question']}\n"
                "Отправьте ваш ответ."
            )

@dp.message(Command("next"))
async def next_task(message: types.Message):
    user_id = message.from_user.id

    async with db_pool.acquire() as conn:
        current_museum = await conn.fetchval("SELECT current_museum FROM users WHERE id=$1", user_id)

        if current_museum is None:
            await message.answer("Сначала выберите музей.")
            return

        print(f"[next_task] User {user_id} is in museum {current_museum}")

        # Получаем текущее задание
        current_task_id = await conn.fetchval(
            "SELECT current_task_id FROM users WHERE id=$1",
            user_id
        )
        print(f"[next_task] Current task ID: {current_task_id}")

        # Находим следующее задание
        next_task = await conn.fetchrow(
            "SELECT id, question, options FROM tasks WHERE id>=$1 AND museum_id=$2 ORDER BY id ASC LIMIT 1",
            current_task_id, current_museum
        )

        if not next_task:
            print(f"[next_task] No more tasks found for user {user_id}")

            # Загружаем список музеев
            museums = await get_museums()
            keyboard = ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text=museum)] for museum in museums],
                resize_keyboard=True
            )

            await message.answer(
                "🎉 Вы прошли все задания в этом музее!\n"
                "Хотите перейти в другой музей? Выберите его ниже:",
                reply_markup=keyboard
            )
            return

        # Обновляем текущее задание
        await conn.execute(
            "UPDATE users SET current_task_id = $1 WHERE id=$2",
            next_task["id"], user_id
        )
        print(f"[next_task] Updated user {user_id} current_task_id to {next_task['id']}")

        # Получаем список опций, если они есть
        options = next_task.get('options')
        # print(options, 1)
        if options:
            try:
                # Убираем лишние пробелы и гарантируем правильный формат
                options = options.strip()

                # Проверяем, является ли строка валидным JSON
                if options.startswith('[') and options.endswith(']'):
                    options = json.loads(options)
                else:
                    options = []

            except json.JSONDecodeError:
                options = []  # Если не удается распарсить JSON, делаем options пустым

        # print(options)
        if options:
            # print(f"options перед созданием клавиатуры: {options}")

            # Создаем inline-кнопки
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text=opt, callback_data=f"answer:{opt}")]
                    for opt in options
                ]
            )

            await message.answer(
                f"📌 Вопрос: {next_task['question']}",
                reply_markup=keyboard
            )
        else:
            # Старый способ: просто текст
            await message.answer(
                f"📌 Вопрос: {next_task['question']}\n"
                "Отправьте ваш ответ."
            )


@dp.message(lambda message: not message.text.startswith('/'))
async def handle_answer(message: types.Message):
    if message.text == "Следующее задание":
        return  # Игнорируем это сообщение, так как оно обрабатывается в next_task

    user_id = message.from_user.id
    answer = message.text.strip().lower()

    async with db_pool.acquire() as conn:
        current_museum = await conn.fetchval(
            "SELECT current_museum FROM users WHERE id=$1", user_id
        )

        if current_museum is None:
            await message.answer("Сначала выберите музей.")
            return

        task = await conn.fetchrow(
            "SELECT id, correct_answer FROM tasks WHERE id = ("
            "  SELECT current_task_id FROM users WHERE id=$1"
            ") AND museum_id=$2",
            user_id, current_museum
        )

        if not task:
            await message.answer("Задание не найдено или оно не относится к выбранному музею.")
            return

        # Проверка правильности ответа
        if answer == task["correct_answer"].lower():
            # Обновляем счёт и задание
            await conn.execute(
                "UPDATE users SET score = score + 1, current_task_id = current_task_id + 1 WHERE id=$1",
                user_id
            )

            # Создаём клавиатуру с кнопкой для следующего задания
            keyboard = ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="/next (Следующее задание)")]],
                resize_keyboard=True
            )

            await message.answer("✅ Правильный ответ!", reply_markup=keyboard)
        else:
            await message.answer("❌ Неправильный ответ. Попробуйте снова.")


async def show_leaderboard(message: types.Message, group_code=None):
    async with db_pool.acquire() as conn:
        # Показываем группы пользователя
        user_groups = await conn.fetch(
            "SELECT g.code, g.name FROM user_groups ug "
            "JOIN groups g ON ug.group_code = g.code "
            "WHERE ug.user_id = $1",
            message.from_user.id
        )

        if user_groups:
            groups_text = "\n".join([f"• {g['name']} (код: {g['code']})" for g in user_groups])
            await message.answer(f"📌 Ваши группы:\n{groups_text}")

        # Показываем рейтинг
        if group_code:
            # Рейтинг внутри группы
            top_users = await conn.fetch(
                "SELECT u.username, u.score FROM user_groups ug "
                "JOIN users u ON ug.user_id = u.id "
                "WHERE ug.group_code = $1 "
                "ORDER BY u.score DESC LIMIT 10",
                group_code
            )
            group_name = await conn.fetchval(
                "SELECT name FROM groups WHERE code = $1",
                group_code
            )
            leaderboard_text = f"🏆 Топ группы {group_name}:\n"
        else:
            # Общий рейтинг
            top_users = await conn.fetch(
                "SELECT username, score FROM users ORDER BY score DESC LIMIT 10"
            )
            leaderboard_text = "🏆 Общий топ игроков:\n"

        for i, user in enumerate(top_users, start=1):
            leaderboard_text += f"{i}. {user['username']}: {user['score']} очков\n"

        await message.answer(leaderboard_text)


@dp.message(Command("help"))
async def help_cmd(message: types.Message):
    help_text = (
        "📌 Доступные команды:\n"
        "/start - Начать игру и зарегистрироваться\n"
        "/tasks - Получить следующее задание\n"
        "/leaderboard - Посмотреть рейтинг игроков\n"
        "/help - Показать это сообщение с инструкциями"
    )
    await message.answer(help_text)


async def main():
    await create_db_pool()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())