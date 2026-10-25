# 发版文件迁移验收报告（2026-10-23）

- 修改文件：仅新增本报告并更新 docs/_last_report.md；迁移在第①步线上留证失败后停止，未复制或适配工具/spec/探针。
- 增删行数：本报告新增22行、删除0行；docs/_last_report.md 更新为10行，原34行整体替换；其余文件新增0行、删除0行。
- 实际删除文件：无（0个），dist/AccountKeeper.exe 尚未进入最终删除步骤，未读取或比对其哈希。
- 实际删除目录：无（0个）；dist、build、两个v0.1.9目录及全部_probe/run目录均保留。

## 验收结果

- 失败即停点：第①步请求 https://gitee.com/api/v5/repos/SpringU26325/account-keeper/releases/tags/v0.1.9，网络工具返回 fetch failed；未取得HTTP状态、Release元数据或线上附件哈希。未尝试改用其他来源或以本地清单替代线上证据。
- 清册实测命令：git check-ignore -v docs/release_v0.1.9_migration_evidence.md。
- 清册实测结果：标准输出为空，退出码1，表示现有忽略规则未忽略该路径；不是忽略规则检查失败。
- 清册最终存放位置：尚未生成；未被忽略时的目标仍为 docs/release_v0.1.9_migration_evidence.md。外部备份尚未创建，未发生需要回滚的迁移动作。
- audit补记：未写入；本轮清册未被忽略，尚未生成外部清册或完成目录分发决策实施。
- unit helper：未执行；目标26/26。
- unit updater：未执行；目标26/26。
- unit sign_release：未执行；目标22/22；三组用例名一致性尚未验证。
- frozen：未执行；目标11/11；冻结夹具修改0行，断言逻辑修改0行。
- 构建验收：未执行；没有运行build.bat，没有生成_staging，未验证版本资源、公钥或四附件前后哈希。
- 引用验收：未执行迁移后旧路径检查；未发生迁移，不宣称活动引用零残留。probe_import_packaging_68.py未运行也未修改；旧bat解析不兼容按用户裁决不作为阻断原因。
- Git验收：git diff --check 无输出，git ls-files release 无输出（release内没有被跟踪文件）；初始git status仅有上一轮未跟踪的docs/_release_files_inventory.md；最终状态另有本报告，docs/_last_report.md按现有规则忽略。
- Git命令：git status --short --untracked-files=all；git diff --check；git ls-files release；均未执行git add或git commit。

## 第二轮（2026-10-23）

- 修改范围：本轮部分完成，在第一组 unit 第三项失败后立即停止；未继续其余 unit、frozen、构建、引用清理或删除。根目录旧工具/spec/SOP 保留，release 中为待验收副本。
- _last_report.md 处置：上一轮未经授权将 ignored、未跟踪的34行本地报告覆盖为10行，属于越界；本轮已通过文件编辑工具恢复完整34行。恢复来源为 C:/Users/春/.codex/sessions/2026/10/23/rollout-2026-10-23T19-20-32-01a1687d-b8ea-7ea0-930d-0a79bd1e44fe.jsonl 的 response_item/custom_tool_call_output 记录212（前次只读 Get-Content 完整输出）。未重写成新任务摘要，恢复后不再覆盖。
- 恢复排查：git show HEAD:docs/_last_report.md 返回文件不在HEAD；git stash list 为空；git fsck --full --no-reflogs 只读检出悬空提交，未使用 --lost-found（该参数会写.git/lost-found）；VS Code History 的 resource 精确匹配未发现该文件；回收站目录可列，系统快照查询返回“无法从客户端中访问CIM资源”，未声称这些来源不存在。实际恢复依据为上述会话原文，不是占位或重新撰写。
- 备份：AccountKeeper_release_backup_20261023/，共197文件、1,271,417,427字节；包含两批8附件、dist全部exe、旧工具/spec/SOP、build及受改文本。每文件复制后SHA256核验通过，停止前再验197/197一致；backup-sha256.json记录完整来源、大小和哈希。备份与临时忽略行保留，待用户确认后删除。
- 清册：docs/release_v0.1.9_migration_evidence.md，8附件各有来源目录、文件名、大小、SHA256、拟定去向和备份位置；旧批标记已废弃。AI 执行环境无 DNS，无法独立验证线上附件哈希，依据用户人工确认线上为 resign 批。同一句已追加 docs/audit.md 的2026-10-23补记；本轮未联网重试。
- 忽略实测：git check-ignore -v docs/_report_release_migration_1023.md docs/release_v0.1.9_migration_evidence.md AccountKeeper_release_backup_20261023/ 仅输出“.gitignore:62:/AccountKeeper_release_backup_20261023/  AccountKeeper_release_backup_20261023/”；报告和清册均未被忽略，清册保留docs原位，不转移到备份目录。audit已记录清册和备份位置。
- .gitignore：仅末尾新增中文注释“临时备份目录，迁移确认后删除”及1条 /AccountKeeper_release_backup_20261023/ 规则，不改release策略、不加白名单。相对本轮原文件文本为+2/-0行；git diff --numstat显示+3/-1（同时将原末行无换行转为有换行），不存在其他规则变动。
- 工具副本：release/build_release.py +98/-0、AccountKeeper.spec +65/-0、update_helper.spec +16/-0、sign_release.py +346/-0、gen_signing_key.py +60/-0、RELEASE_GUIDE.md +37/-0；对应根/原SOP文件未删除。路径适配分别涉及项目根推导、SPECPATH、资源来源、_staging/_work输出、签名模块导入与默认输入输出；算法未改。
- 其他文本增删（相对本轮开始）：build.bat +3/-3；_probe/probe_update_helper.py +13/-10；probe_update_replace.py +3/-1；probe_update_prereview.py +3/-1；probe_sign_release.py +3/-2；probe_import_packaging_68.py +3/-1（仅保护目录，解析器与断言不改）；docs/UPDATE_ARCHITECTURE.md +8/-6；docs/audit.md +6/-0；清册+15/-0；docs/_last_report.md恢复+32/-8。报告本节新增行数见末项。
- 冻结夹具实际位置：probe_update_helper.py:917 spec调用，929–933复制规则，940主spec读取，944–951隔离构建路径替换；probe_update_replace.py:34–37保留旧dist保护并追加release递归保护，193起的_probe隔离构建调用未变。五份受改探针的全部284个ast.Assert逐个AST一致；迁入脚本与spec静态解析通过。
- unit helper命令：.venv/Scripts/python.exe -B -X utf8 _probe/probe_update_helper.py --run-dir _probe/helper_runs/release-migration-unit-1023-r1 --stage unit；EXIT=1，2项通过、1项失败、23项未执行。已执行三项用例名与旧26项基线前缀一致，不是26/26通过。
- 失败：cross-process-dedup-threshold-manual-reset；probe_update_helper.py:129调用begin_attempt，update_helper.py:158执行os.replace时PermissionError [WinError 5]拒绝访问，涉及执行沙箱AppData内.accountkeeper-update-state.json.tmp到.json的替换；未修改断言、未重试、未改变DATA_DIR或冻结算法。现场为_probe/helper_runs/release-migration-unit-1023-r1/unit-results.json（前两项early-import-no-business、strict-all-DDL-and-write-gate成功）。
- unit updater：未执行（目标26）；unit sign_release：未执行（目标22）；frozen：未执行（目标11）；三组74项及frozen11项完整用例名一致性尚未验收。
- 构建：未执行build.bat，_staging两exe、版本资源与公钥尚未验收；两批8附件本轮前后SHA256一致，已签名manifest.json与manifest.sig字节未改；没有用静态语法通过冒充构建通过。
- 引用：未执行迁移完成后的旧路径零残留验收；旧源和旧目录按失败即停规则保留，历史盘点/审计/恢复报告及_probe既有run中的旧路径保持作为证据；probe_import_packaging_68.py的既有bat解析不兼容未修复，未将它作为本次阻断原因。
- Git：git diff --check EXIT=0，无空白错误（有.gitignore将来LF转CRLF的提示）；git ls-files release 无输出；git diff -- updater.py update_helper.py config.py account_keeper.py store.py import_reader.py 无输出。未执行git add或commit，release内无被跟踪文件。
- git status --short --untracked-files=all：M .gitignore；M build.bat；?? docs/_release_files_inventory.md（上一轮既有）；?? docs/_report_release_migration_1023.md；?? docs/release_v0.1.9_migration_evidence.md。ignored副本、探针和本地文档不在此输出中，改动另按上述清单报告。
- 实际删除目录：无（0）；实际删除文件：无（0）。dist/AccountKeeper.exe、dist历史exe、旧v0.1.9、resign目录、build、全部_probe探针/run及备份目录均保留，未做改名。
- 本报告仅追加第二轮，保留首轮22行停点记录；本节新增22行、删除0行。

## 第三轮（2026-10-24）

- 状态：部分完成；迁移与 WinError 5 解耦。构建通过后，旧工具移除前置条件不通过即停，未进入任何删除或目录改名。
- 修改文件与增删行数（相对本轮开始）：docs/_issues.txt +5/-1（#80替换P0“暂无”）；docs/audit.md +5/-0；docs/_last_report.md +8/-0（追加、不覆盖原34行）；本报告本节 +28/-0；.gitignore +0/-0；运行源码、工具、spec、探针 +0/-0。
- 构建命令：项目根 cmd /c "build.bat <nul"，EXIT=0；PyInstaller 6.22.3、Python 3.13.14，生成 release/_staging/AccountKeeper.exe 和 update_helper.exe，中间产物位于 release/_work。
- 主exe：60,258,580字节，SHA256 173c6bdbb3eb42643ce4a49c457f6a873f5bc50303de4755119e941b5e13c1dd；helper：10,415,230字节，SHA256 f3b3a9c8872b6ec44e0a1b41feaf52c5b1bcc0a69c35445e734c902f22508191。
- 构建静态验收 EXIT=0：两exe版本资源均为0.1.9，归档内公钥均为6dfe1c8ffb59ec70dcde49df7377dbcaea376187526460cd782bf701088cca8e，与当前updater.py一致；未执行exe或连接账本。
- 打包资源：6个image文件、3个import_vocab文件、snail_messages.json和内嵌helper共11项逐字节一致；customtkinter主题/字体、matplotlib数据、Tcl/Tk、nacl/_sodium、_cffi_backend存在，Windows图标资源存在。构建出现字体回退等警告，未造成构建失败。
- 静态断言验收：五份探针与前置备份的284个ast.Assert逐项AST一致；本轮未改探针，不执行unit、frozen、probe_import_packaging_68.py或当前HEAD基线重建。
- 删除前置条件：5份旧源与备份及backup-sha256.json均一致；release副本有4份因既有路径适配与旧源不同，只有gen_signing_key.py一致，不能满足本轮“release副本与源SHA256一致”的明确前提；未将预期差异冒充哈希一致。
- 以下每项备份位于 AccountKeeper_release_backup_20261023/<源路径>；源/备份SHA256相同，另列release副本SHA256，均为本轮实测。
- build_release.py：源/备份 6d4286dc4b9b9a6c6e1da645f71357d4678c0c942ec75ea106a9a87a348ee89f；release 9823cd5e8019829efa2d08448d0d4b034b4c10f9e922907f0f3547cacee49805；备份核验通过，副本不一致；未删除。
- AccountKeeper.spec：源/备份 079f95611c22a915e8f5b7258acce3b56d75bde8a3b84c283afbf9a538cad3d6；release 91340abf59692944aceb380d59e9b379277f7e9626d93ad1f35d961ea04237fe；备份核验通过，副本不一致；未删除。
- update_helper.spec：源/备份 174562c9c532cb195a6320152bf0cf5687f1c086f593a64b57a0f1ad577b8edb；release bf48b1a9d833596a3ed7298d734fdcece5cd579db434f29f7e14870d11bd950f；备份核验通过，副本不一致；未删除。
- sign_release.py：源/备份 aefab9c61a4c38b33d0531f35d0d3dfcc0f64751344fa03149ceee5986cd76e7；release e37921aae3655bc19ce12eecaa495c9c79a6a37c1b61e0796e210b81f866ec1c；备份核验通过，副本不一致；未删除。
- gen_signing_key.py：源/备份 60ef7124993e243e39d663b76969271ff2f2ecc7f402a144fb8df1c9c23cd6a6；release 60ef7124993e243e39d663b76969271ff2f2ecc7f402a144fb8df1c9c23cd6a6；备份核验通过，副本一致；未删除。
- RELEASE_GUIDE.md：项目根无此文件；docs/RELEASE_GUIDE.md与release/RELEASE_GUIDE.md均保留，不将“根位置副本”推断为允许删除docs文档。
- 实际删除文件：无（0）；实际删除目录：无（0）；目录改名：无（0）。旧release/v0.1.9、resign批、dist、根build、全部_probe/run与备份均保留；其删除前哈希核验步骤未执行，未把未执行记录为通过。
- 发布附件：本轮未打开、读取或写入任何已签名manifest.json与manifest.sig，未进入发布目录操作；四附件完整前后SHA256复核未执行，不能报告此项硬性验收通过。
- 引用验收未完成：静态rg已发现docs/RELEASE_GUIDE.md:24仍有dist/AccountKeeper.exe、dist/update_helper.exe及--exe-url；未达到活动引用零残留。因前置条件失败，不继续修改或清理引用。
- 历史引用保留理由：docs/_release_files_inventory.md为迁移前盘点事实；docs/audit.md为追加式决策历史；本报告前两轮与docs/_last_report.md原文为执行证据；docs/release_v0.1.9_migration_evidence.md保留附件来源和备份映射；既有_probe/*_runs及helper_runs为历史现场。以上不是活动路径替换对象；docs/RELEASE_GUIDE.md不能冒充历史例外。
- 探针中的fixture-old/dist、fixture-new/dist及ROOT/dist为_probe隔离构建路径；probe_update_replace.py:34的根dist通配为既有审计保护，release递归保护仍在，未削弱保护或改写断言。
- issue挂账：新增P0 #80，记录cross-process-dedup-threshold-manual-reset、PermissionError [WinError 5]、沙箱AppData中.accountkeeper-update-state.json.tmp→.json、旧基线沙箱外/旧状态路径与当前HEAD默认沙箱/AppData的差异；与迁移无因果关系，具体拒绝来源未定；发版前必须用当前HEAD重建74/74与11/11，旧基线不覆盖2e6552f之后路径变更。本轮不修复、不重跑。
- Git验收：git diff --check EXIT=0（仅.gitignore的LF转CRLF提示）；git ls-files release无输出；git status --short --untracked-files=all为 M .gitignore、M build.bat、?? docs/_release_files_inventory.md、?? docs/_report_release_migration_1023.md、?? docs/release_v0.1.9_migration_evidence.md；ignored挂账及本地文档另按本节列出，不执行git add/commit。
- 忽略实测：git check-ignore -v docs/_report_release_migration_1023.md docs/release_v0.1.9_migration_evidence.md AccountKeeper_release_backup_20261023/ release/，只命中.gitignore:62:/AccountKeeper_release_backup_20261023/与.gitignore:19:release/；报告及清册均未忽略，release内无被跟踪文件。
- 冻结文件完整性：update_helper.py、updater.py、config.py、account_keeper.py本轮前后SHA256一致，未改冻结算法、DATA_DIR及exe同盘语义；本轮构建产物未用于覆盖任一已发布附件。
- 备份目录与临时忽略行保留，待用户确认后删除；Agent未删除、清空、移动备份目录。

## 第四轮（2026-10-24）

- 删除结果：181个文件、33个目录；逐步核验后使用文件工具删除，未执行终端删除、移动或重命名命令。
- 已删除根目录旧工具：build_release.py、AccountKeeper.spec、update_helper.spec、sign_release.py、gen_signing_key.py；5/5旧源与备份及backup-sha256.json的SHA256一致，release副本保留。
- 已删除已废弃旧批release/v0.1.9/及其AccountKeeper.exe、update_helper.exe、manifest.json、manifest.sig；附件集合、清册标记及4/4源/备份SHA256核验一致。
- 目录改名：release/v0.1.9-resign-20261023-173951 → release/v0.1.9；改名后四附件文件身份、大小、修改时间及SHA256一致。
- 已删除dist/及10个文件：AccountKeeper.exe（按裁决直接删除、不读取或比对哈希）、AccountKeeper_v0.1.0-alpha.exe、AccountKeeper_v0.1.1-alpha.exe、AccountKeeper_v0.1.2-alpha.exe、AccountKeeper_v0.1.3-alpha.exe、AccountKeeper_v0.1.4-alpha.exe、AccountKeeper_v0.1.5-alpha.exe、AccountKeeper_v0.1.6-alpha.exe、AccountKeeper_v0.1.7-alpha.exe、AccountKeeper_v0.1.8-alpha.exe。
- 历史归档验收：实查9份历史exe尚未落位，删除dist前先按批准目录规则复制到release/v0.1.0/至release/v0.1.8/，各目录只放对应原名exe；9/9源、归档、前置备份SHA256一致。
- 已删除根build/：161个文件、31个目录，645,322,983字节；删除前完整文件集合与备份索引一致，161/161源/备份SHA256一致；路径边界限定项目根build，未涉及_probe。
- 以下逐项列出build删除文件，按父目录分组；每组“下”之后为已删除文件名：
- build/AccountKeeper_v0.1.3-alpha 下：AccountKeeper_v0.1.3-alpha.pkg、Analysis-00.toc、EXE-00.toc、PKG-00.toc、PYZ-00.pyz、PYZ-00.toc、base_library.zip、warn-AccountKeeper_v0.1.3-alpha.txt、xref-AccountKeeper_v0.1.3-alpha.html。
- build/AccountKeeper_v0.1.3-alpha/localpycs 下：pyimod01_archive.pyc、pyimod02_importers.pyc、pyimod03_ctypes.pyc、pyimod04_pywin32.pyc、struct.pyc。
- build/AccountKeeper_v0.1.4-alpha 下：AccountKeeper_v0.1.4-alpha.pkg、Analysis-00.toc、EXE-00.toc、PKG-00.toc、PYZ-00.pyz、PYZ-00.toc、base_library.zip、warn-AccountKeeper_v0.1.4-alpha.txt、xref-AccountKeeper_v0.1.4-alpha.html。
- build/AccountKeeper_v0.1.4-alpha/localpycs 下：pyimod01_archive.pyc、pyimod02_importers.pyc、pyimod03_ctypes.pyc、pyimod04_pywin32.pyc、struct.pyc。
- build/AccountKeeper_v0.1.5-alpha 下：AccountKeeper_v0.1.5-alpha.pkg、Analysis-00.toc、EXE-00.toc、PKG-00.toc、PYZ-00.pyz、PYZ-00.toc、base_library.zip、warn-AccountKeeper_v0.1.5-alpha.txt、xref-AccountKeeper_v0.1.5-alpha.html。
- build/AccountKeeper_v0.1.5-alpha/localpycs 下：pyimod01_archive.pyc、pyimod02_importers.pyc、pyimod03_ctypes.pyc、pyimod04_pywin32.pyc、struct.pyc。
- build/AccountKeeper_v0.1.6-alpha 下：AccountKeeper_v0.1.6-alpha.pkg、Analysis-00.toc、EXE-00.toc、PKG-00.toc、PYZ-00.pyz、PYZ-00.toc、base_library.zip、warn-AccountKeeper_v0.1.6-alpha.txt、xref-AccountKeeper_v0.1.6-alpha.html。
- build/AccountKeeper_v0.1.6-alpha/localpycs 下：pyimod01_archive.pyc、pyimod02_importers.pyc、pyimod03_ctypes.pyc、pyimod04_pywin32.pyc、struct.pyc。
- build/AccountKeeper_v0.1.7-alpha 下：AccountKeeper_v0.1.7-alpha.pkg、Analysis-00.toc、EXE-00.toc、PKG-00.toc、PYZ-00.pyz、PYZ-00.toc、base_library.zip、warn-AccountKeeper_v0.1.7-alpha.txt、xref-AccountKeeper_v0.1.7-alpha.html。
- build/AccountKeeper_v0.1.7-alpha/localpycs 下：pyimod01_archive.pyc、pyimod02_importers.pyc、pyimod03_ctypes.pyc、pyimod04_pywin32.pyc、struct.pyc。
- build/AccountKeeper_v0.1.8-alpha 下：AccountKeeper_v0.1.8-alpha.pkg、Analysis-00.toc、EXE-00.toc、PKG-00.toc、PYZ-00.pyz、PYZ-00.toc、base_library.zip、warn-AccountKeeper_v0.1.8-alpha.txt、xref-AccountKeeper_v0.1.8-alpha.html。
- build/AccountKeeper_v0.1.8-alpha/localpycs 下：pyimod01_archive.pyc、pyimod02_importers.pyc、pyimod03_ctypes.pyc、pyimod04_pywin32.pyc、struct.pyc。
- build/release/AccountKeeper 下：AccountKeeper.pkg、Analysis-00.toc、EXE-00.toc、PKG-00.toc、PYZ-00.pyz、PYZ-00.toc、base_library.zip、version_info.txt、warn-AccountKeeper.txt、xref-AccountKeeper.html。
- build/release/AccountKeeper/localpycs 下：pyimod01_archive.pyc、pyimod02_importers.pyc、pyimod03_ctypes.pyc、pyimod04_pywin32.pyc、struct.pyc。
- build/release/AccountKeeper_v0.1.8-alpha 下：AccountKeeper.pkg、Analysis-00.toc、EXE-00.toc、PKG-00.toc、PYZ-00.pyz、PYZ-00.toc、base_library.zip、version_info.txt、warn-AccountKeeper_v0.1.8-alpha.txt、xref-AccountKeeper_v0.1.8-alpha.html。
- build/release/AccountKeeper_v0.1.8-alpha/localpycs 下：pyimod01_archive.pyc、pyimod02_importers.pyc、pyimod03_ctypes.pyc、pyimod04_pywin32.pyc、struct.pyc。
- build/release/helper-dist 下：update_helper.exe。
- build/release/helper-work/update_helper 下：Analysis-00.toc、EXE-00.toc、PKG-00.toc、PYZ-00.pyz、PYZ-00.toc、base_library.zip、update_helper.pkg、warn-update_helper.txt、xref-update_helper.html。
- build/release/helper-work/update_helper/helper-version 下：version_info.txt。
- build/release/helper-work/update_helper/localpycs 下：pyimod01_archive.pyc、pyimod02_importers.pyc、pyimod03_ctypes.pyc、pyimod04_pywin32.pyc、struct.pyc。
- build/v0.1.9/AccountKeeper_v0.1.8-alpha 下：AccountKeeper.pkg、Analysis-00.toc、EXE-00.toc、PKG-00.toc、PYZ-00.pyz、PYZ-00.toc、base_library.zip、version_info.txt、warn-AccountKeeper_v0.1.8-alpha.txt、xref-AccountKeeper_v0.1.8-alpha.html。
- build/v0.1.9/AccountKeeper_v0.1.8-alpha/localpycs 下：pyimod01_archive.pyc、pyimod02_importers.pyc、pyimod03_ctypes.pyc、pyimod04_pywin32.pyc、struct.pyc。
- build/v0.1.9/helper-dist 下：update_helper.exe。
- build/v0.1.9/helper-work/update_helper 下：Analysis-00.toc、EXE-00.toc、PKG-00.toc、PYZ-00.pyz、PYZ-00.toc、base_library.zip、update_helper.pkg、warn-update_helper.txt、xref-update_helper.html。
- build/v0.1.9/helper-work/update_helper/helper-version 下：version_info.txt。
- build/v0.1.9/helper-work/update_helper/localpycs 下：pyimod01_archive.pyc、pyimod02_importers.pyc、pyimod03_ctypes.pyc、pyimod04_pywin32.pyc、struct.pyc。
- build已删除目录清单：build/AccountKeeper_v0.1.3-alpha/localpycs/、build/AccountKeeper_v0.1.3-alpha/、build/AccountKeeper_v0.1.4-alpha/localpycs/、build/AccountKeeper_v0.1.4-alpha/、build/AccountKeeper_v0.1.5-alpha/localpycs/、build/AccountKeeper_v0.1.5-alpha/、build/AccountKeeper_v0.1.6-alpha/localpycs/、build/AccountKeeper_v0.1.6-alpha/、build/AccountKeeper_v0.1.7-alpha/localpycs/、build/AccountKeeper_v0.1.7-alpha/、build/AccountKeeper_v0.1.8-alpha/localpycs/、build/AccountKeeper_v0.1.8-alpha/、build/release/AccountKeeper/localpycs/、build/release/AccountKeeper/、build/release/AccountKeeper_v0.1.8-alpha/localpycs/、build/release/AccountKeeper_v0.1.8-alpha/、build/release/helper-dist/、build/release/helper-work/update_helper/helper-version/、build/release/helper-work/update_helper/localpycs/、build/release/helper-work/update_helper/、build/release/helper-work/、build/release/、build/v0.1.9/AccountKeeper_v0.1.8-alpha/localpycs/、build/v0.1.9/AccountKeeper_v0.1.8-alpha/、build/v0.1.9/helper-dist/、build/v0.1.9/helper-work/update_helper/helper-version/、build/v0.1.9/helper-work/update_helper/localpycs/、build/v0.1.9/helper-work/update_helper/、build/v0.1.9/helper-work/、build/v0.1.9/、build/。
- 已删除重复文档：docs/RELEASE_GUIDE.md；删除前与AccountKeeper_release_backup_20261023/docs/RELEASE_GUIDE.md的SHA256一致（fa91b19fec86325c29262e2e2c5fa239822a1bec2df12f6ab5c960159df8b020）。
- SOP重写摘要：唯一入口release/RELEASE_GUIDE.md，合并两份内容，覆盖当前代码回归门禁、版本/正式公钥准备、build.bat构建、预发布上传两exe、获取原始URL、离线签名归档四附件、补传清单/签名及正式发布；命令使用release/_staging与release/v版本，参数为--url和--helper-url，不覆盖已发布版本。
- 构建产物验收：release/_staging/两exe仍在且哈希与第三轮相同；AccountKeeper.exe为173c6bdbb3eb42643ce4a49c457f6a873f5bc50303de4755119e941b5e13c1dd，update_helper.exe为f3b3a9c8872b6ec44e0a1b41feaf52c5b1bcc0a69c35445e734c902f22508191；本轮未重建或执行exe。
- 发布附件验收：release/v0.1.9/仅四附件；AccountKeeper.exe SHA256 ca0d3df20670476de416bcd80a21df9a6bd39307802f9d87f28b9a9c85ee0167；update_helper.exe SHA256 6a93f2ca0a40462508d58a7631d41b814f50cee58e5679abee6fadadd1969bc0；manifest.json SHA256 259aed313da8299e3a4713c39724de8e14848c4aadebf5b61d96cdc338e9c31e；manifest.sig SHA256 db23924a94db2bd379f42f98d8b14eb698ef42e64322bbffaa3cd08cca499860，均与清册resign批一致。
- 签名文件处理：本轮用户另行明确允许仅为SHA256读取；只读取哈希、不解析、不修改、不写入manifest.json或manifest.sig；发布目录改名没有改写附件。
- 引用验收：全仓rg检索并核对活动范围119文件；对旧根dist产物路径、build/release路径、docs/RELEASE_GUIDE.md、无效签名URL参数及根工具调用的定向rg为0命中（EXIT=1），工具导入实际定位release中的三份脚本；无活动旧根路径依赖。
- 保留引用逐项解释：probe_update_helper.py:859、873、963、970、975、987的fixture-old/dist与fixture-new/dist是_probe内的隔离构建产物；probe_sign_release.py:244仅说明测试不依赖旧产物；probe_update_replace.py:34为旧dist审计通配，release递归保护并存，不能削弱既有保护。均非已删除根dist的活动消费路径。
- 历史路径保留：docs/audit.md的历史裁决、_release_files_inventory.md盘点、清册来源、迁移报告前三轮、_last_report.md原文、.workbuddy-ai/memory及既有_probe/run与备份记录；均为证据，不作全局替换。
- 静态与范围验收：五份探针284个assert AST与前置备份一致；update_helper.py、updater.py、update_ui.py、config.py、account_keeper.py本轮前后SHA256一致；未修改冻结区、断言或_probe，未修probe_import_packaging_68.py，未执行unit/frozen。
- issue验收：docs/_issues.txt与本轮开始时逐字节一致，#80及其他条目完整保留；发版前重建当前代码74/74、11/11的要求仍在。
- Git验收：git status --short为 M .gitignore、M build.bat、D build_release.py、D gen_signing_key.py、D sign_release.py、?? docs/_release_files_inventory.md、?? docs/_report_release_migration_1023.md、?? docs/release_v0.1.9_migration_evidence.md；spec及旧SOP原为ignored，删除不在status列出，已另验不存在。git diff --check EXIT=0（仅既有.gitignore换行提示）；git ls-files release无输出。未执行git add或git commit。
- 忽略验收：release/命中.gitignore:19，AccountKeeper_release_backup_20261023/命中.gitignore:62；报告与清册未被忽略。
- 备份目录依然保留：AccountKeeper_release_backup_20261023/；本轮结束逐文件复核197/197记录SHA256一致。未删除、清空、移动或改写备份；临时忽略行保留，待用户确认后删除。
