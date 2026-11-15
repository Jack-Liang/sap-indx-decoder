# Vendored: aamelin1/sap-long-texts

This directory contains a verbatim copy of
[github.com/aamelin1/sap-long-texts](https://github.com/aamelin1/sap-long-texts)
at commit `19c8c272b23f0ab481f7d63b6b36c45db15fd095` (2026-08-20), vendored so
the reference material survives independent of the upstream repository.

## Why vendored

`sap_indx_decoder/lzh.py` embeds a Python port of this project's `stxl_decode.py` LZH
decompression routine (the project itself is an executable Python mirror of the
author's SQLScript AMDP decoder for SAPscript long texts in `STXL-CLUSTD`).
The original is kept here verbatim — including the ABAP/CDS sources of the
`ZCLBC_LTEXT_DECODE` AMDP class — for provenance, comparison and because it is
an independently useful executable specification of the container format.

## Contents

| file | description |
|------|-------------|
| `stxl_decode.py` | Python reference decoder for STXL long-text clusters (LZH + marker stream, TLINE rows) |
| `zclbc_ltext_decode.clas.abap` | ABAP AMDP class: in-database CLUSTD decoder (SQLScript) |
| `zbc_ltext_cds_sources.abap` | CDS table-function sources wrapping the AMDP class |
| `imgs/` | illustrations from the original README |
| `LICENSE` | MIT, Copyright (c) 2026 Andrey — applies to everything in this directory |

All modifications to files outside this directory (the `sap_indx_decoder/lzh.py` port)
are covered by the repository's own LICENSE; everything inside this directory
remains under the upstream MIT license, verbatim.

Upstream: <https://github.com/aamelin1/sap-long-texts> — see also the author's
blog post: [Beyond READ_TEXT: Decoding SAPscript Long Texts with SQLScript in
CDS](https://community.sap.com/t5/technology-blog-posts-by-members/beyond-read-text-decoding-sapscript-long-texts-stxl-with-sqlscript-in-cds/ba-p/14468331).
