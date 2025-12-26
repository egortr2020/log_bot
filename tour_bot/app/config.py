from pathlib import Path
from typing import List, Optional

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    BOT_TOKEN: Optional[SecretStr] = None
    ADMINS: List[int] = []

    # новый параметр: ключ внешнего API расписаний
    YANDEX_RASP_API_KEY: Optional[str] = None

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
    )


settings = Settings()
