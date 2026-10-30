"""输入、标签、工具栏与表格的公共兼容出口。

所有导出均指向实现模块中的同一个类或函数，调用方共享类身份。
"""

# 只重导出对象，不持有副本状态；实现模块不得反向导入本兼容出口。
from chip_widgets import TagChipsFrame as TagChipsFrame
from input_frame import InputFrame as InputFrame
from record_table import (
    RecordTableFrame as RecordTableFrame,
    TagBadgeRenderer as TagBadgeRenderer,
    TreeviewTagTooltip as TreeviewTagTooltip,
    summarize_tags as summarize_tags,
    tag_layout as tag_layout,
)
from table_amount_colors import AmountColorRenderer as AmountColorRenderer
from toolbar_frame import ToolbarFrame as ToolbarFrame
