"""SQLite persistence for AccountKeeper."""

import csv
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
import shutil
import sqlite3
from typing import Iterable, Iterator

from config import CSV_FIELDS, DB_PATH

# 本版程序要求数据库最终达到的结构版本号，存在 SQLite 自带的 PRAGMA user_version 里。
# 语义是「目标版本」而不是「已完成的步骤数」：新库默认 0，启动时由 _migrate_to_multi_tag
# 一次性抬到 2；已迁过的库靠 `version >= _SCHEMA_USER_VERSION` 整体跳过，保证迁移只跑一次。
# 选它而不是自建标记表 / 往 settings.json 塞字段：SQLite 自带、无需额外文件，
# 一条 PRAGMA 就能从命令行或探针直接看出库处于哪个版本。
#
# 0 → 1 与 1 → 2（Step 2c-1 抬高本常量时新增的那一段）做的是同一件事——
# 把 accounts.category 里还没有进 record_tags 的值补成一行标签——所以没有再切一个函数，
# 直接让 _migrate_to_multi_tag 复用同一段 SQL 一起完成，不存在歧义。
#
# 【规矩】将来再加**破坏性**步骤（会改写或删除既有数据、无法靠「重跑一次」收敛的那种）
# 必须另立一个独立的迁移函数，不能挂进 _migrate_to_multi_tag。两个理由：
# 1) 本函数的门槛是 `version >= _SCHEMA_USER_VERSION`，对已经升到 2 的库会整体跳过，
#    新步骤混在这里就永远不会执行；
# 2) 本函数只读 accounts、可安全反复重跑，与破坏性步骤「有备份才敢动、动过就不能再动」
#    是两种不同性质，混在一起会把两套安全策略搅坏。
_SCHEMA_USER_VERSION = 2


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
    # 顺序有语义（就是展示顺序），tuple 有序正好承载；具体顺序见 _group_tags 的说明。
    # 【已删除的 shim】Step 2a 曾挂一个 Account.category property 把 tags 拼成单类别字符串，
    # 供 UI 的 6 处 record.category 读取点过渡使用；Step 2c-2 已把那些读取点全改成
    # "、".join(record.tags)，兼容层随之删除。拼展示串的口径只有一句「顿号连接」（§3.14.4），
    # 各处就地拼即可，不再需要数据层额外提供一个属性（少一层就少一处不一致的可能）。
    tags: tuple[str, ...]
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
        # 建表之后才迁移：迁移语句要往 record_tags 里写，表必须先存在。
        # 放在 load() 之前，等 Step 2 让 load() 开始读 record_tags 时就不用再调整顺序。
        # 「迁移前要不要先备份」不再靠「库文件本来就存在」这个布尔值判断——那个判据必须在
        # 建表之前问，而 _connect() 本身就会把新库凭空创建出来，时机太脆；改由迁移内部按
        # 「是否真有记录缺标签」决定（Step 2c-1）：全新库 / 无待补数据的库根本不进备份分支。
        self._migrate_to_multi_tag()
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
        调用门槛由 _migrate_to_multi_tag 把着：**只有「确有记录缺标签」时才轮到本方法**，
        所以全新库与无待补数据的库都不会在用户目录里留下一个空的 .bak。
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

    def _migrate_to_multi_tag(self) -> None:
        """把历史 category 值搬进 record_tags（#58 Step 1 建表 + Step 2c-1 补漏）。

        本函数一次承担 0 → 1 与 1 → 2 两段（都由 user_version 保证只跑一次）：
        - 0 → 1（Step 1）：全新安装，或从未迁移过的老库，把 accounts.category 搬进 record_tags；
        - 1 → 2（Step 2c-1）：Step 1 与 Step 2b 之间落库的记录、以及迁移被跳过的库，
          其 category 列还有值却一行关联都没有，这里把它们补上。
        两段要做的事逐字相同（都只是「给零关联行的记录补一行标签」），所以不另立函数、
        共用同一段 SQL；版本常量上方写了「将来破坏性步骤必须另立函数」的规矩，改这里前先看它。

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
            # 版本与条数都在关连接之前读完，因为文件级备份要读一份「静止」的库，
            # 不能跟尚未落盘的 journal 状态纠缠。
            with self._connect() as connection:
                version_row = connection.execute("PRAGMA user_version").fetchone()
                # PRAGMA 一定返回一行，这里仍做兜底，避免脏库返回空结果时下标报错。
                version = version_row[0] if version_row else 0
                if version >= _SCHEMA_USER_VERSION:
                    # 已经迁过：直接放行。用 >= 而不是 ==，将来把常量继续往上加时不会误跑本段。
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
                connection.execute(f"PRAGMA user_version = {_SCHEMA_USER_VERSION}")
        except sqlite3.Error as error:
            # 覆盖「读版本 / 数待补条数」与「备份后写库」两条路径上的数据库错误。
            print(f"警告：标签迁移失败，本次跳过（下次启动重试）：{error}")

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

    def load(self) -> None:
        """Read all records from SQLite."""
        with self._connect() as connection:
            # 两条 SELECT 走同一个连接 = 同一个事务快照，不会出现「读完 accounts 再去读
            # record_tags 时库已被改动」的错位，也省掉一次开文件。
            # category 列已从 SELECT 里去掉（#58 Step 2c-2）：标签的真源只有 record_tags，
            # 旧列不再参与任何读取，少一列就少一次「到底以哪边为准」的歧义。
            account_rows = connection.execute(
                "SELECT id, record_date, amount, note FROM accounts ORDER BY id"
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
        for record_id, record_date, amount, note in account_rows:
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
                    # 关联表里没有行的记录在这里是 ()，即 Q2 允许的「0 个标签」。
                    tags_by_id.get(record_id, ()),
                    note,
                )
            )
        self.records = records

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
        # 形参既然已经改成序列，就在入口把这条错误用法喊出来：调用方应传 (category,)。
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

    def add(
        self, record_date: str, amount: Decimal, tags: tuple[str, ...], note: str
    ) -> None:
        # 入库前先校验金额，非法金额直接拒绝写入，绝不允许 NaN / Infinity 污染账本。
        if not self._is_valid_amount(amount):
            raise ValueError(f"非法金额：{amount!r}")
        # 形参从「单个 category 字符串」改成 tags 元组（#58 Step 2c-2），UI 侧同步改成传
        # (category,) —— 数据层从此只认序列，裸字符串会被 _normalize_tags 的护栏直接拒绝。
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
            # lastrowid 类型上可能为 None（未插入 / 极老 SQLite），此时宁可不写标签，
            # 也绝不能把标签挂到一个错的 record_id 上；记录本身照常插入、后面照常 load()。
            if new_id is not None:
                self._replace_tags(connection, new_id, tags)
        # 写库成功后重新加载缓存，使内存数据与数据库立刻保持一致。
        self.load()

    def delete(self, record_id: int) -> bool:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM accounts WHERE id = ?", (record_id,))
            # rowcount 为 0 说明该 ID 不存在，交由调用方提示「找不到该记录」。
            deleted = cursor.rowcount > 0
            # 显式清理关联行（§3.14.5）：sqlite3 默认 PRAGMA foreign_keys=OFF，建表时也没写
            # FOREIGN KEY，所以 ON DELETE CASCADE 根本不会生效，只能自己删。
            # 只在真的删掉主记录时才清：删不到的路径上不该有任何写操作；
            # 而且 record_tags 里出现孤儿行本身就是「数据出了问题」的信号，
            # 顺手静默清掉会把信号一起抹掉，宁可留着让它暴露。
            if deleted:
                connection.execute(
                    "DELETE FROM record_tags WHERE record_id = ?", (record_id,)
                )
        if not deleted:
            return False
        self.load()
        return True

    def update(
        self,
        record_id: int,
        record_date: str,
        amount: Decimal,
        tags: tuple[str, ...],
        note: str,
    ) -> bool:
        """更新指定记录，记录不存在或数据库操作失败时返回 False。"""
        # 与 add 同源：金额非法一律按「更新失败」处理，返回 False 让 UI 弹提示而不是崩溃。
        if not self._is_valid_amount(amount):
            return False
        # 与 add 同款：形参是 tags 元组，裸字符串会被护栏拒绝（理由见 _normalize_tags）。
        tags = self._normalize_tags(tags)
        try:
            with self._connect() as connection:
                # WHERE 只依据主键 id，且 id 不参与 SET，保证记录 ID 永远不变（需求 3.5.1）。
                cursor = connection.execute(
                    "UPDATE accounts SET record_date = ?, amount = ?, "
                    "category = ?, note = ? WHERE id = ?",
                    (
                        record_date,
                        f"{amount:.2f}",
                        # 与 add 一致：category 列改存空串，标签只走 record_tags。
                        "",
                        note.strip(),
                        record_id,
                    ),
                )
                updated = cursor.rowcount > 0
                # 只有主记录真的被改到才动标签：id 不存在时若照样替换，
                # 就会往关联表塞一份永远没人认领的孤儿标签，长期污染 get_tags() 的候选池。
                if updated:
                    self._replace_tags(connection, record_id, tags)
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
        # 与 load() 共用同一份归并规则，保证导出内容与界面显示一致。
        tags_by_id = self._group_tags(tag_rows)

        # 表头直接取 config.CSV_FIELDS（#58 Step 2c-2）：该常量的第 4 项已从 category 改成 tags，
        # 不再需要函数内做运行时改名；列序与名字都只有一个真源。
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
            for record_id, record_date, amount, note in account_rows
        ]

        # utf-8-sig 会写入 BOM，目的是让 Excel 双击打开中文 CSV 时不出现乱码。
        # 这里不吞异常：权限不足、磁盘已满等情况交给 UI 层捕获并提示（需求 3.6）。
        with save_path.open("w", newline="", encoding="utf-8-sig") as file:
            writer = csv.writer(file)
            writer.writerow(CSV_FIELDS)
            writer.writerows(rows)
        return save_path
