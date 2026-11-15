"""类型化解码单元测试(离线,不依赖数据库)。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sap_indx_decoder.typedecode import (TYPE_MAP, decode_row_typed,
                                         row_width_ok, storage_width)


def test_int4_little_endian():
    # 实测样本: LI/TYPE_CHILDS 的 (8,4) 字段
    vals, kinds = decode_row_typed([(0xAA, 8, 4)], b"\x69\x0c\x00\x00", 1)
    assert vals == [3177] and kinds == ["I"]
    vals, _ = decode_row_typed([(0xAA, 8, 4)], b"\xff\xff\xff\xff", 1)
    assert vals == [-1]


def test_char_and_date_time():
    # OJTL 形状: (0,4)=CHAR, (1,8)=DATS, (3,6)=TIMS
    fields = [(0xAA, 0, 4), (0xAA, 1, 8), (0xAA, 3, 6)]
    row = b"OJTL" + b"20260101" + b"125945"
    vals, kinds = decode_row_typed(fields, row, 1)
    assert vals == ["OJTL", "20260101", "125945"]
    assert kinds == ["C", "D", "T"]


def test_numc_and_storage_width():
    assert TYPE_MAP[6] == "N"
    fields = [(0xAA, 6, 8)]  # NUMC(4),latin-1 时 8 字节
    vals, _ = decode_row_typed(fields, b"000001"[:8] if False else b"19900101", 1)
    assert vals == ["19900101"]
    assert storage_width((0xAA, 6, 8), 1) == 8
    assert row_width_ok(fields, 8, 1)


def test_utf16_char_field():
    fields = [(0xAA, 0, 8)]  # CHAR(4),UTF-16 下 8 字节
    vals, _ = decode_row_typed(fields, b"D\x000\x001\x001\x00", 2)
    assert vals == ["D011"]


def test_unknown_type_falls_back_to_hex():
    vals, kinds = decode_row_typed([(0xAA, 99, 3)], b"\x01\x02\x03", 1)
    assert vals == ["010203"] and kinds == ["RAW"]


def test_bcdd_via_ddic_path():
    from sap_indx_decoder.ddic import _decode_field
    # DEC(4,2) → 3 字节 BCD: 12 34 5F = +123.45
    val, kind = _decode_field(b"\x12\x34\x5f", 0, 3, "P", 1, "1100", 3)
    assert val == 12345 and kind == "P"  # 无小数缩放,调用方按 DECIMALS 处理
