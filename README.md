# sap-indx-decoder

[![Test](https://github.com/Jack-Liang/sap-indx-decoder/actions/workflows/test.yml/badge.svg)](https://github.com/Jack-Liang/sap-indx-decoder/actions/workflows/test.yml)
[![PyPI](https://img.shields.io/pypi/v/sap-indx-decoder)](https://pypi.org/project/sap-indx-decoder/)
[![Python](https://img.shields.io/pypi/pyversions/sap-indx-decoder)](https://pypi.org/project/sap-indx-decoder/)

Decode SAP data clusters — the binary blobs inside `INDX`, `STXL`, and every
other INDX-style cluster table — **without an SAP system**.

`EXPORT ... TO DATABASE` writes ABAP variables into cluster tables as
`CLUSTD` blobs: a proprietary container, optionally LZH-compressed, wrapping a
self-describing serialization of your data. SE16 shows you the hex; this tool
shows you the data — **typed, column-aligned, and searchable**.

> **📖 The full reverse-engineering story** (in Chinese):
> [逆向 SAP 数据簇格式:不登录 SAP 也能读懂 INDX 表里的二进制](https://jack-liang.com/blog/sap-indx-data-cluster-decoder/)

```
$ python3 -m sap_indx_decoder parse STXL -k MANDT=000 -k RELID=TX \
    -k TDOBJECT=TEXT -k TDNAME=RHTEXT01 -k TDID=ST -k TDSPRAS=D
簇 'TX'/TX/TDOBJECT=TEXT/TDNAME=RHTEXT01/TDID=ST  容器 v5 代码页 1100  72→172 B (LZH)
  [表] TLINE                            字段[0:2 0:132] 1 行  [DDIC 类型化]
      TDFORMAT  TDLINE
      *         jdjdjdjdjdjdj
```

## What it does

| command | description |
|---------|-------------|
| `tables` | discover all cluster tables in a schema |
| `stats` / `list` | browse clusters by area / key |
| `parse` | fetch one cluster, decompress, parse, print (typed via DDIC) |
| `parse-file` | same, from an offline `.bin`/`.hex` dump (auto-loads `.ddic.json` sidecar) |
| `dump` | export raw cluster bytes (`--with-ddic` adds a DDIC sidecar) |
| `scan` | batch-parse with success-rate report |
| `export` | batch-export as `--format json\|sqlite\|parquet` |
| `serve` | local web preview (db mode or `--offline-dir`) |
| `bench` | LZH decompression throughput |

* Typed decoding: when a component name exists in the ABAP data dictionary
  (`DD02L`/`DD03L`), rows are decoded into named, typed columns — e.g. `TLINE`
  → `TDFORMAT`/`TDLINE`, `INT4` fields → real integers (little-endian). Field
  semantics come from the descriptor stream itself (`0=C 1=D 3=T 6=N 8=I`,
  confirmed against DD03L); DDIC contributes column names and validation.
* Local web preview: `serve` runs a zero-dependency UI (stdlib `http.server`)
  for browsing tables → clusters → typed component tables with a hex toggle.
  `--offline-dir` points it at dumped `.bin` files — no database needed.
* Fast: the LZH decompressor ships as a native Rust extension (PyO3, abi3)
  with a byte-for-byte differential-tested pure-Python fallback — 4.5 MB in
  ~7 ms (≈680 MB/s, ~60× the pure-Python baseline).

The full reverse-engineered container/LZH/marker-stream specification lives in
[docs/FORMAT.md](docs/FORMAT.md).

## Installation

Wheels are built by CI for Linux (x86_64/aarch64), macOS (arm64) and Windows
once a `v*` tag is pushed. Building from source requires a Rust toolchain
(`cargo`) because of the native extension; without one you can still run the
pure-Python fallback by copying `sap_indx_decoder/` (without `_native`).

```bash
pip install sap-indx-decoder[hana]     # + hdbcli for database access
pip install sap-indx-decoder[parquet]  # + pyarrow for parquet export
pip install sap-indx-decoder[dev]      # + pytest
```

## Usage

```bash
# against a database (env vars: HANA_HOST, HANA_PORT, HANA_USER, HANA_PASSWORD)
python3 -m sap_indx_decoder tables --schema SAPA4H
python3 -m sap_indx_decoder stats INDX
python3 -m sap_indx_decoder parse INDX TN QUEUE_ID
python3 -m sap_indx_decoder parse STXL -k MANDT=000 -k RELID=TX \
    -k TDOBJECT=TEXT -k TDNAME=RHTEXT01 -k TDID=ST -k TDSPRAS=D
python3 -m sap_indx_decoder parse INDX GC TRPT --json      # typed rows in JSON

# offline dumps (+ DDIC sidecar so parse-file stays typed)
python3 -m sap_indx_decoder dump STXL -k ... -o cluster.bin --with-ddic
python3 -m sap_indx_decoder parse-file cluster.bin
python3 -m sap_indx_decoder parse-file cluster.bin --json

# batch
python3 -m sap_indx_decoder scan INDX --limit 100
python3 -m sap_indx_decoder export INDX --relid TN --format sqlite -o out_dir
python3 -m sap_indx_decoder export STXL --relid TX --format parquet -o out_dir

# interactive
python3 -m sap_indx_decoder serve --open                    # db mode
python3 -m sap_indx_decoder serve --offline-dir examples    # offline mode
python3 -m sap_indx_decoder bench --file big_cluster.bin    # decompression throughput
```

Installed via pip? Use the console script: `indx parse ...` (short alias) or
`sap-indx-decoder parse ...` — same commands.

## Verification

On one ABAP Cloud Developer Trial 2025 system (`SAPA4H.INDX`, 5,568 rows):

* 100 random clusters: **90 parsed at ≥95 % byte coverage**; all 10 failures
  are 8 empty (all-zero) clusters and 2 exotic `nwbc_*` variants —
  ≈98 % of non-empty clusters
* `STXL` long texts: 12/12, rows decode to the documented `TLINE` layout
* LZH output always matches the container header's declared uncompressed size;
  native and pure-Python decoders are differential-tested byte-for-byte
  (fixtures + a 60-cluster live corpus)
* DDIC hit rate on structured components is intentionally best-effort:
  component names are ABAP variable names and often differ from DDIC names
  (e.g. `GT_MONITOR_INFO`); hits like `TLINE`/`OJTL`/`MIME` decode typed

The parser self-validates: row width = sum of field descriptor lengths, and
every parse reports the share of stream bytes it could explain.

## Architecture

```
sap_indx_decoder/
├── lzh.py         # LZH inflate: pure Python + native bridge (Rust, optional)
├── container.py   # 24-byte container header, code page, compression dispatch
├── marker.py      # anchored marker-stream parser (v3–v6) + text views
├── ddic.py        # DD02L/DD03L lookup + INTTYPE-based typed decoding
├── db.py          # hdbcli access, cluster-key inference, fetch/iterate
├── export.py      # sqlite / parquet writers
├── web.py         # serve: zero-dependency local preview
└── cli.py         # argparse front end
```

## Limitations

* LZC (`0x10`) compression not implemented (LZH `0x12` is what HANA-based
  systems emit)
* field semantics confirmed for `C/D/T/N/I`; other descriptor type bytes and
  the `AF` opcode fall back to raw hex
* component-header hash bytes are not decoded (occasionally cosmetic noise in
  names)
* containers older than v3 and the `nwbc_uid`/`nwbc_cache_counter` variant
* `DD03L`-based enrichment skips `.INCLUDE`-expanded fields and variable-length
  (`STRING`) columns

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
