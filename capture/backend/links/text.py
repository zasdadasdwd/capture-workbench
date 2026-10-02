"""全文片段扫描：流式解压正文，不受详情预览和 JSON 字段提取限制。"""

import base64
import codecs
import gzip
import io
import re
import zlib
from pathlib import Path

import brotli
import zstandard


def decoded_chunks(stream, encoding):
    """逐块解压常见 HTTP 编码；每块输出有界，支持跨块片段。"""
    if encoding in ("", "identity"):
        while chunk := stream.read(65536):
            yield chunk
    elif encoding in ("gzip", "zstd"):
        decoder = (
            gzip.GzipFile(fileobj=stream)
            if encoding == "gzip"
            else zstandard.ZstdDecompressor().stream_reader(stream)
        )
        with decoder:
            while chunk := decoder.read(65536):
                yield chunk
    elif encoding == "deflate":
        head = stream.read(2)
        wrapped = (
            len(head) == 2
            and head[0] & 15 == 8
            and int.from_bytes(head, "big") % 31 == 0
        )
        decoder = zlib.decompressobj(zlib.MAX_WBITS if wrapped else -zlib.MAX_WBITS)
        data = head
        while data:
            yield decoder.decompress(data, 65536)
            data = decoder.unconsumed_tail or stream.read(65536)
        yield decoder.flush()
        if not decoder.eof:
            raise ValueError("压缩正文不完整")
    elif encoding == "br":
        decoder = brotli.Decompressor()
        while chunk := stream.read(65536):
            yield decoder.process(chunk, output_buffer_limit=65536)
            while not decoder.can_accept_more_data():
                yield decoder.process(b"", output_buffer_limit=65536)
        while not decoder.is_finished():
            chunk = decoder.process(b"", output_buffer_limit=65536)
            if not chunk:
                break
            yield chunk
        if not decoder.is_finished():
            raise ValueError("压缩正文不完整")
    else:
        raise ValueError("不支持的正文压缩编码")


def message_matches(folder, flow, query, cancelled):
    """返回每个匹配位置的计数和首个字符偏移，不向结果复制敏感报文。"""
    hits, warnings = [], []

    def add(field, text, at, name=False):
        needle = query.casefold() if name else query
        haystack = text.casefold() if name else text
        count = haystack.count(needle)
        if count:
            hits.append(
                {
                    "field": field,
                    "count": count,
                    "offset": haystack.find(needle),
                    "at": at,
                }
            )

    add("url", flow.get("url", ""), flow.get("started", 0))
    for part in ("request", "original_request", "response"):
        message = flow.get(part)
        if not message:
            continue
        if message.get("truncated"):
            warnings.append(
                f"{flow['id']} 的 {part} 抓包正文已截断，未保存部分无法搜索"
            )
        at = flow.get("started", 0) + (
            (flow.get("duration") or 0) / 1000 if part == "response" else 0
        )
        if part != "response" and message.get("url") != flow.get("url"):
            add(part + ".url", message.get("url", ""), at)
        headers = message.get("headers", [])
        counts = {}
        for name, value in headers:
            key = name.lower()
            index = counts.get(key, 0)
            counts[key] = index + 1
            field = f"{part}.headers.{key}[{index}]"
            add(field + ".name", name, at, name=True)
            add(field, value, at)
        filename = message.get("body_file")
        if filename and Path(filename).name != filename:
            raise ValueError("正文路径无效")
        count, first = 0, None
        try:
            stream = (
                (folder / "bodies" / filename).open("rb")
                if filename
                else io.BytesIO(
                    base64.b64decode(message["body_b64"])
                    if message.get("body_b64")
                    else message.get("body_text", "").encode()
                )
            )
            content_encoding = next(
                (
                    v.lower().strip()
                    for k, v in headers
                    if k.lower() == "content-encoding"
                ),
                "identity",
            )
            content_type = next(
                (v for k, v in headers if k.lower() == "content-type"), ""
            )
            charset = re.search(
                r"charset\s*=\s*[\"']?([\w-]+)", content_type, re.IGNORECASE
            )
            decoder = codecs.getincrementaldecoder(charset[1] if charset else "utf-8")(
                errors="replace"
            )
            carry, length, count, first, last = "", 0, 0, None, -1

            def consume(text):
                nonlocal carry, length, count, first, last
                joined = carry + text
                base = length - len(carry)
                offset = joined.find(query)
                while offset >= 0:
                    absolute = base + offset
                    if absolute > last:
                        first = absolute if first is None else first
                        last = absolute
                        count += 1
                    offset = joined.find(query, offset + 1)
                length += len(text)
                carry = joined[-(len(query) - 1) :] if len(query) > 1 else ""

            with stream:
                for chunk in decoded_chunks(stream, content_encoding):
                    if cancelled.is_set():
                        raise InterruptedError("分析已取消")
                    consume(decoder.decode(chunk))
                consume(decoder.decode(b"", final=True))
        except InterruptedError:
            raise
        except (
            ValueError,
            LookupError,
            OSError,
            EOFError,
            zlib.error,
            brotli.error,
            zstandard.ZstdError,
        ):
            warnings.append(f"{flow['id']} 的 {part} 正文缺失或无法解码，搜索不完整")
        if count:
            hits.append(
                {"field": part + ".body", "count": count, "offset": first, "at": at}
            )
    return hits, warnings
