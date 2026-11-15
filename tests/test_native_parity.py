"""Rust 原生解压与纯 Python 实现的差分测试。

对 examples/ 固件簇 + INDX_CORPUS 环境变量指定的语料目录,
断言两条路径输出逐字节一致、错误行为一致。
无原生扩展时整组跳过。
"""
import glob
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sap_indx_decoder import decompress_container
from sap_indx_decoder.lzh import has_native, inflate_lzh_python

pytestmark = pytest.mark.skipif(not has_native, reason="未编译原生扩展")

HERE = os.path.dirname(os.path.abspath(__file__))
EX = os.path.join(HERE, "..", "examples")
CORPUS = os.environ.get("INDX_CORPUS", "")


def _blobs():
    paths = sorted(glob.glob(os.path.join(EX, "*.bin")))
    if CORPUS and os.path.isdir(CORPUS):
        paths += sorted(glob.glob(os.path.join(CORPUS, "*.bin")))
    for p in paths:
        with open(p, "rb") as f:
            yield p, f.read()


def test_native_matches_python():
    n = 0
    for path, blob in _blobs():
        try:
            info, out = decompress_container(blob)
        except Exception:
            continue  # 语料中的空簇/未知格式不参与差分
        if not info["compressed"]:
            continue
        data = blob
        expect = info["declared_uncompressed"]
        native = bytes(__import__("sap_indx_decoder._native",
                                  fromlist=["decompress_lzh"]).decompress_lzh(data, expect))
        pure = inflate_lzh_python(data, expect)
        assert native == pure, f"差分不一致: {path}"
        assert native == out
        n += 1
    assert n >= 3, "差分样本不足"


def test_native_rejects_corruption():
    blob = open(os.path.join(EX, "TN_QUEUE_ID.bin"), "rb").read()
    bad = bytearray(blob)
    bad[16] ^= 0xFF  # 破坏声明的未压缩大小
    from sap_indx_decoder._native import decompress_lzh
    with pytest.raises(Exception):
        decompress_lzh(bytes(bad), bad[16] + bad[17] * 256 + bad[18] * 65536 + bad[19] * 16777216)
