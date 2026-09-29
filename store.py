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
    # 标签用 tuple 而不是 list（§3.14.2）：records 是全局内存缓存、UI 多处直接读同一批
    # Account 对象，list 允许某处 append() 静默改掉内存里这条记录而数据库没变；
    # tuple 使这种误改直接抛 AttributeError，第一时间暴露不一致。
    # tuple 不可变可哈希、能当 set/dict 键（Step 4 聚合会用到），而 Account 自身在
    # dataclass(eq=True) 下不可哈希，所以这层好处只是 tags 这一项的。
    # 顺序有语义（就是展示顺序），tuple 有序正好承载；具体顺序见 _merge_tags 的说明。
    tags: tuple[str, ...]
    note: str

    @property
    def category(self) -> str:
        """临时兼容层：把 tags 拼成单类别字符串（#58 Step 2a 新增，Step 2b 删除）。

        为什么要它：category → tags 改名会立刻打断 UI 的 6 处 record.category 读取
        （ui.py:165/179/374、chart_window.py:52/55、dialogs.py:93），其中 ui.py:165 就在
        启动后的 _refresh_tree 里——不加这个兼容层，程序**启动即崩**，Step 2a 就无法
        单独验收（违反二.2「修完一个、验证一个」）。
        Step 2a 里每条记录最多 1 个标签，所以返回值与改造前**逐字符相同**（0 标签 → ""）。
        用顿号连接：§3.14.4 规定表格单元格就是这个口径，顺带已经是对齐目标的写法。

        【切勿给这个 property 加类型注解】dataclass 会把带注解的类属性当成字段：
        写成 `category: str` 就会多出一个 category 形参、并把这个 property 整个盖掉，
        使 load() 里按位置传的 5 个参数整体错位（note 被喂给 category）。去掉注解才是属性。
        """
        return "、".join(self.tags)


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

    @staticmethod
    def _merge_tags(
        account_rows: list[tuple[int, str]],
        tag_rows: list[tuple[int, str]],
    ) -> dict[int, tuple[str, ...]]:
        """把两条查询的结果按 record_id 归并成 {记录 ID: 标签元组}。

        两条查询分别取 accounts 与 record_tags，在 Python 里归并——**不用 JOIN**：
        JOIN 会把一条多标签记录膨胀成 N 行、调用方还得再折叠一次，反而多一步
        （§3.14.5 已定此口径）。
        为什么抽成独立方法：load() 与 export_month_csv() 都要这套规则，各写一遍就是
        「口径改动易漏改」（正是 issues #16 的主题），所以归并规则只留这一份。

        【B1 回退，Step 2a 的过渡逻辑，Step 2b 删除】：record_tags 里有行就以它为准；
        一行都没有的记录才回退去读 accounts.category。因为本步的 add / update 仍然只写
        accounts.category、不写 record_tags，不回退的话「Step 1 迁移之后新增/编辑的记录」
        在界面上会显示成 0 标签。这也正好对应 §3.14.2 对 category 列的定性：
        保留、停止写入、**降为 legacy 只读**。
        回退只针对「一条关联行都没有」的记录：有行的记录严格以 record_tags 为准，
        免得把用户主动删空标签的记录又从旧 category 里携回一个标签。
        """
        merged: dict[int, list[str]] = {}
        # tag_rows 已按 (record_id, tag) 排序，逐行 append 就保住了这个顺序
        # （也就是字典序，见 load() 里的说明）。
        for record_id, tag in tag_rows:
            merged.setdefault(record_id, []).append(tag)
        for record_id, category in account_rows:
            if record_id in merged:
                # 已经在 record_tags 里出现过：以关联表为准，不看旧列。
                continue
            legacy = str(category).strip() if category else ""
            if legacy:
                # 旧列里的空白值当「无标签」处理，与迁移 SQL 的 TRIM 口径保持一致。
                merged[record_id] = [legacy]
        # 一次性冻结成 tuple：调用方拿到的是不可变值，不可能被原地修掉。
        return {record_id: tuple(tags) for record_id, tags in merged.items()}

    def load(self) -> None:
        """Read all records from SQLite."""
        with self._connect() as connection:
            # 两条 SELECT 走同一个连接 = 同一个事务快照，不会出现「读完 accounts 再去读
            # record_tags 时库已被改动」的错位，也省掉一次开文件。
            account_rows = connection.execute(
                "SELECT id, record_date, amount, category, note "
                "FROM accounts ORDER BY id"
            ).fetchall()
            # ORDER BY record_id, tag：一条记录的多个标签顺序由 SQL 定，落到 tags 里
            # 就是**字典序**（不是用户打字顺序）。DDL 没有顺序列，这是直接后果，
            # 取舍已备案在 _issues.txt #58；顺带让 idx_record_tags_tag 派上用场。
            tag_rows = connection.execute(
                "SELECT record_id, tag FROM record_tags ORDER BY record_id, tag"
            ).fetchall()
        # 归并规则（含 B1 回退）收在 _merge_tags 里，与 export_month_csv 共用同一份。
        # 第 0 列 = id、第 3 列 = category，与上面 SELECT 的列序一致。
        tags_by_id = self._merge_tags(
            [(row[0], row[3]) for row in account_rows], tag_rows
        )
        # 数据库里金额是字符串，这里重新构造 Decimal，保证后续汇总计算不损失精度。
        # 但要逐行捕获异常：历史库或手工改过的库里可能出现 "abc" 这类无法解析的金额，
        # Decimal() 会抛出 InvalidOperation，而 load() 是在 __init__ 里调用的，
        # 一旦抛出就会让整个程序在启动阶段闪退（用户只看到「双击没反应」）。
        records: list[Account] = []
        # category 列在这里已无用（标签已由 _merge_tags 算好），用下划线开头表明刻意不用。
        for record_id, record_date, amount, _category, note in account_rows:
            try:
                parsed_amount = Decimal(amount)
            except (InvalidOperation, TypeError, ValueError) as error:
                # 只跳过这一行：数据库里的原始记录保持原样，绝不顺手改写或删除用户数据。
                print(
                    f"警告：记录 ID {record_id} 的金额无法解析（{amount!r}），已跳过该行：{error}"
                )
                continue
            records.append(
                Account(
                    record_id,
                    record_date,
                    parsed_amount,
                    # 没查到关联、又没有旧 category 的记录在这里是 ()，
                    # 即 Q2 允许的「0 个标签」。
                    tags_by_id.get(record_id, ()),
                    note,
                )
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

        【#58 Step 2a 起的现状（只追加说明，实现一个字未改）】
        - 本方法已被 get_tags() 取代，进入退场倒计时。
        - 当前唯一生产调用点是 ui.py:97（category_prefs.ensure_migrated 的入参）；
          Step 2b 改 UI + category_prefs.py → tag_prefs.py 时一并删除，同时结掉 #47。
        - 保留期内**禁止新增调用点**，否则 Step 2b 删不干净。
        上面那段 #47 的定性分析（不是死代码、而是一次性迁移依赖 + 每次启动的无效查询）
        是历史结论，一个字都不删。
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

    def get_tags(self) -> list[str]:
        """返回库里所有用过的标签（去重、按字典序）。

        与 get_categories() 的三点差异：
        1. 数据源是 record_tags（新世界的真源），不读 accounts.category；
        2. 不再按金额正负拆成 (支出, 收入) 两组——Q1 取消收支方向，标签池只有一份；
        3. 排序口径固定为 tag 的字典序（顺着 idx_record_tags_tag，结果稳定可复现）。

        刻意不做并集回退（Step 2a 决策 3）：不把 accounts.category 的非空值并进来。
        本方法是「新世界」的接口；其生产用途（Step 2b 的首启迁移候选池）届时读同一批数据，
        而 Step 1 → Step 2b 之间新增记录的标签进不了候选池是可接受的（未发布、无存量用户）。

        已知现状：本步（Step 2a）里它**还没有任何生产调用点**，是纯新增，Step 2b 才会被
        tag_prefs 的首启迁移接上。请勿因为「没人用」就当死代码删掉——#47 踩过这个坑
        （get_categories 曾因「看起来没人用」被误判成死代码）。
        """
        with self._connect() as connection:
            # DISTINCT + ORDER BY 都由 SQLite 做，既能命中 idx_record_tags_tag，
            # 也避免把全表标签捞回 Python 再去重排序。
            rows = connection.execute(
                "SELECT DISTINCT tag FROM record_tags ORDER BY tag"
            ).fetchall()
        return [row[0] for row in rows]

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
            account_rows = connection.execute(
                "SELECT id, record_date, amount, category, note "
                "FROM accounts WHERE record_date LIKE ? ORDER BY id",
                (f"{month}-%",),
            ).fetchall()
            # record_tags 不做月份过滤：归并时只按 record_id 取值、而 accounts 已被月份筛过，
            # 多带回来的那几个月的关联行不会被用到（好处是不用给 record_tags 再拼一次月份条件）。
            # 注意这里**不能**用 _merge_tags 的回退去导「当月新记录」以外的场景——
            # account_rows 已限月，回退只会作用在当月记录上，口径与界面显示一致。
            tag_rows = connection.execute(
                "SELECT record_id, tag FROM record_tags ORDER BY record_id, tag"
            ).fetchall()
        # 与 load() 共用同一份归并规则（含 B1 回退），保证导出内容与界面显示一致。
        # 第 0 列 = id、第 3 列 = category，与上面 SELECT 的列序一致。
        tags_by_id = self._merge_tags(
            [(row[0], row[3]) for row in account_rows], tag_rows
        )

        # 表头按「名字」把 category 换成 tags，而不是按下标拼一份新顺序：
        # 将来 config.CSV_FIELDS 的列序若调整，两边会一起跟着走；写死元组等于复制一份顺序真源。
        # 本步刻意不改 config.CSV_FIELDS（Step 2b 才改），所以这个替换只在函数内部做，
        # 属过渡逻辑（TODO(Step 2b)：改掉 config.CSV_FIELDS 后这里也一并简化）。
        header = ["tags" if field == "category" else field for field in CSV_FIELDS]
        # 多标签用竖线连接（§3.14.4）：若用逗号，csv.writer 会给该字段自动加双引号
        # （"日用,家庭"）——虽然合法，但用户拿 Excel「文本分列」或脚本 split(',') 时会踩坑；
        # 竖线在标签名里极罕见，整行仍是规整的逗号分隔。
        # 0 标签时 "|".join(()) 天然得到空串，正好对应「0 标签写空串」，无需特判。
        rows = [
            (
                record_id,
                record_date,
                amount,
                "|".join(tags_by_id.get(record_id, ())),
                note,
            )
            # category 列已无用（标签已由 _merge_tags 算好），下划线开头表明刻意不用。
            for record_id, record_date, amount, _category, note in account_rows
        ]

        # utf-8-sig 会写入 BOM，目的是让 Excel 双击打开中文 CSV 时不出现乱码。
        # 这里不吞异常：权限不足、磁盘已满等情况交给 UI 层捕获并提示（需求 3.6）。
        with save_path.open("w", newline="", encoding="utf-8-sig") as file:
            writer = csv.writer(file)
            writer.writerow(header)
            writer.writerows(rows)
        return save_path
