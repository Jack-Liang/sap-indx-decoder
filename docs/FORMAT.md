# The SAP Data Cluster Binary Format

How `EXPORT ... TO DATABASE` stores ABAP objects inside INDX-style cluster
tables, as stored by an ABAP Platform system on SAP HANA. Reverse-engineered
byte by byte; everything below was verified against live data (see
[Verification](#verification)). Companion tool: `indx_tool.py` in this
repository.

Audience: anyone who ever looked at an `INDX`, `STXL`, or custom INDX-type
table in SE16/DBeaver and wondered what the `CLUSTD` hex blob actually is.

## 1. Cluster tables

A cluster table (ABAP DDIC table category "cluster") has this shape:

| column  | type            | meaning                          |
|---------|-----------------|----------------------------------|
| MANDT   | CLNT(3)         | client                           |
| RELID   | CHAR(2)         | cluster area id (`EXPORT TO DATABASE INDX(id)`) |
| ...     | ...             | additional key columns (e.g. `SRTFD`, or STXL's `TDOBJECT/TDNAME/TDID/TDSPRAS`) |
| SRTF2   | INT(4)/INT2     | chunk number, 0..n — **last key column** |
| ...     | ...             | non-key fields (INDX: `LOEKZ SPERR AEDAT USERA PGMID BEGDT ENDDT`) |
| CLUSTR  | INT2            | valid byte count of this chunk   |
| CLUSTD  | LRAW/VARBINARY  | chunk payload (~2886 bytes/chunk observed) |

One *logical cluster* = all rows sharing the key (everything except `SRTF2`),
concatenated in `SRTF2` order after trimming each `CLUSTD` to its `CLUSTR`
length. The last chunk may carry trailing garbage from earlier, larger writes.

Empirical rule: **every column physically before `SRTF2` is part of the cluster
key**; everything after it is payload metadata. That is how `indx_tool.py`
derives the key automatically for both INDX and STXL layouts.

## 2. Container header (versions 3–6)

The concatenated blob starts with a 24-byte container header:

```
offset  size  field
0       1     signature, always 0xFF
1       1     container version (03..06 observed)
2       6     variant metadata — not decoded, ignored
8       4     SAP code page, ASCII digits: '1100' (latin-1), '4102'/'4103' (UTF-16 BE/LE)
12      4     zeros observed
16      4     uncompressed length, uint32 LITTLE-endian (compressed case only)
20      1     compression algorithm: 0x12 = LZH, 0x10 = LZC (LZC not implemented here)
21      2     compression magic 0x1F 0x9D (inherited from Unix compress(1))
23      1     observed 0x02 — not decoded
24..          LZH bit stream (see §3)
```

* If bytes 21–22 are **not** `1F 9D`, the payload is stored **uncompressed** and
  starts at offset 16 (runs to end of blob).
* Code pages `4102`/`4103` mean every name and every row is UTF-16 (2 bytes per
  character).

The uncompressed length check is a free integrity test: a correct LZH
decompression always reproduces exactly that many bytes.

## 3. LZH decompression

SAP's LZH is LZ77 (16 KiB window) with deflate-style canonical Huffman coding —
three alphabets (literals+lengths, distances, code-lengths) exactly like
deflate, but with different framing: a 2-bit value precedes the first block,
Huffman codes accumulate MSB-first while the bit reader is LSB-first. The
reference implementation in this repo (`inflate_lzh`) is a Python port of the
SQLScript decoder published by [@aamelin1/sap-long-texts](https://github.com/aamelin1/sap-long-texts)
(MIT; vendored verbatim under [`third_party/sap-long-texts/`](../third_party/sap-long-texts/VENDORED.md)),
which in turn references the SAP MaxDB sources (`vpa108csulzh.cpp`).
The same container/LZH machinery also appears in SAPCAR archives, REPOSRC and
DIAG traffic.

## 4. The marker stream

After decompression the payload is a self-describing *marker stream*. It is a
sequence of **components** — one per ABAP variable exported into the cluster:

```
component := class_byte header name [descriptor-group] rows
class_byte := 0x01 (elementary value) | 0x02 (structure) | 0x03 (table)
```

The component header sits between the class byte and the name. Its exact
layout varies by version and class (it contains the name length, the row
width, and ~8 bytes of hash/checksum); we do not decode it — the parser
*anchors* on the name instead (see §5).

### 4.1 Field descriptors

Between the group markers, each structure field is described by:

| version | encoding                                  | notes                     |
|---------|-------------------------------------------|---------------------------|
| v3–v5   | `AA`/`AF` + type u8 + length u16 BE (4 B) | `AA` char-like, `AF` 1-byte flags — both contribute their length to the row |
| v6      | `AA`/`AF` + 6 more bytes, length = u32 BE at +3 (7 B) | |

The declared row width equals the **sum of all descriptor lengths** — this is
the strongest self-consistency check available and the parser verifies it.

Descriptor `type` byte semantics observed so far: `0x00` = char, `0x01`/`0x03`
= date/time (stored as characters), `0x06`/`0x08` = numeric/binary (values are
raw bytes, not yet decoded to numbers).

### 4.2 Groups and rows

| element | v3–v5 | v6 |
|---------|-------|----|
| group start   | `AB`/`AD` + `0E` + row width u16 BE | `AB`/`AD` + marker bytes + row width u32 BE |
| group end     | `AC`/`AE` + `0E` + row width u16 BE | `AC`/`AE` + row width u32 BE |
| row (struct)  | `BB` + row bytes | `BC` + len u32 BE + row bytes |
| row (table)   | `BB`-prefixed rows | section `BE` + row width u32 BE + row count u32 BE, then per row: `BC` + len u32 BE + data, separated by `BD`, section closed by `BF` |
| elementary    | name + `BB` + row (width from component header) | name + `BC` + len u32 BE + data |

* `AB`/`AC` wraps a structure (single row), `AD`/`AE` wraps a table (observed
  on v5; treat both end markers as equivalent in code).
* **Empty tables are legal**: the group may end without any row.
* The stream ends with `04` (plus zero filler up to the block boundary).

## 5. Anchored parsing (implementation strategy)

Not every header byte is decoded, so `indx_tool.py` uses anchoring instead of
a full state machine:

1. Scan from a class byte for the first marker in `BB/AB/AD/BC`; the printable
   run immediately before it is the component name.
2. Parse the descriptor group and **verify** `sum(lengths) == declared width`.
3. Consume rows; after the last row the next byte must be a class byte, `04`,
   `00` or end of stream — otherwise resync by scanning forward.

The tool reports a *coverage* percentage (bytes explained by validated
components), which doubles as a corruption/confidence indicator.

## 6. Verification

On one ABAP Cloud Developer Trial 2025 system (HANA 2.00.087, SID HDB,
schema `SAPA4H`, table `INDX` with 5,568 rows / 24 schemas):

* random 100 clusters: 90 parsed with ≥95 % byte coverage — the 10 failures
  are 8 all-zero (empty) clusters and 2 exotic `nwbc_uid`/`nwbc_cache_counter`
  variants; **≈98 % of non-empty clusters**
* `STXL` (SAPscript long texts, composite 6-column key): 12/12
* decompressed length always matches the container header exactly
* `STXL` rows decode to the documented `TLINE` structure (`TDFORMAT` 2 chars +
  `TDLINE` 132 chars), German umlauts and UTF-16 both handled

## 7. Known gaps

* LZC (`0x10`) decompression — not observed on HANA-based systems
* numeric field values are shown as raw bytes, not typed to INT4/PACKED/FLOAT
* the component-header layout (hash bytes) is not decoded; names may pick up
  printable hash bytes (cosmetic)
* containers of version < 3 and the `nwbc_*` exotic variant
