"""Application-wide paths and constants."""

from pathlib import Path
import sys

try:
    from platformdirs import user_data_dir
except ImportError:
    print("警告：无法导入 platformdirs，将使用用户主目录作为数据目录。")

    def user_data_dir(_appname: str, _appauthor: str) -> str:
        return str(Path.home() / "AccountKeeper")


CSV_FIELDS = ("id", "date", "amount", "category", "note")
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(user_data_dir("AccountKeeper", "SpringU26325"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "account.db"
CSV_PATH = DATA_DIR / "account.csv"
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", BASE_DIR))
SNAIL_IMAGE_PATH = RESOURCE_DIR / "image" / "snail.png"
SNAIL_IMAGE_CANDIDATES = (
    SNAIL_IMAGE_PATH,
    RESOURCE_DIR / "image" / "snail_blue_anti.png",
)
SNAIL_MESSAGES_PATH = RESOURCE_DIR / "snail_messages.json"
DEFAULT_SNAIL_MESSAGE = "今天也要好好记账哦！"
