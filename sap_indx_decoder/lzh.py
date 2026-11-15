"""SAP LZH 解压(LZ77 + 规范 Huffman,deflate 风格帧)。

纯 Python 实现移植自 aamelin1/sap-long-texts 的 stxl_decode.py(MIT),
是作者 SQLScript AMDP 解码器的可执行规格。若编译了原生扩展
(sap_indx_decoder._native,见 native/),自动优先使用,接口一致:
    inflate_lzh(data: bytes, expect: int) -> bytes
输出长度必须等于 expect(容器头声明的未压缩大小),否则抛 ValueError。
"""
import struct

try:  # 原生加速(见 native/src/lib.rs),不可用时回退纯 Python
    from sap_indx_decoder._native import decompress_lzh as _native_decompress
except ImportError:  # pragma: no cover - 无原生模块的常规路径
    _native_decompress = None

__all__ = ["inflate_lzh", "has_native"]

has_native = _native_decompress is not None


class BitReader:
    """LSB-first 位读取器,镜像 SQLScript 的循环。"""

    def __init__(self, data, pos):
        self.data = data          # list[int] / bytes
        self.pos = pos            # 下一字节下标(0 起)
        self.buf = 0
        self.cnt = 0

    def bit(self):
        if self.cnt == 0:
            self.buf = self.data[self.pos]
            self.pos += 1
            self.cnt = 8
        b = self.buf & 1
        self.buf >>= 1
        self.cnt -= 1
        return b

    def bits(self, n):
        """n 位,LSB 在前(低位先收)。"""
        v = 0
        for k in range(n):
            v += self.bit() << k
        return v


class Huff:
    """由码长构建的规范 Huffman 解码器:cnt/first/offs/syms。"""

    def __init__(self, lengths):
        maxlen = 15
        self.cnt = [0] * (maxlen + 1)
        for l in lengths:
            if l > 0:
                self.cnt[l] += 1
        self.first = [0] * (maxlen + 1)
        self.offs = [0] * (maxlen + 1)
        code = tot = 0
        pos = [0] * (maxlen + 1)
        for l in range(1, maxlen + 1):
            if l > 1:
                code = (code + self.cnt[l - 1]) * 2
            self.first[l] = code
            self.offs[l] = tot
            tot += self.cnt[l]
            pos[l] = tot - self.cnt[l]
        self.syms = [0] * tot
        for sym, l in enumerate(lengths):
            if l > 0:
                self.syms[pos[l]] = sym
                pos[l] += 1

    def decode(self, br):
        code = length = 0
        while True:
            code = code * 2 + br.bit()
            length += 1
            if length > 15:
                raise ValueError("LZH: 无效 Huffman 码")
            if self.cnt[length] > 0:
                tmp = code - self.first[length]
                if 0 <= tmp < self.cnt[length]:
                    return self.syms[self.offs[length] + tmp]


# deflate 的长度/距离码表(镜像 la_lenx/la_lens/la_dstx/la_dsts)
LEN_EXTRA = [0] * 8 + [c for c in range(1, 6) for _ in range(4)] + [0]
LEN_BASE = []
_c = 3
for _j in range(28):
    LEN_BASE.append(_c)
    _c += 1 << LEN_EXTRA[_j]
LEN_BASE.append(258)
LEN_EXTRA = LEN_EXTRA[:29]

DST_EXTRA = [0 if j <= 3 else (j - 2) // 2 for j in range(30)]
DST_BASE = []
_c = 1
for _j in range(30):
    DST_BASE.append(_c)
    _c += 1 << DST_EXTRA[_j]

# 码长字母表的读取顺序
CL_ORDER = [16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15]


def inflate_lzh_python(data, expect):
    """解压从字节 24(0 起)开始的 SAP LZH 位流,返回 bytes。"""
    br = BitReader(data, 24)
    out, win, wc = bytearray(), bytearray(16384), 0

    v1 = br.bits(2)               # deflate 块之前的 SAP 帧头
    if v1 > 0:
        br.bits(v1)               # 内核读取后丢弃

    last = 0
    while last == 0:
        last = br.bits(1)
        btype = br.bits(2)

        if btype == 1:            # 固定 Huffman 表
            lit_len = [8] * 144 + [9] * 112 + [7] * 24 + [8] * 8
            dst_len = [5] * 30
        elif btype == 2:          # 动态表
            nlit, ndist, nbl = 257 + br.bits(5), 1 + br.bits(5), 4 + br.bits(4)
            cl_len = [0] * 19
            for j in range(nbl):
                cl_len[CL_ORDER[j]] = br.bits(3)
            cl_huff = Huff(cl_len)

            def read_lengths(n):
                res, lastlen = [], 0
                while len(res) < n:
                    c = cl_huff.decode(br)
                    if c < 16:
                        res.append(c)
                        lastlen = c
                    elif c == 16:
                        res += [lastlen] * (3 + br.bits(2))
                    elif c == 17:
                        res += [0] * (3 + br.bits(3))
                        lastlen = 0
                    else:
                        res += [0] * (11 + br.bits(7))
                        lastlen = 0
                return res

            lit_len = read_lengths(nlit)
            dst_len = read_lengths(ndist)
        else:
            raise ValueError("LZH: 未知块类型 %d" % btype)

        lit, dst = Huff(lit_len), Huff(dst_len)

        while True:
            sym = lit.decode(br)
            if sym < 256:
                out.append(sym)
                win[wc % 16384] = sym
                wc += 1
            elif sym == 256:
                break
            else:
                lc = sym - 256    # 1 起的长度码,与 SQLScript 一致
                mlen = LEN_BASE[lc - 1] + br.bits(LEN_EXTRA[lc - 1])
                dc = dst.decode(br)
                dist = DST_BASE[dc] + br.bits(DST_EXTRA[dc])
                src = wc - dist
                for _ in range(mlen):
                    b = win[src % 16384]
                    out.append(b)
                    win[wc % 16384] = b
                    wc += 1
                    src += 1

    if len(out) != expect:
        raise ValueError("LZH: 解压长度不匹配 %d != %d" % (len(out), expect))
    return bytes(out)


def inflate_lzh(data, expect):
    """对外入口:原生扩展优先,否则纯 Python。data 支持 bytes/list[int]。"""
    if _native_decompress is not None:
        return bytes(_native_decompress(bytes(data), int(expect)))
    return inflate_lzh_python(data, expect)
