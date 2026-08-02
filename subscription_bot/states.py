from aiogram.fsm.state import State, StatesGroup


class AdminBroadcast(StatesGroup):
    waiting_content = State()
