"""Application-wide paths and constants."""

from pathlib import Path
import sys

# platformdirs 是第三方库，用于跨平台获取规范的「用户数据目录」。
# 万一运行环境缺少该依赖，也不能让程序直接崩溃，所以这里做降级处理。
try:
    from platformdirs import user_data_dir
except ImportError:
    print("警告：无法导入 platformdirs，将使用用户主目录作为数据目录。")

    def user_data_dir(appname: str, appauthor: str) -> str:  # 参数名与第三方函数一致，避免同名绑定的关键字签名冲突。
        # 降级方案：把数据写到用户主目录下的 AccountKeeper 文件夹，
        # 保证即使缺少依赖，记账数据也能落盘而不是丢失。
        return str(Path.home() / "AccountKeeper")


# CSV 表头字段顺序，必须与数据库查询列的顺序保持一致，否则导出后列会错位。
# 旧 CSV 迁移逻辑已在 #40 中删除，该常量现仅被 store.export_month_csv 用于写表头。
# 第 4 项由 category 改名 tags（#58 Step 2c-2）：导出的是多标签字段，
# 多个标签用竖线连接（§3.14.4）、0 个标签写空串，具体拼接由 store 负责。
CSV_FIELDS = ("id", "date", "amount", "tags", "note")

# 预置标签合并为单一候选池：先保留支出原序，再追加收入中尚未出现的项。
# “其他”在两组里重复，只保留首次出现的位置；元组不可变，避免调用方原地污染配置。
DEFAULT_TAGS = (
    "餐饮", "交通", "购物", "居住", "水电", "通讯", "医疗", "娱乐", "人情", "其他",
    "工资", "奖金", "投资", "兼职", "红包", "报销", "退款",
)
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

# 旧版类别偏好文件路径：仅供 tag_prefs.py 迁移时只读，不再作为当前偏好的写入目标。
# 保留独立路径是为了把升级用户已有的两段列表并入 tags.json；迁移不会改写或删除旧文件。
CATEGORY_PREFS_PATH = DATA_DIR / "categories.json"

# 用户常用标签文件：新格式的唯一读写目标，不再按支出 / 收入分段。
# 与 account.db 同目录，同样便于用户整体备份/迁移。
TAG_PREFS_PATH = DATA_DIR / "tags.json"

# 映射独立保存，避免 settings.save_settings 整份覆盖时洗掉用户显式保存的映射。
IMPORT_MAPPINGS_PATH = DATA_DIR / "import_mappings.json"

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
