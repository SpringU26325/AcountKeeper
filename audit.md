# AccountKeeper审计报告（详细版）

> 生成日期：2026-09-26
> 说明：本文件是 `issues.txt` 中「待修复清单」的详细版本，包含每一项的详情、影响与建议。
> `issues.txt` 只保留精简待办；需要追溯细节时查阅本文件。
> 本次审计为只读审计，未修改任何 `.py` 文件。

---

================审计新增（2026-09-26） ================
审计结论：本次新增以下问题，按严重程度 P0 > P1 > P2 > P3 排列。
说明：本次审计只读代码，未修改任何 .py 文件。

[ ] #12 - 需求 3.11「用户自定义存储路径」完全未实现：无 settings.json 读取、无设置界面、db_path/csv_dir 不可配置 (涉及文件: config.py, store.py, ui.py, widgets.py, dialogs.py)
    详情：全项目 grep 不到 settings.json / csv_dir 的任何实现；config.py 只有模块级常量 DB_PATH、CSV_PATH，
    以及 import 时执行 DATA_DIR.mkdir() 的副作用，没有任何「启动先读配置 + 配置缺失/损坏/非法时回退默认值」的逻辑。
    需求 2 与 3.11 明确要求 settings.json（默认位于项目目录）保存 db_path 与 csv_dir。
    影响：DB_PATH 在 import 时即被固定，用户无法迁移账本数据目录；打包成 exe 后 _MEIPASS 为只读，更不可能改。
    连带：ui.open_data_folder 固定打开 config.DATA_DIR，一旦支持自定义路径会打开到错误目录。

[ ] #13 - store.load() 没有异常保护，数据库里出现非法 amount 字符串时程序启动即闪退（已实测复现） (涉及文件: store.py, account_keeper.py)
    详情：load() 里直接 Decimal(amount)，若某行金额是 "abc" 这类无法解析的字符串会抛 decimal.InvalidOperation；
    AccountStore.__init__ -> load() 无 try/except，account_keeper.py 的 main() 中 AccountKeeperApp(AccountStore())
    也没有任何兜底，结果是进程直接退出、连窗口都不弹，用户只看到「双击没反应」。
    实测：用临时数据库写入 amount="abc" 后构造 AccountStore(path=...)，立刻在 store.py:134 抛 InvalidOperation。
    备注：上一轮只补了「非有限数（NaN/Infinity）」守卫，不覆盖「非法字符串」这一类脏数据。
    建议：load() 内逐行 try/except 跳过或置零非法行；main() 外层包 try/except 并弹 messagebox 提示。
    同源问题：_initialize_database() 也没有异常捕获，数据库文件损坏/被占用时 sqlite3.DatabaseError 会直接抛出。

[ ] #14 - 打包脚本 AccountKeeper.spec 丢失 snail_messages.json（相对 v0.1.1 是功能回归） (涉及文件: AccountKeeper.spec, AccountKeeper_v0.1.1-alpha.spec, snail.py)
    详情：旧版 AccountKeeper_v0.1.1-alpha.spec 的 datas 为 [('image','image'), ('snail_messages.json','.')]，
    新的 AccountKeeper.spec 只剩 [('image','image')]，snail_messages.json 没有被收进去。
    后果：打包后 RESOURCE_DIR(=sys._MEIPASS) 下找不到该文件，点击蜗牛永远只显示 DEFAULT_SNAIL_MESSAGE，
    且每次点击都会在控制台打印「警告：无法读取蜗牛消息」。
    附带：新 spec 用 collect_data_files('customtkinter') 取代了旧版的 collect_all('customtkinter') + collect_all('matplotlib')，
    matplotlib 的 data 文件与 hiddenimports 不再被显式收集（官方 hook 可能兜住，但属于未经验证的退化）。

[ ] #15 - 需求 3.6 导出 CSV 未传 initialdir，CSV 默认目录需求未实现 (涉及文件: ui.py)
    详情：export_csv 调用 filedialog.asksaveasfilename() 时只传了 defaultextension、initialfile、filetypes，
    缺少需求 3.6 明确要求的 initialdir=str(csv_dir)，用户每次导出都要自己重新选目录。
    依赖：#12（需要先有 settings.csv_dir）。

[ ] #16 - 收入/支出汇总逻辑在三处重复实现，口径改动极易漏改 (涉及文件: ui.py, chart_window.py, store.py)
    详情：ui.filter_records、ui.show_stats、chart_window.show_chart_window 各自写了一份「按金额正负分类累加」，
    连 is_finite() 守卫都复制了三份。任何口径调整（是否计入 0 元、是否过滤非有限数、支出是否取绝对值）
    都必须同步改三处，属于典型的高危重复逻辑。
    建议：抽成 store.py 或新增 stats.py 里的单一函数（如 summarize(records)）。

[ ] #17 - 图标路径在两处硬编码，未复用 config.RESOURCE_DIR (涉及文件: account_keeper.py, dialogs.py, config.py)
    详情：account_keeper.py 的 iconbitmap 与 dialogs._apply_logo_icon 都用
    Path(__file__).resolve().parent / "image" / "logo.ico"，而 config.py 已提供带 sys._MEIPASS 兜底的 RESOURCE_DIR。
    目前能正常工作，但一旦改用 onedir 打包、自定义 runtime_tmpdir 或调整资源目录结构就会静默失效
    （dialogs 侧 except Exception: pass 会吞掉错误，表现为「图标莫名消失」且无任何提示）。

[ ] #18 - 死代码：amount_var 与 search_var 实际不参与任何逻辑，容易误导后续维护 (涉及文件: widgets.py, ui.py)
    详情：InputFrame.amount_var 从未绑定到任何控件（金额框为了 placeholder_text 放弃了 textvariable），
    ui.add_record 里的 self.amount_var.set("") 是空操作。
    ToolbarFrame.search_var 注册了 trace_add("write", ...)，但全项目没有任何地方给它赋值，
    这个监听永远不会触发（实际筛选靠 search_entry 的事件 + 直接 get()）。
    需求 3.9 只要求「存在 trace_add」，保留无妨，但应补注释或用 _ 前缀标明它是接口占位，避免后人误以为它是数据源。

[ ] #19 - 数据库连接开销与列表全量重建，记录量大后明显卡顿 (涉及文件: store.py, ui.py)
    详情：_connect() 每次操作都新建/关闭一个 sqlite 连接，且 add/update/delete 之后还会再 load() 全量重查整张表；
    ui.filter_records 每敲一个字符就清空 Treeview 再逐行重新 insert。
    记录数达到数千条时，输入搜索时会明显卡顿。

[ ] #20 - 图表窗口没有设置 logo 图标，与其它窗口不一致 (涉及文件: chart_window.py, dialogs.py)
    详情：dialogs 里的三个对话框都会调用 _apply_logo_icon，图表窗口只建了 Toplevel 而没有设置 iconbitmap，
    标题栏/任务栏图标与主窗口不统一。也没有调用 transient(app)。

[ ] #21 - chart_window 只捕获 ImportError，matplotlib 其它初始化失败仍会崩溃 (涉及文件: chart_window.py)
    详情：_render_chart_window 只有 except ImportError 分支。若 matplotlib 已安装但 TkAgg 后端初始化失败
    （版本不匹配、缺少 Tk 绑定、字体缓存损坏等），会抛出非 ImportError 异常，直接崩溃。
    需求 3.10 要求「matplotlib 相关失败不能崩溃」，建议把导入 + FigureCanvasTkAgg 创建一起纳入 try/except。

[ ] #22 - 月份前缀匹配写法不一致，存在隐性 bug 隐患 (涉及文件: ui.py, store.py)
    详情：store.export_month_csv 用 LIKE '{month}-%'，ui.show_stats 用 startswith(month + "-")，
    但 ui.export_csv 的「该月是否有记录」判断用的是 startswith(month)（少了 "-"）。
    当前日期格式规范所以结果一致，但属于隐性隐患，建议统一成 month + "-"。

[ ] #23 - 蜗牛图片处理与需求 3.7 描述不符，且未使用 lift() (涉及文件: config.py, snail.py)
    详情：需求 3.7 要求使用 snail.png、保留 GGB 网格线、并「必须取消透明度，恢复到 100% 不透明」；
    实际用的是 snail_blue_anti.png（项目 image/ 下根本没有 snail.png），
    且 _prepare_snail_image 把接近纯白的像素 alpha 置 0 做成了透明、把蓝色线条强制改写为 (30,58,138)。
    另外需求要求「用 lift() 确保不被其它控件遮挡」，代码没有调用 lift()（当前靠创建顺序侥幸在上层，一旦调整控件创建顺序就会出问题）。
    性能：100x100 的逐像素 getpixel/putpixel 双重循环属于性能债（每次启动几十毫秒），可用 PIL 的 point/ImageOps 优化。

[ ] #24 - 编辑窗口金额带符号显示，与「用户始终输入正数」的交互约定不统一 (涉及文件: dialogs.py)
    详情：添加记录用「支出/收入」切换按钮 + 正数输入；编辑窗口却把 -200.00 原样预填，需要用户自行判断符号方向。
    需求 3.5.1 只要求「数据校验与添加记录保持一致」，未要求交互一致，故列为体验债而非缺陷。

[ ] #25 - ui.open_data_folder 的 except 变量未使用 (涉及文件: ui.py)
    详情：except Exception as error: 分支内部并没有使用 error，属于无用变量，建议改为 except Exception:。

[ ] #26 - README.md 与 v0.1.2 现状不符，发布前需同步 (涉及文件: README.md)
    详情：下载说明与文件名仍写 AccountKeeper_v0.1.1-alpha.exe（dist/ 里也确实还没有 v0.1.2）；
    「已知 Bug」仍只写日期需手动输入，完全没有提及「设置界面 / 自定义存储路径」这一大块未实现（需求 3.11）。

[ ] #27 - 删除确认文案与需求 3.5 措辞不一致 (涉及文件: dialogs.py)
    详情：需求 3.5 写的是询问「确认删除该记录吗？」，实际弹窗文案是「确定要删除这条记录吗？」。
    属最低优先级；因涉及 UI 文字改动，需先经确认再调整。

---

## 附：精简清单中新增的两项（来自原 issues.txt 历史条目）

[ ] #28 - 日期选择（日历/月历）未实现 (涉及文件: widgets.py, ui.py, dialogs.py)
    详情：原 issues.txt 第 3 条「不能选择类型和日期」的**未完成部分**。
    「支出/收入」已由 CTkSegmentedButton 解决；但日期仍必须手动格式化输入，缺少日历/月历点击选择器。
    需求 3.2 要求日期格式 YYYY-MM-DD，需求未强制要求日历控件，但属于明确的 user-friendly 缺口。

[ ] #29 - 卸载和更新功能未实现
    详情：原 issues.txt 第 9 条。目前只有手动下载 exe 覆盖，没有卸载入口、没有版本更新检测。

[ ] #30 - 按 Alt 时蜗牛暂停 (涉及文件: snail.py)
    详情：原 issues.txt 第 11 条。本次审计未能在代码中复现该行为，
    snail.py 只对点击事件（_snail_clicked）做了处理，未发现 Alt 相关绑定。需用户补充复现步骤后再确认。

---

## Step 1.5 返工说明（2026-09-26）
- 原实现按「可自定义 db_path + csv_dir」设计。
- 后根据实际开发成本，取消 db_path 自定义，仅保留 csv_dir。
- settings.py 已收敛，返回值改为三元组 (csv_dir, is_fallback, is_first_run)，区分首次运行与异常回退。
- #12.1 正式完成。

---

## Step 2 完成说明（2026-09-26）

对应待办 #12.2「启动流程读取 csv_dir 配置并处理回退提示」。

1. **account_keeper.py 接入配置读取**：
   `main()` 在创建 store 之前调用 `settings.get_effective_csv_dir()`，拿到
   `(csv_dir, is_fallback, is_first_run)`。该函数内部已兜住全部异常并保证目录真实可用，
   因此调用点不需要再包 try/except。

2. **迁移路径跟随 csv_dir**：
   创建 store 时使用 `AccountStore(legacy_csv_path=csv_dir / "account.csv")`，
   显式**不传 `path`**——数据库路径按需求 3.11 固定为 `config.DB_PATH`，不接受配置覆盖。

3. **启动回退提示**：
   弹窗时机放在主窗口创建之后、`app.mainloop()` 之前（先有窗口再提示，观感是「程序已启动，顺便提醒」）。
   判断条件为 `is_fallback and not is_first_run`，使用 `messagebox.showwarning`，
   文案「之前设置的 CSV 导出目录不可用，已回退到默认目录。」；
   `is_first_run=True`（首次运行、从未配置过）属正常情况，静默处理、不弹窗。

4. **AccountKeeperApp 构造函数新增可选参数**：
   `__init__(self, store, csv_dir: Path = DEFAULT_CSV_DIR, settings_path: Path = SETTINGS_PATH)`，
   两者均带默认值（取自 `config` 常量），保存为 `self.csv_dir` / `self.settings_path`。
   用可选参数而非必填：既保证启动流程能注入配置，也不破坏「单独构造窗口」等现有调用方式，
   且默认值直接引用常量，避免在 UI 层再写一份回退逻辑。

5. **结论：#12.2 正式完成**，已用 `tempfile` 隔离环境验证三种启动场景
   （首次运行 → 不弹窗；配置损坏 → 弹窗；正常配置 → 不弹窗且迁移路径跟随 csv_dir），
   并确认 `AccountStore` 在临时库上构造正常、`DB_PATH` 未被覆盖。
   本次未修改 `store.py`、`settings.py`、`config.py`、`widgets.py`、`dialogs.py`、`snail.py`、`chart_window.py`。

6. **剩余待办**：#12.3（设置界面对话框）、#12.4（export_csv 使用 csv_dir）按计划分步实施。

---

## 方案变更说明：csv_dir → last_export_dir（2026-09-26）

> 本节是 v0.1.2 的最新结论；上文 Step 1.5 与 Step 2 两节描述的 csv_dir 方案**已被本节取代**，仅作历史记录保留。

### 为什么改（原方案太重）
旧方案要求用户「自定义 CSV 导出目录」，为此需要一整套机制：设置界面、`is_fallback` / `is_first_run` 两个信号、
启动弹窗提示「你配置的目录不能用了」。以实际使用场景衡量，性价比不成立：

- 用户真正的痛点只是「每次导出都要重新点选一遍目录」，而不是「我要长期固定一个导出目录」；
- 为一个便利功能引入了「配置错了要弹窗」这种让普通用户困惑的概念（用户并不理解 csv_dir 是什么）；
- 设置界面 + 回退弹窗 + 双布尔返回值 + 只有一项的表单，实现与维护成本远高于收益。

### 新方案（轻量）
`settings.json` 只存一个字段 `last_export_dir`：**记住用户上次导出时实际选的目录**。

- 没有设置界面，没有「存储设置」按钮，没有 `CTkToplevel` 对话框；
- 启动只读一次，读不到或当前不可用就静默用 `DATA_DIR`，**不弹窗**；
- 导出时用它作 `initialdir`；导出成功后把用户实际所选的目录写回配置。

### 本次改了什么
1. `settings.py`
   - `get_effective_csv_dir()` → `get_last_export_dir()`，返回值由 `(Path, is_fallback, is_first_run)` 收敛为 `(Path, has_saved_dir)`；
   - `save_settings(csv_dir)` → `save_settings(last_export_dir)`，JSON 字段名改为 `last_export_dir`（由模块常量 `SETTINGS_KEY` 统一，避免读写两处各写一遍字符串）；
   - 目录判定拆成语义清晰的两个辅助函数：`_is_usable()`（只读探测，回答「上次那个目录现在还认不认得」，**刻意不创建目录**）与 `_prepare_directory()`（写配置前 mkdir 并确认可写）；
   - 删除 `_parse_csv_dir` / `_ensure_usable` 及 `is_fallback` / `is_first_run` 的全部说明与分支。
2. `account_keeper.py`（**最小改动，已与开发者确认**）
   - import 与调用改为 `get_last_export_dir()`，解包由三元组改为二元组；
   - 删除启动回退弹窗（`if is_fallback and not is_first_run:` 整段）——新方案没有需要告知用户的「回退」；
   - `AccountKeeperApp(...)` 调用改用 `last_export_dir=`；因设置界面已取消，`settings_path=` 失去唯一消费者，一并移除（连带删掉已无用的 `from config import SETTINGS_PATH`）。
3. `ui.py`
   - `AccountKeeperApp.__init__` 的 `csv_dir` / `settings_path` 两个参数收敛为 `last_export_dir: Path = DEFAULT_CSV_DIR`；新增 `import settings`；
   - `export_csv` 增加 `initialdir=str(self.last_export_dir)`（**即原先的 #15**），并在导出成功后调用 `settings.save_settings()` 写回实际所选目录、同步内存值；写回失败只打控制台警告，不影响已完成的导出。
4. `widgets.py` / `dialogs.py`：**本次未改动**。核对后确认 Step 3 的「存储设置」按钮、`settings_callback`、`ask_storage_settings()`、`open_settings()` 实际都还没写进代码，因此「撤回」动作为空。
5. 文档同步
   - `requirements.md`：§2（存储/配置）、§3.1（account.csv 说明）、§3.6（initialdir 与写回）、§3.11（整节重写为「记住上次导出路径」）；
   - `issues.txt`：#12 / #12.3 / #12.4 移入新增的「搁置」区；新增 `[已修复] #12'`；#15 与 #12.2 一并并入 #12'；头部编号变更说明同步。

### 影响与遗留
- 数据库路径不受影响，仍固定 `config.DB_PATH`（`DATA_DIR / "account.db"`）。
- 旧 CSV 迁移路径仍沿用 `last_export_dir / "account.csv"`（保持 Step 2 的既有形状）。**语义上略有别扭**：「上次导出目录」与「历史 account.csv 所在目录」本无关系，保留只为避免扩大改动面；若要改回 `config.CSV_PATH`（即 `DATA_DIR / "account.csv"`），只需改 `account_keeper.py` 一行。
- `config.py` 不在本次允许修改的清单内，其 `DEFAULT_CSV_DIR` 常量名与注释维持原样（语义仍成立：它是「记录缺失/失效时」的回退目标；常量名沿用是为了不扩大改动面）。
- 工具栏「打开数据目录」文案仍未改为「打开默认数据目录」（需求 3.11 约束项），属独立小尾巴，未纳入本次改动。
- 本次全部改动仍遵守 `PROJECT_RULES.md`：未使用终端命令操作文件、未在聊天框输出完整文件、关键逻辑均带中文注释解释「为什么」。

---

## 收尾说明：删除 legacy CSV 迁移逻辑（2026-09-26）

### 为什么删
- 项目暂无真实用户从旧版本升级，`_migrate_legacy_csv` 属于「为不存在的场景写的代码」：每次启动都要判一次文件存在性、读一次表头、再决定是否导入。它带来的阅读成本与出错面（表头校验、逐行校验、任一行非法就整体放弃）远大于实际收益，是纯粹的负债。
- 它还与 `last_export_dir` 形成了语义上别扭的绑定（见上一节遗留说明）：把「上次导出目录」当成「历史 `account.csv` 所在目录」去找数据，本就是凑合出来的形状；删除后这层概念纠缠随之消失。

### 本次改了什么
1. `store.py`
   - 删除 `_migrate_legacy_csv()` 整个方法，以及 `__init__` 中对它的调用；
   - `AccountStore.__init__` 简化为 `(self, path: Path = DB_PATH)`，不再接收 `legacy_csv_path`，也不再保存 `self.legacy_csv_path`；
   - 删除只服务于迁移的 `from datetime import datetime`（`strptime` 是它在此文件的唯一用途）；
   - **保留** `import csv` 与 `CSV_FIELDS`：`export_month_csv` 仍要用它们写表头，已在注释里注明用途；
   - **保留** `path` 参数：数据库位置已固定为 `config.DB_PATH`，该参数现仅作为测试注入临时库的接口。
2. `config.py`
   - 删除常量 `CSV_PATH`（删除迁移后已无任何引用）；
   - `DEFAULT_CSV_DIR` 改名为 `DEFAULT_EXPORT_DIR`，注释改为「无 last_export_dir 时使用的回退目录」；
   - 顺手修掉 `DB_PATH` 上方仍写着「可配置项收窄为仅 csv_dir」的过时注释。
3. `settings.py`：同步引用新常量名 `DEFAULT_EXPORT_DIR`（代码与文档串一并更新）。
4. `ui.py`：**仅两处**随之改名（import 行与 `last_export_dir` 的默认值）——该常量在 UI 层被用作默认回退目录，不改会直接 `ImportError`；除此之外 `ui.py` 未动一行（已单独向开发者确认）。
5. `account_keeper.py`：创建 store 时改为 `AccountStore()`，不再传 `legacy_csv_path`。
6. `requirements.md`：删除 §3.1 的 `account.csv` 存放描述；删除 §3.11「数据迁移说明」整节；删除 §3.11 约束里基于旧方案的「打开数据目录」文案条目；§3.11 只保留 `last_export_dir` 的读取、回退、导出时使用、导出成功后写回；常量名同步为 `DEFAULT_EXPORT_DIR`。
7. `issues.txt`：#40 记入已修复历史，顶部「编号变更」区追加一行。
8. 验证：`get_errors` 对 `config.py` / `settings.py` / `store.py` / `account_keeper.py` / `ui.py` 全部无错误；`store.py` 内 `csv` / `CSV_FIELDS` / `InvalidOperation` 均已确认仍有其它用途后保留。

### 与上文的冲突说明
- 本文档上文出现的 `DEFAULT_CSV_DIR`、`CSV_PATH`、`legacy_csv_path`，以及「旧 CSV 迁移路径沿用 `last_export_dir`」等描述，**自本节起不再适用**；保留原文仅为追溯当时决策，不做回改。
- 其中「`config.py` 不在允许修改清单内，其 `DEFAULT_CSV_DIR` 常量名与注释维持原样」一条已被本节第 2 点取代。

---

## 蜗牛焦点暂停、启动入口与右键彩蛋说明（2026-09-26）

### #30 复现结论（推翻上文「未能复现」的判断）
- 真实成因：`_animate_snail()` 把 `self.master.winfo_ismapped()` 当作「窗口是否可用」的判据。按下 Alt（或 Alt+Tab 切走）时 Tk 会短暂认为主窗口未映射，动画定时器据此提前 `return`，蜗牛就停在原地不再动，表现为「按 Alt 后蜗牛卡死」。
- 修复：`_animate_snail()` 删除 `winfo_ismapped()` 判断，只用 `if self.master.winfo_width() <= 1:` 作为「窗口尚未完成布局」的重试条件。
  - 注意：这个判据单独用是不够的 —— 未显示的窗口 `winfo_width()` 返回的是 Tk 默认的 **200** 而不是 1，于是首帧就把它当真实宽度，蜗牛改从窗口偏左处入场。后续已用 `_window_shown`（由 `<Map>` 事件置位）替换，详见下一节。
- 暂停/恢复改由窗口焦点事件驱动：`<FocusOut>` 暂停、`<FocusIn>` 恢复。用焦点事件而不是键盘事件判断，才不会把 Alt 这类系统按键误当成「离开窗口」。
- 收口补丁：`<FocusIn>` 不再无条件恢复动画，只有 `_bubble_canvas is None`（没有活动气泡）时才恢复；`_destroy_active_bubble()` 也只在窗口持有焦点（`_window_focused`）时才恢复。否则「点开气泡 → 切走 → 切回」或「切走后气泡到点自动关闭」都会让蜗牛在气泡还挂着 / 窗口在后台时继续爬行。

### 启动入口位置回归修复（由 #30 的修复引入）
- 现象：软件启动时蜗牛不是从窗口最右侧出发，而是从窗口偏左的某个固定位置冒出来。
- 真实成因：`SnailManager.start()` 是在主窗口 `geometry()` 之后、但窗口还没真正显示时被调用的，此时 `winfo_width()` 返回 Tk 默认的 **200**（不是 1），`winfo_ismapped()` 为 0。删掉 `winfo_ismapped()` 判断后，`winfo_width() <= 1` 这个唯一的「等窗口就绪」判据在启动阶段失效：首帧动画就在 width=200 下把入口定在 `x=200` 并置 `snail_started = True`；等 `<Map>` 到达、真实宽度变成 1200 时，因为已经 `snail_started` 而不再回到最右侧，蜗牛就从 x=200 一路向左爬。
- 修复：新增「只在启动阶段判一次」的 `_window_shown` 状态（由 `<Map>` 事件置位），与 Alt 无关，因此不会让 #30 的卡死回归。
  - `start()`：移除内联的右侧摆放，改为绑定 `self.master.bind("<Map>", self._on_window_shown)`；保留「窗口早已显示、之后才创建蜗牛」的分支（该分支同时置 `_window_shown = True`）。
  - 新增 `_on_window_shown()`：`<Map>` 到达时置 `_window_shown = True`，且仅在尚未入场时按真实宽度摆到最右侧。从最小化恢复同样会触发 `<Map>`，靠 `snail_started` 防止重摆。
  - 新增 `_enter_from_right(window_width)`：把「摆到最右侧」抽成一个方法，宽度 ≤ 1 时直接返回，留给下一帧重试。
  - `_animate_snail()`：重试判据改为 `if not self._window_shown or self.master.winfo_width() <= 1:`，首帧分支改用 `_enter_from_right()`。
- 验证（真实启动序列，内联脚本未落盘）：首帧 `winfo_width=200`、`ismapped=0` → 正确跳过不摆放；`<Map>` 后 `winfo_width=1200` → `snail_x` 从 1200 开始逐帧 −2px 左移，`snail_label.winfo_x()` 与 `snail_x` 一致。彩蛋 #41 回归 4/4 通过（传送落点在路线区间内、气泡期间静止、关闭后恢复爬行、左键随机文案不受影响、爬出左边界后重新从最右侧入场）。

### #41 右键彩蛋
- 行为：在蜗牛上右键（`<Button-3>`），蜗牛随机传送到行进路线上的某一点，并弹出气泡「彩蛋：诶~我躲」；气泡 3 秒后自动关闭或点击立即关闭，之后蜗牛从新位置继续爬行。
- 「行进路线」的定义与动画保持一致：可停留的横向区间 = 窗口宽度内减去一个蜗牛身位，并挖掉与标题控件重叠的那一段（左侧一段 + 标题右侧到窗口右缘一段，按区间长度加权随机取点）。这样传送后蜗牛既不会压住标题，也不会露出窗口外。
- 文案常量 `SNAIL_EASTER_EGG_MESSAGE` 放在 `snail.py` 而不是 `snail_messages.json`：后者是左键随机抽取的扁平列表，把这句塞进去会按比例出现在普通左键点击里，彩蛋就不再是彩蛋了。
- 一个必须注意的 Tk 细节：`place_configure()` 不会立刻更新 `winfo_x()`（实测需一次 idle 周期），而气泡是按 `snail_label.winfo_x()` 定位的。因此 `_teleport_snail()` 末尾显式调用 `self.master.update_idletasks()`，否则气泡会挂在蜗牛传送前的位置。
- 另一处细节：传送后把 `snail_started` 置为 `True`，否则 `_animate_snail()` 的首帧会按「从右侧爬进来」重置坐标，把传送结果冲掉。

### 本次改了什么
1. `snail.py`（唯一改动的代码文件）
   - 模块级新增常量 `SNAIL_EASTER_EGG_MESSAGE`；
   - `_animate_snail()`：删除 `winfo_ismapped()` 判断；
   - `start()`：新增 `<FocusOut>` / `<FocusIn>` / `<Button-3>` 三个绑定；
   - 新增 `_on_focus_out()` / `_on_focus_in()` / `_snail_right_clicked()` / `_snail_route_segments()` / `_teleport_snail()`；
   - 启动入口回归修复：新增 `_window_shown` 状态与 `_on_window_shown()` / `_enter_from_right()`，`start()` 改绑 `<Map>`，`_animate_snail()` 重试判据改为 `not self._window_shown or winfo_width() <= 1`；
   - `_destroy_active_bubble()`：恢复条件加上 `self._window_focused`。
2. `issues.txt`：#41 移出 P3 并记入已修复历史；顶部「编号变更」区那行改为「现已实现（右键传送）」；另追加 #30 回归修复记录（已修复历史新增一条 + 顶部编号变更区补一行说明）。
3. 未改动 `snail_messages.json`（原因见上），也未触碰 `ui.py` / `config.py` / `settings.py` / `store.py` / `dialogs.py` / `widgets.py` / `chart_window.py` / `account_keeper.py`。
4. 验证：`ast.parse` 语法检查通过；用真实 Tk 实例跑过冒烟测试（内联脚本，未落盘）——路线区间计算正确（示例 `[(0, 102), (475, 1077)]`）、连续传送后 `winfo_x()` 与 `snail_x` 一致、右键后气泡生成且居中于蜗牛新位置、气泡关闭后动画恢复、左键随机文案不受影响、传送后动画从新位置继续左移（未被首帧重置）。

### 与上文的冲突说明
- 本文档「附：精简清单中新增的两项」中 #30 一条的「本次审计未能在代码中复现该行为」结论已被本节推翻；保留原文仅为追溯当时判断。

---

## 2026-09-29 从 _issues.txt 头部迁移，原样搬运未改一字

> 状态标记：[ ] 未完成 ｜ [x] / [已修复] 已完成 ｜ [搁置] 明确不修但保留记录
> 详细审计说明（详情 / 影响 / 建议）统一见 `audit.md`（不带版本号、跨版本累积）
> 编号变更：#28（日期选择器）并入 #3 待办子任务；#10（添加设置？）并入 #12；
> 已修复历史原本分两批、编号互相重复，此处统一重编为 #31 起。
> #12 方案变更（2026-09-26）：「用户自定义 csv_dir + 设置界面 + 启动回退弹窗」过重，
> 已改用轻方案 #12'：settings.json 只记 last_export_dir，无设置界面、无回退弹窗；详见 audit.md 方案变更一节。
> #40 已解决：删除 legacy CSV 迁移逻辑，AccountStore 简化为只接收 path 参数。
> #30 拆分：#30 修复 Alt 卡死已解决；彩蛋部分拆分为 #41，现已实现（右键传送）。
> #30 回归修复：删掉 winfo_ismapped() 后启动入口一度摆错（未显示的窗口 winfo_width() 谎报 200），已改为等 <Map> 事件再摆入口。
> #3 拆分：#3.1 日期选择器；#3.2 月份选择器（已完成，2026-09-29 记录见下）。
> 2026-09-26：#3.1 完成，输入区改为两行布局（日期/金额一行，类别/备注/按钮一行）。
> 2026-09-26：#3.1 布局再调整——输入区由「固定宽度 + 右侧留白」改为「按 grid 列权重 15:25 比例自适应」，仅图标型控件保持固定宽度（记录为 #44）。
> #43 新增：类别字段计划添加 ▼ 选择器（从历史记录提取已有类别），本版本不实现。
> 2026-09-26：#45 完成，删除工具栏汇总标签（该标签当初由 #39 补充过总记录数，现已随需求变更整体移除），
> 同时把工具栏按钮由 pack 固定宽度改为 grid 列权重等分。
> #43 完成（方案变更）：类别字段改为「CTkEntry + ▼ 按钮」组合（原计划用 CTkComboBox，因其原生下拉列表位置偏移无法修正而放弃），点 ▼ 弹出 category_picker.ask_category 类别列表（CTkToplevel + 一列可点击项 + 底部取消），候选 = 预置类别 + 历史用过的类别，用户输入的新类别下次自动出现。
> 2026-09-27：#46 完成，类别弹窗支持 toggle（再点 ▼ 关闭）、点击外部关闭、ESC 关闭；为支持 toggle 刻意去掉 grab_set，代价是外部点击会穿透到被点控件；
> 编辑弹窗改为把自身的 grab 临时借给类别弹窗、关闭时归还，以保持编辑弹窗的模态性。
> 2026-09-27：#48 完成（小步 1），新增 category_prefs.py：候选来源由「预置类别 + 数据库历史」切换为「预置类别 + 用户保存的类别（user）− 已隐藏的类别（hidden）」，由 category_prefs.build_candidates() 现算、不缓存；
> 新增用户类别偏好文件 categories.json（config.CATEGORY_PREFS_PATH，与 account.db 同目录）；历史类别改由启动时的一次性迁移 category_prefs.ensure_migrated() 快照进 user 段，此后不再直接读数据库；store.py / settings.py 零改动。
> 2026-09-27：#49 完成，删除类别弹窗底部冗余的「取消」按钮（关闭已有 toggle / 点外面 / ESC / 点选项 / 右上角 × 五条路，取消按钮与它们完全重复），footer 区域整体移除、弹窗高度 −40 逻辑像素、宽度不变；
> 并让唤起弹窗的 ▼ 图标跟随开合在 ▲ / ▼ 之间切换，还原动作统一放在 category_picker._cleanup()，覆盖全部关闭路径（含父窗口连带销毁）。
> 2026-09-27：#50 完成，修掉 category_picker._reanchor() 只守 dialog、没守 anchor 导致的 TclError: bad window path name（父窗口被销毁时锚点先于弹窗死去，回调却还挂在锚点所在顶层窗口的 <Configure> 上）。
> 2026-09-27：#51 完成（小步 2），类别弹窗把底部操作区加回来，并首次把四类动作做进弹窗：新增「+ 保存」把类别框里的内容写进 categories.json 的 user 段，列表项右侧新增「×」把该项加进 hidden、新增「←」把它从 hidden 恢复，另有「已隐藏 (n) / ← 返回」在候选列表与已隐藏视图之间切换；ask_category 新增关键字形参 direction（无默认值，强制调用点声明收/支，避免静默写错另一份列表）；弹窗内四个动作一律「落盘后原地重画列表、不关弹窗」，写盘失败复用 footer 提示语变红而不是弹 messagebox（messagebox 会被「点弹窗外就关」的监视器当成外部点击、反而先把列表关掉）。
> 2026-09-27：#53 完成，上面 #51 里的隐藏机制已废弃，改为直接删除（hidden / 已隐藏视图 / 恢复 全部移除）。
> 2026-09-27：#52 完成，#51 里「+ 保存」的取值口径改掉：原设计是「读主窗口类别输入框此刻的内容」，要求用户「先在主窗口打字 → 再点 ▼ → 再点保存」，流程绕且容易误会成「打了字就存下了」；现改为 footer 第一行自带一个输入框（新建时预填 current、回车等价于点保存），保存只认这个框里的文字，成功后清空、失败保留；「已隐藏 (n) / ← 返回」让到提示语那一行右端，footer 高度 68px 与弹窗高度公式均不变。
> 2026-09-27：#53 完成（小步 3），类别管理机制简化：#51 引入的 hidden 层整个废弃（含「已隐藏视图 / 恢复」），categories.json 只剩 {"version":1, "expense":[...], "income":[...]} 两段裸列表，user 是唯一真源；写入接口由 save_user / hide / restore 收敛为 save_user / delete_user；build_candidates(direction) 返回 list[str]（不再是二元组）；预置类别只在首次启动时作为 user 初值写入一次，之后 config 里的 DEFAULT_*_CATEGORIES 不再参与运算（否则删掉预置项下次又回来）；ensure_migrated 签名不变但语义改为「初始化 user」；容错从「格式不符则拒绝写入」改为「当空处理并允许写入」；footer 由「两行提示语 + 右端已隐藏按钮」改为「单行提示语」，高度 68 → 50；ask_category 删掉已无用的 values 形参，两个调用点同步改为关键字传参。
> 2026-09-27：#54 完成，修掉类别弹窗「底部提示语被压扁、候选多时最后一行还露不全」——根因是 footer 与列表区两处尺寸口径不一致：CTkLabel 不给 height 时一律按 28 要空间、而 _FOOTER_HEIGHT 里这一行只预算了 18；CTkScrollableFrame 额外占 2 × 圆角半径（16px）、而两条高度公式都只按视口高算。叠加 pack 的规则（空间不够时被压的是最后 pack 的那个、expand=True 只分配剩余空间不负责压缩），亏空全落在 footer 上、footer 内部又继续压它最后的 hint_label。修法 1 反转 pack 顺序（footer 先落地，亏空转嫁给可滚动的列表区），修法 2 对齐口径（hint_label 补 height=_HINT_ROW_HEIGHT、创建时与 _resize_dialog 两条公式补 + 2 * _LIST_CORNER_RADIUS）。
> 2026-09-27：#55 完成，类别列表支持置顶：列表项右侧在「×」**左边**新增「顶」按钮（汉字 U+9876，YaHei UI 自带字形 0x0bbd），点它把该项移到 user 列表最前（category_prefs.move_to_top，先摘下再插到最前、不是与第 0 位交换，所以其余项相对顺序不变）；第一行的「顶」置灰禁用并显式给 text_color_disabled（主题默认 gray74 比正常态还深、语义相反）；置顶后列表滚回顶部、保存成功后列表滚到底；置顶成功不给文字反馈（列表变化即反馈），失败才报红字；动作按钮的 pack 顺序必须是「× → 顶 → 名字」，同侧先 pack 的占最外端（× 保持最右），顺序反了最窄窗口下被裁掉的就是 ×。
> 2026-09-27：#56 完成（卡顿优化的**第一批：局部优化**）：类别弹窗开窗耗时未变（80.1 → 82.3ms），但交互卡顿基本消除——「点顶」168.8 → **28.9ms**、「点×」75.2 → **37.7ms**；三项改动为「_scroll_list 挪 after_idle + 行数算式替代 bbox("all")」「置顶/删除改为列表局部更新（新增 _row_refs / _set_pin_state）」
> 「iconbitmap / protocol / <Escape> / 全局点击监视器 延后到首次 after_idle」；日历侧只做了同样的延后，实测几乎无变化（日历本来就没有自有的同步 flush），真正提速要等 #57 的弹窗实例复用。
> 2026-09-27：#57 第一步（Step 2.1）完成，category_picker.py 与 calendar_picker.py 两处创建弹窗时改用 tk.Wm.resizable(dialog, False, False)，绕开 CTk 覆写里那笔 after(10, _windows_set_titlebar_color)（实测省 12.5ms sync / 34.8ms visible）；代价是失去 _last_resizable_args 记录，但该字段 CTk 内部没有读取点，两个弹窗也无 resize 需求。
> 2026-09-27：#57 第二步（Step 2.2a）完成，只动 calendar_picker.py（category_picker.py 一行未动），把日历弹窗的实例复用做掉：355 → 516 行。做法是「懒创建 + 构造即显示」（建完就藏会被 CTk 构造期安排的 after(5, _revert_withdraw_...) 原样撤销）、窗口级引用进 _win、打开级状态进 _ses 且回调一律现读（不靠闭包捕获，日期格子因此不会串月）、wait_window 换成 wait_variable + _wake()（关闭 = 隐藏时 wait_window 永不返回）、_cleanup 里显式 grab_release（withdraw 不释放 grab）、<Configure> 用 tk.Misc.bind 拿 funcid 以便精确解绑、渲染失败与用户取消走同一条路（隐藏 + 返回 None，复用后不能再 destroy）。
> 2026-09-27：#57 第二步补铉 (b) 完成，复用打开时先在 deiconify 之前用「上一次显示量到的尺寸」预置中心坐标，窗口第一帧就落在中心，消掉「先在用户上次拖到的位置露一帧再跳回来」那段错位帧（实测 29~46ms）；尺寸取自 _win.last_size（_close 时窗口还可见，winfo_width() 可信），首次打开没有该值、仍走原路径。落地前已用只读探针确证两件事：预置坐标不会重入 CTkToplevel 的 titlebar 状态机（全程 3 个调用点只命中首次创建那一次），落位后也没有第二次异步移动。
> 2026-09-27：探针附带修正一个旧结论——复用打开时的错位帧从「90~110ms」修正为 **29~46ms**，旧值来自采样点落在 ask_date 同步段之外的粗粒度 tracer，新值由直接挂在 dialog 上的 <Configure> 给出（首次尺寸/位置事件 → 落到中心的那个事件）。
> #58 新增（用户新需求，2026-09-28）：把「类别」改造为「多标签」（标签替代类别，非新增维度）；
> 本轮只落需求文档（requirements.md §3.14）与标记【待实现】，不写任何代码，详细规格以 §3.14 为准。
> 2026-09-28：#57 第二步补铉（A1：首次打开预置坐标）完成。根因：`dialog.geometry("320x380")`
> 只给尺寸不给坐标，坐标由 Win32 按 CW_USEDEFAULT 随机分配（实测 32/96/192/224，跨进程都不一致），
> 首次打开会先在错误位置露一帧，再等到 +85ms 才被摆回中心。修法：建窗时紧接 `geometry(...)`
> 之后也预置一次坐标（尺寸只能由「逻辑常量 × 当前 DPI 缩放」算，未映射时 winfo_width() 只会谎报 200）。
> 落地后探针实测：MAP 首帧即目标位置，全程零位移。
> 2026-09-28：#57 第二步（定位方式改动）完成，日历弹窗由「屏幕居中」改为「贴在日期输入框下方」
> （对齐全项目既有惯例：类别 ▼ 弹窗也是贴输入框，而不是飘在屏幕中央）：新增 `_physical_size()`
> 与 `_anchor_to_input()`，`_center_window()` 降为「没有可用 anchor 时」的回退；`ask_date` 加
> `anchor` 形参，widgets.py / dialogs.py 两个 ▼ 调用点传入各自的日期输入框。与 category_picker._reanchor
> 刻意保留四处差异：宽度不跟 anchor（7 列网格不能变窄）、不绑 anchor 的 <Configure> 也不起 50ms 轮询
> （用户必须先点 ▼，anchor 必然已完成布局，测得的 rootx/rooty 可信）、正因不绑事件所以用户把日历
> 拖走后**永远不会被吸回去**（日历是带 grab 的模态窗，用户可能想挪开看主窗口数字，这是有意行为）、
> anchor 失效时退回屏幕居中而不是静默 return 干等。另：换月导致行数变化时，`_recenter()` 仍按 anchor
> 重算（而不是屏幕中心），所以变矮后会自己重新贴回输入框下方。5 场景只读探针（_probe/probe_anchor.py）
> 实测首帧即落位：贴左下角、跨窗口坐标、贴屏底向上翻转、贴屏右缘横向钳制、拖动后复用打开归位。
> 2026-09-29：#22 完成，export_csv 的「该月有无记录」守卫补上结尾的 "-"（原 startswith(month) 会把 2024-010 这类脏数据算进 2024-01），
> 与 store.export_month_csv 的 LIKE 'YYYY-MM-%'、show_stats 的 startswith(month + "-") 统一口径；短路口径当时由 dialogs.ask_month 的 re.fullmatch 保证 month 恒为 YYYY-MM。
> （补注：同日完成的 #3.2 把月份输入改成月历点选，那个 re.fullmatch 随之删除——这个安全性前提已转移到 calendar_picker.ask_month，见下方 #3.2 与 #22 条目。）
> 2026-09-29：#21 完成，chart_window._render_chart_window 的 try 范围由「导入链」扩到「FigureCanvasTkAgg 挂载完成」，
> 新增 except Exception 分支（matplotlib 已装但 TkAgg 后端加载不了时抛 TclError/ValueError/RuntimeError 等，只捕 ImportError 兜不住），
> 加 chart_window 哨兵以便异常时销毁可能已建出的空窗，close_chart / protocol 留在 try 之外、原样不动。
> 2026-09-29：#27 完成，删除确认正文改为需求 §3.5 原文「确认删除该记录吗？」；同时把 requirements.md §3.5 的「使用 GUI 原生弹窗」
> 改为反映现状的「使用自定义 CTkToplevel 弹窗（理由：原生弹窗无法定制配色、无法标红危险按钮，Windows 原生弹窗还会自动显示 Y/N 快捷键字母，与中文界面不搭）」，本条不新增 issue。
> 2026-09-29：#47 定性修正（代码零改动，本轮唯一非修复项）。store.get_categories() 不是死代码——ui.py:97 是无条件生产调用、每次启动都跑，
> 只是 ensure_migrated 第一行 CATEGORY_PREFS_PATH.exists() 就早退、参数被丢弃；「一次性迁移依赖 + 每次启动的无效查询」才是准确描述。
> 删掉会让升级用户（或清理过 categories.json 的用户）的历史类别不可逆丢失，故本轮不动，留到 #58 的 get_tags() 改造一起结掉；仅同步 store.py 那段过时的 docstring。
> 2026-09-29：#3.2 完成，月份字段由「手输 YYYY-MM 的输入框」改为「月历点选」（4 列 × 3 行共 12 个「n 月」格子、
> 标题只显示年、◀ / ▶ 翻年、底部左键由「今日」变为「本月」、弹窗高度逻辑 320×300）；统计 / 导出 / 图表三处调用点统一，
> 项目里不再有任何手输 YYYY-MM 的入口。做法是把 ask_date 的骨架抽成 _begin_session / _finish_session 复用，
> 日期侧原有回调一行未改；两种模式各持一份状态（state = 日期模式的年 + 月 + 选中日，month_state = 月份模式的年 + 选中月）。
> 顺带记录两点现状：(1) 三处月份弹窗都不传 anchor、固定屏幕居中（工具栏按钮无控件引用），补齐见新增的 #60；
> (2) #22 的安全性前提随本条转移，见下方 #22 条目。只读探针同步扩到 9 场景
> （原 5 个日期场景 + 月份居中 / 翻年不跑位 / 跨模式 日期→月份 / 跨模式 月份→日期），_probe/probe_anchor.py 实测 9 项全通过。
> 2026-09-29：#17 完成（实际 4 处硬编码全改，不止 issues 原文的 2 处）、
> #20 完成（图表窗口补 logo 图标，自带内层 try/except）。
> 2026-09-29：#58 第一步（Step 1：数据层地基）完成——**本条不算修复**，按 §3.14.5 拆的 4 步里，本步只做了步① 的「库结构迁移」一半。
> store.py：新增 `record_tags` 关联表（`(record_id, tag)` 复合主键天然去重）+ `idx_record_tags_tag` 索引（按标签聚合/筛选才不用全表扫）；
> 新增 `_backup_database()`（迁移前 `shutil.copy2` 整库到同目录 `account.db.bak`；路径基于 `self.path` 而非 `config.DB_PATH`，
> 这样测试注入临时库时备份也落在临时目录，不会写到真实数据目录去；刻意不用 `with_suffix(".bak")`，那会得到 `account.bak`）
> 与 `_migrate_to_multi_tag()`（`PRAGMA user_version` 0 → 1 保证只跑一次，用 `>=` 判断门槛以便将来 1 → 2 不误触发）。
> 迁移 SQL 全部 `INSERT OR IGNORE`、只读 `accounts.category` 且 `TRIM` 后非空才产标签（空/纯空白 = 0 标签，对应 Q2）、
> 不改写 `accounts` 任何列，所以整个迁移可回滚；插入与升版本在同一个事务里提交，不会出现「标签写了、版本没升」的半迁移。
> 备份失败即中止迁移并保持 `user_version = 0`（下次启动重试）；全新安装（库文件本次才被 `_connect()` 创建）不备份、版本照样置 1；
> 迁移整段包了 `except sqlite3.Error`，失败只打控制台警告、不抛——`AccountStore()` 是在 `main()` 的 try 里构造的，异常逃逸会让程序直接起不来。
> config.py：只新增 `TAG_PREFS_PATH`（`DATA_DIR / "tags.json"`）；`CATEGORY_PREFS_PATH` / `CSV_FIELDS` / `DEFAULT_*_CATEGORIES` 一行未改（改名项留 Step 2）。
> .gitignore：加 `account.db.bak`（裸 `account.db` 规则匹配不到这个新文件名）。
> 行数：store.py 230 → 333（+103）、config.py 72 → 79（+7）、.gitignore 48 → 51（+3），共 **+113 行 / 0 删除**；
> 既有方法（`load` / `add` / `update` / `delete` / `export_month_csv` / `get_categories`）一行未改，Step 1 落地后程序行为与改造前完全一致。
> 验证：探针 `_probe/probe_58_step1.py`（全程 `tempfile` 隔离、不碰真实 `account.db`）**20 项全通过**——
> 全新安装（建表/索引/版本=1/不留 .bak）、老库迁移（TRIM / 空白不产标签 / `accounts` 原值原样保留 / `.bak` 是「迁移尚未发生」的快照且可整库还原）、
> 幂等（重复打开不变、`.bak` 不被二次覆盖、把版本手改回 0 强制重跑无重复行）、备份失败中止（不抛异常 / 版本仍 0 / 不写任何标签 / `load()` 照常）。
> 附带修正一个方案错误：原打算「把 `account.db.bak` 建成目录」来逼出 `OSError` 是错的（`shutil.copy2` 遇目录会自动改成 `dst/源文件名`、反而成功），
> 探针改为「目录下再放一个同名子目录」才真实抛出 `Permission denied`，仍是真实 `shutil` 抛的真实异常、未打桩。
> 2026-09-29：#61 新增（用户新需求）。当前手动备份 account.db 的体验不友好；因 #58 正在改 schema（tags.json 未落地），本项排到 #58 收尾后的 v0.1.5 版本再做。

---

## #58 多标签改造

[ ] #58 - 【进行中】多标签改造：把「类别」替换为「多标签」，涉及 schema 迁移 / UI 重做 / 图表统计口径 (涉及文件: store.py, ui.py, widgets.py, dialogs.py, chart_window.py, category_prefs.py, config.py, .gitignore, requirements.md)
         **Step 1（数据层地基）、Step 2a（只读侧）、Step 2b（写入侧）、Step 2c-1（1→2 再迁移 + 备份门槛改版）已完成并验收，但改造整体未完成**——按 §3.14.5 拆的 4 步，
         目前做到了步① 的「库结构迁移」与「数据层读写改造」；步① 剩下的收尾（删 B1 回退 + 改公开签名 + config / tag_prefs 改名 + UI 调用点同步）另立为 **Step 2c**，尚未开始：
         store.py 建 `record_tags` 表 + `idx_record_tags_tag` 索引、`_backup_database()`、`_migrate_to_multi_tag()`（user_version 0→1、
         迁移前整库备份到 account.db.bak、备份失败即中止、全新安装不备份、只读 accounts.category 不改写）；config.py 只新增 `TAG_PREFS_PATH`；
         .gitignore 加 `account.db.bak`。行数 store.py 230→333 / config.py 72→79 / .gitignore 48→51（+113 / −0），既有方法一行未改、程序行为不变；
         探针 _probe/probe_58_step1.py（tempfile 隔离）20 项全通过。详见头部 2026-09-29 记录。
         剩余工作：
         ① **Step 2 — 数据层 CRUD + 导出**（读写侧已由 Step 2a / 2b 落地，剩余「改名 + 删回退 + UI 同步」另立 Step 2c）：`load()` 两条 SELECT（accounts + record_tags）后在 Python 按 record_id 归并（不用 JOIN）；
            `add` / `update` 在同一个 `_connect()` 事务里「先 DELETE 该记录旧关联 → 再批量 INSERT」（update 必须整份替换，否则被删掉的标签残留）；
            `delete` 显式 `DELETE FROM record_tags`（不依赖 ON DELETE CASCADE，sqlite3 默认 foreign_keys=OFF）；`export_month_csv` 用「|」连接；
            `get_categories()` → `get_tags() -> list[str]`（顺带结掉 #47）；`Account.category: str` → `Account.tags: tuple[str, ...]`。
            同一步还要做 **json 合并迁移**：categories.json（两段）→ tags.json（单一 tags 列表），触发条件 = tags.json 不存在且 categories.json 存在，
            取并集、`expense` 全部原序 + `income` 中未出现过的原序，**不排序**（顺序就是用户的置顶顺序），旧文件保留不删作回滚依据；
            以及 **config.py 的改名项**（`CSV_FIELDS` 的 category → tags、`DEFAULT_EXPENSE_CATEGORIES` / `DEFAULT_INCOME_CATEGORIES` → 单一 `DEFAULT_TAGS`、
            `CATEGORY_PREFS_PATH` → `TAG_PREFS_PATH`）与 `category_prefs.py` → `tag_prefs.py` 的模块改名（`build_candidates()` → `build_tag_candidates()`、无参数）。
         ② **Step 3 — UI 形态**：widgets.py 类别字段 → 标签区（chips + 输入框 + ▼，含 15:25 网格与卡片高度重算）；dialogs.py 编辑弹窗同上、
            去掉「类别不能为空」校验（**注意**：四.1 里「编辑区金额带符号预填」是刻意例外（#24），与本次改造无关，不要顺手改掉）；
            `category_picker.py` → `tag_picker.py`，单选回填 → 多选切换（可连点、不关窗），`ask_category` → `ask_tags`、删除 `direction` 形参。
         ③ **Step 4 — 图表与搜索口径**：chart_window.py 按标签聚合 + 新增 `未分类` 虚拟桶 + 图内「分别计入」的诚实标注；
            ui.py 的 `filter_records`（搜索覆盖标签）与 `export_csv`（表头 + 「|」连接）。
         ⚠️ 遗留（本步引入、Step 2 前请留意）：`user_version` 一旦置 1 就不再重跑，因此 **Step 1 与 Step 2 之间新增/编辑的记录，其 category 不会进入 record_tags**。
            本步尚未落地 add/update 的标签写入，所以 interval 内的数据需要补齐时，可把库的 `user_version` 手改回 0 再启动一次——迁移幂等，
            探针场景 3d/3e 已实测「版本回退到 0 重跑结果完全一致、无重复行」。另外用户下次启动真实程序时，真实 account.db 会被迁移并在同目录生成 account.db.bak（预期行为）。
            ✅ **这条遗留已由 Step 2c-1 结掉**：1 → 2 的再迁移会在启动时自动补齐，**不要再手改 user_version**（手改会让备份门槛误判、白白多备份一次）；
         ⚠️ 待定项（§3.14.6，实现时再定，不影响本步成立）：迁移后 `add` / `update` 对 `category` 列是「写空串」还是「原值不动」；
            `account.db.bak` 每次迁移都覆盖还是保留带时间戳的多份（本步取「前者」，每次覆盖同一份）。
         〘连带：本条的 `get_tags()` 改造会顺手结掉 #47；标签聚合成第 4 处重复口径、实现时应一并按 #16 抽公共函数；`load` / `update` 事务面变大，按 #19 一并加固。〙
         --- Step 2 再拆两半：**Step 2a（只读侧，本轮）** 与 **Step 2b（写入侧 + 改名）**。Step 2a 只动 store.py，只做读路径，目的是把改造拦住 UI 的那个断点先消化掉、且单独可验收。---
         【Step 2a 三项决策（2026-09-29 确认，实现时直接引用，不再重新论证）】
         **(1) 加 `Account.category` 兼容只读属性（shim）**：`Account.category: str` → `tags: tuple[str, ...]` 会立刻打断 UI 的 6 处 `record.category` 读取
         （ui.py:165/179/374、chart_window.py:52/55、dialogs.py:93，其中 ui.py:165 在启动后的 `_refresh_tree` 里 → 不加 shim 则程序**启动即崩**，Step 2a 无法单独验收，违反 二.2）。
         故新增 `@property def category(self) -> str`，返回 `"、".join(self.tags)`（顿号恰是 §3.14.4 的表格口径，顺带已对齐目标；Step 2a 里每条最多 1 个标签，返回值与改造前逐字符相同）。
         **实现陷阱**：`@dataclass` 上的属性**绝不能带类型注解**——写成 `category: str` 会被 dataclass 当成第 4 个字段、多出一个 `category` 形参并盖掉 property，让 `load()` 的按位置传参错位到 `note`（注释里必须写明）。Step 2b 删该属性时，UI 那 6 处一并改。
         **(2) `load()` 用 B1 回退规则**：`record_tags` 有行就用它；**没有行时回退读 `accounts.category`**（TRIM 后非空则作为唯一标签）。
         理由：Step 2a 里 `add` / `update` 仍只写 `accounts.category`，若严格只读 `record_tags`，则「Step 1 迁移之后新增/编辑的记录」标签全为空、UI 显示空白类别，改造无法与现状对照。
         B1 也正是 §3.14.2 对 `category` 列的定性——「保留、停止写入、**降为 legacy 只读**」。
         **连带定死 §3.14.6 的待定项**：`add` / `update` 对 `category` 列取「**写空串**」（而不是「原值不动」）——否则 Step 2b 删掉 B1 回退后，被编辑成 0 标签的记录会因残留旧 `category` 而把旧标签**复活**。
         **(3) `get_tags()` 不带并集回退**：只读 `record_tags` 的 DISTINCT，不并 `accounts.category` 的非空值。
         理由：`get_tags()` 是「新世界」的接口；其生产用途（Step 2b 的首启迁移候选池）届时读的是同一批数据，而 Step 1→Step 2b 之间新增记录的标签不进候选池是可接受的（未发布、本地数据无保护价值；且 `get_categories()` 仍在跑首启迁移兜底）。
         **字典序决定（DDL 无顺序列的直接结果）**：`record_tags` 的 DDL 只有 `(record_id, tag)`、没有顺序列，故一条记录的标签顺序由 SQL 决定，**展示顺序 = 字典序（`ORDER BY tag`），不是用户打标签的先后顺序**。
         取字典序的理由：确定、可复现、能走 `idx_record_tags_tag`。代价：用户先打「家庭」后打「日用」，列表仍显示「家庭、日用」。
         要保留打字顺序必须给 DDL 加一列并走 `user_version` 1→2 重迁移；§3.14 未要求保留打字顺序，故不为此加列。
         这条会被 Step 3 的 chips 与 Step 4 的图表一并继承，先记在此备案。
         --- Step 2a / 2b 落地记录（2026-09-29 同步；注：提示词里提到的「规则一.8」在 PROJECT_RULES.md 里**不存在**——「一、」只有 1~4 条，
         最接近的是「二、」第 3 条「跟踪文档（_issues.txt / audit.md / requirements.md / PROJECT_RULES.md）的同步修改，无需额外报备」，本次同步据此执行）---

         【Step 2a 落地（只读侧；已提交 b264d8f）】store.py 480 行，git diff --numstat = **+153 / −6**：
         - `Account` 新增 `tags: tuple[str, ...]`（用 tuple 不用 list：records 是全局内存缓存，list 允许某处 append() 静默改掉内存而库里没变）。
         - 新增兼容只读属性 `@property category -> "、".join(tags)`（Step 2c 删除；**切勿加类型注解**——dataclass 会把带注解的类属性当字段，
           写成 `category: str` 就会多出一个形参并盖掉 property，使 `load()` 的按位置传参整体错位）。
         - 新增 `_merge_tags(account_rows, tag_rows)`：两条查询在 Python 里按 record_id 归并（**不用 JOIN**，JOIN 会把多标签记录膨胀成 N 行还得再折叠），
           `load()` 与 `export_month_csv()` 共用这一份口径；内含 **B1 回退**——关联表里有行就以它为准，一行都没有才回退读 `accounts.category`。
         - `load()` 由一条 SELECT 变两条（同一 `_connect()` = 同一事务快照）；逐行 Decimal 容错策略不变。
         - 新增 `get_tags() -> list[str]`（`SELECT DISTINCT tag FROM record_tags ORDER BY tag`，**不带并集回退**，即 2a 决策 3）；本步仍无生产调用点。
         - `export_month_csv` 表头把 `category` 换成 `tags`、多标签用「|」连接（属过渡逻辑，Step 2c 随 `CSV_FIELDS` 一并简化）。
         - `get_categories()` **实现一个字未改**，只补「现状 + 退场倒计时 + 禁止新增调用点」的说明。
         探针 _probe/probe_58_step2a.py 32 项全通过。

         【Step 2b 落地（写入侧；本次改动，待提交）】store.py 480 → **571** 行，git diff --numstat = **+107 / −16（净 +91）**，
         预算 +70 ~ +78、超约 13 行；新增行构成：6 空行 / 30 纯注释 / 71 其它，超支原因 = 规则三.5 强制的「为什么这么做」中文注释 + 三处取舍说明，已报备且被接受（不返工）：
         - 新增 `_normalize_tags(raw_tags) -> tuple[str, ...]`：TRIM → 丢空 → 按首次出现顺序去重；**去重在本步是 no-op**（调用方只包 1 项），
           留作 Step 2c「输入变 N 项」时 `(record_id, tag)` 复合主键的**护栏**（同批出现重复标签会让 executemany 抛 IntegrityError 并回滚整个事务）。
         - 新增 `_replace_tags(connection, record_id, tags)`：先 DELETE 再 executemany INSERT（必须先删后插，否则 update 把标签改少时旧标签会「自己长回来」）；
           **收调用方的 connection**——只有落在同一个事务里，「写 accounts」与「写 record_tags」才原子。
         - `add()` / `update()`：入参 `category` 显式包成 `(category,)` 再当标签写；`accounts.category` 一律写**空串**（落实 2a 决策 2 的连带定死项——
           否则删掉 B1 回退后，被编辑成 0 标签的记录会因残留旧 category 把旧标签**复活**）。
           **公开签名刻意不变**（形参仍叫 category、仍是单个字符串）：`ui.py` 按位置传参，此时若把形参改名 / 改成 tuple，`"餐饮"` 会被静默拆成 `("餐","饮")`
           两个单字标签 —— 不抛异常、不报错、界面看起来还像正常数据，属最难发现的一类静默损坏；改名必须与两个 UI 调用点**同一轮**改（即 Step 2c）。
         - `delete()` 增加「只在主记录真删掉时才清关联」（`if deleted:`）；保留用户口径：删不到的路径上不该有任何写操作，
           且 record_tags 的孤儿行本身就是「数据出了问题」的信号，顺手静默清掉会把信号一起抹掉。
         探针 _probe/probe_58_step2b.py **53 项全通过**（含用 `RAISE(ABORT)` 触发器实测真实回滚，非仅断言）。
         关键实测：Step 2b 之后 `get_categories()` 只能返回空串（category 列已被写空），真正的标签只在 `record_tags` 里、由 `get_tags()` 读出。
         新增**回归红线**（场景 11c / 11d，用手工构造的老库：`accounts.category` 有真值、`record_tags` 无行、user_version=1）：
         「老记录仍能被 `get_categories()` 读到」+「B1 回退让老记录仍能显示出标签」——Step 2c 删回退后必须重跑并保持全绿。

         【Step 2c-1 落地（数据层对齐；本次改动，待提交）】只动 store.py 一个文件，571 → **615** 行（净 **+44**；工作区相对 HEAD 累计 +179 / −44），
         预算 +45、实得 +44、未超。四处改动点：
         - `_SCHEMA_USER_VERSION` 1 → **2**，并把常量头上那段注释整个重写。原注释把常量当「已完成步骤数」，抬高后语义就错了；
           现在写明它是「**目标版本**」（`PRAGMA user_version` 的值只会等于目标版本，不会等于已跑的步骤数），以及
           0→1 与 1→2 做的是同一件事（缺标签的就补），所以共用 `_migrate_to_multi_tag`。同时立了一条【规矩】：
           将来加**破坏性**迁移步骤（会改写 / 删除数据、无法靠重跑自然收敛的）**必须另立函数**。两条理由：① `version >= _SCHEMA_USER_VERSION` 这个门槛
           会把「已完成的老库」直接挡在外面，破坏性步骤会**整段被跳过**；② 本函数是「只读 + 幂等可重跑」的，与「有备份才允许动数据」是配套的，破坏性步骤套进来会把这条护栏冲掉。
         - `_migrate_to_multi_tag` 由「Step 1 建表 + 迁移」改写为「阶段式」三段（同一事务、同一份 `try / except sqlite3.Error`）：
           ① 只读连接取 `PRAGMA user_version` + 统计待补条数；② 待补条数 > 0 才备份、备份失败立刻报错返回；③ 写连接补齐标签并在同一事务里把版本推到目标版本。
         - **备份门槛改版**（Step 2c-1 的核心）：由原先的「库文件是否已存在」改为「**待补条数是否大于 0**」。原方案把「库文件存在」当判据，
           但「文件存在」≠「有历史记录要补」—— 全新库与刚迁完的库都会白留一个空的 account.db.bak 在用户目录里。现在先只读统计，为 0 就跳过备份直接升版本；
           大于 0 才备份，失败则中止并**保持原版本、不写标签**（`load()` 照常读）。连带删掉 `database_existed` 形参与 `__init__` 里那条解释它的注释，
           `_backup_database()` 的 docstring 里补上「调用门槛由 `_migrate_to_multi_tag` 把着」。
         - 待补条数 > 0 时打一条控制台信息（对用户明示「备份落在哪」），不再静默迁移。
         **判定条件必须带 `NOT EXISTS`**（统计与 INSERT 两处都是）：只有「该记录在 record_tags 里**一行都没有**」才补——这与 `_merge_tags` 的 B1 回退条件**完全对齐**，
         也是「迁移前后 `load()` 逐条相等」的来源（B1 只在该记录 0 行时才回退读旧列，两者取并集后结果不变）。
         若漏掉 `NOT EXISTS`，「已有标签 + 旧列值不同」的记录会被**多补一个假标签**。`NULL` 天然被排除（`TRIM(NULL)` 为 NULL，`NULL <> ''` 结果也是 NULL 而非真）。
         注：真实 DDL 里 `category` 是 `NOT NULL`，但库里可能有历史脏值，故判定仍按「TRIM 后非空」写。
         三条预裁决：图表与搜索口径留 Step 3 / Step 4、CSV 不保留 legacy category 列（只改表头与连接符）。
         验收（新增 _probe/probe_58_step2c1.py，tempfile 隔离、不碰真实 account.db）**49 项全通过**，覆盖：新增库建表加版本 2 且不产生 .bak；
         v0 有数据 / v1 有待补数据 → 备份 + 补齐 + 版本 2、**accounts 原值不改写**；v1 无待补数据 → 不备份、版本 2；
         空串 / 纯空白 / NULL 不产标签；幂等（重复构造、版本回退到 1 重跑、版本 5 的库直接跳过，均无重复行、版本不回退）；
         备份失败（v0 与 v1 两条路径：不抛异常、版本不变、不写标签、`load()` 照常）；不变量差分（7 行异质库迁移前后 `load()` 指纹逐条相等）。
         连带同步：`probe_58_step1.py` 的期望版本 1 → 2（含「备份失败中止」场景改用**有数据**的库构造——新门槛下无数据的库根本不会尝试备份，测不到失败分支），
         **23 项**全通过；`probe_58_step2a.py` / `probe_58_step2b.py` 的造库默认版本由写死 1 改为引用 `_SCHEMA_USER_VERSION`——
         常数抬高后「版本 1」已落回迁移门槛之内，不跟上的话 1→2 迁移会跑进探针的 fixture，把旧列值也补进 record_tags，「标签来自写入侧还是迁移」就分不清了（这是常量抬高最容易被漏掉的一处连带影响）。
         重跑：step1 23 / step2a 32 / step2b 53 / step2c1 49 项，**FAIL=0**。

         【Step 2c 待办（步① 收尾，Step 2c-1 已落地、2c-2 及之后尚未开始）】删 `_merge_tags` 的 B1 回退与 `Account.category` shim，并把 UI 那 6 处 `record.category` 读取同步改掉；
         `add` / `update` 形参 `category` → `tags: tuple[str, ...]`（与两个调用点同一轮改）；`config.CSV_FIELDS` 的 `category` → `tags`、删掉 `export_month_csv` 里的运行时改名；
         `DEFAULT_EXPENSE_CATEGORIES` / `DEFAULT_INCOME_CATEGORIES` → 单一 `DEFAULT_TAGS`；`category_prefs.py` → `tag_prefs.py`、`CATEGORY_PREFS_PATH` → `TAG_PREFS_PATH`、
         `build_candidates(direction)` → `build_tag_candidates()`（无参数）、`ensure_migrated(expense, income)` 改为首启以 `store.get_tags()` 播种（含 categories.json → tags.json 的并集迁移、旧文件保留作回滚依据）；
         `get_categories()` 与 ui.py:97 那处调用一并删除（**顺带结掉 #47**）。
         ✅ **前置条件（删回退之前必须先做）已由 Step 2c-1 结掉**：`user_version` 1 → 2 的**再迁移**已落地——Step 1 与 Step 2b 之间落库的记录、以及迁移被跳过的库，
         其标签只存在于 `accounts.category`，现在启动时会被补齐进 `record_tags`（幂等、不改写 accounts、`load()` 结果逐条不变），所以 2c-2 可以放心删回退。
