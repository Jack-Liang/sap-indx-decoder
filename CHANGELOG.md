# Changelog

## Unreleased

### Added
- `indx` short console-script alias (same as `sap-indx-decoder`).
- `test.yml` CI workflow: pytest matrix (3.9–3.13, macOS/Windows) on push/PR,
  plus a native build job running the Rust↔Python parity tests for real.
- `serve --port` accepted as alias of `--http-port`.

### Removed
- `indx_tool.py` compatibility shim — use `python3 -m sap_indx_decoder` or the
  installed `sap-indx-decoder` console script.

### Changed
- Build artifacts (`native/target/`, `sap_indx_decoder/_native.abi3.so`) are no
  longer tracked in git; wheels are built per-platform by CI.

## 0.2.0 — 2026-11-15

### Added
- **Typed decoding**: component rows decode to named, typed columns when the
  component name exists in the ABAP data dictionary (`DD02L`/`DD03L`) —
  `TLINE` → `TDFORMAT`/`TDLINE`, `INT4` → real integers. Descriptor type-byte
  semantics (`0=C 1=D 3=T 6=N 8=I`) reverse-engineered and verified against
  DD03L ground truth. `--no-ddic` disables.
- **Native LZH decompressor in Rust** (PyO3/maturin, abi3-py39):
  4.5 MB in ~7 ms (≈680 MB/s, ~60× pure-Python). Byte-for-byte
  differential-tested against the pure-Python implementation on fixtures and a
  60-cluster live corpus; graceful fallback when the native module is absent.
- **Local web preview**: `serve` (stdlib `http.server`, no framework) —
  browse tables → clusters → typed component tables with hex toggle.
  `--offline-dir` mode browses dumped `.bin` files without a database.
- **Export formats**: `export --format json|sqlite|parquet` (parquet via
  optional `pyarrow`, extra `[parquet]`).
- **DDIC sidecar**: `dump --with-ddic` writes `<file>.ddic.json`;
  `parse-file` auto-loads it so offline parses stay typed.
- `bench` subcommand for decompression throughput.
- `release.yml`: multi-platform wheels (manylinux x86_64/aarch64, macOS arm64,
  Windows) + sdist + PyPI trusted publishing on `v*` tags.

### Changed
- Single-file tool restructured into the `sap_indx_decoder` package
  (`lzh/container/marker/typedecode/ddic/db/export/web/cli`);
  `indx_tool.py` remains as a compatibility shim.
- Parser reports v6 field descriptors explicitly (opcode/type/length triples)
  instead of collapsing them into a single row-width.

## 0.1.0 — 2026-11-15

- Initial public release: CLI (`tables/stats/list/parse/parse-file/dump/scan/export`)
  for SAP data-cluster tables; container + LZH + marker-stream decoding
  (v3–v6, latin-1/UTF-16); vendored upstream reference under `third_party/`.
