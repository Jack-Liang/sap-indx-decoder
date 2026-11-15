"""按字段描述符把行字节解码为类型化值(格式见 docs/FORMAT.md §4.1)。

描述符 type 字节 → ABAP 内部类型(经 DD02L/DD03L 真值验证):
    0 → C (CHAR/LANG)   1 → D (DATS)   3 → T (TIMS)
    6 → N (NUMC)        8 → I (INT4, 4 字节小端有符号)
未收录的 type 或 AF 操作码 → 'RAW'(十六进制显示,不猜语义)。
字符类字段的存储宽度 = 字符数 × csize;INT 恒为 4 字节。
"""
from .marker import CP_MAP

__all__ = ["TYPE_MAP", "decode_row_typed", "storage_width", "row_width_ok"]

# 描述符 type 字节 → ABAP 内部类型(DD03L.INTTYPE)
TYPE_MAP = {0: "C", 1: "D", 3: "T", 6: "N", 8: "I"}

# 字符类:值按 csize 展开为字符;其余按字节处理
CHAR_LIKE = {"C", "D", "N", "T"}


def abap_type(desc):
    """描述符 (op, type, len) → ('C'/'D'/'T'/'N'/'I'/'RAW')。"""
    op, t, ln = (desc[0], desc[1], desc[2]) if len(desc) == 3 else (0xAA, desc[0], desc[1])
    return TYPE_MAP.get(t, "RAW")


def storage_width(desc, csize):
    """该字段的行内存储宽度(字节)。"""
    t = abap_type(desc)
    ln = desc[-1]
    if t in CHAR_LIKE:
        return ln  # 已按 csize 计入(v6 len 即字节;v5 latin-1 len 即字节)
    if t == "I":
        return 4
    return ln  # RAW/未知按声明的字节宽


def row_width_ok(fields, rowlen, csize):
    """校验 Σ存储宽度 == 行宽(类型化解码的前提)。"""
    return sum(storage_width(f, csize) for f in fields) == rowlen


def decode_row_typed(fields, row, csize, cp="1100"):
    """行字节 → 值列表。

    C/D/N/T → 字符串(去尾空格);I → int;RAW → 'aabb..' 十六进制。
    返回 (values, kinds);解码失败的字段值为 None。
    """
    values, kinds = [], []
    off = 0
    for f in fields:
        t = abap_type(f)
        w = storage_width(f, csize)
        chunk = row[off:off + w]
        off += w
        if t in CHAR_LIKE:
            if csize == 2:
                chars = []
                for i in range(0, len(chunk) - 1, 2):
                    c = chunk[i] + chunk[i + 1] * 256
                    chars.append(chr(c))
                val = "".join(chars).rstrip()
            else:
                val = chunk.decode(CP_MAP.get(cp, "latin-1"), errors="replace").rstrip()
            kinds.append(t)
        elif t == "I":
            try:
                val = int.from_bytes(chunk, "little", signed=True)
            except Exception:
                val = None
            kinds.append("I")
        else:
            val = chunk.hex()
            kinds.append("RAW")
        values.append(val)
    return values, kinds
