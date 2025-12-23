# app/states.py
from aiogram.fsm.state import StatesGroup, State

class TourPlanStates(StatesGroup):
    waiting_city_list = State()
    waiting_dates = State()
    waiting_transport_pref = State()
    waiting_buffer_before = State()
    waiting_buffer_after = State()
    confirm_and_build = State()
