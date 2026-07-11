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

try:
    from app.core.open_115 import OpenAPI_115
except ModuleNotFoundError:
    OpenAPI_115 = None

from app.core.media_metadata import require_complete_category_routes
from app.utils.logger import Logger


debug_mode = False
logger: Optional[Logger] = None
bot_config = {}
openapi_115 = None
module_registry = None

CONFIG_FILE = "/config/config.yaml"
CONFIG_FILE_EXAMPLE = "/config/config.yaml.example"
DB_FILE = "/config/db.db"
TOKEN_FILE = "/config/115_tokens.json"
APP = "/app"
CONFIG = "/config"
TEMP = "/tmp"
IMAGE_PATH = "/app/images"

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"

if debug_mode:
    CONFIG_FILE = "config/config.yaml"
    CONFIG_FILE_EXAMPLE = "config/config.yaml.example"
    DB_FILE = "config/db.db"
    TOKEN_FILE = "config/115_tokens.json"
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


def initialize_115open():
    global openapi_115
    if OpenAPI_115 is None:
        if logger:
            logger.error("115 OpenAPI 模块不存在，无法初始化。")
        openapi_115 = None
        return False
    try:
        openapi_115 = OpenAPI_115()
        if openapi_115.access_token and openapi_115.refresh_token:
            user_info = openapi_115.get_user_info()
            if not OpenAPI_115.is_valid_user_info(user_info):
                logger.error("115 OpenAPI客户端初始化失败: OpenAPI测试失败！")
                openapi_115 = None
                return False
            logger.info("115 OpenAPI客户端初始化成功")
            return True
        logger.error("115 OpenAPI客户端初始化失败: 无法获取有效的token")
        openapi_115 = None
        return False
    except Exception as e:
        logger.error(f"115 OpenAPI客户端初始化失败: {e}")
        openapi_115 = None
        return False


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
