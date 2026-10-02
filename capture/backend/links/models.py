"""分析任务与视图的有界输入模型。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from capture.backend.filters import FlowFilters


class TransformRule(BaseModel):
    """声明式转换规则，不执行用户代码；匹配结果保留规则名称。"""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=60)
    operation: Literal["sha256", "sha1", "md5", "hex", "strip_prefix"]
    prefix: str = Field(default="", max_length=100)


class LinkOptions(BaseModel):
    """字段发现和后续追踪使用同一任务通道，计算只在子进程执行。"""

    model_config = ConfigDict(extra="forbid")
    operation: Literal["fields", "trace", "search"] = "trace"
    session_id: str = Field(min_length=1, max_length=100)
    flow_id: str = Field(min_length=1, max_length=100)
    field: str = Field(default="", max_length=2000)
    query: str = Field(default="", max_length=4096)
    query_from: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    query_scope: Literal["field", "value", "all"] = "all"
    reveal: bool = False
    window_seconds: int = Field(default=300, ge=1, le=86400)
    scan_limit: int = Field(default=500, ge=1, le=2000)
    node_limit: int = Field(default=100, ge=1, le=500)
    host: str = Field(default="", max_length=253)
    direction: Literal["both", "backward", "forward"] = "both"
    # 范围由详情中的文本选择生成，不保存选中参数的明文。
    value_range: tuple[int, int] | None = None
    request_ids: list[str] | None = Field(default=None, max_length=2000)
    target_filters: FlowFilters | None = None
    transforms: bool = True
    contains: bool = False
    allow_common: bool = False
    include_overlap: bool = False
    rules: list[TransformRule] = Field(default_factory=list, max_length=8)
    incremental_from: str | None = Field(default=None, max_length=32)

    @model_validator(mode="after")
    def selected_field(self):
        if self.operation == "trace" and not self.field:
            raise ValueError("请选择具体字段后追踪")
        if self.operation == "search" and not (
            self.query or self.query_from or self.field
        ):
            raise ValueError("请输入参数名或数据片段")
        if (
            self.value_range
            and not 0 <= self.value_range[0] < self.value_range[1] <= 65536
        ):
            raise ValueError("选中的参数范围无效")
        return self


class LinkView(BaseModel):
    """保存任务引用、布局和人工判断；不保存完整报文和敏感原值。"""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="数据链路", min_length=1, max_length=100)
    job_ids: list[str] = Field(max_length=20)
    positions: dict[str, list[float]] = Field(default_factory=dict, max_length=2001)
    annotations: dict[str, dict[str, str]] = Field(
        default_factory=dict, max_length=2000
    )
    hidden_nodes: list[str] = Field(default_factory=list, max_length=2001)
    camera: list[float] = Field(default_factory=list, max_length=6)

    @model_validator(mode="after")
    def bounded_values(self):
        import math

        if any(
            len(position) != 3
            or any(not math.isfinite(v) or abs(v) > 1e6 for v in position)
            for position in self.positions.values()
        ):
            raise ValueError("节点坐标必须是三个有限数值")
        if any(
            any(len(k) > 100 or len(v) > 2000 for k, v in note.items())
            for note in self.annotations.values()
        ):
            raise ValueError("备注过长")
        if any(not math.isfinite(v) or abs(v) > 1e6 for v in self.camera):
            raise ValueError("相机坐标无效")
        return self
