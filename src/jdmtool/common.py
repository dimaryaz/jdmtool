from functools import cache
import pathlib

import platformdirs

from .const import APP_NAME


class JdmToolException(Exception):
    pass


@cache
def get_data_dir() -> pathlib.Path:
    path = pathlib.Path(platformdirs.user_data_dir(APP_NAME))
    path.mkdir(parents=True, exist_ok=True)
    return path


@cache
def get_config_dir() -> pathlib.Path:
    path = pathlib.Path(platformdirs.user_config_dir(APP_NAME))
    path.mkdir(parents=True, exist_ok=True)
    return path
