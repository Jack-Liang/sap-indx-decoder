//! SAP LZH 解压的 Rust 实现(PyO3 扩展),与 sap_indx_decoder.lzh 的纯
//! Python 版逐位等价;差分测试保证输出一致。
//!
//! LZ77(16KB 窗口)+ 规范 Huffman,帧格式:首块前 2-bit 前导、
//! Huffman 码 MSB 累积、位读取器 LSB-first。长度校验:输出必须等于
//! 容器头声明的未压缩字节数。

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

/// deflate 风格长度/距离码表(镜像 la_lenx/la_lens/la_dstx/la_dsts)
const LEN_EXTRA: [u32; 29] = [
    0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4, 5, 5,
    5, 5, 0,
];
const LEN_BASE: [u32; 29] = [
    3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31, 35, 43, 51, 59, 67,
    83, 99, 115, 131, 163, 195, 227, 258,
];
const DST_EXTRA: [u32; 30] = [
    0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10, 10, 11,
    11, 12, 12, 13, 13,
];
const DST_BASE: [u32; 30] = [
    1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129, 193, 257, 385, 513,
    769, 1025, 1537, 2049, 3073, 4097, 6145, 8193, 12289, 16385, 24577,
];
/// 码长字母表的读取顺序
const CL_ORDER: [usize; 19] = [16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15];

struct BitReader<'a> {
    data: &'a [u8],
    pos: usize,
    buf: u8,
    cnt: u32,
}

impl<'a> BitReader<'a> {
    fn new(data: &'a [u8], pos: usize) -> Self {
        BitReader { data, pos, buf: 0, cnt: 0 }
    }

    fn bit(&mut self) -> PyResult<u32> {
        if self.cnt == 0 {
            if self.pos >= self.data.len() {
                return Err(PyValueError::new_err("LZH: 位流越界"));
            }
            self.buf = self.data[self.pos];
            self.pos += 1;
            self.cnt = 8;
        }
        let b = self.buf & 1;
        self.buf >>= 1;
        self.cnt -= 1;
        Ok(b as u32)
    }

    /// n 位,LSB 在前(低位先收)
    fn bits(&mut self, n: u32) -> PyResult<u32> {
        let mut v = 0u32;
        for k in 0..n {
            v += self.bit()? << k;
        }
        Ok(v)
    }
}

struct Huff {
    cnt: [i32; 16],
    first: [i32; 16],
    offs: [i32; 16],
    syms: Vec<i32>,
}

impl Huff {
    /// 由码长构建规范 Huffman 解码器(0 = 未使用)
    fn new(lengths: &[i32]) -> Huff {
        let mut cnt = [0i32; 16];
        for &l in lengths {
            if l > 0 {
                cnt[l as usize] += 1;
            }
        }
        let mut first = [0i32; 16];
        let mut offs = [0i32; 16];
        let mut code = 0i32;
        let mut tot = 0i32;
        let mut pos = [0i32; 16];
        for l in 1..=15usize {
            if l > 1 {
                code = (code + cnt[l - 1]) * 2;
            }
            first[l] = code;
            offs[l] = tot;
            tot += cnt[l];
            pos[l] = tot - cnt[l];
        }
        let mut syms = vec![0i32; tot as usize];
        for (sym, &l) in lengths.iter().enumerate() {
            if l > 0 {
                syms[pos[l as usize] as usize] = sym as i32;
                pos[l as usize] += 1;
            }
        }
        Huff { cnt, first, offs, syms }
    }

    fn decode(&self, br: &mut BitReader) -> PyResult<i32> {
        let mut code = 0i32;
        let mut length = 0i32;
        loop {
            code = code * 2 + br.bit()? as i32;
            length += 1;
            if length > 15 {
                return Err(PyValueError::new_err("LZH: 无效 Huffman 码"));
            }
            if self.cnt[length as usize] > 0 {
                let tmp = code - self.first[length as usize];
                if 0 <= tmp && tmp < self.cnt[length as usize] {
                    return Ok(self.syms[(self.offs[length as usize] + tmp) as usize]);
                }
            }
        }
    }
}

fn read_lengths(br: &mut BitReader, cl: &Huff, n: usize) -> PyResult<Vec<i32>> {
    let mut res: Vec<i32> = Vec::new();
    let mut lastlen = 0i32;
    while res.len() < n {
        let c = cl.decode(br)?;
        match c {
            0..=15 => {
                res.push(c);
                lastlen = c;
            }
            16 => {
                let rep = 3 + br.bits(2)? as usize;
                for _ in 0..rep {
                    res.push(lastlen);
                }
            }
            17 => {
                let rep = 3 + br.bits(3)? as usize;
                for _ in 0..rep {
                    res.push(0);
                }
                lastlen = 0;
            }
            _ => {
                let rep = 11 + br.bits(7)? as usize;
                for _ in 0..rep {
                    res.push(0);
                }
                lastlen = 0;
            }
        }
    }
    Ok(res)
}

fn inflate_lzh_impl(data: &[u8], expect: usize) -> PyResult<Vec<u8>> {
    let mut br = BitReader::new(data, 24); // 位流从字节 24 开始
    let mut out: Vec<u8> = Vec::with_capacity(expect);
    let mut win = [0u8; 16384];
    let mut wc: usize = 0;

    let v1 = br.bits(2)?; // deflate 块之前的 SAP 帧头
    if v1 > 0 {
        br.bits(v1)?; // 内核读取后丢弃
    }

    let mut last = 0u32;
    while last == 0 {
        last = br.bits(1)?;
        let btype = br.bits(2)?;

        let lit_len: Vec<i32>;
        let dst_len: Vec<i32>;
        match btype {
            1 => {
                // 固定 Huffman 表
                lit_len = [vec![8; 144], vec![9; 112], vec![7; 24], vec![8; 8]].concat();
                dst_len = vec![5; 30];
            }
            2 => {
                // 动态表
                let nlit = 257 + br.bits(5)? as usize;
                let ndist = 1 + br.bits(5)? as usize;
                let nbl = 4 + br.bits(4)? as usize;
                let mut cl_len = [0i32; 19];
                for j in 0..nbl {
                    cl_len[CL_ORDER[j]] = br.bits(3)? as i32;
                }
                let cl_huff = Huff::new(&cl_len);
                lit_len = read_lengths(&mut br, &cl_huff, nlit)?;
                dst_len = read_lengths(&mut br, &cl_huff, ndist)?;
            }
            _ => return Err(PyValueError::new_err("LZH: 未知块类型")),
        }

        let lit = Huff::new(&lit_len);
        let dst = Huff::new(&dst_len);

        loop {
            let sym = lit.decode(&mut br)?;
            if sym < 256 {
                let b = sym as u8;
                out.push(b);
                win[wc % 16384] = b;
                wc += 1;
            } else if sym == 256 {
                break;
            } else {
                let code_idx = sym as usize - 257; // lc = sym-256,LEN_BASE[lc-1]
                if code_idx >= 29 {
                    return Err(PyValueError::new_err("LZH: 长度码越界"));
                }
                let mlen = LEN_BASE[code_idx] as usize + br.bits(LEN_EXTRA[code_idx])? as usize;
                let dc = dst.decode(&mut br)? as usize;
                if dc >= 30 {
                    return Err(PyValueError::new_err("LZH: 距离码越界"));
                }
                let dist = DST_BASE[dc] as usize + br.bits(DST_EXTRA[dc])? as usize;
                if dist > wc {
                    return Err(PyValueError::new_err("LZH: 回溯距离越界"));
                }
                let mut src = wc - dist;
                for _ in 0..mlen {
                    let b = win[src % 16384];
                    out.push(b);
                    win[wc % 16384] = b;
                    wc += 1;
                    src += 1;
                }
            }
        }
    }

    if out.len() != expect {
        return Err(PyValueError::new_err(format!(
            "LZH: 解压长度不匹配 {} != {}",
            out.len(),
            expect
        )));
    }
    Ok(out)
}

/// 解压 SAP LZH 位流(从 data[24:] 开始),输出长度必须等于 expect。
/// 解压期间释放 GIL,多线程批量取簇时解压可并行。
#[pyfunction]
fn decompress_lzh(py: Python<'_>, data: &[u8], expect: usize) -> PyResult<Vec<u8>> {
    let owned = data.to_vec();
    py.allow_threads(|| inflate_lzh_impl(&owned, expect))
}

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(decompress_lzh, m)?)?;
    Ok(())
}
