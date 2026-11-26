"""账本持久化与内存缓存：只管理 SQLite，不依赖 UI 或偏好文件。

初始化顺序为建基础表、迁移标签到版本 2、迁移导入批次到版本 3、load 完整缓存。
金额按带符号 Decimal 接收、以两位小数 TEXT 写入；数据层不再根据收支按钮赋符号。
record_tags 是记录标签来源，accounts.category 仅供历史迁移；常用候选由 tag_prefs 管理。
单笔 add/delete/update 提交后只在有序缓存里增删改并返回变更记录；批次导入/撤销只提交，
由调用方另行 load 并刷新 UI。缓存读取顺序按记录 ID，界面日期排序、标签摘要及筛选由表现层处理。
"""

import bisect
import csv
import re
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from functools import wraps
from pathlib import Path
import shutil
import sqlite3
from time import monotonic
from typing import Callable, Collection, Iterable, Iterator
from uuid import uuid4

from budget_logic import BudgetLimit, budget_month_start, normalize_budget_tag
from config import CSV_FIELDS, DB_PATH


def _csv_safe_cell(value: object) -> object:
    text = str(value)
    # 仅完整的十进制负数（含科学计数）可免转义，-1+2等表达式仍须按公式处理。
    negative_number = re.fullmatch(r"-(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", text)
    if text.startswith(("=", "+", "-", "@")) and not negative_number:
        return "'" + text
    return value

# 历史标签迁移固定到 2；不能把目标改成 3，否则已迁过的库会再次补 legacy 标签。
_MULTI_TAG_USER_VERSION = 2
_IMPORT_BATCH_USER_VERSION = 3
_BUDGET_USER_VERSION = 4
_SCHEMA_USER_VERSION = 5


def _business_write(method):
    @wraps(method)
    def guarded(self, *args, **kwargs):
        if not self._business_ready:  # 更新初始化只准迁移；提交前禁止普通账目写入。
            raise RuntimeError("更新尚未提交，禁止业务写入")
        return method(self, *args, **kwargs)
    return guarded


@dataclass
class Account:
    """完整收支记录；tags 不可原地增删，但 Account 自身并非冻结对象。"""

    # 金额使用 Decimal 而不是 float，避免 0.1 + 0.2 这类浮点误差导致账目对不上。
    record_id: int
    record_date: str
    amount: Decimal
    # 标签用 tuple 而不是 list（§3.14.2）：records 是全局内存缓存、UI 多处直接读同一批
    # Account 对象，list 允许某处 append() 静默改掉内存里这条记录而数据库没变；
    # tuple 使这种误改直接抛 AttributeError，第一时间暴露不一致。
    # tags 元组可以作值比较或集合键，但不代表 Account 自身可哈希或已冻结。
    # 顺序有语义（就是展示顺序），tuple 有序正好承载；具体顺序见 _group_tags 的说明。
    # 此处始终保存完整标签，不挂 category 展示属性；表格可做摘要，CSV 另按竖线连接。
    tags: tuple[str, ...]
    note: str
    budget_excluded: bool = False


@dataclass(frozen=True)
class ImportMerchant:
    key: str
    name: str


@dataclass(frozen=True)
class MerchantMapping:
    merchant: ImportMerchant
    direction: str
    tag: str
    revision: str


@dataclass(frozen=True)
class MerchantMappingChange:
    merchant: ImportMerchant
    direction: str
    tag: str
    expected_revision: str | None = None


class MerchantMappingConflict(ValueError):
    """商户映射在预览之后发生变化，需要重新核对。"""


@dataclass(frozen=True)
class EditMerchantContext:
    merchant: ImportMerchant
    mappings: tuple[MerchantMapping, ...]

    def for_direction(self, direction: str) -> MerchantMapping | None:
        return next((item for item in self.mappings if item.direction == direction), None)


@dataclass(frozen=True)
class ImportRecord:
    """预览确认后的账本字段和标签，可携带独立的商户信息。"""

    record_date: str
    amount: Decimal
    note: str
    tags: tuple[str, ...] = ()
    merchant: ImportMerchant | None = None


@dataclass(frozen=True)
class ImportBatch:
    """原始导入数量快照，不随单笔编辑或删除改写。"""

    batch_id: int
    imported_at: str
    file_name: str
    imported_count: int
    skipped_count: int
    non_transaction_count: int


class AccountStore:
    """管理一个账本及其完整缓存；调用方不可把修改缓存当成已经写库。"""

    def __init__(self, path: Path = DB_PATH, *, strict_migrations: bool = False) -> None:
        # 数据库位置固定为 config.DB_PATH，不再接受配置覆盖；
        # 保留 path 参数仅是给测试留接口，便于注入临时库而不污染真实账本。
        self.path = path
        self.strict_migrations = strict_migrations
        self._business_ready = not strict_migrations
        # 内存缓存：UI 的筛选与汇总都读它，避免用户每敲一个字就查一次数据库。
        self.records: list[Account] = []
        self._unparsed_record_ids: set[int] = set()
        # 先建表、再把数据读进内存，这个顺序不能颠倒。
        # 旧 CSV 迁移逻辑已在 #40 中整体删除（项目还没有真实用户从旧版本升级，迁移属于纯负债）。
        self._initialize_database()
        # 建表之后才迁移：迁移语句要往 record_tags 里写，表必须先存在。
        # 放在 load() 之前，保证第一次读缓存时历史标签已按关联表口径迁移。
        # 「迁移前要不要先备份」不再靠「库文件本来就存在」这个布尔值判断——那个判据必须在
        # 建表之前问，而 _connect() 本身就会把新库凭空创建出来，时机太脆；改由迁移内部按
        # 「是否真有记录缺标签」决定（Step 2c-1）：全新库 / 无待补数据的库根本不进备份分支。
        self._migrate_to_multi_tag()
        # 前一步失败时不跨级；基础账本仍可启动，批次接口通过就绪标记拒绝使用。
        self.import_batches_available = self._migrate_to_import_batches()
        self.budgets_available = self.import_batches_available and self._migrate_to_budgets()
        self.merchants_available = self.budgets_available and self._migrate_to_merchants()
        if strict_migrations and not self.merchants_available:
            raise sqlite3.DatabaseError("更新初始化要求全部迁移成功")
        self.load()

    def enable_business_writes(self) -> None:
        self._business_ready = True  # 仅由入口验证持久提交后放行，不以主窗出现代替。

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """每次新建连接，成功提交写事务、异常回滚，最后关闭；不提供嵌套共享事务。"""
        # 目录可能被用户删掉或首次运行不存在，写库前先补建，防止 sqlite3.connect 直接报错。
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        try:
            # 外键开关按连接生效；标签表没有外键，仍由 CRUD 显式清理，不改变旧口径。
            connection.execute("PRAGMA foreign_keys = ON")
            # SQLite 自带上下文同时覆盖事务体和提交失败，避免 commit 异常漏过 rollback。
            with connection:
                yield connection
        finally:
            # 无论成功或失败都必须关闭连接，否则会累积文件句柄。
            connection.close()

    def snapshot_database(self, destination: Path) -> Path:
        """生成经过完整性检查的独立数据库快照，不修改源库。"""
        source_path = self.path.resolve()
        destination = Path(destination).resolve()
        if destination == source_path:
            raise ValueError("快照目标不能是源数据库。")
        created = False
        completed = False
        try:
            # mode=ro 拒绝源库缺失，不能沿用会创建空库的 _connect。
            # timeout=0 让锁等待交给 backup 的进度回调，避免先被默认五秒拖住。
            with closing(sqlite3.connect(
                source_path.as_uri() + "?mode=ro", uri=True, timeout=0
            )) as source:
                # 独占创建只允许新目标，也能拒绝现有文件及指向源库的硬链接。
                with destination.open("xb"):
                    pass
                created = True
                with closing(sqlite3.connect(destination)) as target:
                    deadline = monotonic() + 3.0

                    def check_deadline(_status: int, _remaining: int, _total: int) -> None:
                        # 单调时钟不受系统校时影响；忙重试也会回调，不能无限卡住界面。
                        if monotonic() >= deadline:
                            raise TimeoutError("数据库正忙，请稍后重试")

                    source.backup(
                        target, pages=256, progress=check_deadline, sleep=0.05
                    )
                    # backup 已完成目标事务；检查副本而非源库，避免对真实账本做测试写入。
                    if target.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                        raise sqlite3.DatabaseError("数据库快照完整性检查失败。")
                completed = True
            return destination
        finally:
            if created and not completed:
                # 两个 closing 都已退出后才删失败产物，Windows 不能删除仍被连接占用的库。
                for suffix in ("", "-journal", "-wal", "-shm"):
                    Path(str(destination) + suffix).unlink(missing_ok=True)

    def _initialize_database(self) -> None:
        # IF NOT EXISTS 只补基础表，不提升版本；数据迁移及批次列由后续专门入口负责。
        with self._connect() as connection:
            if self.strict_migrations:
                connection.execute("BEGIN IMMEDIATE")  # 严格启动的基础DDL也必须成组回滚。
            # amount 故意用 TEXT 存储，是为了原样保留 Decimal 的精度，读出时再转回 Decimal。
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    record_date TEXT NOT NULL,
                    amount TEXT NOT NULL,
                    category TEXT NOT NULL,
                    note TEXT NOT NULL
                )
                """
            )
            # 多标签改造（#58 Step 1）的关联表：一条记录 × 一个标签 = 一行。
            # 不给标签单建字典表：候选列表的唯一真源是 tags.json，数据库再存一份标签名
            # 就变成「两处真源」（否决理由见 requirements §3.14.2 的方案 A）。
            # (record_id, tag) 复合主键天然去重，同一条记录打两次同名标签只留一行，
            # 迁移 SQL 也才能靠 INSERT OR IGNORE 做到幂等。
            # 标签表继续不写 FOREIGN KEY，删除由 delete/undo_import_batch 显式清理；
            # 新批次外键启用不等于为旧标签表增设级联（§3.14 保留原口径）。
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS record_tags (
                    record_id INTEGER NOT NULL,
                    tag       TEXT    NOT NULL,
                    PRIMARY KEY (record_id, tag)
                )
                """
            )
            # 没有这个索引，按标签聚合 / 筛选就只能全表扫描。
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_record_tags_tag ON record_tags(tag)"
            )

    def _backup_database(self) -> Path | None:
        """迁移前把库原样复制一份到同目录的 account.db.bak，失败返回 None。

        此旧迁移路径在自己的读取连接关闭后复制主库文件，不包含 WAL 等伴随文件；
        它不是在线一致性快照，不应复用为常规备份，常规备份由 snapshot_database 提供。
        备份路径基于 self.path、不是 config.DB_PATH：测试注入临时库时备份也该落在
        临时目录里，绝不能写到真实数据目录去。每次覆盖同一份、不带时间戳，
        这是迁移副本的固定命名约定，不影响常规 ZIP 备份的命名与保留规则。
        调用门槛由 _migrate_to_multi_tag 把着：**只有「确有记录缺标签」时才轮到本方法**，
        所以全新库与无待补数据的库都不会在用户目录里留下一个空的 .bak。
        """
        # 不能用 with_suffix(".bak")：那会把 .db 替换掉、得到 account.bak，
        # 名字既与 requirements §3.14.2 对不上，.gitignore 的规则也匹配不到它。
        backup_path = self.path.parent / (self.path.name + ".bak")
        try:
            shutil.copy2(self.path, backup_path)
        except OSError as error:
            if self.strict_migrations:
                raise  # 严格更新不能带着迁移备份失败继续初始化。
            # 备份失败就中止迁移：宁可这次不迁，也不能在没有退路的情况下改用户的库。
            # 只打控制台警告、不抛异常——启动阶段抛出去会让整个程序起不来。
            print(f"警告：迁移前备份数据库失败（{backup_path}），本次跳过迁移：{error}")
            return None
        return backup_path

    def _migrate_to_multi_tag(self) -> None:
        """把历史 category 值搬进 record_tags（#58 Step 1 建表 + Step 2c-1 补漏）。

        本函数一次承担 0 → 1 与 1 → 2 两段（都由 user_version 保证只跑一次）：
        - 0 → 1（Step 1）：全新安装，或从未迁移过的老库，把 accounts.category 搬进 record_tags；
        - 1 → 2（Step 2c-1）：Step 1 与 Step 2b 之间落库的记录、以及迁移被跳过的库，
          其 category 列还有值却一行关联都没有，这里把它们补上。
        两段要做的事逐字相同（都只是「给零关联行的记录补一行标签」），所以不另立函数、
        共用同一段 SQL；本段目标固定为 2，后续批次迁移必须另立函数，不能提高本段门槛。

        补漏判定**必须带 NOT EXISTS**（只补「一行关联都没有」的记录）：若不加，给「已有标签、
        但旧 category 仍有值且与之不同」的记录再补一行，load() 就会凭空多出一个标签。
        这条判据也是 Step 2c-2 敢删掉 _merge_tags 那条 B1 回退的前提：本函数把「零关联行的
        老记录」补齐之后，`record_tags 里有行才算有标签` 就成了唯一口径，不再需要回退兜底。

        整体包一层 try：本函数是在 __init__ 里被调用的，而 AccountStore() 又是在
        account_keeper.main() 的 try 里构造的，一旦异常逃逸出去，用户看到的就是
        「启动失败」弹窗、程序根本起不来。迁移失败属于可降级问题（下次启动会重试），
        所以按「备份失败也中止迁移」的同款处置：打警告、保持原版本号、
        让程序照常启动。迁移只读 accounts.category，不写 accounts 的任何列，
        所以整个迁移是可回滚的（§3.14.2 的「老数据不丢三重保证」）。
        """
        try:
            # 第一段（只读，连接用完即关）：先读版本判断要不要跑，再数一遍待补条数。
            # 读完即关闭本连接再复制，避免本轮连接跨过备份阶段；这并不锁住外部写入者。
            with self._connect() as connection:
                version_row = connection.execute("PRAGMA user_version").fetchone()
                # PRAGMA 一定返回一行，这里仍做兜底，避免脏库返回空结果时下标报错。
                version = version_row[0] if version_row else 0
                if version >= _MULTI_TAG_USER_VERSION:
                    # 已经迁过：直接放行。固定门槛 2，后续 schema 升版不会重新补旧标签。
                    return
                # 待补条数 =「TRIM 后非空」且「关联表里一行都没有」的记录数。
                # 这里的 WHERE 与下面 INSERT 的 WHERE 逐字一致，所以数出 0 就真的无事可做。
                pending_row = connection.execute(
                    "SELECT COUNT(*) FROM accounts "
                    "WHERE TRIM(category) <> ''"
                    "AND NOT EXISTS (SELECT 1 FROM record_tags "
                    "WHERE record_id = accounts.id)"
                ).fetchone()
                pending = pending_row[0] if pending_row else 0
            # 第二段：**只有真有待补记录才备份**（Step 2c-1 的新门槛，取代原先的「库文件是否
            # 早已存在」判据）。待补为 0 时迁移一条数据都不改、只动 user_version，没有退路可言，
            # 就不该在用户目录里留下一个空的 .bak；版本号照样抬到位——空库本来就是新的地基形态，
            # 留成旧版本会让迁移延后到用户已经录了数据的第二次启动才触发，时机变得不可预期。
            if pending > 0:
                backup_path = self._backup_database()
                if backup_path is None:
                    if self.strict_migrations:
                        raise sqlite3.DatabaseError("标签迁移备份未成功")
                    # 备份失败（警告已在上一步打出）：保持原版本号让下次启动重试，
                    # 绝不带着「没有退路」的状态去写用户的库。
                    return
                # 只在这条路径上提示用户：无待补数据时迁移完全是透明的，不必打扰。
                print(
                    f"数据库迁移：{pending} 条历史记录待补标签，原库已备份至 {backup_path}"
                )
            # 第三段（写，与升版本同一事务）：补标签 + 把版本号抬到当前值。
            with self._connect() as connection:
                # TRIM 掉首尾空白，空串 / 纯空白不产生标签，正好对应「允许 0 标签」。
                # NULL 由 WHERE 天然排除：TRIM(NULL) 仍是 NULL，而 `NULL <> ''` 结果为 NULL、不是真。
                # INSERT OR IGNORE + 复合主键 = 重复执行结果完全一致（幂等），中途断电后重跑
                # 也不会产生重复行；NOT EXISTS 则保证已有标签的记录不会被旧 category 再补一行。
                connection.execute(
                    "INSERT OR IGNORE INTO record_tags(record_id, tag) "
                    "SELECT id, TRIM(category) FROM accounts "
                    "WHERE TRIM(category) <> ''"
                    "AND NOT EXISTS (SELECT 1 FROM record_tags "
                    "WHERE record_id = accounts.id)"
                )
                # 升版本与上面的插入在同一个事务里提交，不可能出现
                # 「标签已写入、版本号却没升」的半迁移状态。
                # PRAGMA 的位置参数不走占位符绑定，只能拼进 SQL；值来自模块常量、
                # 不是外部输入，没有注入面。
                connection.execute(f"PRAGMA user_version = {_MULTI_TAG_USER_VERSION}")
        except sqlite3.Error as error:
            # 覆盖「读版本 / 数待补条数」与「备份后写库」两条路径上的数据库错误。
            if self.strict_migrations:
                raise  # helper必须收到真实失败，不能被普通启动的降级策略掩盖。
            print(f"警告：标签迁移失败，本次跳过（下次启动重试）：{error}")

    def _migrate_to_import_batches(self) -> bool:
        """独立的 2→3 累计迁移；仅加表加列，不新增自动备份。"""
        try:
            with self._connect() as connection:
                # 默认 sqlite3 不会为 DDL 自动开事务，必须先锁定并开启，才能整段回滚。
                connection.execute("BEGIN IMMEDIATE")
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                if not _MULTI_TAG_USER_VERSION <= version <= _SCHEMA_USER_VERSION:
                    return False  # 旧迁移未完成或未来结构，不猜测、不跨级、不降低版本。
                if version == _MULTI_TAG_USER_VERSION:
                    connection.execute(
                        "CREATE TABLE IF NOT EXISTS import_batches ("
                        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                        "imported_at TEXT NOT NULL, file_name TEXT NOT NULL, "
                        "note TEXT NOT NULL DEFAULT '', "
                        "imported_count INTEGER NOT NULL CHECK(imported_count > 0), "
                        "skipped_count INTEGER NOT NULL CHECK(skipped_count >= 0), "
                        "non_transaction_count INTEGER NOT NULL "
                        "CHECK(non_transaction_count >= 0))"
                    )
                    columns = {
                        row[1] for row in connection.execute("PRAGMA table_info(accounts)")
                    }
                    if "import_batch_id" not in columns:
                        # 可空且默认 NULL，让历史行、手动添加及旧版显式列名 INSERT 保持合法。
                        connection.execute(
                            "ALTER TABLE accounts ADD COLUMN import_batch_id INTEGER "
                            "REFERENCES import_batches(id)"
                        )
                    connection.execute(
                        "CREATE INDEX IF NOT EXISTS idx_accounts_import_batch_id "
                        "ON accounts(import_batch_id)"
                    )
                self._validate_import_schema(connection)
                if version == _MULTI_TAG_USER_VERSION:
                    # 批次迁移只升到 3，预算迁移有独立的提交和回滚点。
                    connection.execute(f"PRAGMA user_version = {_IMPORT_BATCH_USER_VERSION}")
            return True
        except sqlite3.Error as error:
            if self.strict_migrations:
                raise  # 扩展表DDL和结构校验失败同样禁止确认健康。
            print(f"警告：导入批次迁移未就绪，下次启动重试：{error}")
            return False

    @staticmethod
    def _validate_import_schema(connection: sqlite3.Connection) -> None:
        """在迁移事务内检查必需列及批次引用；版本号正确也不能代表结构一定完整。"""
        batch_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(import_batches)")
        }
        required = {
            "id", "imported_at", "file_name", "note", "imported_count",
            "skipped_count", "non_transaction_count",
        }
        account_columns = {
            row[1]: row for row in connection.execute("PRAGMA table_info(accounts)")
        }
        column = account_columns.get("import_batch_id")
        foreign_keys = connection.execute("PRAGMA foreign_key_list(accounts)").fetchall()
        # 对残缺或人为改过的结构不盲目标记迁移成功，也不重建表覆盖用户现场。
        valid_reference = any(
            row[2:5] == ("import_batches", "import_batch_id", "id")
            for row in foreign_keys
        )
        # table_info 第 4 项是 NOT NULL 标记，必须允许为空以兼容手动/历史记录；仅有同名列不够。
        if (
            not required <= batch_columns or column is None
            or column[3] or not valid_reference
        ):
            raise sqlite3.DatabaseError("导入批次 schema 不完整，不能启用导入。")

    def _require_import_batches(self) -> None:
        # 缓存可读不代表批次表已就绪，所有批次入口都先过这个门槛，防止降级后继续写入。
        if not self.import_batches_available:
            raise sqlite3.DatabaseError("导入批次数据库尚未就绪。")

    def _migrate_to_budgets(self) -> bool:
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")  # 表、字段和版本一起提交，失败保住原库。
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                if not _IMPORT_BATCH_USER_VERSION <= version <= _SCHEMA_USER_VERSION:
                    return False
                columns = {row[1]: row for row in connection.execute("PRAGMA table_info(accounts)")}
                if version == _IMPORT_BATCH_USER_VERSION:
                    # 空标签键专指总预算，NOT NULL 让复合主键也能拦住重复的月总额。
                    connection.execute(
                        "CREATE TABLE IF NOT EXISTS monthly_budgets ("
                        "month TEXT NOT NULL, tag TEXT NOT NULL, amount TEXT NOT NULL, "
                        "PRIMARY KEY(month, tag)) WITHOUT ROWID"
                    )
                    if "budget_excluded" not in columns:
                        connection.execute(
                            "ALTER TABLE accounts ADD COLUMN budget_excluded INTEGER "
                            "NOT NULL DEFAULT 0 CHECK(budget_excluded IN (0, 1))"
                        )  # 旧账和导入的账默认计入，无需逐笔改写旧数据。
                        columns = {row[1]: row for row in connection.execute("PRAGMA table_info(accounts)")}
                flag = columns.get("budget_excluded")
                limits = {row[1]: (row[2], row[3], row[5])
                          for row in connection.execute("PRAGMA table_info(monthly_budgets)")}
                if (flag is None or flag[2:5] != ("INTEGER", 1, "0") or limits != {
                    "month": ("TEXT", 1, 1), "tag": ("TEXT", 1, 2), "amount": ("TEXT", 1, 0),
                }):
                    raise sqlite3.DatabaseError("预算 schema 不完整，不能启用预算。")
                if version == _IMPORT_BATCH_USER_VERSION:
                    connection.execute(f"PRAGMA user_version = {_BUDGET_USER_VERSION}")
            return True
        except sqlite3.Error as error:
            # 普通启动仍可查看基础账本，严格更新必须回退，不能把部分迁移标记为健康。
            if self.strict_migrations:
                raise
            print(f"警告：预算迁移未就绪，下次启动重试：{error}")
            return False

    def _require_budgets(self) -> None:
        if not self.budgets_available:
            raise sqlite3.DatabaseError("预算数据库尚未就绪。")

    def _migrate_to_merchants(self) -> bool:
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                if version not in (_BUDGET_USER_VERSION, _SCHEMA_USER_VERSION):
                    return False
                if version == _BUDGET_USER_VERSION:
                    # 已有账目及其标签无需回填；级联删除同时覆盖单笔删除和撤销批次。
                    connection.execute(
                        "CREATE TABLE IF NOT EXISTS import_merchants ("
                        "record_id INTEGER PRIMARY KEY NOT NULL REFERENCES accounts(id) ON DELETE CASCADE, "
                        "merchant_key TEXT NOT NULL, merchant_name TEXT NOT NULL)"
                    )
                    connection.execute(
                        "CREATE TABLE IF NOT EXISTS merchant_mappings ("
                        "merchant_key TEXT NOT NULL, direction TEXT NOT NULL CHECK(direction IN ('支出', '收入')), "
                        "merchant_name TEXT NOT NULL, tag TEXT NOT NULL, revision TEXT NOT NULL, "
                        "PRIMARY KEY(merchant_key, direction)) WITHOUT ROWID"
                    )
                # 即使版本号已经存在，也必须确认两张表的主键和级联关系可用。
                sources = {row[1]: (row[2], row[3], row[5])
                           for row in connection.execute("PRAGMA table_info(import_merchants)")}
                mappings = {row[1]: (row[2], row[3], row[5])
                            for row in connection.execute("PRAGMA table_info(merchant_mappings)")}
                foreign = [(row[2], row[3], row[4], row[6])
                           for row in connection.execute("PRAGMA foreign_key_list(import_merchants)")]
                if (sources != {"record_id": ("INTEGER", 1, 1), "merchant_key": ("TEXT", 1, 0),
                                "merchant_name": ("TEXT", 1, 0)}
                        or mappings != {"merchant_key": ("TEXT", 1, 1), "direction": ("TEXT", 1, 2),
                                        "merchant_name": ("TEXT", 1, 0), "tag": ("TEXT", 1, 0),
                                        "revision": ("TEXT", 1, 0)}
                        or foreign != [("accounts", "record_id", "id", "CASCADE")]):
                    raise sqlite3.DatabaseError("商户 schema 不完整，不能启用归类。")
                if version == _BUDGET_USER_VERSION:
                    connection.execute(f"PRAGMA user_version = {_SCHEMA_USER_VERSION}")
            return True
        except sqlite3.Error as error:
            if self.strict_migrations:
                raise
            print(f"警告：商户迁移未就绪，下次启动重试：{error}")
            return False

    def _require_merchants(self) -> None:
        if not self.merchants_available:
            raise sqlite3.DatabaseError("商户数据库尚未就绪。")

    def list_merchant_mappings(self) -> tuple[MerchantMapping, ...]:
        self._require_merchants()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT merchant_key, merchant_name, direction, tag, revision "
                "FROM merchant_mappings ORDER BY merchant_name, direction, merchant_key"
            ).fetchall()
        return tuple(MerchantMapping(ImportMerchant(key, name), direction, tag, revision)
                     for key, name, direction, tag, revision in rows)

    def edit_merchant_context(self, record_id: int) -> EditMerchantContext | None:
        if not self.merchants_available:
            return None
        with self._connect() as connection:
            # 一个查询读取商户和两个方向的修订号，避免上下文来自不同快照。
            rows = connection.execute(
                "SELECT i.merchant_key, i.merchant_name, m.direction, m.tag, m.revision, m.merchant_name "
                "FROM import_merchants AS i LEFT JOIN merchant_mappings AS m ON m.merchant_key = i.merchant_key "
                "WHERE i.record_id = ?", (record_id,),
            ).fetchall()
        if not rows:
            return None
        merchant = ImportMerchant(*rows[0][:2])
        return EditMerchantContext(merchant, tuple(
            MerchantMapping(ImportMerchant(key, mapped_name), direction, tag, revision)
            for key, _name, direction, tag, revision, mapped_name in rows if direction is not None
        ))

    @_business_write
    def delete_merchant_mapping(self, mapping: MerchantMapping) -> None:
        self._require_merchants()
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM merchant_mappings WHERE merchant_key = ? AND direction = ? AND revision = ?",
                (mapping.merchant.key, mapping.direction, mapping.revision),
            )
            if cursor.rowcount != 1:
                raise MerchantMappingConflict(f"「{mapping.merchant.name}」的映射已变化，请刷新后重试。")

    @staticmethod
    def _check_mapping_revisions(connection: sqlite3.Connection, expectations, changes) -> None:
        # 必须在同一写事务持锁后比对，避免另一个实例在检查后覆盖或重建映射。
        checks = [(item.merchant, item.direction, item.revision) for item in expectations]
        checks.extend((item.merchant, item.direction, item.expected_revision) for item in changes)
        for merchant, direction, expected in checks:
            row = connection.execute(
                "SELECT revision FROM merchant_mappings WHERE merchant_key = ? AND direction = ?",
                (merchant.key, direction),
            ).fetchone()
            if (row[0] if row else None) != expected:
                raise MerchantMappingConflict(f"「{merchant.name}」的{direction}映射已变化，请重新核对。")

    @staticmethod
    def _write_merchant_mappings(connection: sqlite3.Connection, changes) -> None:
        # 新修订号能区分删除后又重建为同名同标签的映射。
        connection.executemany(
            "INSERT INTO merchant_mappings (merchant_key, direction, merchant_name, tag, revision) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(merchant_key, direction) DO UPDATE SET "
            "merchant_name = excluded.merchant_name, tag = excluded.tag, revision = excluded.revision",
            [(item.merchant.key, item.direction, item.merchant.name, item.tag, uuid4().hex)
             for item in changes],
        )

    def list_budgets(self, month: str) -> tuple[BudgetLimit, ...]:
        self._require_budgets()
        budget_month_start(month)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT tag, amount FROM monthly_budgets WHERE month = ? ORDER BY tag", (month,),
            ).fetchall()
        try:
            return tuple(BudgetLimit(month, Decimal(amount), tag or None) for tag, amount in rows)
        except (InvalidOperation, ValueError, TypeError) as error:
            # 异常额度要显式报错，不能悄悄当成用户没有设置预算。
            raise sqlite3.DatabaseError("预算额度数据无效。") from error

    @_business_write
    def set_budget(self, month: str, amount: Decimal, *, tag: str | None = None) -> BudgetLimit:
        self._require_budgets()
        limit = BudgetLimit(month, amount, tag)
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO monthly_budgets (month, tag, amount) VALUES (?, ?, ?) "
                "ON CONFLICT(month, tag) DO UPDATE SET amount = excluded.amount",
                (month, limit.tag or "", format(limit.amount, "f")),
            )
        return limit

    @_business_write
    def delete_budget(self, month: str, *, tag: str | None = None) -> bool:
        self._require_budgets()
        budget_month_start(month)
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM monthly_budgets WHERE month = ? AND tag = ?",
                                        (month, normalize_budget_tag(tag) or ""))
            deleted = cursor.rowcount > 0
        return deleted

    @_business_write
    def copy_previous_budgets(self, month: str) -> int:
        self._require_budgets()
        first = budget_month_start(month)
        if first.year == 1 and first.month == 1:
            return 0  # 合法的最早月份没有上月。
        previous = (first - timedelta(days=1)).isoformat()[:7]
        with self._connect() as connection:
            # 单语句补齐缺项，让另一个实例刚写入的本月额度也不会被复制覆盖。
            cursor = connection.execute(
                "INSERT INTO monthly_budgets (month, tag, amount) "
                "SELECT ?, tag, amount FROM monthly_budgets WHERE month = ? "
                "ON CONFLICT(month, tag) DO NOTHING", (month, previous),
            )
            copied = cursor.rowcount
        return copied

    def list_import_batches(self) -> list[ImportBatch]:
        """现读批次原始数量快照，按批次 ID 倒序；不从当前记录数反推导入笔数。"""
        self._require_import_batches()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, imported_at, file_name, imported_count, skipped_count, "
                "non_transaction_count FROM import_batches ORDER BY id DESC"
            ).fetchall()
        return [ImportBatch(*row) for row in rows]

    @_business_write
    def import_batch(
        self, records: Iterable[ImportRecord], file_name: str,
        skipped_count: int = 0, non_transaction_count: int = 0,
        *, expected_mappings: Iterable[MerchantMapping] = (),
        merchant_changes: Iterable[MerchantMappingChange] = (),
    ) -> ImportBatch | None:
        """一次事务导入；调用者在提交成功后单独 load/刷新，避免把显示失败当回滚。"""
        self._require_import_batches()
        # bool 是 int 的子类，计数这里要求真实整数，不能把 True 当作一笔跳过记录。
        if any(type(count) is not int or count < 0 for count in (
            skipped_count, non_transaction_count,
        )):
            raise ValueError("跳过笔数及非交易行数必须为非负整数。")
        expectations, changes = tuple(expected_mappings), tuple(merchant_changes)
        values, linked_choices = [], set()  # 先验证完整批次，拒绝和实际入账无关的学习请求。
        for record in records:
            date = datetime.strptime(record.record_date, "%Y-%m-%d").date().isoformat()
            if not self._is_valid_amount(record.amount):
                raise ValueError("导入金额必须是有限非零数。")
            # 按输入小数位严格拦截，包括尾随零；不得靠格式化掩盖超过两位小数的异常。
            if int(record.amount.as_tuple().exponent) < -2:
                raise ValueError("超过两位小数的导入金额禁止入账。")
            tags, merchant = self._normalize_tags(record.tags), record.merchant
            if merchant is not None:
                if not merchant.key.strip() or not merchant.name.strip():
                    raise ValueError("商户键及显示名称不能为空。")
                direction = "收入" if record.amount > 0 else "支出"
                linked_choices.update((merchant.key, direction, tag) for tag in tags)
            values.append((date, f"{record.amount:.2f}", record.note.strip(), tags, merchant))
        keys = {(change.merchant.key, change.direction) for change in changes}
        if len(keys) != len(changes) or any(
            (change.merchant.key, change.direction, change.tag) not in linked_choices for change in changes
        ):
            raise ValueError("学习请求必须唯一且对应本批已选标签。")
        has_links = any(tags or merchant for _, _, _, tags, merchant in values)
        if has_links or changes or expectations:
            self._require_merchants()
        if not values:
            return None  # 零笔不创建批次，也不产生空事务。
        name = Path(file_name).name  # 批次仅留文件名，不持久化用户机器上的完整来源路径。
        if not name:
            raise ValueError("导入文件名不能为空。")
        imported_at = datetime.now().isoformat(timespec="seconds")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._check_mapping_revisions(connection, expectations, changes)
            cursor = connection.execute(
                "INSERT INTO import_batches (imported_at, file_name, imported_count, "
                "skipped_count, non_transaction_count) VALUES (?, ?, ?, ?, ?)",
                (imported_at, name, len(values), skipped_count, non_transaction_count),
            )
            batch_id = cursor.lastrowid
            if batch_id is None:
                raise sqlite3.DatabaseError("未能取得导入批次 id。")
            insert_record = (
                "INSERT INTO accounts "
                "(record_date, amount, category, note, import_batch_id) "
                "VALUES (?, ?, '', ?, ?)"
            )
            if not has_links:
                # 保留无关联批次的批量写入路径。
                connection.executemany(insert_record, [(day, amount, note, batch_id)
                                       for day, amount, note, _, _ in values])
            else:
                tag_rows, merchant_rows = [], []
                for day, amount, note, tags, merchant in values:
                    record_id = connection.execute(insert_record, (day, amount, note, batch_id)).lastrowid
                    if record_id is None:
                        raise sqlite3.DatabaseError("未能取得导入记录 id。")
                    tag_rows.extend((record_id, tag) for tag in tags)
                    if merchant is not None:
                        merchant_rows.append((record_id, merchant.key, merchant.name))
                connection.executemany("INSERT INTO record_tags (record_id, tag) VALUES (?, ?)", tag_rows)
                connection.executemany(
                    "INSERT INTO import_merchants (record_id, merchant_key, merchant_name) VALUES (?, ?, ?)",
                    merchant_rows,
                )
            if changes:
                self._write_merchant_mappings(connection, changes)
            result = ImportBatch(
                batch_id, imported_at, name, len(values),
                skipped_count, non_transaction_count,
            )
        return result  # 只有 commit 成功才返回；缓存及 UI 刷新不在写入事务里。

    @_business_write
    def undo_import_batch(self, batch_id: int) -> bool:
        """原子撤销一个批次；不调用逐笔 delete，不触及任何偏好文件。"""
        self._require_import_batches()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            exists = connection.execute(
                "SELECT 1 FROM import_batches WHERE id = ?", (batch_id,)
            ).fetchone()
            if exists is None:
                return False
            # 编辑不改 import_batch_id，故撤销含已编辑记录；单笔已删的记录无需再恢复或删除。
            # 导入后可能被编辑并加标签，必须先按当前批次记录清理全部关联，再删主记录。
            connection.execute(
                "DELETE FROM record_tags WHERE record_id IN "
                "(SELECT id FROM accounts WHERE import_batch_id = ?)", (batch_id,),
            )
            connection.execute(
                "DELETE FROM accounts WHERE import_batch_id = ?", (batch_id,),
            )
            connection.execute("DELETE FROM import_batches WHERE id = ?", (batch_id,))
        return True  # 业务层之后 load 一次；任何删除或 commit 失败均不会走到此处。

    @staticmethod
    def _group_tags(tag_rows: list[tuple[int, str]]) -> dict[int, tuple[str, ...]]:
        """把 record_tags 的查询结果按 record_id 归成 {记录 ID: 标签元组}。

        只用 tag_rows 一张表就够了（#58 Step 2c-2 起不再需要 accounts 那一侧）：迁移已把
        历史 category 补成关联行，add / update 从一开始就只往 record_tags 写标签，
        所以「某条记录有哪些标签」完全由这张表说了算。
        原先那条「关联表里一行都没有就回退去读 accounts.category」的 B1 分支正是本步删掉的
        东西——留着它反而有个副作用：用户把一条记录主动删成 0 标签后，旧 category 值仍在，
        下次 load() 会把那个早就不算数的标签又携回界面（add / update 把该列写空串就是为了防它）。

        为什么抽成独立方法：load() 与 export_month_csv() 都要这套规则，各写一遍就是
        「口径改动易漏改」（正是 issues #16 的主题），所以归并规则只留这一份。

        顺序就是 ORDER BY record_id, tag 给出的**字典序**（不是用户打字顺序）：DDL 没有顺序列，
        这是直接后果，取舍已备案在 _issues.txt #58。
        """
        grouped: dict[int, list[str]] = {}
        # tag_rows 已按 (record_id, tag) 排序，逐行 append 就保住了这个顺序。
        for record_id, tag in tag_rows:
            grouped.setdefault(record_id, []).append(tag)
        # 一次性冻结成 tuple：调用方拿到的是不可变值，不可能被原地修掉。
        return {record_id: tuple(tags) for record_id, tags in grouped.items()}

    @property
    def load_skipped_count(self) -> int:
        """当前缓存因金额无法解析而缺少的账目笔数。"""
        return len(self._unparsed_record_ids)

    def load(self) -> None:
        """重建完整缓存，不写回数据库；金额无法解析的行仅从本轮缓存排除。"""
        with self._connect() as connection:
            # 两条 SELECT 复用连接减少开关成本；这里未显式 BEGIN，不能承诺并发写入下的共同快照。
            # category 列已从 SELECT 里去掉（#58 Step 2c-2）：标签的真源只有 record_tags，
            # 旧列不再参与任何读取，少一列就少一次「到底以哪边为准」的歧义。
            # 迁移失败时仍用旧结构读账，预算接口由就绪标记单独关闭。
            flag_column = "budget_excluded" if self.budgets_available else "0"
            account_rows = connection.execute(
                f"SELECT id, record_date, amount, note, {flag_column} FROM accounts ORDER BY id"
            ).fetchall()
            # ORDER BY record_id, tag：一条记录的多个标签顺序由 SQL 定，落到 tags 里
            # 就是**字典序**（不是用户打字顺序）。DDL 没有顺序列，这是直接后果，
            # 取舍已备案在 _issues.txt #58；顺带让 idx_record_tags_tag 派上用场。
            tag_rows = connection.execute(
                "SELECT record_id, tag FROM record_tags ORDER BY record_id, tag"
            ).fetchall()
        # 归并规则收在 _group_tags 里，与 export_month_csv 共用同一份。
        tags_by_id = self._group_tags(tag_rows)
        # 数据库里金额是字符串，这里重新构造 Decimal，保证后续汇总计算不损失精度。
        # 但要逐行捕获异常：历史库或手工改过的库里可能出现 "abc" 这类无法解析的金额，
        # Decimal() 会抛出 InvalidOperation，而 load() 是在 __init__ 里调用的，
        # 一旦抛出就会让整个程序在启动阶段闪退（用户只看到「双击没反应」）。
        records: list[Account] = []
        unparsed_record_ids: set[int] = set()
        for record_id, record_date, amount, note, budget_excluded in account_rows:
            try:
                parsed_amount = Decimal(amount)
            except (InvalidOperation, TypeError, ValueError) as error:
                # 只跳过这一行：数据库里的原始记录保持原样，绝不顺手改写或删除用户数据。
                print(
                    f"警告：记录 ID {record_id} 的金额无法解析（{amount!r}），已跳过该行：{error}"
                )
                unparsed_record_ids.add(record_id)  # 保留主键供统计提示，后续修正或删除时可移除。
                continue
            # 能解析的 NaN/Infinity 仍会进入缓存；此方法不做有限性筛除，写入及聚合另有护栏。
            records.append(
                Account(
                    record_id,
                    record_date,
                    parsed_amount,
                    # 关联表里没有行的记录在这里是 ()，即 Q2 允许的「0 个标签」。
                    tags_by_id.get(record_id, ()),
                    note,
                    bool(budget_excluded),
                )
            )
        # 缓存与异常主键按同一轮加载结果重建，重复加载不会累加异常。
        self.records, self._unparsed_record_ids = records, unparsed_record_ids

    def get_tags(self) -> list[str]:
        """返回库里所有用过的标签（去重、按字典序）。

        数据源是 record_tags，不读 legacy 的 accounts.category，也不按金额方向拆组。
        排序口径固定为 tag 的字典序（顺着 idx_record_tags_tag，结果稳定可复现）。

        刻意不做并集回退（Step 2a 决策 3）：不把 accounts.category 的非空值并进来。
        UI 启动时把本方法结果作为新标签偏好初始化的历史输入；偏好模块只接收该结果，
        不直接依赖数据层，保证 store 与 tag_prefs 的职责边界清楚。
        """
        with self._connect() as connection:
            # DISTINCT + ORDER BY 都由 SQLite 做，既能命中 idx_record_tags_tag，
            # 也避免把全表标签捞回 Python 再去重排序。
            rows = connection.execute(
                "SELECT DISTINCT tag FROM record_tags ORDER BY tag"
            ).fetchall()
        return [row[0] for row in rows]

    def next_id(self) -> int:
        """按当前缓存估算下一编号，不预留主键，也不读取 SQLite 的自增序列。"""
        # 真正插入以 lastrowid 为准；缓存可能跳过脏行或尚未刷新，不能把估算值当数据库 ID。
        return max((record.record_id for record in self.records), default=0) + 1

    def _cache_index(self, record_id: int) -> int:
        """在按主键升序的缓存里二分定位，返回插入点（该位置的主键可能不等于目标）。

        load() 用 ORDER BY id 填充，add 只往尾部追加（自增主键必为最大值），
        update/delete 不改变主键，因此 records 始终按 record_id 升序，二分结论才成立。
        """
        return bisect.bisect_left(self.records, record_id, key=lambda item: item.record_id)

    def _cache_record(
        self, record_id: int, record_date: str, amount: Decimal, tags: tuple[str, ...], note: str,
        budget_excluded: bool,
    ) -> Account:
        """按 load() 的同一口径构造缓存对象，保证局部维护与整表重读结果逐字段一致。

        金额取两位小数字符串再解析，标签按字典序（对应 load() 的 ORDER BY tag），
        备注去掉首尾空白（对应写库时的 note.strip()），三者任一不一致都会让界面
        在下次全量刷新时「悄悄跳一下」，所以口径必须收在这一处。
        """
        return Account(
            record_id,
            record_date,
            Decimal(f"{amount:.2f}"),
            tuple(sorted(tags)),
            note.strip(),
            budget_excluded,
        )

    @staticmethod
    def _is_valid_amount(amount: Decimal) -> bool:
        """金额必须是可比较、可汇总的有限数，且不能为 0。

        这一层是数据层的最后一道防线：Decimal("nan") / Decimal("Infinity")
        可被解析，但 NaN 比较可能抛 InvalidOperation，Infinity 会污染汇总；
        不能把解析成功等同于可用的记账金额。
        因此无论多少条调用链，写入前都必须在这里被拦下。
        """
        if not isinstance(amount, Decimal):
            return False
        return amount.is_finite() and amount != 0  # 先判有限性再比较，避免 NaN 比较触发异常。

    @staticmethod
    def _normalize_tags(raw_tags: Iterable[str]) -> tuple[str, ...]:
        """把一批标签名整理成「TRIM + 去空 + 去重」的元组。

        与 tag_prefs._clean_list 的口径刻意保持一致（那边管 JSON 候选列表，
        这里管真正写库的标签）：两处一旦不同步，就会出现「候选列表接受了、写库却被丢弃」
        或反过来的怪现象。

        去重从 #58 Step 2c-2 起才真正在干活 —— 调用方 add / update 的形参已变成 N 项序列，
        不再是「1 个字符串包成 1 元组」的过渡形态。它同时也是**主键护栏**：
        record_tags 的主键是 (record_id, tag)，同一批里出现重复标签会让 executemany
        直接抛 IntegrityError 并回滚整个事务。
        去重按首次出现顺序保留（而不是排序）：候选列表的顺序就是用户的置顶顺序，
        写库这一侧不该擅自重排；标签在界面上的展示顺序另有口径（字典序，见 load()）。
        """
        # 护栏（#58 Step 2c-2）：str 自己也是 Iterable[str]，若放行，迭代 "餐饮" 会得到
        # "餐"、"饮" 两个单字标签——不抛异常、界面还显示得像正常数据，属于最难发现的
        # 一类静默损坏（Step 2b 正是靠「store 内部不肯直接迭代字符串」躲开了它）。
        # 调用方应传完整标签序列（单标签也是一项元组），不能把展示摘要送入数据库。
        if isinstance(raw_tags, str):
            raise TypeError(
                f"tags 必须是标签序列，不能传裸字符串（0 个标签请传 ()）：{raw_tags!r}"
            )
        normalized: list[str] = []
        for raw in raw_tags:
            tag = raw.strip()
            # 必须先 TRIM 再判重：否则 "日用 " 与 "日用" 会被当成两个不同标签各存一行，
            # 而界面上两者显示一模一样，用户完全看不出为什么会多出一个重复项。
            # 空串 / 纯空白一律丢弃，正好对应 Q2 的「允许 0 标签」。
            if not tag or tag in normalized:
                continue
            normalized.append(tag)
        return tuple(normalized)

    @staticmethod
    def _replace_tags(
        connection: sqlite3.Connection, record_id: int, tags: tuple[str, ...]
    ) -> None:
        """在调用方的事务里，把某条记录的标签**整份替换**为 tags。

        必须先 DELETE 再 INSERT，不能只 INSERT：update 允许把标签改少、甚至改成 0 个，
        只插不删的话被用户取消掉的那些旧标签会一直留在关联表里，下次 load() 又把它们
        读回来 —— 表现就是「删掉的标签自己长回来」。
        顺序也不需要在这里维护：界面上的展示顺序由 load() 的 ORDER BY 决定，
        所以批量写就行，省掉 N 次 execute 往返。

        为什么收外部 connection 而不是自己 with self._connect()：本方法必须落在**调用方
        的那个事务**里，否则「写 accounts」与「写 record_tags」会变成两个独立事务，
        中途异常就会留下「记录改了、标签没改」的半成品，破坏 §3.14 的原子性要求。
        """
        connection.execute("DELETE FROM record_tags WHERE record_id = ?", (record_id,))
        if not tags:
            # 0 标签是合法状态（Q2）：删完就走。executemany 传空列表虽然无害，但显式早退更清楚。
            return
        connection.executemany(
            "INSERT INTO record_tags (record_id, tag) VALUES (?, ?)",
            [(record_id, tag) for tag in tags],
        )

    @_business_write
    def add(
        self, record_date: str, amount: Decimal, tags: tuple[str, ...], note: str,
        *, budget_excluded: bool = False,
    ) -> Account | None:
        """写主记录及完整标签，提交后把新记录追加进缓存并返回；日期和金额方向由提交入口先处理。

        返回 None 只在拿不到自增主键的极端情况下出现，调用方须回退全量刷新。
        """
        # 入库前先校验金额，非法金额直接拒绝写入，绝不允许 NaN / Infinity 污染账本。
        if not self._is_valid_amount(amount):
            raise ValueError(f"非法金额：{amount!r}")
        if type(budget_excluded) is not bool:
            raise ValueError("预算外标记必须是布尔值。")
        budget_excluded = budget_excluded and amount < 0  # 收入不参与预算，无需保存标记。
        if budget_excluded:
            self._require_budgets()
        # 标签按完整序列清理，空元组合法；清理保留输入顺序，重载后才按字典序展示。
        tags = self._normalize_tags(tags)
        # 金额统一格式化为两位小数后再入库，避免出现 "5.0" 与 "5.00" 混用的情况。
        # 两条写语句共用一个 _connect() 事务：只 commit 一次、中途异常整体回滚，
        # 不会出现「记录写进去了、标签没写」的半成品（需求 3.1 / §3.14 的原子性）。
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO accounts (record_date, amount, category, note) "
                "VALUES (?, ?, ?, ?)",
                # category 列固定写空串、不再存标签（§3.14.6 待定项 a 已定为「写空串」）：
                # 该列从 Step 2b 起降为 legacy 只读、只留给人工对账用。
                # 写空串的用处已在 Step 2c-2 兑现：_group_tags 删掉 B1 回退后不再读它，
                # 所以即使旧列里还留着值，也不会把用户已删掉的标签又携回界面。
                (record_date, f"{amount:.2f}", "", note.strip()),
            )
            # lastrowid 是刚插入行的自增主键，用它把标签挂到正确的记录上。
            new_id = cursor.lastrowid
            if budget_excluded:
                if new_id is None:
                    raise sqlite3.DatabaseError("无法为新增支出保存预算外标记。")
                # 与账目和标签共用事务，避免支出保存成功但漏掉预算外标记。
                connection.execute("UPDATE accounts SET budget_excluded = 1 WHERE id = ?", (new_id,))
            # lastrowid 类型上可能为 None（未插入 / 极老 SQLite），此时宁可不写标签，
            # 也绝不能把标签挂到一个错的 record_id 上；记录本身照常插入、后面照常 load()。
            if new_id is not None:
                self._replace_tags(connection, new_id, tags)
        if new_id is None:
            return None  # 拿不到主键就不猜缓存位置：交给调用方回退全量刷新。
        # 写库已提交，按同一口径构造缓存对象；自增主键必为当前最大值，尾部追加仍保持升序。
        record = self._cache_record(new_id, record_date, amount, tags, note, budget_excluded)
        self.records.append(record)
        return record

    @_business_write
    def delete(self, record_id: int) -> Account | None:
        """按主键原子删除记录及标签；找不到返回 None，成功则从缓存摘除并返回被删记录。"""
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM accounts WHERE id = ?", (record_id,))
            # rowcount 为 0 说明该 ID 不存在，交由调用方提示「找不到该记录」。
            deleted = cursor.rowcount > 0
            # 显式清理关联行（§3.14.5）：record_tags 没写 FOREIGN KEY，
            # 即便连接启用批次外键，标签关联也没有 ON DELETE CASCADE，仍须自己删。
            # 只在真的删掉主记录时才清：删不到的路径上不该有任何写操作；
            # 而且 record_tags 里出现孤儿行本身就是「数据出了问题」的信号，
            # 顺手静默清掉会把信号一起抹掉，宁可留着让它暴露。
            if deleted:
                connection.execute(
                    "DELETE FROM record_tags WHERE record_id = ?", (record_id,)
                )
        if not deleted:
            return None
        self._unparsed_record_ids.discard(record_id)  # 删除已提交，这笔账不再计入加载异常。
        # 删除只改主键集合、不动其余顺序：二分摘除后列表仍严格升序。
        index = self._cache_index(record_id)
        if index < len(self.records) and self.records[index].record_id == record_id:
            return self.records.pop(index)
        # 数据库删掉了但缓存里没有（例如外部进程改库）：不假装成功，让界面提示找不到。
        return None

    @_business_write
    def update(
        self,
        record_id: int,
        record_date: str,
        amount: Decimal,
        tags: tuple[str, ...],
        note: str,
        *,
        budget_excluded: bool | None = None,
    ) -> Account | None:
        """整份更新记录及标签，保留主键/批次归属；金额非法、缺记录或写库失败返回 None。"""
        try:
            record = self._commit_edit(record_id, record_date, amount, tags, note, budget_excluded)
        except sqlite3.Error:
            return None  # 保留普通编辑调用者的失败返回约定。
        return self.apply_committed_edit(record) if record is not None else None

    @_business_write
    def commit_merchant_edit(
        self, record_id: int, record_date: str, amount: Decimal, tags: tuple[str, ...], note: str,
        *, budget_excluded: bool | None = None, change: MerchantMappingChange,
        read_common_tags: Callable[[], Collection[str]],
    ) -> Account:
        """后台提交账目及商户映射，返回已提交记录，由主线程更新缓存。"""
        self._require_merchants()
        record = self._commit_edit(record_id, record_date, amount, tags, note, budget_excluded,
                                   change=change, read_common_tags=read_common_tags)
        if record is None:
            raise ValueError("找不到该记录或编辑字段无效。")
        return record

    def _commit_edit(
        self, record_id: int, record_date: str, amount: Decimal, tags: tuple[str, ...], note: str,
        budget_excluded: bool | None, *, change: MerchantMappingChange | None = None,
        read_common_tags: Callable[[], Collection[str]] | None = None,
    ) -> Account | None:
        if not self._is_valid_amount(amount):
            return None
        if budget_excluded is not None and (type(budget_excluded) is not bool or not self.budgets_available):
            return None
        if amount > 0:
            budget_excluded = False  # 改为收入后清除不再适用的支出标记。
        flag_sql, flag_values = "", ()
        if self.budgets_available and budget_excluded is not None:
            flag_sql, flag_values = ", budget_excluded = ?", (int(budget_excluded),)
        tags = self._normalize_tags(tags)
        with self._connect() as connection:
            if change is not None:
                if change.tag not in tags or change.direction != ("收入" if amount > 0 else "支出"):
                    raise ValueError("记忆标签须属于本笔最终标签和收支方向。")
                connection.execute("BEGIN IMMEDIATE")
                source = connection.execute(
                    "SELECT merchant_key, merchant_name FROM import_merchants WHERE record_id = ?", (record_id,),
                ).fetchone()
                if source != (change.merchant.key, change.merchant.name):
                    raise MerchantMappingConflict("这条账目的商户关联已变化，请重新核对。")
                self._check_mapping_revisions(connection, (), (change,))
            # 由主键定位，保留原记录 ID 与批次归属。
            cursor = connection.execute(
                "UPDATE accounts SET record_date = ?, amount = ?, "
                f"category = '', note = ?{flag_sql} WHERE id = ?",
                (record_date, f"{amount:.2f}", note.strip(), *flag_values, record_id),
            )
            if cursor.rowcount == 0:
                return None  # 缺少主记录时不得创建孤立标签或商户映射。
            self._replace_tags(connection, record_id, tags)
            if self.budgets_available and budget_excluded is None:
                # 未指定预算标记时，保留本次事务中的现值。
                current = connection.execute(
                    "SELECT budget_excluded FROM accounts WHERE id = ?", (record_id,)
                ).fetchone()
                if current is None:
                    raise sqlite3.DatabaseError("无法读取更新后账目的预算外标记。")
                budget_excluded = bool(current[0])
            if change is not None:
                prior = connection.execute(
                    "SELECT tag FROM merchant_mappings WHERE merchant_key = ? AND direction = ?",
                    (change.merchant.key, change.direction),
                ).fetchone()
                # 等锁期间另一实例可以删签，写映射前现读，失效时连同本笔编辑一起回滚。
                if read_common_tags is None or change.tag not in read_common_tags():
                    raise MerchantMappingConflict("常用标签已变化，请重新选择要记住的标签。")
                if prior != (change.tag,):
                    self._write_merchant_mappings(connection, (change,))
        return self._cache_record(record_id, record_date, amount, tags, note, bool(budget_excluded))

    def apply_committed_edit(self, record: Account) -> Account:
        """在主线程发布已经成功提交的单条编辑。"""
        record_id = record.record_id
        self._unparsed_record_ids.discard(record_id)  # 修正金额已提交，这笔账不再计入加载异常。
        index = self._cache_index(record_id)
        if index < len(self.records) and self.records[index].record_id == record_id:
            self.records[index] = record
        else:
            # 缓存缺这条时按主键补入，保持有序。
            self.records.insert(index, record)
        return record


    def export_month_csv(self, month: str, save_path: Path) -> Path:
        """现读指定月份导出 CSV，不依赖筛选后的表格或缓存，路径及失败提示由调用方管理。"""
        # 用 "YYYY-MM-%" 做 LIKE 前缀匹配，可精确命中该月所有日期，且不会误伤其它月份。
        with self._connect() as connection:
            # category 列已从 SELECT 里去掉（#58 Step 2c-2）：标签只认 record_tags。
            account_rows = connection.execute(
                "SELECT id, record_date, amount, note "
                "FROM accounts WHERE record_date LIKE ? ORDER BY id",
                (f"{month}-%",),
            ).fetchall()
            # record_tags 不做月份过滤：归并时只按 record_id 取值、而 accounts 已被月份筛过，
            # 多带回来的那几个月的关联行不会被用到（好处是不用给 record_tags 再拼一次月份条件）。
            tag_rows = connection.execute(
                "SELECT record_id, tag FROM record_tags ORDER BY record_id, tag"
            ).fetchall()
        # 与 load() 共用标签归并；金额按库内 TEXT 原值导出，不经过 load 的脏金额跳过逻辑。
        tags_by_id = self._group_tags(tag_rows)

        # 表头直接取 config.CSV_FIELDS（#58 Step 2c-2）：该常量的第 4 项已从 category 改成 tags，
        # 不再需要函数内做运行时改名；列序与名字都只有一个真源。
        # CSV 标签字段按 §3.14.4 用竖线连接，与表格的顿号前缀/计数徽标分开，不能导出摘要。
        # 0 标签时 "|".join(()) 天然得到空串，正好对应「0 标签写空串」，无需特判。
        rows = [
            (
                record_id,
                record_date,
                amount,
                "|".join(tags_by_id.get(record_id, ())),
                note,
            )
            for record_id, record_date, amount, note in account_rows
        ]

        # utf-8-sig 会写入 BOM，目的是让 Excel 双击打开中文 CSV 时不出现乱码。
        # 这里不吞异常：权限不足、磁盘已满等情况交给 UI 层捕获并提示（需求 3.6）。
        # 配置、导入映射及SQLite辅助文件同样不能被CSV截断；比较身份也覆盖硬链接别名。
        protected = [self.path] + [self.path.parent / name for name in (
            "tags.json", "categories.json", "settings.json", "import_mappings.json",
            *(self.path.name + suffix for suffix in (".bak", "-wal", "-shm", "-journal")),
        )]
        for source in protected:
            try:
                if save_path.resolve() == source.resolve() or save_path.samefile(source):
                    raise ValueError("导出目标不能是当前账本数据库或应用数据文件，请选择其他文件。")
            except FileNotFoundError:
                continue  # 尚不存在的普通导出目标可新建；其他访问错误必须向上传播。
        with save_path.open("w", newline="", encoding="utf-8-sig") as file:
            writer = csv.writer(file)
            writer.writerow(CSV_FIELDS)
            # 按完整单元格判断公式；纯负数保持数字表示，数据库原值不作任何修改。
            writer.writerows(tuple(_csv_safe_cell(value) for value in row) for row in rows)
        return save_path
