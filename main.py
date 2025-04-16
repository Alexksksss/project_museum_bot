import logging
import asyncpg
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import Command

import json

def load_config():
    with open('config.json') as f:
        return json.load(f)

config = load_config()
API_TOKEN = config['bot_token']
DB_URL = config['database_url']


logging.basicConfig(level=logging.INFO)

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

db_pool = None

async def create_db_pool():
    global db_pool
    db_pool = await asyncpg.create_pool(DB_URL)


@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO users (id, username, score, current_task_id)
            VALUES ($1, $2, 0, 0)
            ON CONFLICT (id) DO UPDATE
            SET current_task_id = 0
            """,
            message.from_user.id, message.from_user.username
        )

    keyboard = ReplyKeyboardMarkup(
        keyboard=[[]],  # Пустое состояние клавиатуры
        resize_keyboard=True
    )

    museums = await get_museums()
    keyboard.keyboard.extend([[KeyboardButton(text=museum)] for museum in museums])

    await message.answer("Выберите музей:", reply_markup=keyboard)


async def get_museums():
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT name FROM museums")
        return [row["name"] for row in rows]


@dp.message(lambda message: not message.text.startswith('/'))
async def handle_text(message: types.Message):
    async with db_pool.acquire() as conn:
        museums = await get_museums()

        # Если текст является названием музея, обновляем текущий музей у пользователя
        if message.text in museums:
            museum = await conn.fetchrow(
                "SELECT id FROM museums WHERE name=$1",
                message.text
            )

            await conn.execute(
                "UPDATE users SET current_museum=$1, current_task_id=0 WHERE id=$2",
                museum['id'], message.from_user.id
            )

            await message.answer(
                f"Вы выбрали музей: {message.text}\n"
                "Теперь можете получить задания командой /tasks."
            )
            return

        # Проверяем, выбрал ли пользователь музей
        current_museum = await conn.fetchval(
            "SELECT current_museum FROM users WHERE id=$1",
            message.from_user.id
        )

        if current_museum is None:
            await message.answer("Сначала выберите музей.")
            return

        # Если музей выбран, обрабатываем как ответ
        await handle_answer(message)


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
            "SELECT id, question FROM tasks WHERE id=$1 AND museum_id=$2",
            current_task_id, current_museum
        )

        if not task:
            await message.answer("Для выбранного музея пока нет заданий.")
            return

        await message.answer(
            f"Вопрос: {task['question']}\n"
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
            "SELECT id, question FROM tasks WHERE id>=$1 AND museum_id=$2 ORDER BY id ASC LIMIT 1",
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

        # Отправляем следующее задание
        await message.answer(
            f"📌 Следующее задание:\n{next_task['question']}\n"
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


@dp.message(Command("leaderboard"))
async def show_leaderboard(message: types.Message):
    async with db_pool.acquire() as conn:
        top_users = await conn.fetch("SELECT username, score FROM users ORDER BY score DESC LIMIT 10")

        if not top_users:
            await message.answer("Пока нет игроков в рейтинге.")
            return

        leaderboard_text = "🏆 Топ-10 игроков:\n"
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
