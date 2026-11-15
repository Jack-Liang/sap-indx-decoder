# sap-indx-decoder

Decode SAP data clusters — the binary blobs inside `INDX`, `STXL`, and every
other INDX-style cluster table — **without an SAP system**.

`EXPORT ... TO DATABASE` writes ABAP variables into cluster tables as
`CLUSTD` blobs: a proprietary container, optionally LZH-compressed, wrapping a
self-describing serialization of your data. SE16 shows you the hex; this tool
shows you the data.

> **📖 The full reverse-engineering story** (in Chinese):
> [逆向 SAP 数据簇格式:不登录 SAP 也能读懂 INDX 表里的二进制](https://jack-liang.com/blog/sap-indx-data-cluster-decoder/)

```
$ python3 indx_tool.py parse INDX TN QUEUE_ID
簇 'TN'/TN/QUEUE_ID  容器 v6 代码页 1100  162→769 B (LZH)  组件 1(有名 1)  覆盖 99%
  [表] PAT01_TAB                        字段[v6:154] 4 行
      | SAPKB70202          001EPILOGUE            X
      | SAPKA70202          002EPILOGUE            X
      | SAPK-70202INPIBASIS 003EPILOGUE            X
```

## What it does

| command | description |
|---------|-------------|
| `tables` | discover all cluster tables in a schema |
| `stats` / `list` | browse clusters by area / key |
| `parse` | fetch one cluster from the DB, decompress, parse, print |
| `parse-file` | same, from an offline `.bin`/`.hex` dump |
| `dump` | export raw cluster bytes |
| `scan` | batch-parse with success-rate report |
| `export` | batch-export parsed clusters as JSON |

Works against any SAP HANA database (via `hdbcli`) — including the
[ABAP Cloud Developer Trial](https://hub.docker.com/r/sapse/abap-cloud-developer-trial)
Docker images — and fully offline on exported blobs. Both classic `INDX`
(`RELID`/`SRTFD` keys) and composite-key cluster tables like `STXL` are
supported; the key structure is derived from the table definition.

The full reverse-engineered container/LZH/marker-stream specification lives in
[docs/FORMAT.md](docs/FORMAT.md).

## Installation

Requires Python 3.9+. `hdbcli` is only needed for database access
(`parse-file` works without it):

```bash
pip install hdbcli
# or: pip install .   (after publishing)
```

## Usage

```bash
# against a database (env vars: HANA_HOST, HANA_PORT, HANA_USER, HANA_PASSWORD)
python3 indx_tool.py tables --schema SAPA4H
python3 indx_tool.py stats INDX
python3 indx_tool.py list INDX --relid TN
python3 indx_tool.py parse INDX TN QUEUE_ID --json

# composite-key cluster tables (STXL long texts)
python3 indx_tool.py parse STXL -k MANDT=000 -k RELID=TX -k TDOBJECT=TEXT \
    -k TDNAME=RHTEXT01 -k TDID=ST -k TDSPRAS=D

# offline
python3 indx_tool.py dump INDX GC TRPT -o trpt.bin
python3 indx_tool.py parse-file trpt.bin
python3 indx_tool.py parse-file trpt.bin --json    # machine-readable

# batch
python3 indx_tool.py scan INDX --limit 100
python3 indx_tool.py export INDX --relid TN -o out_dir
```

## Verification

On one ABAP Cloud Developer Trial 2025 system (`SAPA4H.INDX`, 5,568 rows):

* 100 random clusters: **90 parsed at ≥95 % byte coverage**; all 10 failures
  are 8 empty (all-zero) clusters and 2 exotic `nwbc_*` variants —
  ≈98 % of non-empty clusters
* `STXL` long texts: 12/12, rows decode to the documented `TLINE` layout
* LZH output always matches the container header's declared uncompressed size

The parser self-validates: row width = sum of field descriptor lengths, and
every parse reports the share of stream bytes it could explain.

## Limitations

* LZC (`0x10`) compression not implemented (LZH `0x12` is what HANA-based
  systems emit)
* numeric (non-char) field values are shown as raw bytes, not typed
* component-header hash bytes are not decoded (occasionally cosmetic noise in
  names)
* containers older than v3 and the `nwbc_uid`/`nwbc_cache_counter` variant

## Credits

* LZH decompression: Python port of the SQLScript reference decoder from
  [aamelin1/sap-long-texts](https://github.com/aamelin1/sap-long-texts) (MIT),
  which references the SAP MaxDB sources — see
  [the accompanying blog post](https://community.sap.com/t5/technology-blog-posts-by-members/beyond-read-text-decoding-sapscript-long-texts-stxl-with-sqlscript-in-cds/ba-p/14468331).
  A verbatim copy of the upstream repository is vendored under
  [`third_party/sap-long-texts/`](third_party/sap-long-texts/VENDORED.md) so
  the reference survives upstream changes
* The generic marker-stream grammar for non-STXL clusters (component classes,
  `AF` descriptors, empty tables, UTF-16, v6 row sections) was
  reverse-engineered for this project — see docs/FORMAT.md
* [pysap](https://github.com/pysap/pysap) for prior art on SAP binary formats

## License

MIT — see [LICENSE](LICENSE).
