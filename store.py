"""SQLite persistence for AccountKeeper."""

import csv
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
import sqlite3
from typing import Iterator

from config import CSV_FIELDS, DB_PATH


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
        # 先建表、再把数据读进内存，这个顺序不能颠倒。
        # 旧 CSV 迁移逻辑已在 #40 中整体删除（项目还没有真实用户从旧版本升级，迁移属于纯负债）。
        self._initialize_database()
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
