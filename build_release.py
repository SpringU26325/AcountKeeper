"""统一构建入口；导入只定义函数，不加载业务模块或执行构建。"""
import argparse
import ast
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys


def _version_parts(version: str) -> tuple[int, ...]:
    # 只接受稳定版三段数字，拒绝前导零和超出 Windows WORD 的版本字段。
    if not re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", version):
        raise ValueError("APP_VERSION 必须是主.次.补丁形式的稳定版本。")
    parts = tuple(map(int, version.split(".")))
    if any(part > 65535 for part in parts):
        raise ValueError("APP_VERSION 各段不得超过 65535。")
    return (*parts, 0)


def read_app_version(config_path: Path) -> str:
    tree = ast.parse(Path(config_path).read_text(encoding="utf-8"))
    # 同时检查所有写入，避免顶层值被条件赋值、嵌套赋值或增量赋值覆盖。
    stores = [n for n in ast.walk(tree) if isinstance(n, ast.Name)
              and n.id == "APP_VERSION" and isinstance(n.ctx, ast.Store)]
    assignments = [n for n in tree.body if isinstance(n, ast.Assign) and len(n.targets) == 1
                   and isinstance(n.targets[0], ast.Name) and n.targets[0].id == "APP_VERSION"]
    if len(stores) != 1 or len(assignments) != 1:
        raise ValueError("APP_VERSION 必须是唯一的顶层单目标赋值。")
    value = assignments[0].value
    if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
        raise ValueError("APP_VERSION 必须直接赋字符串字面量。")
    _version_parts(value.value)
    return value.value


def write_version_resource(version: str, directory: Path) -> Path:
    parts = _version_parts(version)
    # 资源仅是打包输入；文本与数值均来自调用者刚从 config 读取的同一版本。
    fields = {"FileVersion": version, "ProductVersion": version,
              "InternalName": "AccountKeeper", "OriginalFilename": "AccountKeeper.exe",
              "FileDescription": "AccountKeeper", "ProductName": "AccountKeeper"}
    strings = ",\n".join(f"StringStruct({key!r}, {value!r})" for key, value in fields.items())
    content = (
        f"VSVersionInfo(ffi=FixedFileInfo(filevers={parts!r}, prodvers={parts!r}, "
        "mask=0x3f, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)), "
        f"kids=[StringFileInfo([StringTable('040904B0', [{strings}])]), "
        "VarFileInfo([VarStruct('Translation', [1033, 1200])])])\n"
    )
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    resource = directory / "version_info.txt"
    resource.write_text(content, encoding="utf-8")
    return resource


def main() -> int:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="构建固定名称的 AccountKeeper.exe")
    parser.add_argument("--distpath", type=Path, default=root / "dist")
    parser.add_argument("--workpath", type=Path, default=root / "build" / "release")
    args = parser.parse_args()
    config = root / "config.py"
    try:
        before = hashlib.sha256(config.read_bytes()).digest()
        version = read_app_version(config)
        print(f"开始构建 AccountKeeper {version}", flush=True)
        helper_dist = args.workpath.resolve() / "helper-dist"
        subprocess.run([
            sys.executable, "-B", "-m", "PyInstaller", "--noconfirm",
            "--distpath", str(helper_dist),
            "--workpath", str(args.workpath.resolve() / "helper-work"),
            str(root / "update_helper.spec"),
        ], cwd=root, check=True)
        helper = helper_dist / "update_helper.exe"
        helper_hash = hashlib.sha256(helper.read_bytes()).hexdigest()
        environment = dict(os.environ, ACCOUNTKEEPER_HELPER_PATH=str(helper), ACCOUNTKEEPER_HELPER_SHA256=helper_hash)
        # 同一解释器执行现有 spec；-B 防止 spec 导入构建模块时写源码缓存。
        subprocess.run([
            sys.executable, "-B", "-m", "PyInstaller", "--noconfirm",
            "--distpath", str(args.distpath.resolve()),
            "--workpath", str(args.workpath.resolve()),
            str(root / "AccountKeeper.spec"),
        ], cwd=root, check=True, env=environment)
        if hashlib.sha256(config.read_bytes()).digest() != before:
            raise ValueError("构建期间 config.py 发生变化，产物不得发布。")
    except (OSError, ValueError, SyntaxError, subprocess.CalledProcessError) as error:
        # 失败显式返回非零；bat 不得把失败产物或上次旧包提示为构建成功。
        print(f"构建失败：{error}", file=sys.stderr)
        return getattr(error, "returncode", 1)
    print(f"构建完成：{args.distpath.resolve() / 'AccountKeeper.exe'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
