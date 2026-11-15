"""命令行入口(indx_tool 的实现主体)。"""
import argparse
import json
import os
import sys
import time

from . import ToolError, __version__, parse_blob
from .db import (connect, current_schema, fetch_cluster, iter_cluster_ids,
                 qid, table_keyinfo)
from .marker import decode_row, component_to_json


def resolve_table(args, table_arg):
    if "." in table_arg:
        schema, table = table_arg.split(".", 1)
    else:
        schema, table = None, table_arg
    if schema is None:
        conn = connect(args)
        cur = conn.cursor()
        schema = current_schema(cur)
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


# ---------------------------------------------------------------- 子命令

def cmd_tables(args):
    conn = connect(args)
    cur = conn.cursor()
    schema = args.schema or current_schema(cur)
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


def cmd_dump(args):
    schema, table = resolve_table(args, args.table)
    conn = connect(args)
    identity = table_keyinfo(conn, schema, table)
    kv = _kv_from_args(args, identity)
    blob = fetch_cluster(conn, schema, table, identity, kv)
    if args.with_ddic and args.out:
        with open(args.out, "wb") as f:
            f.write(blob)
        info, out, comps, covered, named = parse_blob(blob)
        from .ddic import DDIC
        ddic = DDIC(conn)
        defs = {}
        for c in comps:
            if c["kind"] != "value" and len(c["name"]) >= 3:
                f = ddic.get(c["name"])
                if f:
                    defs[c["name"]] = f
        conn.close()
        if defs:
            sidecar = args.out + ".ddic.json"
            with open(sidecar, "w", encoding="utf-8") as f:
                json.dump(defs, f, ensure_ascii=False)
            print("已导出 %s (%d 字节) + DDIC 侧车 %s (%d 个结构)" %
                  (args.out, len(blob), sidecar, len(defs)))
        else:
            print("已导出 %s (%d 字节) (无 DDIC 命中,未生成侧车)" % (args.out, len(blob)))
        return
    conn.close()
    if args.out:
        with open(args.out, "wb") as f:
            f.write(blob)
        print("已导出 %s (%d 字节)" % (args.out, len(blob)))
    else:
        print(blob.hex())


def _load_sidecar(path):
    """<file>.ddic.json 侧车 → DDIC 预载数据(不存在返回 None)。"""
    for cand in (path + ".ddic.json", os.path.splitext(path)[0] + ".ddic.json"):
        if os.path.exists(cand):
            with open(cand, encoding="utf-8") as f:
                return json.load(f)
    return None


def _maybe_enrich(comps, info, conn, no_ddic):
    """在线模式下用 DD02L/DD03L 丰富组件(类型化行 + 列名)。"""
    if no_ddic or conn is None:
        return
    from .ddic import DDIC, enrich_components
    enrich_components(comps, info, DDIC(conn))


def cmd_parse(args):
    schema, table = resolve_table(args, args.table)
    conn = connect(args)
    identity = table_keyinfo(conn, schema, table)
    kv = _kv_from_args(args, identity)
    blob = fetch_cluster(conn, schema, table, identity, kv)
    info, out, comps, covered, named = parse_blob(blob)
    _maybe_enrich(comps, info, conn, args.no_ddic)
    conn.close()
    if args.json:
        print(json.dumps(dict(
            cluster=dict(keys=kv, table="%s.%s" % (schema, table)),
            container=info, coverage_pct=covered, components_named=named,
            components=[component_to_json(c, info["char_size"], info["codepage"])
                        for c in comps]), ensure_ascii=False, indent=2))
        return
    _print_parsed(kv.get("RELID", "(kv)"), _label(identity, kv),
                  info, comps, covered, named)


def _print_typed_table(c):
    cols = c["columns"]
    names = [col["name"] for col in cols]
    rows = c["typed_rows"]
    widths = []
    for i, n in enumerate(names):
        w = len(n)
        for r in rows[:20]:
            w = max(w, len(str(r.get(n))[:24]))
        widths.append(min(w, 24))
    head = "  ".join(n[:w].ljust(w) for n, w in zip(names, widths))
    print("      " + head[:160])
    for r in rows[:3]:
        line = "  ".join(str(r.get(n))[:w].ljust(w) for n, w in zip(names, widths))
        print("      " + line[:160])
    if len(rows) > 3:
        print("      ... 共 %d 行" % len(rows))


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
            fl = " ".join("%s:%s" % (f[-2], f[-1]) for f in c["fields"][:8])
            tag = "表" if c["kind"] == "table" else "结构"
            ddic_tag = "  [DDIC 类型化]" if c.get("typed") else ""
            print("  [%s] %-32s 字段[%s] %d 行%s" %
                  (tag, c["name"][:32], fl, len(c["rows"]), ddic_tag))
            if c.get("typed"):
                _print_typed_table(c)
            else:
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
    info, out, comps, covered, named = parse_blob(raw)
    preload = _load_sidecar(args.file)
    if preload:
        from .ddic import DDIC, enrich_components
        enrich_components(comps, info, DDIC(None, preload))
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
            info, out, comps, covered, named = parse_blob(blob)
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
            info, out, comps, covered, named = parse_blob(blob)
            _maybe_enrich(comps, info, conn, args.no_ddic)
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


def cmd_bench(args):
    """解压吞吐基准:对 examples/ 与 --file 指定的簇测 LZH 速度。"""
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    files = [args.file] if args.file else [
        os.path.join(base, "examples", n) for n in
        ("TN_QUEUE_ID.bin", "RH_SAPLRHW001TS30200106.bin", "VM_BW_STARTDEVELOPER.bin")]
    from . import decompress_container
    from .lzh import inflate_lzh, inflate_lzh_python, has_native
    total_c = total_u = 0
    for fn in files:
        if not fn or not os.path.exists(fn):
            continue
        raw = open(fn, "rb").read()
        info, out = decompress_container(raw)
        big = list(open(fn, "rb").read())
        expect = info["declared_uncompressed"] or info["uncompressed_bytes"]
        t0 = time.time()
        if has_native:
            inflate_lzh(big, expect)
        else:
            inflate_lzh_python(big, expect)
        dt = time.time() - t0
        total_c += info["compressed_bytes"]
        total_u += info["uncompressed_bytes"]
        print("  %-36s %7dB→%8dB  %.3fs  %.1f MB/s" %
              (os.path.basename(fn), info["compressed_bytes"],
               info["uncompressed_bytes"], dt,
               info["uncompressed_bytes"] / 1e6 / max(dt, 1e-9)))
    print("解码器: %s | 合计 %.1fKB→%.1fMB" %
          ("原生(Rust)" if has_native else "纯 Python",
           total_c / 1024, total_u / 1e6))


# ---------------------------------------------------------------- CLI

def main():
    ap = argparse.ArgumentParser(
        prog="sap-indx-decoder",
        description="SAP 数据簇(INDX/STXL 等簇表 CLUSTD)提取与解码工具")
    ap.add_argument("--host", default=os.environ.get("HANA_HOST", "localhost"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("HANA_PORT", "30215")))
    ap.add_argument("--user", default=os.environ.get("HANA_USER", "SAPA4H"))
    ap.add_argument("--password", default=os.environ.get("HANA_PASSWORD", "ABAPtr2025#SP00"))
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("tables", help="发现 INDX 型簇表")
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
    p.add_argument("--no-ddic", action="store_true", help="禁用 DD02L/DD03L 类型化")
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
    p.add_argument("--with-ddic", action="store_true",
                   help="同时导出 .ddic.json 侧车(簇内结构的 DD03L 定义)")
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
    p.add_argument("--no-ddic", action="store_true", help="禁用 DD02L/DD03L 类型化")
    p.add_argument("-o", "--out", required=True)
    p.set_defaults(fn=cmd_export)

    p = sub.add_parser("bench", help="LZH 解压吞吐基准")
    p.add_argument("--file", help="指定单个簇文件;缺省用 examples/")
    p.set_defaults(fn=cmd_bench)

    args = ap.parse_args()
    try:
        args.fn(args)
    except ToolError as e:
        print("错误: %s" % e, file=sys.stderr)
        sys.exit(1)
