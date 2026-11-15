"""统一异常类型。"""


class ToolError(Exception):
    """用户可读的工具级错误(缺键/格式不符/簇不存在等)。"""
