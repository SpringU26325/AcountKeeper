# 当前版本回归基线与探针清理容错报告（2026-10-25）

状态：完成。本轮使用极短目录、沙箱外直接执行，完整 frozen 11/11、unit 74/74（26+26+22）全部通过，四次探针命令 EXIT CODE 均为0。cross-process-dedup-threshold-manual-reset 通过；frozen-corrupt 的 config-loaded.json、business.json 均不存在。

基线对应 Git HEAD 68ce28ccb154038369ed27d1462263aa5cdfb2c8 加当前未提交修复；不是未修改 HEAD 的测试结果。
产品 update_helper.py SHA256：8b2ea75a9ab107515b1336c25e44665d8cf469da20950853469025dbef3a56ee。
当前 helper 探针 SHA256：6271e4aa8074a2c36507916c56c546eb3f9e28ff033681b4d49ce8e7a23b6c5f。

## 本轮修改及精确行数

唯一代码修改文件：_probe/probe_update_helper.py，kill() 第890和892行，共修改2行；差异为新增2行、删除2行，净增0行。
- 退出竞争错误码白名单从 (5, 1168) 扩展为 (5, 1168, 31, 87)。
- 同步中文注释，说明查询/终止可能与进程退出竞争。
- h.wait_signal(handle, 2000) 的同一句柄退出确认条件保持原样；只有白名单错误且对象确认退出才跳过。对象存活、无法证明退出或其他错误继续抛出。
- 原有 PID/创建时间/映像身份比较、独占路径检查、禁止终止驱动的断言、最终等待断言均保留。
- 产品 update_helper.py、config.py、updater.py、update_ui.py 本轮完全未改；另75份顶层探针 .py 文件未改。未改任何业务断言、注册用例名或旧dist保护。

[完整两行差异](C:/ZCY/python/AcountKeeper/_probe/probe_fix_1025_evidence/cleanup-31-87/probe-change.patch)。

## AST 与退出条件自证

四份探针的全部 ast.Assert 按源码行号排序，逐项比较 ast.dump(include_attributes=False)，不对断言做任何归一化或子表达式替换：
- probe_update_helper.py：128/128 完全一致。
- probe_updater.py：32/32 完全一致。
- probe_sign_release.py：73/73 完全一致。
- probe_update_replace.py：55/55 完全一致。
- 合计288/288；其余三份全文哈希也一致。
- 另外对完整 helper 探针模块 AST 检查：仅将本轮唯一的新增错误码元组还原为原白名单后，整个模块 AST 与修改前完全一致。此步骤用于核对修改范围，不参与上述断言比较。

隔离编译当前 kill()，模拟错误码5、1168、31、87及对照错误码6，每码分别模拟“同一句柄退出”和“仍存活”，共10/10通过：
- 四个白名单码：仅退出时跳过，存活时原异常继续抛出。
- 对照码6：即使对象退出也不忽略该错误。
- 10组均没有调用真实终止API；模拟描述失败时，TerminateProcess调用数均为0，句柄均按 finally 关闭。

核验由Python标准输入脚本直接执行（python -），EXIT=0；没有 python -c、外层 subprocess 包装或修改探针来进行自证。
[完整脚本、命令、结果与diff](C:/ZCY/python/AcountKeeper/_probe/probe_fix_1025_evidence/cleanup-31-87/audit.json)。

## 执行环境、顺序及原命令

工作目录：C:/ZCY/python/AcountKeeper。执行方式均为 require_escalated（沙箱外），使用全新短目录，没有复用旧失败现场。先按本轮要求直接跑完整frozen，通过后补跑已授权的三组unit，使74/74也对应当前产品版本。
没有外层 subprocess 包装、没有自行构造 python -c 命令、没有终端文件重定向。探针原有内部子进程逻辑保持原样。

1. 完整 frozen，EXIT=0，11/11：
```text
.venv/Scripts/python.exe -B -X utf8 _probe/probe_update_helper.py --run-dir _probe/f6 --stage frozen --cases success,ddl,hold,timeout,escape,corrupt,shared,avsuccess,avddl,avframe,avtimeout
```
2. helper unit，EXIT=0，26/26：
```text
.venv/Scripts/python.exe -B -X utf8 _probe/probe_update_helper.py --run-dir _probe/u5 --stage unit
```
3. updater unit，EXIT=0，26/26：
```text
.venv/Scripts/python.exe -B -X utf8 _probe/probe_updater.py --run-dir _probe/u6
```
4. sign_release unit，EXIT=0，22/22：
```text
.venv/Scripts/python.exe -B -X utf8 _probe/probe_sign_release.py
```

helper、updater、frozen 的通过数均从探针自身原始结果JSON读取并核对。sign_release 自身不生成结果JSON，依据终端22条PASS及“22/22 通过”；另存摘要明确标注来源为终端输出，没有冒充探针原始JSON。

updater 本地 HTTP 测试服务线程出现 WinError10054/10053 的连接重置/中止输出，完整原文已保存；进程最终EXIT=0、26项结果均为true，没有隐藏或改变这些输出。

[四条命令、执行环境、全部终端输出块及退出码](C:/ZCY/python/AcountKeeper/_probe/probe_fix_1025_evidence/cleanup-31-87/executions.json)。

## 完整 frozen 11/11 明细

以下11项在同一次 _probe/f6 完整运行中全部 ok=true，不拼接单例或旧运行：
- frozen-success
- frozen-ddl
- frozen-hold
- frozen-timeout
- frozen-escape
- frozen-corrupt
- frozen-shared
- frozen-avsuccess
- frozen-avddl
- frozen-avframe
- frozen-avtimeout

[探针原始 frozen-results.json](C:/ZCY/python/AcountKeeper/_probe/f6/frozen-results.json)；
[原始结果留存副本](C:/ZCY/python/AcountKeeper/_probe/probe_fix_1025_evidence/cleanup-31-87/frozen.results.json)；
[完整frozen终端输出](C:/ZCY/python/AcountKeeper/_probe/probe_fix_1025_evidence/cleanup-31-87/frozen.terminal.log)。

corrupt 实测安全边界：
- _probe/f6/case-corrupt/config-loaded.json 不存在。
- _probe/f6/case-corrupt/business.json 不存在。
- startup-error.json 明确拒绝打开账本，损坏事务日志的原有断言通过。
[两个标记与拒绝内容](C:/ZCY/python/AcountKeeper/_probe/probe_fix_1025_evidence/cleanup-31-87/corrupt-proof.json)。

## unit 74/74 与关键回归

- helper：26/26，包括 cross-process-dedup-threshold-manual-reset、真实硬链接及创建/超时释放检查。
- updater：26/26。
- sign_release：22/22。
- 本轮产品代码与frozen受测版本完全相同；unit结果不是此前产品修复前74/74的沿用。
- cross-process-dedup-threshold-manual-reset 本轮 ok=true，没有复现此前#80的AppData os.replace拒绝访问。

[helper 原始JSON](C:/ZCY/python/AcountKeeper/_probe/u5/unit-results.json)；
[updater 原始JSON](C:/ZCY/python/AcountKeeper/_probe/u6/results.json)；
[签名原始终端输出](C:/ZCY/python/AcountKeeper/_probe/probe_fix_1025_evidence/cleanup-31-87/sign-unit.terminal.log)；
[签名结果摘要及来源说明](C:/ZCY/python/AcountKeeper/_probe/probe_fix_1025_evidence/cleanup-31-87/sign-unit.summary.json)。

## 隔离生效与代码完整性

冻结进程共留存45条 frozen=true 隔离记录，全部 resolved==expected。首个冻结业务进程PID12592：
- WIN_PD_OVERRIDE_LOCAL_APPDATA=C:/ZCY/python/AcountKeeper/_probe/f6/case-success/AppData/Local。
- 实际解析目录与预期均为 C:/ZCY/python/AcountKeeper/_probe/f6/case-success/AppData/Local/SpringU26325/AccountKeeper。
- 其余场景也分别指向本轮假AppData，shared场景按设计共用本例数据根；没有回退真实AppData。

[全部冻结进程隔离记录](C:/ZCY/python/AcountKeeper/_probe/probe_fix_1025_evidence/cleanup-31-87/isolation.json)；
[冻结副本与根目录产品源码哈希一致证明](C:/ZCY/python/AcountKeeper/_probe/probe_fix_1025_evidence/cleanup-31-87/frozen-source-proof.json)。

80份受监控源码的磁盘首尾核验显示，本轮仅helper探针发生已批准的两行变化；产品四文件均未变化，修改后到全部测试结束的80份源码哈希均一致。
[修改前源码与哈希](C:/ZCY/python/AcountKeeper/_probe/probe_fix_1025_evidence/cleanup-31-87/before.json)；
[修改后测试前哈希](C:/ZCY/python/AcountKeeper/_probe/probe_fix_1025_evidence/cleanup-31-87/post-edit.json)；
[最终首尾哈希核验](C:/ZCY/python/AcountKeeper/_probe/probe_fix_1025_evidence/cleanup-31-87/after.json)。

## 当前产品行为与自我审查

- 既有产品修复保留：路径解析不提前导入config；重新确认前清除上一轮信号；终止只授权登记且三元组匹配的进程；扫描未知对象只能报错；回滚异常持久记录。本轮没有再修改这些产品实现。
- 最小改动：是，仅探针错误码白名单及对应注释两行。
- 是否扩大范围：没有修改其他代码、业务断言、注册名、旧dist保护或产品冻结区；报告和验证证据属于交付范围。
- 测试是否验证行为：10组隔离校验验证“退出才忽略”的边界；当前版本unit74项与同一次完整frozen11项全部实测通过。真实frozen未新增错误码触发计数，因此不推断每个新增错误码都曾实际发生。
- 阻塞：无。完整回归验收数字已取得；源码Diff仍由用户审查，未暂存、提交、推送Git。
- 旧失败目录保留；成功探针按照原有用例逻辑清理自身材料，本轮没有额外删除或复用其他run-dir。
- 更新前报告留存：[report-before.md](C:/ZCY/python/AcountKeeper/_probe/probe_fix_1025_evidence/cleanup-31-87/report-before.md)。
