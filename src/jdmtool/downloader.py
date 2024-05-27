
import base64
import binascii
import datetime
import hashlib
import json
import pathlib
import typing as T
import xml.etree.ElementTree as ET

import requests


from .service import get_data_dir, get_downloads_dir, get_services_path


GRM_FEAT_KEY = 'grm_feat_key.zip'


class DownloaderException(Exception):
    pass


class Downloader:
    JSUM_URL = 'https://jsum.jeppesen.com/jsum'
    JDAM_VERSION = '3.14.0.60'
    CLIENT_TYPE = 'jdmx_win'
    COV_CHECK_MAGIC = 'L15ak3y'  # Hard-coded in jdm.exe

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers['User-Agent'] = None  # type: ignore

    @classmethod
    def get_cov_check(cls) -> T.Tuple[str, str]:
        now = datetime.datetime.now(datetime.timezone.utc)
        date_str = now.strftime('%a %b %d %H:%M:%S %Y')
        cov_check = hashlib.md5((date_str + cls.COV_CHECK_MAGIC).encode()).hexdigest()
        return date_str, cov_check

    @classmethod
    def get_common_headers_params(cls) -> T.Tuple[T.Dict[str, str], T.Dict[str, str]]:
        date_str, cov_check = cls.get_cov_check()
        headers = {
            'Date': date_str,
        }
        params = {
            'jdam_version': cls.JDAM_VERSION,
            'client_type': cls.CLIENT_TYPE,
            'cov_check': cov_check,
        }
        return headers, params

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
        headers, params = self.get_common_headers_params()

        resp = self.session.get(
            f'{self.JSUM_URL}/verifylogin.php',
            headers=headers,
            params={
                'username': username,
                'pwhash': pwhash,
                **params,
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
        headers, params = self.get_common_headers_params()

        resp = self.session.get(
            f'{self.JSUM_URL}/getserviceslist.php',
            headers=headers,
            params={
                **auth,
                **params,
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
        headers, params = self.get_common_headers_params()

        resp = self.session.get(
            f'{self.JSUM_URL}/downloadgarminkeychainfile',
            headers=headers,
            params={
                **auth,
                **params,
            },
        )

        if not resp.ok:
            raise DownloaderException(f"Unexpected response: {resp}")

        (get_downloads_dir() / GRM_FEAT_KEY).write_bytes(resp.content)

    def download_database(
        self,
        params: T.Dict[str, str],
        dest_path: pathlib.Path,
        expected_crc: T.Optional[int],
        progress_cb: T.Callable[[int], None],
    ) -> None:
        auth = self.get_auth()
        common_headers, common_params = self.get_common_headers_params()

        with self.session.get(
            f'{self.JSUM_URL}/DownloadJDMService',
            stream=True,
            headers=common_headers,
            params={
                **params,
                **auth,
                **common_params,
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
        common_headers, common_params = self.get_common_headers_params()

        with self.session.get(
            f'{self.JSUM_URL}/downloadsff',
            headers=common_headers,
            params={
                **params,
                **auth,
                **common_params,
            },
        ) as resp:
            if not resp.ok:
                raise DownloaderException(f"Unexpected response: {resp}")

            download_path = dest_path.with_name(dest_path.name + '.download')
            download_path.write_bytes(resp.content)

        download_path.rename(dest_path)

    def download_oem(self, params: T.Dict[str, str], dest_path: pathlib.Path) -> None:
        common_headers, common_params = self.get_common_headers_params()

        with self.session.get(
            f'{self.JSUM_URL}/DownloadOEMPackage.php',
            headers=common_headers,
            params={
                **params,
                **common_params,
            },
        ) as resp:
            if not resp.ok:
                raise DownloaderException(f"Unexpected response: {resp}")

            download_path = dest_path.with_name(dest_path.name + '.download')
            download_path.write_bytes(resp.content)

        download_path.rename(dest_path)
