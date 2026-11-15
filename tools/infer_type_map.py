#!/usr/bin/env python3
"""一次性:扫描大量真实簇,推断标记流描述符 type 字节 ↔ DDIC DATATYPE 映射。

方法:解析随机簇 → 结构化组件的名字查 DD02L → 命中则拉 DD03L 字段表,
与标记流 (type,len) 序列做位置对齐,统计 (type_byte, DATATYPE) 组合及
长度一致性,输出可直接固化的映射表。运行: python3 tools/infer_type_map.py
"""
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sap_indx_decoder.db import connect, fetch_cluster, iter_cluster_ids, table_keyinfo

def cli_args(host, port, user, password):
    class A:
        pass
    a = A()
    a.host, a.port, a.user, a.password = host, port, user, password
    return a

def main():
    args = cli_args("localhost", 30215,
                    os.environ.get("HANA_USER", "SAPA4H"),
                    os.environ.get("HANA_PASSWORD", "ABAPtr2025#SP00"))
    conn = connect(args)
    identity = ["MANDT", "RELID", "SRTFD"]
    cur = conn.cursor()

    # 覆盖大簇(GC/LI 的丰富结构)+ 随机簇
    cur.execute("""SELECT RELID, SRTFD FROM SAPA4H.INDX GROUP BY RELID, SRTFD
                   ORDER BY RAND() LIMIT 60""")
    targets = [("GC", "TRPT"), ("LI", "PSAPLSZA0"), ("LI", "PSAPLSDMX")] + \
              [(r[0], r[1]) for r in cur.fetchall()]

    combos = defaultdict(Counter)      # (type,len) -> Counter{DATATYPE: n}
    field_counts = []                  # (组件字段数, DD03L字段数)
    hit = miss = 0
    miss_names = Counter()
    seen_names = set()

    for relid, srtfd in targets:
        try:
            blob = fetch_cluster(conn, "SAPA4H", "INDX", identity,
                                 dict(RELID=relid, SRTFD=srtfd, MANDT="000"))
        except Exception:
            try:
                blob = fetch_cluster(conn, "SAPA4H", "INDX", identity,
                                     dict(RELID=relid, SRTFD=srtfd, MANDT="001"))
            except Exception:
                continue
        try:
            from sap_indx_decoder import parse_blob
            info, out, comps, covered, named = parse_blob(blob)
        except Exception:
            continue
        for c in comps:
            if c["kind"] == "value" or len(c["name"]) < 3:
                continue
            name = c["name"]
            if name in seen_names:
                continue
            seen_names.add(name)
            cur.execute("SELECT TABCLASS FROM DD02L WHERE TABNAME=? AND AS4LOCAL='A'",
                        (name,))
            row = cur.fetchone()
            if not row:
                miss += 1
                miss_names[name] += 1
                continue
            hit += 1
            cur.execute("""SELECT FIELDNAME, DATATYPE, LENG, DECIMALS
                           FROM DD03L WHERE TABNAME=? AND AS4LOCAL='A'
                           ORDER BY POSITION""", (name,))
            dd03l = [r for r in cur.fetchall() if r[1] not in ("STRG", "SSTR", "RSTR")]
            dd03l = [r for r in dd03l if r[0] != ".INCLUDE"]
            if len(dd03l) != len(c["fields"]):
                field_counts.append((name, len(c["fields"]), len(dd03l)))
                continue
            for (t, l), (fname, dt, leng, dec) in zip(c["fields"], dd03l):
                combos[(t, l)][dt] += 1

    print(f"结构化组件 {len(seen_names)} 个: DD02L 命中 {hit}, 未命中 {miss}")
    if miss_names:
        print("  未命中示例:", miss_names.most_common(8))
    mismatched = [fc for fc in field_counts]
    print(f"字段数不匹配 {len(mismatched)} 个:", mismatched[:5])
    print("\n(type,len) → DATATYPE 组合统计:")
    for (t, l), ctr in sorted(combos.items(), key=lambda kv: -sum(kv[1].values())):
        print(f"  type={t:<3} len={l:<5} → {dict(ctr)}")

if __name__ == "__main__":
    main()
