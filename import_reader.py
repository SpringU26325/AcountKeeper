"""通用账单读取：只保留原始单元格，不识别平台、不作行处理结论。"""

import csv
from dataclasses import dataclass
import io
from pathlib import Path


@dataclass(frozen=True)
class RawCell:
    value: object
    kind: str
    number_format: str = ""


@dataclass(frozen=True)
class RawRow:
    row_number: int
    cells: tuple[RawCell, ...]

    @property
    def values(self) -> tuple[object, ...]:
        return tuple(cell.value for cell in self.cells)


@dataclass(frozen=True)
class BillFile:
    file_name: str
    rows: tuple[RawRow, ...]
    encoding: str | None = None
    sheet_name: str | None = None


def _read_csv(path: Path) -> BillFile:
    content = path.read_bytes()
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("CSV 编码无法读取，需要 UTF-8 或 GB18030 文件。")
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    rows = []
    next_line = 1
    for values in reader:
        # 引号内换行会占多条物理行；记录起始行号，不能以交易序号冒充原始行号。
        rows.append(RawRow(next_line, tuple(RawCell(value, "text") for value in values)))
        next_line = reader.line_num + 1
    return BillFile(path.name, tuple(rows), encoding=encoding)


def _read_xlsx(path: Path, sheet_name: str | None) -> BillFile:
    from openpyxl import load_workbook  # 延迟依赖，普通记账启动不读取 XLSX 或初始化解析器。

    workbook = load_workbook(path, read_only=True, data_only=False)
    try:
        if sheet_name is None:
            if len(workbook.sheetnames) != 1:
                raise ValueError("此文件有多个工作表，请明确指定一个，不合并工作表。")
            sheet_name = workbook.sheetnames[0]
        if sheet_name not in workbook.sheetnames:
            raise ValueError("指定工作表不存在。")
        sheet = workbook[sheet_name]
        # read_only 保留日期类型和单元格格式；不执行公式、不把说明区当平台判断依据。
        rows = tuple(
            RawRow(index, tuple(
                RawCell(cell.value, cell.data_type, cell.number_format or "")
                for cell in cells
            ))
            for index, cells in enumerate(sheet.iter_rows(), start=1)
        )
        return BillFile(path.name, rows, sheet_name=sheet_name)
    finally:
        workbook.close()  # 关闭后取消／重读都不会遗留被 Windows 占用的文件句柄。


def read_bill_file(path: Path, *, sheet_name: str | None = None) -> BillFile:
    path = Path(path)
    if path.suffix.casefold() == ".csv":
        if sheet_name is not None:
            raise ValueError("CSV 没有工作表。")
        return _read_csv(path)
    if path.suffix.casefold() == ".xlsx":
        return _read_xlsx(path, sheet_name)
    raise ValueError("本次只支持 CSV 和 XLSX 账单文件。")
