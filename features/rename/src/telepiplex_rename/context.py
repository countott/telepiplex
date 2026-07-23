from __future__ import annotations

import logging


class _RuntimeContext:
    def __init__(self):
        self.config = {}
        self.logger = logging.getLogger("telepiplex.rename")

    def configure(self, config: dict):
        self.config = config


runtime_context = _RuntimeContext()
