"""导入明细的分页与选择状态；不持有 Tk，不修改会话决定。"""


class ImportPagination:
    def __init__(self):
        self.context = self.source = None
        self.epoch = self.token = 0
        self.reset_context()

    def reset_context(self, context=None):
        # 视图、查询或代次变化统一作废；切回旧视图不能复活旧页和旧选择。
        self.context, self.source = context, None
        self.rows, self.indices, self.pair_starts = (), {}, {}
        self.page, self.summary = 1, False
        self.selections, self.target = {}, None
        self.epoch += 1
        self.token += 1

    def bind(self, context, source, loader, summary):
        if context != self.context:
            self.reset_context(context)
        if source is not self.source:
            self.source, self.rows = source, tuple(loader())
            self.indices = {row.row_number: index for index, row in enumerate(self.rows)}
            self.pair_starts = {}
            for index, row in enumerate(self.rows):
                if row.pair_key:
                    self.pair_starts.setdefault(row.pair_key, index)
            # 决定生效可能改变筛选结果，只保留仍属于原页的选择。
            self.selections = {page: number for page, number in self.selections.items()
                               if self.page_of(number) == page}
        self.summary = summary
        self.page = min(max(1, self.page), self.total_pages) if self.rows else 0

    @property
    def total_pages(self):
        return (len(self.rows) + 99) // 100

    @property
    def page_rows(self):
        if self.summary:
            return self.rows[:5]
        start = max(0, self.page - 1) * 100
        return self.rows[start:start + 100]

    def page_of(self, number):
        index = self.indices.get(number)
        return index // 100 + 1 if index is not None else None

    def move(self, value):
        # 输入无效时保持现场，不夹紧用户输入到另一页造成误定位。
        text = str(value).strip()
        if not text.isascii() or not text.isdecimal():
            raise ValueError("请输入有效整数页码")
        page = int(text)
        if not 1 <= page <= self.total_pages:
            raise ValueError(f"页码应在 1 至 {self.total_pages} 之间")
        self.page, self.target = page, None

    def remember(self, number):
        if number is None:
            self.selections.pop(self.page, None)
        elif self.page_of(number) == self.page:
            self.selections[self.page] = number

    def continuation(self, pair):
        return self.pair_starts.get(pair, len(self.rows)) < (self.page - 1) * 100

    def new_render(self):
        self.token += 1
        return self.token

    def close(self):
        self.reset_context()  # 释放完整数据引用，并使所有在途渲染令牌失效。
