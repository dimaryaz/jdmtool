from configparser import ConfigParser
from functools import cache
import pathlib

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


@cache
def get_config() -> ConfigParser:
    config = ConfigParser()
    try:
        with open(get_config_file(), encoding="utf-8") as fd:
            config.read_file(fd)
    except FileNotFoundError:
        config.read_dict(DEFAULT_CONFIG)
        with open(get_config_file(), 'w', encoding="utf-8") as fd:
            config.write(fd)

    return config
