"""容器头解析(格式见 docs/FORMAT.md §2)。"""
from .errors import ToolError
from .lzh import inflate_lzh

__all__ = ["decompress_container"]


def decompress_container(data):
    """data: bytes。返回 (info dict, 解压后 bytes)。

    info 含 version/codepage/char_size/compressed/algorithm/
    compressed_bytes/uncompressed_bytes/declared_uncompressed。
    """
    if len(data) < 24:
        raise ToolError("簇只有 %d 字节,不是标准容器" % len(data))
    b = data
    if not any(b):
        raise ToolError("簇为空(全零,无容器数据)")
    if b[0] != 0xFF:
        raise ToolError("容器签名错误: 首字节 %02X (期望 FF)" % b[0])
    ver = b[1]
    cp = "".join(chr(x) for x in b[8:12])
    csize = 2 if cp in ("4102", "4103") else 1
    expect = b[16] + b[17] * 256 + b[18] * 65536 + b[19] * 16777216  # u32 小端
    compressed = len(b) > 22 and b[21] == 0x1F and b[22] == 0x9D
    algo = None
    if compressed:
        algo = {0x12: "LZH", 0x10: "LZC"}.get(b[20], "0x%02X" % b[20])
        if b[20] != 0x12:
            raise ToolError("压缩算法 %s 暂不支持(仅实现 LZH/0x12)" % algo)
        out = inflate_lzh(b, expect)
    else:
        out = b[16:]
    info = dict(version=ver, codepage=cp, char_size=csize,
                compressed=compressed, algorithm=algo,
                compressed_bytes=len(b), uncompressed_bytes=len(out),
                declared_uncompressed=expect if compressed else None)
    return info, out
