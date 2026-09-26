# AccountKeeper v0.1.2 审计报告（详细版）

> 生成日期：2026-09-26
> 说明：本文件是 `issues.txt` 中「待修复清单」的详细版本，包含每一项的详情、影响与建议。
> `issues.txt` 只保留精简待办；需要追溯细节时查阅本文件。
> 本次审计为只读审计，未修改任何 `.py` 文件。

---

================ v0.1.2 审计新增（2026-09-26） ================
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
