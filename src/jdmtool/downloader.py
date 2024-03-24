
import base64
import binascii
import datetime
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
    COV_CHECK_MAGIC = 'L15ak3y'  # Hard-coded in jdm.exe

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers['User-Agent'] = None  # type: ignore

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
    def get_cov_check(cls) -> T.Tuple[str, str]:
        now = datetime.datetime.now(datetime.UTC)
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
        auth_file = cls.get_data_dir() / 'auth.json'
        try:
            with open(auth_file) as fd:
                return json.load(fd)
        except FileNotFoundError:
            raise DownloaderException("Not logged in")

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

        auth_file = self.get_data_dir() / 'auth.json'
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


        (self.get_data_dir() / 'services.xml').write_text(resp.text)

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

        (self.get_data_dir() / 'grm_feat_key.zip').write_bytes(resp.content)

    def get_services(self) -> T.List[ET.Element]:
        try:
            root = ET.parse(self.get_data_dir() / 'services.xml')
        except FileNotFoundError:
            raise DownloaderException("Need to refresh the services first")

        services = root.findall('./service')
        return services

    def get_database_filename(self, service: ET.Element) -> str:
        filename = service.findtext('./filename', '')
        if not filename:
            raise DownloaderException("Missing filename")

        if '/' in filename or '\\' in filename:
            raise DownloaderException(f"Bad filename: {filename!r}")

        return filename

    def get_sff_dir(self, service: ET.Element) -> pathlib.Path:
        service_id = service.findtext('./unique_service_id', '')
        version = service.findtext('./version', '')

        download_dir = self.get_downloads_dir()
        sff_dir = download_dir / 'sff' / f'{service_id}_{version}'
        sff_dir.mkdir(parents=True, exist_ok=True)

        return sff_dir

    def get_sff_filenames(self, service: ET.Element) -> T.List[str]:
        sff_filenames_str = service.findtext("./oem_garmin_sff_filenames", '')
        if not sff_filenames_str:
            return []

        sff_filenames = sff_filenames_str.split(',')
        for sff_filename in sff_filenames:
            if '/' in sff_filename or '\\' in sff_filename:
                raise DownloaderException(f"Bad filename: {sff_filename!r}")

        return sff_filenames

    def download_database(
        self,
        service: ET.Element,
        progress_cb: T.Callable[[int], None],
    ) -> pathlib.Path:
        filename = self.get_database_filename(service)

        auth = self.get_auth()
        headers, params = self.get_common_headers_params()

        with self.session.get(
            f'{self.JSUM_URL}/DownloadJDMService',
            stream=True,
            headers=headers,
            params={
                'unique_service_id': service.findtext('./unique_service_id'),
                'service_code': service.findtext('./service_code'),
                'version': service.findtext('./version'),
                **auth,
                **params,
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

    def download_sff(
        self,
        service: ET.Element,
        filename: str,
    ) -> pathlib.Path:
        auth = self.get_auth()
        headers, params = self.get_common_headers_params()

        with self.session.get(
            f'{self.JSUM_URL}/downloadsff',
            headers=headers,
            params={
                'unique_service_id': service.findtext('./unique_service_id'),
                'service_code': service.findtext('./service_code'),
                'version': service.findtext('./version'),
                'type': service.findtext('oem_garmin_sff_db_type'),
                'garmin_sec_id': service.findtext('garmin_sec_id'),
                'avionics_id': service.findtext('avionics_id'),
                'filename': filename,
                **auth,
                **params,
            },
        ) as resp:
            if not resp.ok:
                raise DownloaderException(f"Unexpected response: {resp}")

            sff_dir = self.get_sff_dir(service)
            download_path = sff_dir / f'{filename}.download'
            final_path = sff_dir / filename

            download_path.write_bytes(resp.content)

        download_path.rename(final_path)
        return final_path
