"""标记流解析(格式见 docs/FORMAT.md §4)。

v5/v6 的组件(01 单值 / 02 结构 / 03 表)、字段描述符(AA/AF)、
分组(ab/ac、ad/ae)、行区(bb / be+bc+bd+bf),锚定式解析 +
"行宽 = Σ字段长度" 自校验。CP_MAP 负责代码页 → Python 编码名。
"""
from .errors import ToolError

__all__ = ["parse_stream", "decode_row", "component_to_json", "CP_MAP",
           "ToolError"]

CLASS_BYTES = (1, 2, 3)
ANCHOR_MARKS = (0xBB, 0xAB, 0xAD, 0xBC)

CP_MAP = {"1100": "latin-1", "1400": "iso8859-2", "1500": "iso8859-5",
          "1610": "windows-1252"}


def u16be(b, o):
    return (b[o] << 8) | b[o + 1]


def u32be(b, o):
    return (b[o] << 24) | (b[o + 1] << 16) | (b[o + 2] << 8) | b[o + 3]


def printable_start(out, e, lo, csize=1):
    """e 前面的连续可打印串起点(csize=2 时按 UTF-16LE 双字节回退)"""
    s = e
    while s - csize >= lo:
        c = out[s - csize] if csize == 1 else out[s - csize] + out[s - csize + 1] * 256
        if not (32 <= c < 127):
            break
        s -= csize
    return s


def parse_group(out, q, ver):
    """q 指向 ab/ad。返回 (fields, rowlen, q_after_first_bb, has_rows) 或 None"""
    n = len(out)
    q += 1
    if q + 1 >= n or out[q] != 0x0E:
        return None
    rowlen_hdr = u16be(out, q + 1)
    q += 3
    fields = []
    while q < n and out[q] in (0xAA, 0xAF):
        if ver >= 6:
            if q + 7 > n:
                return None
            fields.append((out[q + 1], u32be(out, q + 3)))
            q += 7
        else:
            if q + 4 > n:
                return None
            fields.append((out[q + 1], u16be(out, q + 2)))
            q += 4
    if q >= n or out[q] not in (0xAC, 0xAE):
        return None
    q += 1
    if q < n and out[q] == 0x0E:
        q += 3
    rowlen = sum(l for _, l in fields)
    if rowlen == 0 or (rowlen != rowlen_hdr and ver < 6):
        return None
    has_rows = q < n and out[q] == 0xBB
    if has_rows:
        q += 1
    return fields, rowlen, q, has_rows


def read_name(out, s, e, csize):
    if csize == 1:
        return bytes(out[s:e]).decode("latin1").strip()
    return "".join(chr(out[i] + out[i + 1] * 256)
                   for i in range(s, e, 2)).strip()


def try_component(out, p, ver, csize=1):
    """p 指向组件类别字节(1/2/3)。锚定: 名字(可打印串)后紧跟 bb/ab/ad/bc 标记。"""
    n = len(out)

    # v6 表描述符特例: [03][头32B,名字长@+11][名字@+32][组+字段][be 行区]
    if ver >= 6 and out[p] == 3 and p + 12 <= n:
        namelen = out[p + 11]
        if 1 <= namelen <= 60:
            tname = read_name(out, p + 32, p + 32 + namelen * csize, csize)
            qq = p + 32 + namelen * csize
            fields, rows, bound = [], None, min(qq + 8192, n)
            rowsum = 0
            while qq < bound:
                op = out[qq]
                if op in (0xAA, 0xAF):
                    ln = u32be(out, qq + 3)
                    rowsum += ln
                    fields.append((op, out[qq + 1], ln))   # (操作码, type, len字节)
                    qq += 7
                elif op in (0xAB, 0xAD):
                    qq += 1
                    while qq < bound and out[qq] not in (0xAA, 0xAF):
                        qq += 1
                elif op in (0xAC, 0xAE):
                    qq += 1
                    while qq < bound and out[qq] not in (0xBE, 0xBB):
                        qq += 1
                    if qq < bound and out[qq] == 0xBE:
                        rowlen, nrows = u32be(out, qq + 1), u32be(out, qq + 5)
                        qq += 9
                        rows = []
                        for _ in range(nrows):
                            if qq + 5 > n or out[qq] != 0xBC:
                                rows = None
                                break
                            ln = u32be(out, qq + 1)
                            if qq + 5 + ln > n:
                                rows = None
                                break
                            rows.append(bytes(out[qq + 5:qq + 5 + ln]))
                            qq += 5 + ln
                            if qq < n and out[qq] == 0xBD:
                                qq += 1
                        if rows is not None and qq < n and out[qq] == 0xBF:
                            qq += 1
                    elif qq < bound and out[qq] == 0xBB and rowsum > 0:
                        qq += 1
                        rows = []
                        while True:
                            if qq + rowsum > n:
                                break
                            rows.append(bytes(out[qq:qq + rowsum]))
                            qq += rowsum
                            if qq < n and out[qq] == 0xBB:
                                qq += 1
                            else:
                                break
                    else:
                        rows = []
                    break
                else:
                    return None
            if rows is not None and (qq >= n or out[qq] in CLASS_BYTES + (0x04, 0x00)):
                return dict(kind="table", name=tname,
                            fields=fields or [("v6", rowsum)],
                            rows=rows, end=qq)

    q = p + 1
    found = None
    while q < min(p + 80, n):
        m = out[q]
        if m in ANCHOR_MARKS:
            s = printable_start(out, q, p + 1, csize)
            if 1 <= q - s <= 60:
                found = (m, s)
                break
        q += 1
    if not found:
        return None
    m, s = found
    name = read_name(out, s, q, csize)

    if m == 0xBC:
        if q + 5 > n:
            return None
        ln = u32be(out, q + 1)
        if ln == 0 or q + 5 + ln > n:
            return None
        return dict(kind="value", name=name, value=bytes(out[q + 5:q + 5 + ln]),
                    end=q + 5 + ln)

    if m in (0xAB, 0xAD):
        g = parse_group(out, q, ver)
        if not g:
            return None
        fields, rowlen, q2, has_rows = g
        rows = []
        while has_rows:
            if q2 + rowlen > n:
                return None
            rows.append(bytes(out[q2:q2 + rowlen]))
            q2 += rowlen
            if q2 < n and out[q2] == 0xBB:
                q2 += 1
            else:
                break
        return dict(kind="table" if m == 0xAD else "struct", name=name,
                    fields=fields, rows=rows, end=q2)

    if m == 0xBB and ver < 6:
        rowlen = u16be(out, p + 2)
        if rowlen == 0 or q + 1 + rowlen > n:
            return None
        row = bytes(out[q + 1:q + 1 + rowlen])
        nxt = out[q + 1 + rowlen] if q + 1 + rowlen < n else 0x04
        if nxt not in CLASS_BYTES + (0x04, 0x00):
            return None
        return dict(kind="value", name=name, value=row, end=q + 1 + rowlen)
    return None


def parse_stream(out, ver, csize=1):
    """标记流 → 组件列表。out 为解压后的 bytes/list。"""
    comps, p, n = [], 0, len(out)
    while p < n:
        if out[p] in CLASS_BYTES:
            c = try_component(out, p, ver, csize)
            if c:
                comps.append(c)
                p = c["end"]
                continue
        p += 1
    return comps


def decode_row(row, csize, cp="1100"):
    """行字节 → (文本, 非打印字符数)。csize=2 时按 UTF-16LE 双字节。"""
    if csize == 2:
        chars, bad = [], 0
        for i in range(0, len(row) - 1, 2):
            c = row[i] + row[i + 1] * 256
            if 32 <= c < 127:
                chars.append(chr(c))
            elif c == 0:
                chars.append(" ")
            else:
                chars.append("?")
                bad += 1
        return "".join(chars).rstrip(), bad
    txt = row.decode(CP_MAP.get(cp, "latin-1"), errors="replace")
    bad = sum(1 for c in row if not (32 <= c < 127))
    return txt.rstrip(), bad


def component_to_json(c, csize, cp):
    """组件 → JSON 可序列化 dict。含文本视图;若经 DDIC 丰富化,附加类型化数据。"""
    d = dict(kind=c["kind"], name=c["name"])
    if c["kind"] == "value":
        txt, bad = decode_row(c["value"], csize, cp)
        d["value_text"] = txt
        d["value_bytes"] = len(c["value"])
        d["non_printable"] = bad
    else:
        d["fields"] = [list(f) for f in c["fields"]]
        d["row_bytes"] = sum(f[-1] for f in c["fields"]) if c["kind"] == "struct" else None
        d["rows"] = []
        for r in c["rows"]:
            txt, bad = decode_row(r, csize, cp)
            d["rows"].append(dict(text=txt, bytes=len(r), non_printable=bad))
        # 未类型化的匿名网格:按描述符宽度切列,列起点固定(宽度校验不过则不产出)
        widths = [f[-1] for f in c["fields"]]
        if widths and c["rows"] and all(len(r) == sum(widths) for r in c["rows"]):
            grid = []
            for r in c["rows"]:
                vals, off = [], 0
                for w in widths:
                    chunk = r[off:off + w]
                    off += w
                    vals.append(decode_row(chunk, csize, cp)[0])
                grid.append(vals)
            d["cols"] = widths
            d["grid"] = grid
    if c.get("typed"):
        d["ddic"] = True
        d["columns"] = c["columns"]
        d["typed_rows"] = c["typed_rows"]
    else:
        d["ddic"] = False
    return d
