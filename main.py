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
            "INSERT INTO users (id, username, score, current_task_id) VALUES ($1, $2, 0, 0) ON CONFLICT (id) DO NOTHING",
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
        museums = await get_museums()  # Получаем список музеев

        # Проверяем, является ли введённый текст названием музея
        if message.text not in museums:  # Это не музей, значит, это ответ на вопрос
            await handle_answer(message)
            return

        # Если музей есть в списке, получаем его ID
        museum = await conn.fetchrow(
            "SELECT id FROM museums WHERE name=$1",
            message.text
        )

        await conn.execute(
            "UPDATE users SET current_museum=$1 WHERE id=$2",
            museum['id'], message.from_user.id
        )
        await message.answer(
            f"Вы выбрали музей: {message.text}\n"
            "Теперь можете получить задания командой /tasks"
        )


@dp.message(Command("tasks"))
async def send_tasks(message: types.Message):
    async with db_pool.acquire() as conn:
        user_id = message.from_user.id
        current_task_id = await conn.fetchval(
            "SELECT current_task_id FROM users WHERE id=$1",
            user_id
        )

        if not current_task_id:  # Если текущего задания нет
            await conn.execute(
                "UPDATE users SET current_task_id=1 WHERE id=$1",  # Устанавливаем первое задание
                user_id
            )
            current_task_id = 1

        task = await conn.fetchrow(
            "SELECT id, question FROM tasks WHERE id=$1",
            current_task_id
        )

        if not task:
            await message.answer("Для выбранного музея пока нет заданий.")
            return

        await message.answer(
            f"Вопрос: {task['question']}\n"
            "Отправьте ваш ответ."
        )


@dp.message(lambda message: not message.text.startswith('/'))
async def handle_answer(message: types.Message):
    user_id = message.from_user.id
    answer = message.text.strip().lower()

    async with db_pool.acquire() as conn:
        # Получаем текущее задание
        current_task_id = await conn.fetchval(
            "SELECT current_task_id FROM users WHERE id=$1", user_id,
        )

        if not current_task_id:
            await message.answer("Сначала получите задание командой /tasks.")
            return

        task = await conn.fetchrow(
            "SELECT id, correct_answer FROM tasks WHERE id=$1", current_task_id
        )

        if not task:
            await message.answer("Задание не найдено.")
            return

        # Проверяем правильность ответа
        if answer == task["correct_answer"].lower():
            # Ответ правильный, увеличиваем счет и переходим к следующему заданию
            await conn.execute(
                "UPDATE users SET score = score + 1, current_task_id = current_task_id + 1 WHERE id=$1",
                user_id
            )
            await message.answer("Правильный ответ! Переходите к следующему вопросу командой /tasks.")
        else:
            await message.answer("Неправильный ответ. Попробуйте снова.")


async def main():
    await create_db_pool()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
