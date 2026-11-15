"""Decoder tests against fixture clusters dumped from an ABAP Cloud
Developer Trial 2025 (public SAP trial image, no personal data)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sap_indx_decoder import ToolError, decompress_container, decode_row, parse_stream

HERE = os.path.dirname(os.path.abspath(__file__))
EX = os.path.join(HERE, "..", "examples")


def load(name):
    with open(os.path.join(EX, name), "rb") as f:
        return f.read()


def parse_fixture(name):
    blob = load(name)
    info, out = decompress_container(blob)
    comps = parse_stream(out, info["version"], info["char_size"])
    covered = max((c["end"] for c in comps), default=0) * 100 // max(len(out), 1)
    return info, out, comps, covered


def test_tn_patch_queue():
    """v6 container, code page 1100, single table PAT01_TAB with 4 rows."""
    info, out, comps, covered = parse_fixture("TN_QUEUE_ID.bin")
    assert info["version"] == 6
    assert info["codepage"] == "1100"
    assert info["compressed"] and info["algorithm"] == "LZH"
    assert info["uncompressed_bytes"] == 769 == info["declared_uncompressed"]
    assert len(comps) == 1
    c = comps[0]
    assert c["kind"] == "table" and c["name"] == "PAT01_TAB"
    assert len(c["rows"]) == 4
    first = decode_row(c["rows"][0], 1, "1100")[0]
    assert first.startswith("SAPKB70202")
    assert "001EPILOGUE" in first
    assert covered >= 95


def test_rh_hr_generation_params():
    """v6 container, 8 elementary values incl. dates and times as chars."""
    info, out, comps, covered = parse_fixture("RH_SAPLRHW001TS30200106.bin")
    assert info["version"] == 6
    values = {c["name"]: decode_row(c["value"], 1, "1100")[0] for c in comps
              if c["kind"] == "value"}
    assert values["GEN_DATUM"] == "20060630"
    assert values["GEN_TIME"] == "123709"
    assert values["INDEX_BEGDA"] == "20060630"
    assert values["INDEX_ENDDA"] == "20060630"
    assert values["BACKGROUND"] == ""
    assert len(comps) == 9  # + RELATED_OBJECTS etc.
    assert covered >= 95


def test_vm_utf16_codepage():
    """v6 container, code page 4103 (UTF-16), elementary value SAP_GUID."""
    info, out, comps, covered = parse_fixture("VM_BW_STARTDEVELOPER.bin")
    assert info["version"] == 6
    assert info["codepage"] == "4103" and info["char_size"] == 2
    assert len(comps) == 1
    c = comps[0]
    assert c["kind"] == "value" and c["name"] == "SAP_GUID"
    # 64-byte (32 UTF-16 chars) blank value
    assert len(c["value"]) == 64
    txt, _ = decode_row(c["value"], 2, "4103")
    assert txt == ""
    assert covered >= 95


def test_lzh_length_check_detects_corruption():
    """A corrupted declared size must raise, proving the integrity check."""
    blob = bytearray(load("TN_QUEUE_ID.bin"))
    blob[16] ^= 0xFF  # wreck the LE uncompressed size
    with pytest.raises(Exception):
        decompress_container(bytes(blob))


def test_all_zero_cluster_is_rejected_cleanly():
    with pytest.raises(ToolError, match="全零|empty"):
        decompress_container(bytes(45))


def test_row_width_is_self_consistent():
    """Struct/table rows: declared width == sum of field descriptor lengths."""
    info, out, comps, _ = parse_fixture("TN_QUEUE_ID.bin")
    for c in comps:
        if c["kind"] in ("struct", "table") and c["fields"][0][0] != "v6":
            assert len(c["rows"][0]) == sum(f[-1] for f in c["fields"])
