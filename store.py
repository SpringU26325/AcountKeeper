"""SQLite persistence for AccountKeeper."""

import csv
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
import shutil
import sqlite3
from typing import Iterator

from config import CSV_FIELDS, DB_PATH

# record_tags 关联表的 schema 版本号，存在 SQLite 自带的 PRAGMA user_version 里。
# 新库默认是 0，本段迁移跑完升到 1，用它保证迁移「只跑一次」。
# 选它而不是自建标记表 / 往 settings.json 塞字段：SQLite 自带、无需额外文件，
# 一条 PRAGMA 就能从命令行或探针直接看出库处于哪个版本，以后 1→2 继续往上加即可。
_SCHEMA_USER_VERSION = 1


@dataclass
class Account:
    """A single expense record."""

    # 金额使用 Decimal 而不是 float，避免 0.1 + 0.2 这类浮点误差导致账目对不上。
    record_id: int
    record_date: str
    amount: Decimal
    category: str
    note: str


class AccountStore:
    """Persist account records in a local SQLite database."""

    def __init__(self, path: Path = DB_PATH) -> None:
        # 数据库位置固定为 config.DB_PATH，不再接受配置覆盖；
        # 保留 path 参数仅是给测试留接口，便于注入临时库而不污染真实账本。
        self.path = path
        # 内存缓存：UI 的筛选与汇总都读它，避免用户每敲一个字就查一次数据库。
        self.records: list[Account] = []
        # 「库文件是否早就存在」必须在建表之前问，而且只能问这一次：
        # _connect() 会顺手 mkdir + sqlite3.connect，一个全新安装的库文件正是被它凭空
        # 创建出来的，等 _initialize_database() 之后再判断就永远是 True 了。
        # 这个布尔值只决定「迁移前要不要备份」，用完即弃，所以用局部变量往下传，
        # 不存成实例属性——否则类的状态里会多出一个只读一次的字段，徒增理解成本。
        database_existed = self.path.exists()
        # 先建表、再把数据读进内存，这个顺序不能颠倒。
        # 旧 CSV 迁移逻辑已在 #40 中整体删除（项目还没有真实用户从旧版本升级，迁移属于纯负债）。
        self._initialize_database()
        # 建表之后才迁移：迁移语句要往 record_tags 里写，表必须先存在。
        # 放在 load() 之前，等 Step 2 让 load() 开始读 record_tags 时就不用再调整顺序。
        self._migrate_to_multi_tag(database_existed=database_existed)
        self.load()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        # 目录可能被用户删掉或首次运行不存在，写库前先补建，防止 sqlite3.connect 直接报错。
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        try:
            yield connection
        except Exception:
            # 任何异常都回滚，保证不会留下写了一半的脏数据（需求 3.1 要求数据不丢失）。
            connection.rollback()
            raise
        else:
            # 只有整段 with 块顺利执行完才提交，相当于一次原子事务。
            connection.commit()
        finally:
            # 无论成功或失败都必须关闭连接，否则会累积文件句柄。
            connection.close()

    def _initialize_database(self) -> None:
        with self._connect() as connection:
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
            # 故意不写 FOREIGN KEY：sqlite3 默认 foreign_keys=OFF，写了也不生效，
            # 所以删除记录时由 delete() 显式清理关联（§3.14.5 已明确不依赖级联）。
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

        用文件级复制而不是 SQLite 的备份 API：单文件库在连接关闭后直接 copy 最稳，
        而且留下的是一份「迁移前那一刻」的完整字节快照，出问题直接用 .bak 覆盖回去。
        备份路径基于 self.path、不是 config.DB_PATH：测试注入临时库时备份也该落在
        临时目录里，绝不能写到真实数据目录去。每次覆盖同一份、不带时间戳，
        一份最新备份足够，避免在用户目录里堆文件（§3.14.6 待定项 b 的倾向）。
        """
        # 不能用 with_suffix(".bak")：那会把 .db 替换掉、得到 account.bak，
        # 名字既与 requirements §3.14.2 对不上，.gitignore 的规则也匹配不到它。
        backup_path = self.path.parent / (self.path.name + ".bak")
        try:
            shutil.copy2(self.path, backup_path)
        except OSError as error:
            # 备份失败就中止迁移：宁可这次不迁，也不能在没有退路的情况下改用户的库。
            # 只打控制台警告、不抛异常——启动阶段抛出去会让整个程序起不来。
            print(f"警告：迁移前备份数据库失败（{backup_path}），本次跳过迁移：{error}")
            return None
        return backup_path

    def _migrate_to_multi_tag(self, database_existed: bool) -> None:
        """把历史 category 值搬进 record_tags（#58 Step 1，只跑一次）。

        整体包一层 try：本函数是在 __init__ 里被调用的，而 AccountStore() 又是在
        account_keeper.main() 的 try 里构造的，一旦异常逃逸出去，用户看到的就是
        「启动失败」弹窗、程序根本起不来。迁移失败属于可降级问题（下次启动会重试），
        所以按「备份失败也中止迁移」的同款处置：打警告、保持 user_version = 0、
        让程序照常启动。迁移只读 accounts.category，不写 accounts 的任何列，
        所以整个迁移是可回滚的（§3.14.2 的「老数据不丢三重保证」）。
        """
        try:
            with self._connect() as connection:
                version_row = connection.execute("PRAGMA user_version").fetchone()
                # PRAGMA 一定返回一行，这里仍做兜底，避免脏库返回空结果时下标报错。
                version = version_row[0] if version_row else 0
                if version >= _SCHEMA_USER_VERSION:
                    # 已经迁过：直接放行。用 >= 而不是 == 0，将来加 1→2 时不会误跑本段。
                    return
            # 备份刻意放在连接关闭之后：文件级复制要读一份「静止」的库文件，
            # 不跟尚未落盘的 journal 状态纠缠。
            # 全新安装（库文件是本次才被 _connect() 创建出来的）没有数据可丢，跳过备份，
            # 免得每次首启都在用户目录里留一个空的 account.db.bak；
            # 但版本号照样置 1——空库本来就是新的地基形态，留成 0 会让迁移延后到
            # 用户已经录了数据的第二次启动才触发，迁移时机变得不可预期。
            if database_existed and self._backup_database() is None:
                # 备份失败（警告已在上一步打出）：保持 user_version = 0 让下次启动重试，
                # 绝不带着「没有退路」的状态去写用户的库。
                return
            with self._connect() as connection:
                # TRIM 掉首尾空白，空串 / 纯空白不产生标签，正好对应「允许 0 标签」。
                # INSERT OR IGNORE + 复合主键 = 重复执行结果完全一致（幂等），
                # 中途断电导致下次重跑也不会产生重复行。
                connection.execute(
                    "INSERT OR IGNORE INTO record_tags(record_id, tag) "
                    "SELECT id, TRIM(category) FROM accounts "
                    "WHERE TRIM(category) <> ''"
                )
                # 升版本与上面的插入在同一个事务里提交，不可能出现
                # 「标签已写入、版本号却没升」的半迁移状态。
                # PRAGMA 的位置参数不走占位符绑定，只能拼进 SQL；值来自模块常量、
                # 不是外部输入，没有注入面。
                connection.execute(f"PRAGMA user_version = {_SCHEMA_USER_VERSION}")
        except sqlite3.Error as error:
            # 覆盖「读版本」与「备份后写库」两条路径上的数据库错误。
            print(f"警告：标签迁移失败，本次跳过（下次启动重试）：{error}")

    def load(self) -> None:
        """Read all records from SQLite."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, record_date, amount, category, note "
                "FROM accounts ORDER BY id"
            ).fetchall()
        # 数据库里金额是字符串，这里重新构造 Decimal，保证后续汇总计算不损失精度。
        # 但要逐行捕获异常：历史库或手工改过的库里可能出现 "abc" 这类无法解析的金额，
        # Decimal() 会抛出 InvalidOperation，而 load() 是在 __init__ 里调用的，
        # 一旦抛出就会让整个程序在启动阶段闪退（用户只看到「双击没反应」）。
        records: list[Account] = []
        for record_id, record_date, amount, category, note in rows:
            try:
                parsed_amount = Decimal(amount)
            except (InvalidOperation, TypeError, ValueError) as error:
                # 只跳过这一行：数据库里的原始记录保持原样，绝不顺手改写或删除用户数据。
                print(
                    f"警告：记录 ID {record_id} 的金额无法解析（{amount!r}），已跳过该行：{error}"
                )
                continue
            records.append(
                Account(record_id, record_date, parsed_amount, category, note)
            )
        self.records = records

    def get_categories(self) -> tuple[list[str], list[str]]:
        """返回 (支出类别列表, 收入类别列表)，来自数据库中的 DISTINCT category。

        供首次启动时 category_prefs.ensure_migrated 迁移历史类别使用（把这些历史类别当作
        categories.json 的初始值），这是本方法唯一的生产调用点；日常启动时 categories.json
        已存在，本方法仍会被调用、但结果被 ensure_migrated 丢弃（它第一行就按「文件是否存在」
        早退），即「一次性迁移依赖 + 每次启动的无效查询」，见 #47。
        之所以要读数据库、而不是让界面层自己攒：界面状态一重启就没了，只有数据库才是
        跨会话的长期记忆，而首启迁移要捞的正是这份「手打过、但离开界面就消失」的历史类别，
        所以这个查询必须落在库上（候选本身已由 categories.json 承载，日常不再经过这里）。

        按金额正负拆成两组：业务约定「负数 = 支出、非负 = 收入」（见 ui.add_record 的符号转换），
        所以金额本身就是最可靠的分类依据，不需要再额外加一个「收支类型」字段去维护。
        DISTINCT 负责去重，ORDER BY 保证同一批数据每次刷新顺序都一样，
        否则下拉列表会随机重排，用户刚记住的位置下次就变了。

        注意 amount 列是 TEXT（见 _initialize_database），SQLite 的类型亲和规则会让
        `amount < 0` 退化成字符串比较。这里的结果依然是正确的：
        store.add / update 一律按 f"{amount:.2f}" 写入，所以负数必定以 '-'（0x2D）开头，
        非负数必定以数字（'0'~'9'，0x30 起）开头，而 '-' < '0'，
        字符串比较与数值比较在所有正常数据上完全一致；
        手工改库产生的 'abc' / 'NaN' 这类脏值只会被判入收入组，不会抛异常。
        """
        with self._connect() as connection:
            expense_rows = connection.execute(
                "SELECT DISTINCT category FROM accounts "
                "WHERE amount < 0 ORDER BY category"
            ).fetchall()
            income_rows = connection.execute(
                "SELECT DISTINCT category FROM accounts "
                "WHERE amount >= 0 ORDER BY category"
            ).fetchall()
        return (
            [row[0] for row in expense_rows],
            [row[0] for row in income_rows],
        )

    def next_id(self) -> int:
        # 取当前最大 ID 加一；空表时 default=0 返回 1，避免 max() 在空序列上报错。
        return max((record.record_id for record in self.records), default=0) + 1

    @staticmethod
    def _is_valid_amount(amount: Decimal) -> bool:
        """金额必须是可比较、可汇总的有限数，且不能为 0。

        这一层是数据层的最后一道防线：Decimal("nan") / Decimal("Infinity")
        解析时不会报错，一旦入库，之后所有「金额 > 0」这类大小比较和求和
        都会抛 InvalidOperation 或静默变成 NaN，直接把界面渲染炸掉。
        因此无论多少条调用链，写入前都必须在这里被拦下。
        """
        if not isinstance(amount, Decimal):
            return False
        return amount.is_finite() and amount != 0

    def add(
        self, record_date: str, amount: Decimal, category: str, note: str
    ) -> None:
        # 入库前先校验金额，非法金额直接拒绝写入，绝不允许 NaN / Infinity 污染账本。
        if not self._is_valid_amount(amount):
            raise ValueError(f"非法金额：{amount!r}")
        # 金额统一格式化为两位小数后再入库，避免出现 "5.0" 与 "5.00" 混用的情况。
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO accounts (record_date, amount, category, note) "
                "VALUES (?, ?, ?, ?)",
                (record_date, f"{amount:.2f}", category.strip(), note.strip()),
            )
        # 写库成功后重新加载缓存，使内存数据与数据库立刻保持一致。
        self.load()

    def delete(self, record_id: int) -> bool:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM accounts WHERE id = ?", (record_id,))
            # rowcount 为 0 说明该 ID 不存在，交由调用方提示「找不到该记录」。
            deleted = cursor.rowcount > 0
        if not deleted:
            return False
        self.load()
        return True

    def update(
        self,
        record_id: int,
        record_date: str,
        amount: Decimal,
        category: str,
        note: str,
    ) -> bool:
        """更新指定记录，记录不存在或数据库操作失败时返回 False。"""
        # 与 add 同源：金额非法一律按「更新失败」处理，返回 False 让 UI 弹提示而不是崩溃。
        if not self._is_valid_amount(amount):
            return False
        try:
            with self._connect() as connection:
                # WHERE 只依据主键 id，且 id 不参与 SET，保证记录 ID 永远不变（需求 3.5.1）。
                cursor = connection.execute(
                    "UPDATE accounts SET record_date = ?, amount = ?, "
                    "category = ?, note = ? WHERE id = ?",
                    (
                        record_date,
                        f"{amount:.2f}",
                        category.strip(),
                        note.strip(),
                        record_id,
                    ),
                )
                updated = cursor.rowcount > 0
        except sqlite3.Error:
            # 更新失败属于可恢复错误（例如数据库文件被占用），返回 False 让 UI 弹提示而不是崩溃。
            return False
        if not updated:
            return False
        self.load()
        return True

    def export_month_csv(self, month: str, save_path: Path) -> Path:
        """Export one month's records to the provided save path and return it."""
        # 用 "YYYY-MM-%" 做 LIKE 前缀匹配，可精确命中该月所有日期，且不会误伤其它月份。
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, record_date, amount, category, note "
                "FROM accounts WHERE record_date LIKE ? ORDER BY id",
                (f"{month}-%",),
            ).fetchall()

        # utf-8-sig 会写入 BOM，目的是让 Excel 双击打开中文 CSV 时不出现乱码。
        # 这里不吞异常：权限不足、磁盘已满等情况交给 UI 层捕获并提示（需求 3.6）。
        with save_path.open("w", newline="", encoding="utf-8-sig") as file:
            writer = csv.writer(file)
            writer.writerow(CSV_FIELDS)
            writer.writerows(rows)
        return save_path
