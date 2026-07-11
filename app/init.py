# -*- coding: utf-8 -*-

import os
import shutil
import sys
from typing import Optional

import yaml


def _ensure_module_paths():
    current_dir = os.path.dirname(os.path.abspath(__file__))
    required_paths = [current_dir, os.path.dirname(current_dir)]
    for path in required_paths:
        if path not in sys.path:
            sys.path.insert(0, path)


_ensure_module_paths()

from app.core.media_metadata import require_complete_category_routes
from app.utils.logger import Logger


debug_mode = False
logger: Optional[Logger] = None
bot_config = {}

CONFIG_FILE = "/config/config.yaml"
CONFIG_FILE_EXAMPLE = "/config/config.yaml.example"
APP = "/app"
CONFIG = "/config"
TEMP = "/tmp"
IMAGE_PATH = "/app/images"

if debug_mode:
    CONFIG_FILE = "config/config.yaml"
    CONFIG_FILE_EXAMPLE = "config/config.yaml.example"
    APP = "app"
    CONFIG = "config"
    TEMP = "tmp"
    IMAGE_PATH = "app/images"


def create_logger():
    global logger
    import logging

    level_map = {
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "warning": logging.WARNING,
        "error": logging.ERROR,
        "critical": logging.CRITICAL,
    }
    log_level = str(bot_config.get("log_level", "info")).lower()
    logger = Logger(level=level_map.get(log_level, logging.INFO), debug_model=debug_mode)
    logger.info("Logger init success!")


def load_yaml_config():
    global bot_config
    yaml_path = CONFIG_FILE
    example_config_path = f"{APP}/config.yaml.example"
    loaded_config = False

    try:
        shutil.copy2(example_config_path, CONFIG_FILE_EXAMPLE)
    except Exception as e:
        print(f"Update config example file failed: {e}")

    try:
        if os.path.exists(yaml_path):
            with open(yaml_path, "r", encoding="utf-8") as f:
                bot_config = yaml.safe_load(f) or {}
            loaded_config = True
        elif os.path.exists(example_config_path):
            os.makedirs(os.path.dirname(yaml_path), exist_ok=True)
            shutil.copy2(example_config_path, yaml_path)
            print(f"已复制示例配置文件到 {yaml_path}")
            with open(yaml_path, "r", encoding="utf-8") as f:
                bot_config = yaml.safe_load(f) or {}
            loaded_config = True
        else:
            print("Config example file not found!")
            bot_config = {}
    except Exception:
        print(f"配置文件[{yaml_path}]格式有误，请检查!")
        bot_config = {}

    if loaded_config:
        require_complete_category_routes(bot_config)


def get_bot_token():
    global bot_config
    if "bot_token" not in bot_config and os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            bot_config = yaml.safe_load(f) or {}
    return str(bot_config.get("bot_token") or "")


def create_tmp():
    if not os.path.exists(TEMP):
        os.mkdir(TEMP, mode=0o777)
        os.chmod(TEMP, 0o777)


def check_user(user_id):
    allowed_user = bot_config.get("allowed_user")
    if isinstance(allowed_user, int):
        return user_id == allowed_user
    if isinstance(allowed_user, str):
        return str(user_id) == allowed_user
    return False


def init_log():
    create_logger()


def init():
    load_yaml_config()
    create_logger()
    create_tmp()


if __name__ == "__main__":
    load_yaml_config()
