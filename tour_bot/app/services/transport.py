from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Literal, Optional, Set, Tuple
from urllib.parse import quote

import aiohttp

from tour_bot.app.config import settings  # при желании заменить на: from app.config import settings
import logging

logger = logging.getLogger(__name__)

SUGGEST_URL = "https://api.rasp.yandex.net/v3.0/suggest/"
SEARCH_URL = "https://api.rasp.yandex.net/v3.0/search/"

TransportType = Literal["plane", "train", "other"]


CITY_CODE_MAP: Dict[str, Dict[str, Any]] = {
    "Москва": {"city_code": "c213"},
    "Санкт-Петербург": {"city_code": "c2"},
    "Екатеринбург": {"city_code": "c54"},
}


@dataclass(frozen=True)
class PlaceCodes:
    city_code: Optional[str]
    stations: Tuple[str, ...] = ()


@dataclass(frozen=True)
class TransportOption:
    kind: TransportType
    title: str
    depart_time: datetime
    arrive_time: datetime
    duration_hours: float
    thread_uid: Optional[str]
    from_code: Optional[str]
    to_code: Optional[str]
    price: Optional[float]
    currency: Optional[str]


def build_yandex_thread_link(uid: str, when_date: str,
                            from_code: Optional[str] = None,
                            to_code: Optional[str] = None) -> str:
    base = f"https://rasp.yandex.ru/thread/{quote(uid)}"
    qs = f"?when={quote(when_date)}"
    if from_code:
        qs += f"&fromId={quote(from_code)}"
    if to_code:
        qs += f"&toId={quote(to_code)}"
    return base + qs


class YandexRaspClient:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self._session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self) -> "YandexRaspClient":
        timeout = aiohttp.ClientTimeout(total=25, connect=10)
        connector = aiohttp.TCPConnector(limit=20, ttl_dns_cache=300)
        self._session = aiohttp.ClientSession(
            timeout=timeout,
            connector=connector,
            headers={"User-Agent": "tour-bot/1.0"},
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    async def _get_json(self, url: str, params: Dict[str, Any]) -> Dict[str, Any]:
        if not self._session:
            raise RuntimeError("ClientSession is not initialized. Use 'async with YandexRaspClient(...)'.")

        backoffs = [0.5, 1.0, 2.0, 4.0]
        last_err: Optional[Exception] = None

        for delay in backoffs:
            try:
                async with self._session.get(url, params=params) as resp:
                    if resp.status == 200:
                        return await resp.json()

                    if resp.status in (429, 500, 502, 503, 504):
                        await asyncio.sleep(delay)
                        continue

                    return {}
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last_err = e
                await asyncio.sleep(delay)

        return {}

    async def suggest_place(self, query: str) -> PlaceCodes:
        q = query.strip()
        if not q:
            return PlaceCodes(city_code=None, stations=())

        params = {
            "apikey": self.api_key,
            "format": "json",
            "lang": "ru_RU",
            "q": q,
        }

        data = await self._get_json(SUGGEST_URL, params=params)

        settlements = (data or {}).get("settlements") or []
        stations = (data or {}).get("stations") or []

        city_code: Optional[str] = None
        for s in settlements:
            code = s.get("code") or s.get("yandex_code")
            if isinstance(code, str) and code.startswith("c"):
                city_code = code
                break

        station_ids: List[str] = []
        for st in stations:
            scode = st.get("code") or st.get("yandex_code")
            if isinstance(scode, str) and scode.startswith("s"):
                station_ids.append(scode)

        return PlaceCodes(city_code=city_code, stations=tuple(station_ids))

    async def search(
        self,
        *,
        from_code: str,
        to_code: str,
        date: str,
        transport_types: str,
        offset: int = 0,
        limit: int = 100,
    ) -> Dict[str, Any]:
        params = {
            "apikey": self.api_key,
            "format": "json",
            "lang": "ru_RU",
            "from": from_code,
            "to": to_code,
            "date": date,
            "transport_types": transport_types,
            "transfers": "false",
            "offset": offset,
            "limit": limit,
        }
        return await self._get_json(SEARCH_URL, params=params)


_city_cache: Dict[str, PlaceCodes] = {}


def _normalize_city_key(name: str) -> str:
    return " ".join(name.strip().lower().split())


def _parse_dt_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.replace(tzinfo=None) if dt.tzinfo else dt
    except Exception:
        return None


def _extract_price(seg: Dict[str, Any]) -> Tuple[Optional[float], Optional[str]]:
    ti = seg.get("tickets_info") or {}
    places = ti.get("places") or []
    if not places:
        return None, None

    p = (places[0].get("price") or {})
    val = p.get("value") or p.get("whole") or p.get("rub")
    cur = p.get("currency") or ("RUB" if p.get("rub") else None)

    try:
        return (float(val) if val is not None else None), cur
    except Exception:
        return None, cur


def _segment_to_option(seg: Dict[str, Any]) -> Optional[TransportOption]:
    thread = seg.get("thread") or {}
    transport_type = thread.get("transport_type") or "other"
    kind: TransportType = "plane" if transport_type == "plane" else "train" if transport_type == "train" else "other"

    dep_dt = _parse_dt_iso(seg.get("departure"))
    arr_dt = _parse_dt_iso(seg.get("arrival"))
    if not dep_dt or not arr_dt:
        return None

    dur_seconds = seg.get("duration")
    if isinstance(dur_seconds, (int, float)) and dur_seconds > 0:
        dur_h = float(dur_seconds) / 3600.0
    else:
        dur_h = (arr_dt - dep_dt).total_seconds() / 3600.0

    number = thread.get("number") or ""
    title = thread.get("title") or number or "рейс/поезд"
    uid = thread.get("uid")

    price, currency = _extract_price(seg)

    return TransportOption(
        kind=kind,
        title=title,
        depart_time=dep_dt,
        arrive_time=arr_dt,
        duration_hours=dur_h,
        thread_uid=uid,
        from_code=(seg.get("from") or {}).get("code"),
        to_code=(seg.get("to") or {}).get("code"),
        price=price,
        currency=currency,
    )


def _parse_segments(resp_json: Dict[str, Any], allow_to_codes: Optional[Set[str]] = None) -> List[TransportOption]:
    segments = (resp_json or {}).get("segments") or []
    out: List[TransportOption] = []

    for seg in segments:
        to_obj = seg.get("to") or {}
        to_code = to_obj.get("code")

        if allow_to_codes and to_code not in allow_to_codes:
            continue

        opt = _segment_to_option(seg)
        if opt:
            out.append(opt)

    return out


async def _resolve_place_codes(client: YandexRaspClient, city: str) -> PlaceCodes:
    key = _normalize_city_key(city)
    if key in _city_cache:
        return _city_cache[key]

    mapped = CITY_CODE_MAP.get(city)
    if mapped:
        pc = PlaceCodes(city_code=mapped.get("city_code"), stations=tuple(mapped.get("stations", ())))
        _city_cache[key] = pc
        return pc

    pc = await client.suggest_place(city)
    _city_cache[key] = pc
    return pc


def _collect_dates(window_start: datetime, window_end: datetime) -> List[str]:
    dates: List[str] = []
    cur = window_start.date()
    end = window_end.date()
    while cur <= end:
        dates.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    return dates


async def _search_all_options_for_date(
    client: YandexRaspClient,
    *,
    from_code: str,
    to_code: str,
    date: str,
    transport: str,
    allow_to_codes: Set[str],
) -> List[TransportOption]:
    all_options: List[TransportOption] = []
    offset = 0
    limit = 100
    max_pages = 10  # safety to avoid infinite pagination loops

    for _ in range(max_pages):
        resp = await client.search(
            from_code=from_code,
            to_code=to_code,
            date=date,
            transport_types=transport,
            offset=offset,
            limit=limit,
        )

        parsed = _parse_segments(resp, allow_to_codes=allow_to_codes)
        all_options.extend(parsed)

        pagination = (resp or {}).get("pagination") or {}
        total = pagination.get("total")
        if total is None or offset + limit >= total or not parsed:
            break

        offset += limit

    return all_options


async def fetch_real_options(
    from_city: str,
    to_city: str,
    window_start: datetime,
    window_end: datetime,
) -> List[TransportOption]:
    api_key = settings.YANDEX_RASP_API_KEY
    if not api_key:
        logger.warning("YANDEX_RASP_API_KEY не задан, возвращаем пустой список")
        return []

    async with YandexRaspClient(api_key) as client:
        from_codes = await _resolve_place_codes(client, from_city)
        to_codes = await _resolve_place_codes(client, to_city)

        from_code = from_codes.city_code or (from_codes.stations[0] if from_codes.stations else None)
        to_code = to_codes.city_code or (to_codes.stations[0] if to_codes.stations else None)
        if not from_code or not to_code:
            logger.warning(
                "Не удалось получить коды городов: %s -> %s (from: %s, to: %s)",
                from_city,
                to_city,
                from_codes,
                to_codes,
            )
            return []

        allow_to_codes: Set[str] = set([to_code, *to_codes.stations])

        dates = _collect_dates(window_start, window_end)

        all_options: List[TransportOption] = []
        seen: Set[str] = set()

        for date_str in dates:
            for transport in ("plane", "train"):
                parsed_options = await _search_all_options_for_date(
                    client,
                    from_code=from_code,
                    to_code=to_code,
                    date=date_str,
                    transport=transport,
                    allow_to_codes=allow_to_codes,
                )
                for opt in parsed_options:
                    if opt.depart_time < window_start:
                        continue
                    if opt.arrive_time > window_end:
                        continue

                    key = (opt.thread_uid or opt.title) + "|" + opt.depart_time.isoformat()
                    if key in seen:
                        continue
                    seen.add(key)
                    all_options.append(opt)

        all_options.sort(key=lambda o: o.depart_time)
        logger.info(
            "Всего вариантов %s для %s -> %s в датах %s",
            len(all_options),
            from_city,
            to_city,
            dates,
        )
        return all_options


def filter_and_sort_options(options: List[TransportOption], preference: str) -> List[TransportOption]:
    if preference == "plane":
        return [o for o in options if o.kind == "plane"]
    if preference == "train":
        return [o for o in options if o.kind == "train"]
    if preference == "plane_first":
        return sorted(options, key=lambda o: 0 if o.kind == "plane" else 1)
    if preference == "train_first":
        return sorted(options, key=lambda o: 0 if o.kind == "train" else 1)
    return options
