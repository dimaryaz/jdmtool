import os
import pathlib
import re
import subprocess
import time

import psutil
import requests

class GPluginException(Exception):
    pass


SERIAL_RE = re.compile('[0-9A-F]{4}-[0-9A-F]{4}')
WIN_G_PLUGIN_PATH = 'Jeppesen/Jdm/plugins/oem_garmin/g_plugin.exe'


class GPlugin:
    BASE_URL = 'http://localhost:8010'

    def __init__(self, path: pathlib.Path):
        self.path = path
        self.serial = None
        self.g_plugin_path = None
        self.wine_drive = None
        self.process = None
        self.session = requests.Session()

    def init(self):
        if psutil.LINUX:
            partitions = psutil.disk_partitions()
            device = None
            for p in partitions:
                if p.mountpoint == str(self.path):
                    print(f"Found device: {p.device}")
                    device = pathlib.Path(p.device)
                    break
            else:
                raise GPluginException(f"Could not find the device for {self.path}")

            for f in pathlib.Path('/dev/disk/by-uuid/').iterdir():
                if not f.is_symlink:
                    continue
                if f.resolve().samefile(device):
                    print(f"Found serial number: {f.name}")
                    if not SERIAL_RE.fullmatch(f.name):
                        raise GPluginException("Serial number does not match the format 1234-ABCD.")
                    self.serial = int(f.name.replace('-', ''), 16)
                    break
            else:
                raise GPluginException(f"Could not find the serial number for {device}")

            serial_file = self.path / '.windows-serial'
            print(f"Creating {serial_file} so Wine can find the serial number...")
            serial_file.write_text(f'{self.serial:08X}\n')

            print(f"Getting Windows drive for {self.path}...")
            try:
                self.wine_drive = subprocess.check_output(
                    ['winepath', '-w', self.path], stderr=subprocess.DEVNULL
                ).decode().rstrip().rstrip('\\')
            except FileNotFoundError as ex:
                raise GPluginException(f"Could not run winepath: {ex}") from ex

            if len(self.wine_drive) == 2:
                print(f"Found {self.wine_drive}")
            else:
                raise GPluginException(
                    f"Unexpected windows path: {self.wine_drive}; needs to be a drive letter!"
                )

            self.g_plugin_path = os.getenv("G_PLUGIN_PATH")
            if self.g_plugin_path is not None:
                if pathlib.Path(self.g_plugin_path).exists():
                    print(f"Using {self.g_plugin_path}")
                else:
                    raise GPluginException(f"{self.g_plugin_path!r} does not exist - check G_PLUGIN_PATH")
            else:
                program_files = subprocess.check_output(
                    ['wine', 'cmd', '/c', 'echo %ProgramFiles%'], stderr=subprocess.DEVNULL
                ).decode().rstrip()
                self.g_plugin_path = subprocess.check_output(
                    ['winepath', '-u', program_files + '/' + WIN_G_PLUGIN_PATH], stderr=subprocess.DEVNULL
                ).decode().rstrip()
                if pathlib.Path(self.g_plugin_path).exists():
                    print(f"Found g_plugin at {self.g_plugin_path!r}")
                else:
                    raise GPluginException(f"Could not find the plugin at {self.g_plugin_path!r}!")
        else:
            raise GPluginException("Sorry, g_plugin is only supported on Linux at the moment")

    def start(self):
        assert self.process is None
        assert self.g_plugin_path is not None

        try:
            self.session.get(self.BASE_URL)
            raise GPluginException(f"Something is already running on {self.BASE_URL}")
        except requests.ConnectionError:
            pass

        self.process = subprocess.Popen(['wine', self.g_plugin_path], stderr=subprocess.DEVNULL)

        for _ in range(5):
            time.sleep(1)
            try:
                resp = self.session.get(f'{self.BASE_URL}/getversion')
                if resp.status_code == 200:
                    print("Started g_plugin successfully")
                    break
            except requests.ConnectionError:
                pass
        else:
            self.stop()
            raise GPluginException("Could not start g_plugin")

    def run(self, datafile: str, featunlk: str, garmin_sec_id: str,
            garmin_system_id: str, unique_service_id: str, version: str) -> None:
        try:
            resp = self.session.post(
                self.BASE_URL,
                json={
                    "id": f'{unique_service_id}_{version}',
                    "jsonrpc": "2.0",
                    "method": "program",
                    "params": {
                        "datafile": f'{self.wine_drive}/{datafile}',
                        "featunlk": f'{self.wine_drive}/{featunlk}',
                        "garmin_sec_id": int(garmin_sec_id),
                        "numaircraft": 0,
                        "sysid": f'0x{int(garmin_system_id, 16):08X}',
                        "uid": unique_service_id,
                        "vers": version,
                    },
                },
            )
            if resp.status_code != 200:
                raise GPluginException(f"Got an error: {resp.json()}")

            data = resp.json()
            result = data.get('result')
            error = data.get('error')
            if result:
                print(f"Got response: {result}")
            else:
                raise GPluginException(f"Got an error: {error}")

            serial = self._extract_serial(self.path / featunlk)
            if serial != self.serial:
                raise GPluginException(f"Wrote the wrong serial number! Expected {self.serial:08X}, got {serial:08X}")
        except requests.ConnectionError as ex:
            raise GPluginException("Could not connect to g_plugin") from ex

    @classmethod
    def _extract_serial(cls, path):
        with open(path, 'rb') as fd:
            fd.seek(-897, os.SEEK_END)
            buf = fd.read(4)
        value = int.from_bytes(buf, 'little')
        value = ~((value << 1 & 0xFFFFFFFF) | (value >> 31)) & 0xFFFFFFFF
        return value

    def stop(self):
        if self.process:
            self.process.kill()
            self.process = None
