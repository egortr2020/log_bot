from collections import defaultdict
from datetime import date
from typing import Dict, Iterable, List
from urllib.parse import quote

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from tour_bot.app.services.planner import build_segments
from tour_bot.app.services.transport import (
    TransportOption,
    build_yandex_thread_link,
    fetch_real_options,
    filter_and_sort_options,
)
from tour_bot.app.states import TourPlanStates



router = Router()


def _group_by_departure_day(options: Iterable[TransportOption]) -> Dict[date, List[TransportOption]]:
    grouped: Dict[date, List[TransportOption]] = defaultdict(list)
    for opt in options:
        grouped[opt.depart_time.date()].append(opt)
    return dict(sorted(grouped.items(), key=lambda item: item[0]))


def _format_option(o: TransportOption) -> str:
    icon = "✈️" if o.kind == "plane" else "🚆" if o.kind == "train" else "🚌"
    link_line = ""
    if o.thread_uid:
        link = build_yandex_thread_link(
            o.thread_uid,
            o.depart_time.date().isoformat(),
            o.from_code,
            o.to_code,
        )
        link_line = f"\n🔗 [Открыть на Яндексе]({link})"

    price_line = ""
    if o.price is not None:
        cur = (o.currency or "").upper()
        price_line = f"\nцена от {o.price:.0f} {cur}"

    return (
        f"{icon} {o.title}\n"
        f"выезд {o.depart_time}\n"
        f"прибытие {o.arrive_time}\n"
        f"длительность ~{o.duration_hours:.1f} ч"
        f"{price_line}"
        f"{link_line}"
    )


@router.message(Command("newtour"))
async def start_tour(message: types.Message, state: FSMContext):
    # сбрасываем предыдущее состояние, если оно было
    await state.clear()

    await message.answer(
        "Давай спланируем тур.\n\n"
        "Пришли города по порядку, через запятую.\n"
        "Например:\n"
        "Москва, Санкт-Петербург, Екатеринбург, Казань"
    )

    await state.set_state(TourPlanStates.waiting_city_list)


@router.message(TourPlanStates.waiting_city_list)
async def handle_cities(message: types.Message, state: FSMContext):
    raw = message.text.strip()
    cities = [c.strip() for c in raw.split(",") if c.strip()]

    if len(cities) < 2:
        await message.answer("Нужно минимум 2 города. Пришли ещё раз.")
        return

    # сохраняем список городов в состоянии
    await state.update_data(cities_ordered=cities)

    # готовим шаблон, как надо прислать даты
    sample_lines = "\n".join([f"{c} — ДД.ММ.ГГГГ" for c in cities])

    await message.answer(
        "Теперь пришли даты концертов для каждого города.\n"
        "Формат по строкам, вот так:\n\n"
        f"{sample_lines}\n\n"
        "Все строки одним сообщением."
    )

    await state.set_state(TourPlanStates.waiting_dates)


@router.message(TourPlanStates.waiting_dates)
async def handle_dates(message: types.Message, state: FSMContext):
    """
    Ждём текст вида (в ОДНОМ сообщении, каждая строка отдельно):
    Москва — 10.11.2025
    Санкт-Петербург — 11.11.2025
    Екатеринбург — 13.11.2025
    """

    def norm_city(name: str) -> str:
        # нижний регистр
        n = name.strip().lower()
        # разные длинные тире внутри имён городов не трогаем
        # но заменим подряд идущие пробелы
        parts = n.split()
        n = " ".join(parts)
        # а вот дефисы внутри города превратим в пробел
        # ("санкт-петербург" -> "санкт петербург")
        n = n.replace("-", "-")  # неразрывный дефис
        n = n.replace("–", "-")
        n = n.replace("—", "-")
        n = n.replace("-", " ")
        parts = n.split()
        n = " ".join(parts)
        return n

    text_raw = message.text.replace("\r\n", "\n").strip()
    lines = [ln.strip() for ln in text_raw.split("\n") if ln.strip()]

    data = await state.get_data()
    cities_original: list[str] = data["cities_ordered"]

    # Сопоставление нормализованное->оригинальное
    cities_norm_map = {norm_city(c): c for c in cities_original}

    # сюда будем складывать даты по нормализованному названию
    parsed_dates_norm: dict[str, str] = {}

    debug_lines = []  # соберу отладку, чтобы отправить тебе прямо в чат

    for line in lines:
        # пробуем найти разделитель " — " (пробел-длинное тире-пробел)
        # если нет — пробуем " - " (пробел-дефис-пробел)
        city_part = None
        date_part = None

        if " — " in line:
            left, right = line.split(" — ", 1)
            city_part = left.strip()
            date_part = right.strip()
            used_sep = " — "
        elif " - " in line:
            left, right = line.split(" - ", 1)
            city_part = left.strip()
            date_part = right.strip()
            used_sep = " - "
        else:
            # не нашли ожидаемый разделитель
            debug_lines.append(f"⚠️ Не смог понять строку: «{line}» (нет разделителя)")
            continue

        # пытаемся дату разобрать как ДД.ММ.ГГГГ
        iso_date = None
        try:
            d, m, y = date_part.split(".")
            iso_date = f"{y}-{m}-{d}"  # YYYY-MM-DD
        except Exception:
            debug_lines.append(f"⚠️ Не смог понять дату «{date_part}» в строке: «{line}»")
            continue

        norm_key = norm_city(city_part)
        parsed_dates_norm[norm_key] = iso_date

        debug_lines.append(
            f"✅ Парс строки: [{city_part}] ({norm_key}) -> {iso_date} через разделитель {used_sep}"
        )

    # теперь проверяем, что для каждого города из тура у нас есть дата
    missing_human = []
    final_shows: dict[str, str] = {}

    for orig_city in cities_original:
        nk = norm_city(orig_city)
        if nk not in parsed_dates_norm:
            # нет даты для этого города
            missing_human.append(orig_city)
        else:
            final_shows[orig_city] = parsed_dates_norm[nk]

    if missing_human:
        # добавлю отладку, чтобы ты прямо в телеге видел, что бот распарсил, а что нет
        dbg_text = "\n".join(debug_lines) if debug_lines else "(нет отладочных данных)"
        await message.answer(
            "Не у всех городов есть дата. Не хватает:\n"
            + "\n".join(missing_human)
            + "\n\nЯ распознала так:\n"
            + dbg_text
            + "\n\nПришли, пожалуйста, даты заново в формате:\n"
            "Город — ДД.ММ.ГГГГ"
        )
        return

    # Всё есть — сохраняем
    await state.update_data(shows=final_shows)

    # Спрашиваем предпочтение транспорта
    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text="✈️ Только самолёт", callback_data="pref:plane")],
            [types.InlineKeyboardButton(text="🚆 Только поезд", callback_data="pref:train")],
            [types.InlineKeyboardButton(text="Сначала самолёт, потом поезд", callback_data="pref:plane_first")],
            [types.InlineKeyboardButton(text="Сначала поезд, потом самолёт", callback_data="pref:train_first")],
        ]
    )

    await message.answer(
        "Как предпочтительнее перемещаться между городами?",
        reply_markup=kb
    )

    await state.set_state(TourPlanStates.waiting_transport_pref)
def build_yandex_link(from_city: str, to_city: str, depart_dt) -> str:
    """
    Делает ссылку вида:
    https://rasp.yandex.ru/search/?fromName=Москва&toName=Санкт-Петербург&when=2025-11-11
    """
    when = depart_dt.date().isoformat()  # YYYY-MM-DD
    return (
        "https://rasp.yandex.ru/search/"
        f"?fromName={quote(from_city)}&toName={quote(to_city)}&when={quote(when)}"
    )
def build_yandex_search_link(from_city: str, to_city: str, depart_dt):
    """
    Универсальная ссылка на поиск всех вариантов между городами.
    Пример:
    https://rasp.yandex.ru/search/?fromName=Москва&toName=Санкт-Петербург&when=2025-11-11
    """
    when = depart_dt.date().isoformat()
    return (
        "https://rasp.yandex.ru/search/"
        f"?fromName={quote(from_city)}&toName={quote(to_city)}&when={quote(when)}"
    )
from urllib.parse import quote




@router.callback_query(F.data.startswith("pref:"), TourPlanStates.waiting_transport_pref)
async def handle_pref(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()

    pref = callback.data.split(":", 1)[1]
    await state.update_data(transport_pref=pref)

    await callback.message.answer(
        "За сколько часов до концерта артист должен быть уже в городе?\n"
        "Например: 12"
    )

    await state.set_state(TourPlanStates.waiting_buffer_before)


@router.message(TourPlanStates.waiting_buffer_before)
async def handle_buffer_before(message: types.Message, state: FSMContext):
    # buffer_before_hours = за сколько часов до концерта артист обязан быть на месте
    try:
        before_h = int(message.text.strip())
        if before_h < 0 or before_h > 72:
            raise ValueError
    except ValueError:
        await message.answer("Нужно целое количество часов от 0 до 72. Пришли ещё раз.")
        return

    await state.update_data(buffer_before_hours=before_h)

    await message.answer(
        "Через сколько часов после концерта можно уезжать из города?\n"
        "Например: 3"
    )

    await state.set_state(TourPlanStates.waiting_buffer_after)


@router.message(TourPlanStates.waiting_buffer_after)
async def handle_buffer_after(message: types.Message, state: FSMContext):
    """
    Это финальный шаг:
    - получаем buffer_after_hours;
    - считаем окна между городами;
    - собираем варианты транспорта из внешнего источника/мока;
    - отдаём пользователю план.
    """
    try:
        after_h = int(message.text.strip())
        if after_h < 0 or after_h > 48:
            raise ValueError
    except ValueError:
        await message.answer("Нужно целое количество часов от 0 до 48. Пришли ещё раз.")
        return

    # забираем всё, что накопили до этого
    data = await state.get_data()
    cities = data["cities_ordered"]
    shows = data["shows"]
    pref = data["transport_pref"]
    buf_before = data["buffer_before_hours"]
    buf_after = after_h  # только что прислал пользователь

    # строим логистические сегменты
    segments = build_segments(
        cities_ordered=cities,
        shows=shows,
        buffer_before_hours=buf_before,
        buffer_after_hours=buf_after,
    )

    answer_parts = []

    # для каждого сегмента ищем варианты переезда
    for seg in segments:
        # пробуем реальные данные
        real_opts = await fetch_real_options(
            from_city=seg["from_city"],
            to_city=seg["to_city"],
            window_start=seg["earliest_departure"],
            window_end=seg["latest_arrival"],
        )

        # если не получилось (нет API-ключа / нет кодов / пусто) — мок
        if not real_opts:
            header = (
                f"{seg['from_city']} → {seg['to_city']}\n"
                f"Окно выезда: с {seg['earliest_departure']} "
                f"до приезда не позже {seg['latest_arrival']}\n"
            )
            answer_parts.append(header + "Подходящих вариантов не найдено.\n")
            continue

        # сортируем с учётом предпочтения
        opts_sorted = filter_and_sort_options(real_opts, pref)

        day_groups = _group_by_departure_day(opts_sorted)

        # шапка сегмента
        header = (
            f"{seg['from_city']} → {seg['to_city']}\n"
            f"Окно выезда: с {seg['earliest_departure']} "
            f"до приезда не позже {seg['latest_arrival']}\n"
        )

        if not day_groups:
            body = "Подходящих вариантов не найдено.\n"
        else:
            day_blocks: List[str] = []
            for day, opts in day_groups.items():
                # ограничиваем до 3 вариантов на каждый день, чтобы сообщение не разрасталось
                top_opts = opts[:3]
                options_text = "\n".join(_format_option(o) for o in top_opts)
                day_blocks.append(f"📅 {day.isoformat()}\n{options_text}")

            body = "\n\n".join(day_blocks) + "\n"

        answer_parts.append(header + body)

    full_answer = "План тура готов:\n\n" + "\n".join(answer_parts)

    await message.answer(full_answer,  parse_mode="Markdown")
    await state.clear()
