"""分析接口的范围与条件校验，避免无界读取整个抓包会话。"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from capture.backend.filters import FlowFilters


class ParameterCondition(BaseModel):
    """按明确字段过滤，重复值可通过完整索引路径区分。"""

    field: str = Field(min_length=1, max_length=300)
    operator: Literal["equals", "contains", "exists"] = "equals"
    value: str = Field(default="", max_length=8192)


class AnalysisFilters(FlowFilters):
    """分析查询默认为 20 条摘要，每页最多 100 条。"""

    limit: int = Field(default=20, ge=1, le=100)


class SearchOptions(BaseModel):
    """摘要查询先走 SQL；参数条件最多额外检查 200 条预览。"""

    filters: AnalysisFilters = Field(default_factory=AnalysisFilters)
    parameter: ParameterCondition | None = None
    scan_limit: int = Field(default=100, ge=1, le=200)

    @model_validator(mode="after")
    def bounded(self):
        if self.filters.limit > 100:
            raise ValueError("MCP 查询每页最多 100 条")
        if (
            self.filters.started_after is not None
            and self.filters.started_before is not None
            and self.filters.started_after > self.filters.started_before
        ):
            raise ValueError("时间范围起点不能大于终点")
        return self
