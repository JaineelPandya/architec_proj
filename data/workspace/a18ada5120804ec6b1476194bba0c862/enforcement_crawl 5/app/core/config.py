from pydantic import SecretStr
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    SCRAPEOPS_URL: str = ""
    SCRAPEOPS_API_KEY: SecretStr = SecretStr("")
    SEARCH_API_KEY: SecretStr = SecretStr("")
    SEARCH_API_URL: str = "https://www.searchapi.io/api/v1/search"
    INDIAN_KANOON_TOKEN: SecretStr = SecretStr("")

    SERVICE_TOKEN: SecretStr = SecretStr("")

    HTTP_TIMEOUT_S: float = 30.0
    GATHER_TIMEOUT_S: float = 60.0
    NEWS_HTTP_TIMEOUT_S: float = 20.0

    FINANCE_FETCH_CONCURRENCY: int = 50
    NEWS_FETCH_CONCURRENCY: int = 5

    MAX_BODY_SIZE_BYTES: int = 1_000_000

    MAX_PAIR_IDS: int = 500
    TASK_TIMEOUT_S: float = 15.0
    MAX_DF_ROWS: int = 10_000
    MAX_NEWS_ROWS: int = 50_000

    NCDEX_VALID_SYMBOLS: list[str] = ["SYOREF", "SYOREFKDL", "SUNOIL", "SUNOILKDL", "CASTOROIL", "CPO"]

    YOUTUBE_DATA_API_KEY: SecretStr = SecretStr("")
    TWITTER_BEARER_TOKEN: SecretStr = SecretStr("")
    LINKEDIN_ACCESS_TOKEN: SecretStr = SecretStr("")
    FACEBOOK_ACCESS_TOKEN: SecretStr = SecretStr("")
    INSTAGRAM_ACCESS_TOKEN: SecretStr = SecretStr("")
    INSTAGRAM_BUSINESS_ACCOUNT_ID: str = ""
    TELEGRAM_BOT_TOKEN: SecretStr = SecretStr("")
    SOCIAL_FETCH_CONCURRENCY: int = 10

    REDDIT_CLIENT_ID: SecretStr = SecretStr("")
    REDDIT_CLIENT_SECRET: SecretStr = SecretStr("")
    REDDIT_USER_AGENT: str = "python:enforcement_crawler:v1.0 (by /u/your_reddit_username)"

    LOG_LEVEL: str = "INFO"
    LOG_JSON: bool = False

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
