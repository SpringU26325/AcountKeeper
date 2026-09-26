<div align="center">
  <img width="350" alt="蜗牛气泡框" src="https://github.com/user-attachments/assets/5fcc3c23-4fba-46d6-84f4-cac6085c8be4" />
</div>

# AccountKeeper 本地记账软件

🎉 我的第一个独立使用 vibe-coding 开发的记账软件！

## ✨ 功能特性
- 本地 SQLite 数据存储，数据永远在你自己的电脑上
- 支持添加、编辑、删除、搜索记录
- **新增**：支出/收入切换按钮，无需手动输入负数，记账更省心
- 按月统计收入与支出，并生成可视化图表
- 支持导出账单为 CSV 文件
- 动态蜗牛动画与随机消息气泡（防伪标识）
- **新增**：一键打开数据目录，方便备份与查看

## 📦 如何下载使用
前往 [Releases](https://github.com/SpringU26325/AccountKeeper/releases) 页面，下载最新的 `AccountKeeper_v0.1.1-alpha.exe` 文件，双击即可运行。

⚠️ **首次运行提示**：由于缺少数字签名，Windows 系统可能会弹出安全警告。请点击“更多信息” -> “仍要运行”即可正常使用。

## 🐛 已知 Bug (Alpha 版)
- 日期输入仍需要手动输入 `YYYY-MM-DD`，暂不支持点击日历选择。
- 底部汇总文字在某些极端窗口大小下可能会被轻微截断。

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
