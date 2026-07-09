# -*- coding: utf-8 -*-

import importlib


def load_enabled_modules(registry, module_names):
    loaded = []
    for module_name in module_names or []:
        module_name = str(module_name or "").strip()
        if not module_name:
            continue
        module = importlib.import_module(module_name)
        register_module = getattr(module, "register_module", None)
        if not callable(register_module):
            raise AttributeError(f"{module_name} does not expose register_module(registry)")
        register_module(registry)
        loaded.append(module_name)
    return loaded

