"""DDIC 元数据(DD02L/DD03L)查询、缓存与组件丰富化。

设计要点(实测结论):标记流组件名是 ABAP 变量名,不一定等于 DDIC 结构名,
因此按名查 DD02L 是"尽力而为":命中则用 DD03L 的字段名 + INTTYPE 语义
解码行,未命中则退回文本视图。字段解码宽度以描述符自带长度为准,DDIC 只
提供语义(INTTYPE)与列名,二者不一致的单字段按 RAW 处理,不整体回退。
"""
import struct

__all__ = ["DDIC", "enrich_components"]

_VAR_TYPES = ("STRG", "SSTR", "RSTR")   # 变长类型,不参与定宽行


class DDIC:
    """DD02L/DD03L 只读查询,带进程内缓存;conn=None 时仅用预载数据(离线)。"""

    def __init__(self, conn=None, preload=None):
        self.conn = conn
        self._cache = dict(preload or {})

    def get(self, name):
        """返回 [(FIELDNAME, DATATYPE, LENG, DECIMALS, INTTYPE)] 或 None。"""
        if name in self._cache:
            return self._cache[name]
        if self.conn is None:
            self._cache[name] = None
            return None
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM DD02L WHERE TABNAME=? AND AS4LOCAL='A'", (name,))
        if cur.fetchone()[0] == 0:
            self._cache[name] = None
            return None
        cur.execute("""SELECT FIELDNAME, DATATYPE, LENG, DECIMALS, INTTYPE
                       FROM DD03L WHERE TABNAME=? AND AS4LOCAL='A'
                       ORDER BY POSITION""", (name,))
        fields = [tuple(r) for r in cur.fetchall()
                  if r[1] not in _VAR_TYPES and r[0] != ".INCLUDE"]
        self._cache[name] = fields
        return fields


def _decode_field(row, off, width, inttype, csize, cp, desc_len):
    """按 DDIC INTTYPE 语义解码一个字段;语义与描述符宽度不符时按 RAW。"""
    from .marker import CP_MAP
    chunk = row[off:off + width]
    if inttype in ("C", "D", "N", "T"):
        if desc_len % csize:
            return chunk.hex(), "RAW"
        if csize == 2:
            chars = [chr(chunk[i] + chunk[i + 1] * 256)
                     for i in range(0, len(chunk) - 1, 2)]
            return "".join(chars).rstrip(), inttype
        return chunk.decode(CP_MAP.get(cp, "latin-1"), errors="replace").rstrip(), inttype
    if inttype in ("I", "s", "b", "8"):
        if len(chunk) != width:
            return chunk.hex(), "RAW"
        return int.from_bytes(chunk, "little", signed=True), inttype
    if inttype == "P":  # BCD: 每字节两位数字,最后半字节为符号(D=负)
        if len(chunk) != width:
            return chunk.hex(), "RAW"
        nibbles = []
        for b in chunk:
            nibbles.append(b >> 4)
            nibbles.append(b & 0xF)
        sign_nib = nibbles[-1]
        digits = nibbles[:-1]
        if any(d > 9 for d in digits) or sign_nib < 10:
            return chunk.hex(), "RAW"
        val = 0
        for d in digits:
            val = val * 10 + d
        if sign_nib == 0xD:
            val = -val
        return val, "P"
    if inttype == "F":
        if len(chunk) == 8:
            return struct.unpack("<d", chunk)[0], "F"
        return chunk.hex(), "RAW"
    return chunk.hex(), "RAW"


def enrich_components(comps, info, ddic):
    """就地丰富结构/表组件:附加 DD03L 列名与类型化行。

    条件:组件名在 DD02L 中存在,且 DD03L 字段数与描述符数一致。
    组件获得 c["typed"]=True/False、c["columns"]、c["typed_rows"]。
    """
    cs = info["char_size"]
    cp = info["codepage"]
    for c in comps:
        c["typed"] = False
        if c["kind"] == "value" or not c["rows"]:
            continue
        fields = ddic.get(c["name"]) if ddic else None
        if not fields or len(fields) != len(c["fields"]):
            continue
        cols = [dict(name=f[0], datatype=f[1], leng=f[2], decimals=f[3],
                     inttype=f[4]) for f in fields]
        typed_rows = []
        for r in c["rows"]:
            rowvals, rowkinds = [], []
            off = 0
            for desc, fdef in zip(c["fields"], fields):
                width = desc[-1]
                val, kind = _decode_field(r, off, width, fdef[4], cs, cp, width)
                rowvals.append(val)
                rowkinds.append(kind)
                off += width
            if off != len(r):   # 宽度对不上,整组件回退文本视图
                typed_rows = []
                break
            typed_rows.append(dict(zip([f[0] for f in fields], rowvals)))
        if typed_rows:
            c["columns"] = cols
            c["typed_rows"] = typed_rows
            c["typed"] = True
