#!/usr/bin/env python3
"""向后兼容入口:实现已迁至 sap_indx_decoder 包。

保留本文件使 `python3 indx_tool.py ...` 与既有文档/博客中的用法继续有效。
"""
from sap_indx_decoder.cli import main

if __name__ == "__main__":
    main()
