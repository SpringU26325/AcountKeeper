<div align="center">
  <img width="350" alt="蜗牛气泡框" src="./image/snail_for_README.png" />

# AccountKeeper 本地记账软件

**中文摘要：** AccountKeeper 是一款轻量、开源的本地记账软件，支持日常收支记录、账单导入、可视化财务分析和数据备份。v1.1.0 加入月度总预算和标签预算，记账时可查看超额提醒。

**English summary:** AccountKeeper is a lightweight, open-source desktop app for personal bookkeeping, with local data storage, bill imports, interactive charts, and backups. Version 1.1.0 adds monthly and per-tag budgets, plus alerts when spending crosses a budget threshold.

🎉 我的第一个独立使用 vibe-coding 开发的记账软件，正式跨入 1.0 时代！
</div>

## 🆕 v1.1.0 预算管理

- **月度预算**：设置总预算和单个标签预算，查看剩余额度和逐笔明细，也可以一键补齐上月额度。
- **预算外支出**：新增或编辑支出时可勾选「不计入预算」，账目仍保留在账本与财务分析中。
- **跨档提醒**：保存支出后，接近上限、已用完或超支时，录入区与蜗牛气泡同步提示。
- **图表明细和搜索**：可从财务分析的标签图和每日趋势查看组成金额的账目，主窗按 `Ctrl+F` 聚焦搜索。

详见 [v1.1.0 更新日志](release_note/1.1.0_release_note.md)。

## ✨ 功能特性

- **本地存储**：账本保存在本机 AppData 目录，与程序分离，拒绝云端上传，保护绝对隐私。
- **自动检查更新**：后台静默检查新版，软件内一键下载、确认重启安装。支持断点续传与防篡改校验。
- **可视化图表**：直观查看标签占比、每日花销走势以及近半年收支趋势，多标签记录分别计入，口径透明。
- **智能标签系统**：支持一条记录打多个标签，以 Chips 形式展示；自动记忆高频标签。
- **多维导入导出**：支持导入外部 CSV 与 XLSX 账单，自动解析并支持批次撤销；支持将账单导出为 CSV，并自动记忆导出路径。
- **一键备份**：随时将账本与偏好设置打包为 zip 压缩包。
- **响应式布局**：支持自由调整窗口大小，工具栏自适应等比例分配。
- **趣味体验**：动态蜗牛动画与随机消息气泡，让记账不再枯燥。

## 📦 如何下载使用

请前往发布页，下载最新的 `AccountKeeper.exe`，双击即可运行，无需安装 Python。请保留程序文件名 `AccountKeeper.exe`，以便正常使用自动更新功能。

### ⚠️ 升级须知
如果您当前使用的是 **v1.0.0 至 v1.0.2**，可在软件内通过「检查更新」升级。
如果您使用的是 **v0.1.9 或更早版本**，请手动下载 v1.1.0 的 `AccountKeeper.exe` 替换旧程序。

### 账本在哪里？更新会影响数据吗？
账本和偏好保存在 Windows 用户本地数据目录（AppData）中，通常位于：`%LOCALAPPDATA%\SpringU26325\AccountKeeper`。可通过软件右上角的“文件 → 打开数据目录”查看。

**账本与程序严格分开存放，更新失败或回退绝不会影响你的账本。** 自动更新也绝不会上传你的任何账目数据。

## 🐛 已知问题
- **窗口后台恢复闪烁**：受限于底层 Tkinter 框架与 Windows DWM 的兼容机制，在长时间挂后台或最小化后切回时，界面可能会有极短暂的闪烁或黑块。属于底层框架视觉渲染限制，**绝不影响数据安全与功能逻辑**。
- 大批量密集退款对的导入预览仍可能出现轻微卡顿。
- 外部账单的识别结果仍需人工核对。
- 多实例并发修改同一笔记录时，可能后保存覆盖先保存（后续引入乐观锁）。

## 🛠️ 技术栈

- Python 3.13
- CustomTkinter (GUI)
- SQLite (数据库)
- openpyxl (XLSX 读取)
- Matplotlib (图表)
- Pillow (图片处理)
- platformdirs (用户数据目录管理)

## 📝 运行源码

如果你有 Python 环境，也可以克隆本仓库，安装依赖后直接运行：

```bash
pip install -r requirements.txt
python account_keeper.py
```
