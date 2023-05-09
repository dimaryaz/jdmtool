
import base64
import binascii
import hashlib
import json
import pathlib
import typing as T
import xml.etree.ElementTree as ET

import requests


from .service import get_data_dir, get_downloads_dir, get_services_path


class DownloaderException(Exception):
    pass


class Downloader:
    JSUM_URL = 'https://jsum.jeppesen.com/jsum'
    JDAM_VERSION = '3.14.0.60'
    CLIENT_TYPE = 'jdmx_win'

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers['User-Agent'] = None  # type: ignore

    @classmethod
    def get_auth(cls) -> dict:
        auth_file = get_data_dir() / 'auth.json'
        try:
            with open(auth_file) as fd:
                return json.load(fd)
        except FileNotFoundError:
            raise DownloaderException("Not logged in") from None

    def login(self, username: str, password: str) -> None:
        pwhash = base64.b64encode(hashlib.md5(password.encode()).digest()).decode()

        resp = self.session.get(
            f'{self.JSUM_URL}/verifylogin.php',
            params={
                'username': username,
                'pwhash': pwhash,
                'jdam_version': self.JDAM_VERSION,
                'client_type': self.CLIENT_TYPE,
            },
        )

        if not resp.ok:
            raise DownloaderException(f"Unexpected response: {resp}")

        root = ET.fromstring(resp.text)

        login_valid = root.findtext('./login_valid')
        if login_valid != 'TRUE':
            raise DownloaderException("Invalid login")

        auth_file = get_data_dir() / 'auth.json'
        with open(auth_file, 'w') as fd:
            json.dump(dict(
                username=username,
                pwhash=pwhash,
            ), fd)

    def refresh(self) -> None:
        auth = self.get_auth()

        resp = self.session.get(
            f'{self.JSUM_URL}/getserviceslist.php',
            params={
                'jdam_version': self.JDAM_VERSION,
                'client_type': self.CLIENT_TYPE,
                **auth,
            },
        )

        if not resp.ok:
            raise DownloaderException(f"Unexpected response: {resp}")

        root = ET.fromstring(resp.text)

        response_code = root.findtext('./response_code')
        if response_code != '0x0':
            response_text = root.findtext('./response_text')
            raise DownloaderException(response_text)

        get_services_path().write_text(resp.text)

    def refresh_keychain(self) -> None:
        auth = self.get_auth()

        resp = self.session.get(
            f'{self.JSUM_URL}/downloadgarminkeychainfile',
            params={
                'jdam_version': self.JDAM_VERSION,
                'client_type': self.CLIENT_TYPE,
                **auth,
            },
        )

        if not resp.ok:
            raise DownloaderException(f"Unexpected response: {resp}")

        (get_downloads_dir() / 'grm_feat_key.zip').write_bytes(resp.content)

    def download_database(
        self,
        params: T.Dict[str, str],
        dest_path: pathlib.Path,
        expected_crc: T.Optional[int],
        progress_cb: T.Callable[[int], None],
    ) -> None:
        auth = self.get_auth()

        with self.session.get(
            f'{self.JSUM_URL}/DownloadJDMService',
            stream=True,
            params={
                'jdam_version': self.JDAM_VERSION,
                'client_type': self.CLIENT_TYPE,
                **params,
                **auth,
            },
        ) as resp:
            if not resp.ok:
                raise DownloaderException(f"Unexpected response: {resp}")

            download_path = dest_path.with_name(dest_path.name + '.download')

            crc = 0
            with open(download_path, 'wb') as fd:
                for chunk in resp.iter_content(0x1000):
                    fd.write(chunk)
                    crc = binascii.crc32(chunk, crc)
                    progress_cb(len(chunk))

        if expected_crc is not None and crc != expected_crc:
            raise DownloaderException(f"Invalid checksum: expected {expected_crc:08x}, got {crc:08x}")

        download_path.rename(dest_path)

    def download_sff(self, params: T.Dict[str, str], dest_path: pathlib.Path) -> None:
        auth = self.get_auth()

        with self.session.get(
            f'{self.JSUM_URL}/downloadsff',
            params={
                'jdam_version': self.JDAM_VERSION,
                'client_type': self.CLIENT_TYPE,
                **params,
                **auth,
            },
        ) as resp:
            if not resp.ok:
                raise DownloaderException(f"Unexpected response: {resp}")

            download_path = dest_path.with_name(dest_path.name + '.download')
            download_path.write_bytes(resp.content)

        download_path.rename(dest_path)

    def download_oem(self, params: T.Dict[str, str], dest_path: pathlib.Path) -> None:
        with self.session.get(
            f'{self.JSUM_URL}/DownloadOEMPackage.php',
            params={
                'jdam_version': self.JDAM_VERSION,
                'client_type': self.CLIENT_TYPE,
                **params,
            },
        ) as resp:
            if not resp.ok:
                raise DownloaderException(f"Unexpected response: {resp}")

            download_path = dest_path.with_name(dest_path.name + '.download')
            download_path.write_bytes(resp.content)

        download_path.rename(dest_path)
