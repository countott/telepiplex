from __future__ import annotations


CORE_API_VERSION = "1.1"


class ContractError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)
