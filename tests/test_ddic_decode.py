"""DDIC INTTYPE 字段解码单元测试(离线,不依赖数据库)。

测试对象 ddic._decode_field 是生产路径的唯一类型化解码原语
(ddic.enrich_components 内部调用);语义映射见 docs/FORMAT.md §4.1。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sap_indx_decoder.ddic import _decode_field


def test_int4_little_endian():
    # INTTYPE=I, 4 字节小端有符号(实测 LI/TYPE_CHILDS)
    assert _decode_field(b"\x69\x0c\x00\x00", 0, 4, "I", 1, "1100", 4) == (3177, "I")
    assert _decode_field(b"\xff\xff\xff\xff", 0, 4, "I", 1, "1100", 4) == (-1, "I")


def test_char_and_date_time():
    # INTTYPE C/D/T: latin-1 解码,去尾空格
    assert _decode_field(b"OJTL", 0, 4, "C", 1, "1100", 4) == ("OJTL", "C")
    assert _decode_field(b"20260101", 0, 8, "D", 1, "1100", 8) == ("20260101", "D")
    assert _decode_field(b"125945", 0, 6, "T", 1, "1100", 6) == ("125945", "T")


def test_numc_field():
    # INTTYPE=N (NUMC): 数字字符,按文本处理
    assert _decode_field(b"19900101", 0, 8, "N", 1, "1100", 8) == ("19900101", "N")


def test_utf16_char_field():
    # csize=2(代码页 4103): CHAR(4) 占 8 字节
    assert _decode_field(b"D\x000\x001\x001\x00", 0, 8, "C", 2, "4103", 8) == ("D011", "C")


def test_unknown_inttype_and_short_row_fall_back_to_raw():
    # 未收录 INTTYPE → RAW 十六进制,不猜语义
    assert _decode_field(b"\x01\x02\x03", 0, 3, "X", 1, "1100", 3) == ("010203", "RAW")
    # 行短于声明宽度 → RAW
    assert _decode_field(b"\x01", 0, 4, "I", 1, "1100", 4)[1] == "RAW"


def test_bcd():
    # DEC(4,2) → 3 字节 BCD: 12 34 5F = +123.45(无小数缩放,调用方按 DECIMALS 处理)
    assert _decode_field(b"\x12\x34\x5f", 0, 3, "P", 1, "1100", 3) == (12345, "P")
    # 符号半字节 0xD = 负
    assert _decode_field(b"\x12\x34\x5d", 0, 3, "P", 1, "1100", 3) == (-12345, "P")
