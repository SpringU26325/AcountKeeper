"""Application-wide paths and constants."""

from pathlib import Path


CSV_FIELDS = ("id", "date", "amount", "category", "note")
BASE_DIR = Path(__file__).resolve().parent
CSV_PATH = BASE_DIR / "account.csv"
DB_PATH = BASE_DIR / "account.db"
SNAIL_IMAGE_PATH = BASE_DIR / "image" / "snail.png"
SNAIL_IMAGE_CANDIDATES = (
    SNAIL_IMAGE_PATH,
    BASE_DIR / "image" / "snail_blue_anti.png",
)
SNAIL_MESSAGES_PATH = BASE_DIR / "snail_messages.json"
DEFAULT_SNAIL_MESSAGE = "今天也要好好记账哦！"
