"""Application-wide paths and constants."""

from pathlib import Path
import sys

# platformdirs 是第三方库，用于跨平台获取规范的「用户数据目录」。
# 万一运行环境缺少该依赖，也不能让程序直接崩溃，所以这里做降级处理。
try:
    from platformdirs import user_data_dir
except ImportError:
    print("警告：无法导入 platformdirs，将使用用户主目录作为数据目录。")

    def user_data_dir(_appname: str, _appauthor: str) -> str:
        # 降级方案：把数据写到用户主目录下的 AccountKeeper 文件夹，
        # 保证即使缺少依赖，记账数据也能落盘而不是丢失。
        return str(Path.home() / "AccountKeeper")


# CSV 表头字段顺序，必须与数据库查询列的顺序保持一致，否则导出后列会错位。
# 旧 CSV 迁移逻辑已在 #40 中删除，该常量现仅被 store.export_month_csv 用于写表头。
CSV_FIELDS = ("id", "date", "amount", "category", "note")
# 源码所在目录，用于开发环境下定位 image、snail_messages.json 等资源文件。
BASE_DIR = Path(__file__).resolve().parent
# 用户数据目录（Windows 下通常是 C:\Users\用户名\AppData\Local\AccountKeeper）。
# 需求 3.1 明确要求数据库不能放在程序安装目录或临时解压目录，否则升级/重装会丢数据。
DATA_DIR = Path(user_data_dir("AccountKeeper", "SpringU26325"))
# 启动时就把目录建好，避免后续写数据库时因目录不存在而报错。
DATA_DIR.mkdir(parents=True, exist_ok=True)
# 固定的 SQLite 数据库文件路径。
# 需求 3.11 现已取消全部路径自定义入口（导出目录也只剩「记住上次路径」），数据库位置更不提供配置项，
# 所以这里是唯一权威路径，任何模块都不应再接受外部传入的数据库路径。
DB_PATH = DATA_DIR / "account.db"

# 用户自定义配置文件（需求 3.11）的路径。
# 之所以放在用户数据目录、而不是源码目录：打包成 exe 后源码目录是只读的临时解压目录，
# 往那里写配置会失败；和数据文件放一起也便于用户整体备份/迁移。
SETTINGS_PATH = DATA_DIR / "settings.json"
# 无 last_export_dir 时使用的回退目录。
# 取数据库所在目录，符合「数据集中在一处、方便备份和迁移」的预期；
# settings.json 缺失、损坏、路径非法，或记住的目录已被删除/不可写时，一律回退到它。
DEFAULT_EXPORT_DIR = DATA_DIR

# 资源根目录：PyInstaller 打包后资源会被解压到 sys._MEIPASS，
# 用 getattr 做兼容，未打包时回退到源码目录，保证两种运行方式都能找到图片。
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", BASE_DIR))
# 蜗牛图片的首选路径：必须是 image 目录下真实存在的文件，
# 否则启动时会因找不到图片而放弃加载蜗牛（这里指向抗锯齿版的蓝色蜗牛）。
SNAIL_IMAGE_PATH = RESOURCE_DIR / "image" / "snail_blue_anti.png"
# 按优先级排列的蜗牛图片候选路径，取第一个真实存在的文件，避免换图后程序直接报错。
SNAIL_IMAGE_CANDIDATES = (
    SNAIL_IMAGE_PATH,
    RESOURCE_DIR / "image" / "snail_dark_blue.png",
)
# 蜗牛被点击时随机抽取文案的 JSON 文件路径。
SNAIL_MESSAGES_PATH = RESOURCE_DIR / "snail_messages.json"
# 文案文件读取失败时的兜底提示语，保证点击蜗牛永远不会没有任何反应。
DEFAULT_SNAIL_MESSAGE = "今天也要好好记账哦！"
