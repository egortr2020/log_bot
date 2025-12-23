# app/services/planner.py
from datetime import datetime, timedelta
from typing import List, Dict, TypedDict

class SegmentWindow(TypedDict):
    from_city: str
    to_city: str
    earliest_departure: datetime
    latest_arrival: datetime
    concert_from_date: datetime
    concert_to_date: datetime

def parse_human_date(date_str: str) -> datetime:
    """
    date_str ожидается в формате YYYY-MM-DD.
    Например: "2025-11-10"
    Возвращаем datetime на 00:00 этого дня.
    """
    return datetime.strptime(date_str.strip(), "%Y-%m-%d")

def build_segments(
    cities_ordered: List[str],
    shows: Dict[str, str],
    buffer_before_hours: int,
    buffer_after_hours: int,
) -> List[SegmentWindow]:
    """
    На основе списка городов и дат концертов строим сегменты переезда.
    cities_ordered: ["Москва", "СПб", "Екатеринбург"]
    shows: {"Москва": "2025-11-10", "СПб": "2025-11-11", ...}
    buffer_before_hours: за сколько часов до концерта артист должен быть в городе
    buffer_after_hours: через сколько часов после концерта можно уезжать из предыдущего города
    """

    out: List[SegmentWindow] = []

    for i in range(len(cities_ordered) - 1):
        city_a = cities_ordered[i]
        city_b = cities_ordered[i + 1]

        # дата концерта в городе A
        concert_a_day = parse_human_date(shows[city_a])
        # считаем, что сам концерт заканчивается в 23:00 локального времени
        concert_a_end = concert_a_day.replace(hour=23, minute=0)

        # дата концерта в городе B
        concert_b_day = parse_human_date(shows[city_b])
        # считаем, что артист должен быть готов в городе B к "день концерта 12:00"
        must_be_ready_b = concert_b_day.replace(hour=12, minute=0)

        # ограничители:
        earliest_departure = concert_a_end + timedelta(hours=buffer_after_hours)
        latest_arrival = must_be_ready_b - timedelta(hours=buffer_before_hours)

        segment: SegmentWindow = {
            "from_city": city_a,
            "to_city": city_b,
            "earliest_departure": earliest_departure,
            "latest_arrival": latest_arrival,
            "concert_from_date": concert_a_day,
            "concert_to_date": concert_b_day,
        }
        out.append(segment)

    return out
