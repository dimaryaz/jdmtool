from configparser import ConfigParser
import pathlib
from typing import Optional

from .common import get_config_dir


CONFIG_FILE = 'config.ini'

DEFAULT_CONFIG = {
    "list": {
        "header_style": "1",
        "odd_row_style": "33",
        "even_row_style": "",
    }
}


def get_config_file() -> pathlib.Path:
    return get_config_dir() / CONFIG_FILE


_CONFIG: Optional[ConfigParser] = None


def get_config():
    global _CONFIG

    if _CONFIG is None:
        config = ConfigParser()
        try:
            with open(get_config_file(), encoding="utf-8") as fd:
                config.read_file(fd)
        except FileNotFoundError:
            config.read_dict(DEFAULT_CONFIG)
            with open(get_config_file(), 'w', encoding="utf-8") as fd:
                config.write(fd)
        _CONFIG = config

    return config
