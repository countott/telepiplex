from __future__ import annotations


HOST_API_VERSION = "1.2"


class ContractError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)
