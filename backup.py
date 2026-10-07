"""用户数据归档；数据库操作留在 store，交互和路径记忆留在 UI。"""

from pathlib import Path
from tempfile import TemporaryDirectory
import zipfile

from store import AccountStore


def create_backup(store: AccountStore, save_path: Path) -> Path:
    """先获取数据库快照，再原样收集偏好，成功后发布完整 ZIP。"""
    destination = Path(save_path).expanduser().resolve()
    data_dir = store.path.resolve().parent
    if destination.suffix.lower() != ".zip":
        raise ValueError("备份文件必须使用 .zip 扩展名。")
    try:
        original_target = destination.stat()
    except FileNotFoundError:
        original_target = None  # UI 确认后仍可能有另一实例生成同名文件，发布前必须重查。
    json_paths = [data_dir / name for name in (
        "settings.json", "tags.json", "categories.json", "import_mappings.json"
    )]
    protected = [store.path.resolve(), *json_paths]
    protected.extend(Path(str(store.path.resolve()) + suffix) for suffix in (
        ".bak", "-journal", "-wal", "-shm"
    ))
    for source in protected:
        # resolve 防软链接，samefile 防改成 .zip 名字的硬链接；不能覆盖任何数据源。
        if destination == source.resolve() or (
            destination.exists() and source.exists() and destination.samefile(source)
        ):
            raise ValueError("备份目标不能覆盖数据源文件。")

    # 暂存目录与目标在同一文件系统；只有完整归档校验成功才发布，失败保留旧备份。
    # TemporaryDirectory 在所有文件句柄关闭后退出，同时清理快照和失败 ZIP。
    with TemporaryDirectory(prefix=".accountkeeper-backup-", dir=destination.parent) as work:
        work_dir = Path(work)
        snapshot = store.snapshot_database(work_dir / "account.db")
        payloads: dict[str, bytes] = {}
        # JSON 紧跟耗时较长的快照读取，缩短数据库与偏好读取之间的时间间隔。
        for source in json_paths:
            try:
                payloads[source.name] = source.read_bytes()
            except FileNotFoundError:
                continue  # 缺失是合法状态；不制造空 JSON，不调用会初始化偏好的读写接口。
            # 权限或其他读取错误必须向上传递，不能悄悄宣称备份完整。
        archive_path = work_dir / "backup.zip"
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(snapshot, arcname="account.db")
            for name, payload in payloads.items():
                # 原字节保留格式、编码及损坏现场；备份不能顺手修复用户配置。
                archive.writestr(name, payload)
        with zipfile.ZipFile(archive_path) as archive:
            if archive.testzip() is not None:
                raise zipfile.BadZipFile("备份 ZIP 校验失败。")
        try:
            current_target = destination.stat()
        except FileNotFoundError:
            current_target = None
        if current_target != original_target:
            raise FileExistsError("目标文件在备份期间发生变化，请重新选择并确认。")
        if original_target is None:
            # Windows rename 原子拒绝同名文件；新目标不能无确认覆盖另一实例刚发布的备份。
            archive_path.rename(destination)
        else:
            archive_path.replace(destination)
    return destination
