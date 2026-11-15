#!/usr/bin/env python3
"""indx_tool.py — SAP INDX 型数据簇通用解析工具

针对 SAP 数据簇表(RELID/SRTFD/SRTF2/CLUSTR/CLUSTD 结构,如 INDX、STXL 及各种
INDX 型自定义表)的提取与解析:

    容器(FF+版本+代码页+LZH) → LZH 解压 → 标记流 → 组件(单值/结构/表)

支持容器版本 3-6、代码页 1100(latin-1)与 4102/4103(UTF-16)、单值/结构/表
三类组件、空表、v6 be/bc/bd/bf 行区。LZH 解压源自 aamelin1/sap-long-texts 的
参考实现(SQLScript 的可执行规格移植),标记流语法为对通用 INDX 的实证逆向。

用法:
  indx_tool.py tables                                  # 发现 INDX 型表
  indx_tool.py stats INDX                              # 按 RELID 汇总
  indx_tool.py list INDX --relid TN --limit 20         # 簇清单
  indx_tool.py parse INDX TN QUEUE_ID [--json]         # 取数并解析
  indx_tool.py parse-file cluster.bin [--json]         # 离线解析本地文件
  indx_tool.py dump INDX GC TRPT -o cluster.bin        # 导出原始簇
  indx_tool.py scan INDX --limit 50                    # 批量解析成功率
  indx_tool.py export INDX --relid TN -o out_dir       # 批量导出 JSON

连接: --host/--port/--user/--password,或环境变量 HANA_HOST/HANA_PORT/
HANA_USER/HANA_PASSWORD(默认 localhost:30215,SAPA4H,试验镜像默认密码)。
"""
import argparse
import json
import os
import sys

# ---------------------------------------------------------------- LZH 解压
# 移植自 aamelin1/sap-long-texts stxl_decode.py(SAP LZH = LZ77 + 规范 Huffman,
# deflate 风格;SAP MaxDB CsObjectInt 同源算法)

class BitReader:
    def __init__(self, data, pos):
        self.data, self.pos, self.buf, self.cnt = data, pos, 0, 0

    def bit(self):
        if self.cnt == 0:
            self.buf = self.data[self.pos]
            self.pos += 1
            self.cnt = 8
        b = self.buf & 1
        self.buf >>= 1
        self.cnt -= 1
        return b

    def bits(self, n):
        v = 0
        for k in range(n):
            v += self.bit() << k
        return v


class Huff:
    def __init__(self, lengths):
        maxlen = 15
        self.cnt = [0] * (maxlen + 1)
        for l in lengths:
            if l > 0:
                self.cnt[l] += 1
        self.first = [0] * (maxlen + 1)
        self.offs = [0] * (maxlen + 1)
        code = tot = 0
        pos = [0] * (maxlen + 1)
        for l in range(1, maxlen + 1):
            if l > 1:
                code = (code + self.cnt[l - 1]) * 2
            self.first[l] = code
            self.offs[l] = tot
            tot += self.cnt[l]
            pos[l] = tot - self.cnt[l]
        self.syms = [0] * tot
        for sym, l in enumerate(lengths):
            if l > 0:
                self.syms[pos[l]] = sym
                pos[l] += 1

    def decode(self, br):
        code = length = 0
        while True:
            code = code * 2 + br.bit()
            length += 1
            if length > 15:
                raise ValueError("LZH: 无效 Huffman 码")
            if self.cnt[length] > 0:
                tmp = code - self.first[length]
                if 0 <= tmp < self.cnt[length]:
                    return self.syms[self.offs[length] + tmp]


LEN_EXTRA = [0] * 8 + [c for c in range(1, 6) for _ in range(4)] + [0]
LEN_BASE = []
_c = 3
for _j in range(28):
    LEN_BASE.append(_c)
    _c += 1 << LEN_EXTRA[_j]
LEN_BASE.append(258)
LEN_EXTRA = LEN_EXTRA[:29]
DST_EXTRA = [0 if j <= 3 else (j - 2) // 2 for j in range(30)]
DST_BASE = []
_c = 1
for _j in range(30):
    DST_BASE.append(_c)
    _c += 1 << DST_EXTRA[_j]
CL_ORDER = [16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15]


def inflate_lzh(data, expect):
    br = BitReader(data, 24)
    out, win, wc = [], [0] * 16384, 0
    v1 = br.bits(2)
    if v1 > 0:
        br.bits(v1)
    last = 0
    while last == 0:
        last = br.bits(1)
        btype = br.bits(2)
        if btype == 1:
            lit_len = [8] * 144 + [9] * 112 + [7] * 24 + [8] * 8
            dst_len = [5] * 30
        elif btype == 2:
            nlit, ndist, nbl = 257 + br.bits(5), 1 + br.bits(5), 4 + br.bits(4)
            cl_len = [0] * 19
            for j in range(nbl):
                cl_len[CL_ORDER[j]] = br.bits(3)
            cl_huff = Huff(cl_len)

            def read_lengths(n):
                res, lastlen = [], 0
                while len(res) < n:
                    c = cl_huff.decode(br)
                    if c < 16:
                        res.append(c)
                        lastlen = c
                    elif c == 16:
                        res += [lastlen] * (3 + br.bits(2))
                    elif c == 17:
                        res += [0] * (3 + br.bits(3))
                        lastlen = 0
                    else:
                        res += [0] * (11 + br.bits(7))
                        lastlen = 0
                return res

            lit_len = read_lengths(nlit)
            dst_len = read_lengths(ndist)
        else:
            raise ValueError("LZH: 未知块类型 %d" % btype)
        lit, dst = Huff(lit_len), Huff(dst_len)
        while True:
            sym = lit.decode(br)
            if sym < 256:
                out.append(sym)
                win[wc % 16384] = sym
                wc += 1
            elif sym == 256:
                break
            else:
                lc = sym - 256
                mlen = LEN_BASE[lc - 1] + br.bits(LEN_EXTRA[lc - 1])
                dc = dst.decode(br)
                dist = DST_BASE[dc] + br.bits(DST_EXTRA[dc])
                src = wc - dist
                for _ in range(mlen):
                    b = win[src % 16384]
                    out.append(b)
                    win[wc % 16384] = b
                    wc += 1
                    src += 1
    if len(out) != expect:
        raise ValueError("LZH: 解压长度不匹配 %d != %d" % (len(out), expect))
    return out


# ---------------------------------------------------------------- 标记流解析

CLASS_BYTES = (1, 2, 3)
ANCHOR_MARKS = (0xBB, 0xAB, 0xAD, 0xBC)


def u16be(b, o):
    return (b[o] << 8) | b[o + 1]


def u32be(b, o):
    return (b[o] << 24) | (b[o + 1] << 16) | (b[o + 2] << 8) | b[o + 3]


def printable_start(out, e, lo, csize=1):
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
        return bytes(out[s:e]).decode("latin1").rstrip()
    return "".join(chr(out[i] + out[i + 1] * 256)
                   for i in range(s, e, 2)).rstrip()


def try_component(out, p, ver, csize=1):
    n = len(out)

    # v6 表描述符特例: [03][头32B,名字长@+11][名字@+32][组+字段][be 行区]
    if ver >= 6 and out[p] == 3 and p + 12 <= n:
        namelen = out[p + 11]
        if 1 <= namelen <= 60:
            tname = read_name(out, p + 32, p + 32 + namelen * csize, csize)
            qq = p + 32 + namelen * csize
            rowsum, rows, bound = 0, None, min(qq + 8192, n)
            while qq < bound:
                op = out[qq]
                if op in (0xAA, 0xAF):
                    rowsum += u32be(out, qq + 3)
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
                return dict(kind="table", name=tname, fields=[("v6", rowsum)],
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


# ---------------------------------------------------------------- 容器层

class ToolError(Exception):
    pass


def decompress_container(data):
    """data: bytes。返回 (info dict, 解压后 bytes list)"""
    if len(data) < 24:
        raise ToolError("簇只有 %d 字节,不是标准容器" % len(data))
    b = list(data)
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


CP_MAP = {"1100": "latin-1", "1400": "iso8859-2", "1500": "iso8859-5",
          "1610": "windows-1252"}


def decode_row(row, csize, cp="1100"):
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
    d = dict(kind=c["kind"], name=c["name"])
    if c["kind"] == "value":
        txt, bad = decode_row(c["value"], csize, cp)
        d["value_text"] = txt
        d["value_bytes"] = len(c["value"])
        d["non_printable"] = bad
    else:
        d["fields"] = [[t, l] for t, l in c["fields"]]
        d["row_bytes"] = sum(l for _, l in c["fields"]) if c["kind"] == "struct" else None
        d["rows"] = []
        for r in c["rows"]:
            txt, bad = decode_row(r, csize, cp)
            d["rows"].append(dict(text=txt, bytes=len(r), non_printable=bad))
    return d


# ---------------------------------------------------------------- 数据库层

def connect(args):
    try:
        from hdbcli import dbapi
    except ImportError:
        raise ToolError("需要 hdbcli: pip3 install --break-system-packages --user hdbcli")
    conn = dbapi.connect(address=args.host, port=args.port, user=args.user,
                         password=args.password)
    return conn


def qid(schema, table):
    return '"%s"."%s"' % (schema.replace('"', '""'), table.replace('"', '""'))


def _current_schema(cur):
    cur.execute("SELECT CURRENT_SCHEMA FROM DUMMY")
    return cur.fetchone()[0]


def table_keyinfo(conn, schema, table):
    """簇身份列 = SRTF2 之前的所有列。

    ABAP 簇表约定: SRTF2 是最后一个键列(续块号),其后是非键数据字段
    (LOEKZ/AEDAT...)+ CLUSTR + CLUSTD。故物理顺序在 SRTF2 之前的列
    (MANDT/RELID/SRTFD/其他业务键)构成簇身份,对 INDX 和 STXL 都成立。
    """
    cur = conn.cursor()
    cur.execute("""SELECT COLUMN_NAME, POSITION FROM TABLE_COLUMNS
                   WHERE SCHEMA_NAME=? AND TABLE_NAME=? ORDER BY POSITION""",
                (schema, table))
    cols = [(r[0], r[1]) for r in cur.fetchall()]
    names = [c for c, _ in cols]
    for c in ("SRTF2", "CLUSTR", "CLUSTD"):
        if c not in names:
            raise ToolError("%s.%s 不是簇表(缺 %s 列,现有: %s)" %
                            (schema, table, c, ", ".join(names[:8])))
    srtf2_pos = next(p for c, p in cols if c == "SRTF2")
    identity = [c for c, p in cols if p < srtf2_pos]
    if "RELID" not in identity:
        raise ToolError("%s.%s 键结构异常: RELID 不在 SRTF2 之前" % (schema, table))
    return identity


def fetch_cluster(conn, schema, table, identity, kv):
    kv = dict(kv)
    missing = [c for c in identity if c not in kv]
    cur = conn.cursor()
    if missing:
        if len(missing) > 1:
            raise ToolError("缺少簇键值: %s(用 -k 列=值 提供所有键列)" % ", ".join(missing))
        # 单个缺失键列: 尝试其所有取值,唯一命中则采用(如 MANDT 的 000/001)
        col = missing[0]
        cur.execute('SELECT DISTINCT "%s" FROM %s' % (col, qid(schema, table)))
        candidates = [r[0] for r in cur.fetchall()][:64]
        where_rest = " AND ".join('%s=?' % c for c in identity if c != col)
        params_rest = [kv[c] for c in identity if c != col]
        matched = []
        for val in candidates:
            cur.execute('SELECT 1 FROM %s WHERE "%s"=? AND %s' %
                        (qid(schema, table), col, where_rest), [val] + params_rest)
            if cur.fetchone():
                matched.append(val)
        if len(matched) == 1:
            kv[col] = matched[0]
        elif len(matched) > 1:
            raise ToolError("簇键 %s 有多个取值都包含此簇: %r(用 -k %s=值 指定)" %
                            (col, matched, col))
        else:
            raise ToolError("簇不存在: %s (键 %s 在候选值 %r 中均无匹配)" %
                            (", ".join("%s=%r" % (c, kv[c]) for c in identity if c in kv),
                             col, candidates[:8]))
    where = " AND ".join('%s=?' % c for c in identity)
    cur = conn.cursor()
    cur.execute("SELECT SRTF2, CLUSTR, CLUSTD FROM %s WHERE %s ORDER BY SRTF2"
                % (qid(schema, table), where), [kv[c] for c in identity])
    rows = cur.fetchall()
    if not rows:
        raise ToolError("簇不存在: %s" % ", ".join("%s=%r" % (c, kv[c]) for c in identity))
    return b"".join(bytes(r[2])[:r[1]] for r in rows)


def iter_cluster_ids(conn, schema, table, identity, relid, limit):
    """按簇身份分组遍历。返回 [(kv dict, nchunks, total_bytes), ...] 按大小升序"""
    where = " WHERE RELID=?" if relid else ""
    group = ", ".join(identity)
    cur = conn.cursor()
    cur.execute("""SELECT %s, COUNT(*), SUM(LENGTH(CLUSTD)) FROM %s%s
                   GROUP BY %s ORDER BY SUM(LENGTH(CLUSTD)) ASC"""
                % (group, qid(schema, table), where, group),
                [relid] if relid else [])
    out = []
    for row in cur.fetchall():
        kv = dict(zip(identity, row[:len(identity)]))
        out.append((kv, row[-2], row[-1] or 0))
    return out[:limit]


# ---------------------------------------------------------------- 子命令

def cmd_tables(args):
    conn = connect(args)
    cur = conn.cursor()
    schema = args.schema or _current_schema(cur)
    cur.execute("""SELECT TABLE_NAME FROM TABLE_COLUMNS
                   WHERE SCHEMA_NAME=? AND COLUMN_NAME IN
                   ('RELID','SRTF2','CLUSTR','CLUSTD')
                   GROUP BY TABLE_NAME
                   HAVING COUNT(DISTINCT COLUMN_NAME)=4
                   ORDER BY TABLE_NAME""", (schema,))
    tables = [r[0] for r in cur.fetchall()]
    conn.close()
    print("schema %s 中簇表(INDX 型)%d 张:" % (schema, len(tables)))
    for t in tables:
        print("  " + t)
    if args.json:
        print(json.dumps(tables, ensure_ascii=False))


def resolve_table(args, table_arg):
    if "." in table_arg:
        schema, table = table_arg.split(".", 1)
    else:
        schema, table = None, table_arg
    if schema is None:
        conn = connect(args)
        cur = conn.cursor()
        schema = _current_schema(cur)
        conn.close()
    return schema, table


def _label(identity, kv):
    """簇身份的简短显示: RELID/SRTFD 直接显示值,其余键列带列名"""
    parts = []
    for c in identity:
        if c == "MANDT":
            continue
        if c in ("RELID", "SRTFD"):
            parts.append(str(kv.get(c)))
        else:
            parts.append("%s=%s" % (c, kv.get(c)))
    return "/".join(parts)[:60]


def cmd_stats(args):
    schema, table = resolve_table(args, args.table)
    conn = connect(args)
    cur = conn.cursor()
    cur.execute("""SELECT RELID, COUNT(*), SUM(LENGTH(CLUSTD)) FROM %s
                   GROUP BY RELID ORDER BY 3 DESC""" % qid(schema, table))
    rows = cur.fetchall()
    conn.close()
    print("%s.%s: %d 行 / %d 字节 CLUSTD,按 RELID 分布:" %
          (schema, table, sum(r[1] for r in rows), sum(r[2] or 0 for r in rows)))
    for r in rows[:args.limit]:
        print("  %-4r 行 %6d  %10d B" % (r[0], r[1], r[2] or 0))
    if len(rows) > args.limit:
        print("  ... 其余 %d 个 RELID" % (len(rows) - args.limit))


def cmd_list(args):
    schema, table = resolve_table(args, args.table)
    conn = connect(args)
    identity = table_keyinfo(conn, schema, table)
    targets = iter_cluster_ids(conn, schema, table, identity, args.relid, args.limit)
    conn.close()
    print("簇键列: %s" % ", ".join(identity))
    print("簇 %d 个(按大小升序):" % len(targets))
    for kv, nch, tot in targets:
        print("  %-44s %3d 块  %9d B" % (_label(identity, kv), nch, tot))


def _kv_from_args(args, identity):
    """收集簇键值: -k 列=值(可重复)+ 标准表的位置参数 RELID/SRTFD"""
    kv = {}
    for item in (args.kv or []):
        if "=" not in item:
            raise ToolError("-k 参数格式应为 列=值: %r" % item)
        k, v = item.split("=", 1)
        kv[k.upper()] = v
    if getattr(args, "relid", None):
        kv.setdefault("RELID", args.relid)
    if getattr(args, "srtfd", None):
        kv.setdefault("SRTFD", args.srtfd)
    return kv


def cmd_dump(args):
    schema, table = resolve_table(args, args.table)
    conn = connect(args)
    identity = table_keyinfo(conn, schema, table)
    blob = fetch_cluster(conn, schema, table, identity, _kv_from_args(args, identity))
    conn.close()
    if args.out:
        with open(args.out, "wb") as f:
            f.write(blob)
        print("已导出 %s (%d 字节)" % (args.out, len(blob)))
    else:
        print(blob.hex())


def _parse_blob(blob):
    info, out = decompress_container(blob)
    comps = parse_stream(out, info["version"], info["char_size"])
    covered = max((c["end"] for c in comps), default=0) * 100 // max(len(out), 1)
    named = sum(1 for c in comps if len(c["name"]) >= 3)
    return info, out, comps, covered, named


def cmd_parse(args):
    schema, table = resolve_table(args, args.table)
    conn = connect(args)
    identity = table_keyinfo(conn, schema, table)
    kv = _kv_from_args(args, identity)
    blob = fetch_cluster(conn, schema, table, identity, kv)
    conn.close()
    info, out, comps, covered, named = _parse_blob(blob)
    if args.json:
        print(json.dumps(dict(
            cluster=dict(keys=kv, table="%s.%s" % (schema, table)),
            container=info, coverage_pct=covered, components_named=named,
            components=[component_to_json(c, info["char_size"], info["codepage"])
                        for c in comps]), ensure_ascii=False, indent=2))
        return
    _print_parsed(kv.get("RELID", "(kv)"), _label(identity, kv),
                  info, comps, covered, named)


def _print_parsed(relid, srtfd, info, comps, covered, named):
    print("簇 %r/%s  容器 v%d 代码页 %s  %d→%d B (%s)  组件 %d(有名 %d)  覆盖 %d%%" %
          (relid, srtfd[:40], info["version"], info["codepage"],
           info["compressed_bytes"], info["uncompressed_bytes"],
           info["algorithm"] or "未压缩", len(comps), named, covered))
    cs = info["char_size"]
    for c in comps:
        if c["kind"] == "value":
            txt, bad = decode_row(c["value"], cs, info["codepage"])
            flag = "" if bad == 0 else "  [%d 非打印]" % bad
            print("  [单值] %-32s -> '%s'%s" % (c["name"][:32], txt[:70], flag))
        else:
            fl = " ".join("%s:%s" % (t, l) for t, l in c["fields"][:8])
            print("  [%s] %-32s 字段[%s] %d 行" %
                  ("表" if c["kind"] == "table" else "结构", c["name"][:32], fl, len(c["rows"])))
            for r in c["rows"][:3]:
                txt, bad = decode_row(r, cs, info["codepage"])
                flag = "" if bad == 0 else "  [%d 非打印]" % bad
                print("      | %s%s" % (txt[:100], flag))


def cmd_parse_file(args):
    raw = open(args.file, "rb").read()
    # 自动识别 hex 文本
    try:
        text = raw.decode("ascii").strip()
        if text and all(c in "0123456789abcdefABCDEF \r\n\t" for c in text) and len(text) >= 8:
            raw = bytes.fromhex("".join(text.split()))
    except (UnicodeDecodeError, ValueError):
        pass
    info, out, comps, covered, named = _parse_blob(raw)
    if args.json:
        print(json.dumps(dict(
            file=args.file, container=info, coverage_pct=covered,
            components_named=named,
            components=[component_to_json(c, info["char_size"], info["codepage"])
                        for c in comps]), ensure_ascii=False, indent=2))
        return
    _print_parsed("(file)", os.path.basename(args.file), info, comps, covered, named)


def cmd_scan(args):
    schema, table = resolve_table(args, args.table)
    conn = connect(args)
    identity = table_keyinfo(conn, schema, table)
    targets = iter_cluster_ids(conn, schema, table, identity, args.relid, args.limit)
    ok = fail = 0
    for kv, _nch, _tot in targets:
        label = _label(identity, kv)
        try:
            blob = fetch_cluster(conn, schema, table, identity, kv)
            info, out, comps, covered, named = _parse_blob(blob)
            if covered >= 95 and comps:
                ok += 1
                print("✅ %-44s %7dB→%8dB v%d 组件%3d 覆盖%3d%%" %
                      (label, info["compressed_bytes"], info["uncompressed_bytes"],
                       info["version"], len(comps), covered))
            else:
                fail += 1
                print("❌ %-44s v%d 覆盖%3d%% 组件%d" %
                      (label, info["version"], covered, len(comps)))
        except Exception as e:
            fail += 1
            print("❌ %-44s %s" % (label, e))
    conn.close()
    print("\n扫描结果: ✅ %d  ❌ %d / %d" % (ok, fail, len(targets)))


def cmd_export(args):
    schema, table = resolve_table(args, args.table)
    os.makedirs(args.out, exist_ok=True)
    conn = connect(args)
    identity = table_keyinfo(conn, schema, table)
    targets = iter_cluster_ids(conn, schema, table, identity, args.relid, args.limit)
    ok = fail = 0
    for kv, _nch, _tot in targets:
        try:
            blob = fetch_cluster(conn, schema, table, identity, kv)
            info, out, comps, covered, named = _parse_blob(blob)
            safe = "".join(c if c.isalnum() or c in "-_" else "_"
                           for c in _label(identity, kv))[:80]
            path = os.path.join(args.out, safe + ".json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(dict(cluster=dict(keys=kv,
                                            table="%s.%s" % (schema, table)),
                               container=info, coverage_pct=covered,
                               components_named=named,
                               components=[component_to_json(c, info["char_size"],
                                                             info["codepage"])
                                           for c in comps]),
                          f, ensure_ascii=False, indent=1)
            ok += 1
        except Exception as e:
            fail += 1
            print("❌ %s: %s" % (_label(identity, kv), e))
    conn.close()
    print("导出 %d 个簇到 %s (失败 %d)" % (ok, args.out, fail))


# ---------------------------------------------------------------- CLI

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default=os.environ.get("HANA_HOST", "localhost"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("HANA_PORT", "30215")))
    ap.add_argument("--user", default=os.environ.get("HANA_USER", "SAPA4H"))
    ap.add_argument("--password", default=os.environ.get("HANA_PASSWORD", "ABAPtr2025#SP00"))
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("tables", help="发现 INDX 型表")
    p.add_argument("--schema")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_tables)

    p = sub.add_parser("stats", help="按 RELID 汇总")
    p.add_argument("table")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(fn=cmd_stats)

    p = sub.add_parser("list", help="簇清单")
    p.add_argument("table")
    p.add_argument("--relid")
    p.add_argument("--limit", type=int, default=30)
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("parse", help="取数并解析一个簇")
    p.add_argument("table")
    p.add_argument("relid", nargs="?", help="标准簇表的 RELID")
    p.add_argument("srtfd", nargs="?", help="标准簇表的 SRTFD")
    p.add_argument("-k", "--kv", action="append",
                   help="簇键列=值,可重复;非标准键结构的簇表需用 -k 提供全部键列")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_parse)

    p = sub.add_parser("parse-file", help="离线解析本地簇文件(.bin/.hex)")
    p.add_argument("file")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_parse_file)

    p = sub.add_parser("dump", help="导出原始簇字节")
    p.add_argument("table")
    p.add_argument("relid", nargs="?")
    p.add_argument("srtfd", nargs="?")
    p.add_argument("-k", "--kv", action="append", help="簇键列=值,可重复")
    p.add_argument("-o", "--out")
    p.set_defaults(fn=cmd_dump)

    p = sub.add_parser("scan", help="批量解析并报告成功率")
    p.add_argument("table")
    p.add_argument("--relid")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(fn=cmd_scan)

    p = sub.add_parser("export", help="批量解析导出 JSON")
    p.add_argument("table")
    p.add_argument("--relid")
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("-o", "--out", required=True)
    p.set_defaults(fn=cmd_export)

    args = ap.parse_args()
    try:
        args.fn(args)
    except ToolError as e:
        print("错误: %s" % e, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
