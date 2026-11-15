"""HANA 数据库访问层:连接、簇表键结构推导、取簇、簇遍历。

簇身份推导(对 INDX 与 STXL 均成立):ABAP 簇表的 SRTF2 是最后一个
键列(续块号),其物理顺序之前的所有列构成簇身份;其后是非键数据
字段(LOEKZ/AEDAT...)+ CLUSTR + CLUSTD。
"""
from .errors import ToolError

__all__ = ["connect", "qid", "table_keyinfo", "fetch_cluster",
           "iter_cluster_ids", "current_schema"]


def connect(args):
    try:
        from hdbcli import dbapi
    except ImportError:
        raise ToolError("需要 hdbcli: pip3 install --break-system-packages --user hdbcli")
    return dbapi.connect(address=args.host, port=args.port, user=args.user,
                         password=args.password)


def qid(schema, table):
    return '"%s"."%s"' % (schema.replace('"', '""'), table.replace('"', '""'))


def current_schema(cur):
    cur.execute("SELECT CURRENT_SCHEMA FROM DUMMY")
    return cur.fetchone()[0]


def table_keyinfo(conn, schema, table):
    """返回簇身份列列表(物理顺序在 SRTF2 之前的列)。"""
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
    """按簇身份取整簇字节(按 SRTF2 排序、按 CLUSTR 截断、拼接)。

    单个缺失键列:尝试该列所有取值,唯一命中则采用(如 MANDT 的 000/001),
    多值命中报歧义。
    """
    kv = dict(kv)
    missing = [c for c in identity if c not in kv]
    cur = conn.cursor()
    if missing:
        if len(missing) > 1:
            raise ToolError("缺少簇键值: %s(用 -k 列=值 提供所有键列)" % ", ".join(missing))
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
