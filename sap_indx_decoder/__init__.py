"""sap-indx-decoder — SAP 数据簇(INDX/STXL 等簇表 CLUSTD)解码库。

公共 API:
    parse_blob(blob)            -> (info, out, comps, covered, named)
    decompress_container(blob)  -> (info, out)
    parse_stream(out, ver, csize) -> [component dict]
    decode_row(row, csize, cp)  -> (text, non_printable)
离线即可用;数据库访问见 db.py(hdbcli)。
"""
from .container import decompress_container
from .errors import ToolError
from .marker import component_to_json, decode_row, parse_stream

__version__ = "0.4.2"

__all__ = ["__version__", "ToolError", "decompress_container", "parse_stream",
           "decode_row", "component_to_json", "parse_blob"]


def parse_blob(blob):
    """一步到位:容器解压 + 标记流解析 + 覆盖率统计。

    返回 (info, out, comps, covered, named)。
    """
    info, out = decompress_container(blob)
    comps = parse_stream(out, info["version"], info["char_size"])
    covered = max((c["end"] for c in comps), default=0) * 100 // max(len(out), 1)
    named = sum(1 for c in comps if len(c["name"]) >= 3)
    return info, out, comps, covered, named
