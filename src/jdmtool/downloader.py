
import base64
import binascii
import hashlib
import json
import pathlib
import typing as T
import xml.etree.ElementTree as ET

import platformdirs
import requests


class DownloaderException(Exception):
    pass


class Downloader:
    JSUM_URL = 'https://jsum.jeppesen.com/jsum'
    JDAM_VERSION = '3.14.0.60'
    CLIENT_TYPE = 'jdmx_win'

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers['User-Agent'] = None

    @classmethod
    def get_data_dir(cls) -> pathlib.Path:
        path = pathlib.Path(platformdirs.user_data_dir('jdmtool'))
        path.mkdir(parents=True, exist_ok=True)
        return path

    @classmethod
    def get_downloads_dir(cls) -> pathlib.Path:
        path = cls.get_data_dir() / 'downloads'
        path.mkdir(parents=True, exist_ok=True)
        return path

    @classmethod
    def get_auth(cls) -> dict:
        auth_file = cls.get_data_dir() / 'auth.json'
        try:
            with open(auth_file) as fd:
                return json.load(fd)
        except FileNotFoundError:
            raise DownloaderException("Not logged in")

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

        auth_file = self.get_data_dir() / 'auth.json'
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


        (self.get_data_dir() / 'services.xml').write_text(resp.text)

    def get_services(self) -> T.List[ET.Element]:
        try:
            root = ET.parse(self.get_data_dir() / 'services.xml')
        except FileNotFoundError:
            raise DownloaderException("Need to refresh the services first")

        services = root.findall('./service')
        return services

    def download(
        self,
        service: ET.Element,
        progress_cb: T.Callable[[int], None],
    ) -> None:
        filename = service.findtext('./filename')
        assert '/' not in filename, filename
        assert '\\' not in filename, filename

        auth = self.get_auth()

        with self.session.get(
            f'{self.JSUM_URL}/DownloadJDMService',
            stream=True,
            params={
                'jdam_version': self.JDAM_VERSION,
                'client_type': self.CLIENT_TYPE,
                'unique_service_id': service.findtext('./unique_service_id'),
                'service_code': service.findtext('./service_code'),
                'version': service.findtext('./version'),
                **auth,
            },
        ) as resp:
            if not resp.ok:
                raise DownloaderException(f"Unexpected response: {resp}")

            download_dir = self.get_downloads_dir()
            download_path = download_dir / f'{filename}.download'
            final_path = download_dir / filename

            crc = 0
            with open(download_path, 'wb') as fd:
                for chunk in resp.iter_content(0x1000):
                    fd.write(chunk)
                    crc = binascii.crc32(chunk, crc)
                    progress_cb(len(chunk))

        expected_crc_str = service.findtext('./file_crc')
        if expected_crc_str:
            expected_crc = int(expected_crc_str, 16)
            if crc != expected_crc:
                raise DownloaderException(f"Invalid checksum: expected {expected_crc:08x}, got {crc:08x}")

        download_path.rename(final_path)
        return final_path
