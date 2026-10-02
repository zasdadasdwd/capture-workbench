"""批量导出验证：各格式内容一致，摘要导出不读取正文。"""

import csv
import io
import json

import pytest

from capture.backend.export import (
    csv_export,
    curl_code,
    har_export,
    python_code,
    write_export,
)


@pytest.mark.parametrize("format", ["json", "har", "python", "curl", "csv"])
def test_incremental_export_matches_existing_formats(tmp_path, format):
    """逐条导出与原有转换结果一致，重复 Header、查询参数和正文仍保留。"""
    flows = [
        {
            "id": str(index),
            "method": "POST",
            "url": "http://example.test/?a=1&a=2",
            "status": "complete",
            "started": 1,
            "code": 200,
            "request": {
                "method": "POST",
                "url": "http://example.test/?a=1&a=2",
                "headers": [["X-Test", "one"], ["X-Test", "two"]],
                "body_b64": "YWJj",
            },
        }
        for index in range(3)
    ]

    class ExampleStore:
        """记录读取次数，并拒绝 CSV 获取完整正文。"""

        def __init__(self):
            self.reads = []

        def get_flow(self, session, flow_id):
            assert format != "csv"
            self.reads.append(flow_id)
            return flows[int(flow_id)]

        def get_summary(self, session, flow_id):
            assert format == "csv"
            self.reads.append(flow_id)
            return flows[int(flow_id)]

    store = ExampleStore()
    path = tmp_path / "export"
    write_export(store, "session", ["0", "1", "2"], format, path)
    actual = path.read_text()
    assert store.reads == ["0", "1", "2"]
    if format == "json":
        assert json.loads(actual) == flows
    elif format == "har":
        assert json.loads(actual) == json.loads(har_export(flows))
    elif format == "python":
        assert actual == python_code(flows)
        compile(actual, "capture.py", "exec")
    elif format == "curl":
        assert actual == "".join(curl_code(flow) + "\n\n" for flow in flows)
    else:
        assert list(csv.reader(io.StringIO(actual))) == list(
            csv.reader(io.StringIO(csv_export(flows)))
        )
