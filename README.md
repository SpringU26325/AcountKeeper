<div align="center">
  <img width="350" alt="蜗牛气泡框" src="https://github.com/user-attachments/assets/5fcc3c23-4fba-46d6-84f4-cac6085c8be4" />
</div>

# AccountKeeper 本地记账软件

🎉 我的第一个独立使用 vibe-coding 开发的记账软件！

## ✨ 功能特性
- **本地 SQLite 存储**：数据永远保存在你自己的电脑上，安全可靠。
- **直观的记账体验**：新增【支出 / 收入】切换按钮，用户始终输入正数，系统自动处理正负号，记账更省心。
- **完整的增删改查**：支持添加、双击编辑、删除记录，以及实时搜索与筛选。
- **可视化统计**：按月统计收入与支出，并生成可视化柱状图，支持收入与支出分类对比。
- **智能导出**：支持导出账单为 CSV 文件，**自动记住上次保存路径**，下次导出省去重复选路径的烦恼。
- **趣味体验**：动态蜗牛动画与随机消息气泡（防伪标识）。
- **快捷入口**：一键打开数据目录，方便备份与查看。

## 📦 如何下载使用
前往 [Releases](https://github.com/SpringU26325/AccountKeeper/releases) 页面，下载最新的 `AccountKeeper_v0.1.2-alpha.exe` 文件，双击即可运行。

⚠️ **首次运行提示**：由于缺少数字签名，Windows 系统可能会弹出安全警告。请点击“更多信息” -> “仍要运行”即可正常使用。

## 🐛 已知 Bug & 待办 (Alpha 版)
- **日期输入**：目前仍需手动输入 `YYYY-MM-DD`，暂不支持日历控件（计划在后续版本中加入）。
- **对话框图标**：图表窗口暂未设置应用图标，后续版本会统一。
- **卸载与更新**：目前暂未提供自动更新和卸载功能，需要手动下载覆盖。

## 🛠️ 技术栈
- Python 3.13
- CustomTkinter (GUI)
- SQLite (数据库)
- Matplotlib (图表)
- Pillow (图片处理)
- platformdirs (用户数据目录管理)

## 📝 运行源码
如果你有 Python 环境，也可以克隆本仓库，安装依赖后直接运行：

```bash
pip install -r requirements.txt
python account_keeper.py
