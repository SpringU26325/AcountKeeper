@echo off
:: 切换到 UTF-8 编码，防止中文乱码导致 cmd 解析出错
chcp 65001 >nul
:: AccountKeeper 自动打包脚本
:: 使用方式：在 PyCharm 终端里输入 .\build.bat 运行，或者直接双击本文件

echo ==========================================
echo   开始打包 AccountKeeper
echo ==========================================

:: 版本与构建参数统一交给 Python；失败时原样返回，不输出成功提示。
"%~dp0.venv\Scripts\python.exe" -B -X utf8 "%~dp0build_release.py" %*
if not "%errorlevel%"=="0" exit /b %errorlevel%

echo ==========================================
echo   打包完成！exe 位于 dist/ 文件夹（包含 AccountKeeper.exe 和 update_helper.exe）
echo   请前往 Gitee 上传附件，拿取 URL 后运行 sign_release.py 进行签名。
echo ==========================================
pause
