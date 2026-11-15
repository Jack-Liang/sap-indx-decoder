"""批量导出:SQLite(单库,stdlib)/ Parquet(可选依赖 pyarrow)。"""
import json
import os
import sqlite3

__all__ = ["sqlite_export", "parquet_export"]


def sqlite_export(path, rows):
    """rows: [(label, table_name, kv, info, comps, coverage)] → 单个 .db。

    comps 为 component_to_json 转换后的 JSON dict 列表。
    表结构:
      clusters(id, label, table_name, keys_json, container_json, coverage_pct)
      components(cluster_id, kind, name, typed, fields_json, rows_json,
                 columns_json, typed_rows_json)
    """
    conn = sqlite3.connect(path)
    c = conn.cursor()
    c.execute("""CREATE TABLE clusters(
        id INTEGER PRIMARY KEY AUTOINCREMENT, label TEXT, table_name TEXT,
        keys_json TEXT, container_json TEXT, coverage_pct INTEGER)""")
    c.execute("""CREATE TABLE components(
        id INTEGER PRIMARY KEY AUTOINCREMENT, cluster_id INTEGER, kind TEXT,
        name TEXT, typed INTEGER, fields_json TEXT, rows_json TEXT,
        columns_json TEXT, typed_rows_json TEXT)""")
    n_comp = 0
    for label, table_name, kv, info, comps, coverage in rows:
        c.execute("""INSERT INTO clusters(label, table_name, keys_json,
                     container_json, coverage_pct) VALUES (?,?,?,?,?)""",
                  (label, table_name, json.dumps(kv, ensure_ascii=False, default=str),
                   json.dumps(info, ensure_ascii=False, default=str), coverage))
        cid = c.lastrowid
        for comp in comps:
            c.execute("""INSERT INTO components(cluster_id, kind, name, typed,
                         fields_json, rows_json, columns_json, typed_rows_json)
                         VALUES (?,?,?,?,?,?,?,?)""",
                      (cid, comp["kind"], comp["name"], 1 if comp.get("ddic") else 0,
                       json.dumps(comp.get("fields"), ensure_ascii=False),
                       json.dumps(comp.get("rows"), ensure_ascii=False),
                       json.dumps(comp.get("columns"), ensure_ascii=False),
                       json.dumps(comp.get("typed_rows"), ensure_ascii=False)))
            n_comp += 1
    conn.commit()
    conn.close()
    return len(rows), n_comp


def component_payload(comp, csize, cp):
    """组件 → JSON 兼容 dict(复用 component_to_json 的文本/类型化视图)。"""
    from .marker import component_to_json
    return component_to_json(comp, csize, cp)


def parquet_export(out_dir, label, comps):
    """把一个簇的类型化组件各写成一个 .parquet;返回 (写出数, 跳过数)。

    comps 为 component_to_json 转换后的 JSON dict 列表(含 typed_rows)。
    需要 pyarrow(pip install sap-indx-decoder[parquet])。
    """
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError:
        raise RuntimeError("parquet 导出需要 pyarrow: pip install sap-indx-decoder[parquet]")
    written = skipped = 0
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in label)[:70]
    for i, comp in enumerate(comps):
        if not comp.get("ddic") or not comp.get("typed_rows"):
            skipped += 1
            continue
        table = pa.Table.from_pylist(comp["typed_rows"])
        name = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in comp["name"])[:40]
        pq.write_table(table, os.path.join(out_dir, f"{safe}__{i:02d}_{name}.parquet"))
        written += 1
    return written, skipped

