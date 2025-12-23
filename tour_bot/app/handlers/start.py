from aiogram import Router, types
from aiogram.filters import CommandStart, Command

router = Router()

@router.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer(
        "Привет. Я помогу спланировать тур по городам:\n"
        "— перелёты / поезда между городами\n"
        "— окна выезда и прибытия\n"
        "— учёт дат концертов и буферов\n\n"
        "Чтобы начать планирование маршрута, используй команду /newtour"
    )

@router.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(
        "Как работать со мной:\n\n"
        "1. /newtour — запустить новый расчёт тура.\n"
        "   Тебя попрошу:\n"
        "   • прислать список городов по порядку\n"
        "   • дать даты концертов\n"
        "   • указать предпочтение (самолёт/поезд)\n"
        "   • сказать за сколько часов артист должен быть в городе до концерта\n"
        "   • через сколько часов после концерта он может уезжать\n\n"
        "2. Получишь сводку по каждому перегону.\n\n"
        "Если готов — просто напиши /newtour"
    )
