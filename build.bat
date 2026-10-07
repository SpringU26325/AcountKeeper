@echo off
:: 切换到 UTF-8 编码，防止中文乱码导致 cmd 解析出错
chcp 65001 >nul
:: AccountKeeper 自动打包脚本
:: 使用方式：在 PyCharm 终端里输入 .\build.bat 运行，或者直接双击本文件

echo ==========================================
echo   开始打包 AccountKeeper v0.1.7
echo ==========================================

:: 用 python -m PyInstaller 替代直接调用 pyinstaller
:: 这样可以确保使用的是当前 Python 环境下的打包工具
python -m PyInstaller --noconfirm --onefile --windowed ^
  --icon="image/app_icon.ico" ^
  --name="AccountKeeper_v0.1.7-alpha" ^
  --add-data "image;image" ^
  --add-data "snail_messages.json;." ^
  --collect-all customtkinter ^
  --collect-all matplotlib ^
  account_keeper.py 

echo ==========================================
echo   打包完成！exe 文件位于 dist/ 文件夹下
echo ==========================================
pause
