from __future__ import annotations

from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
import pathlib
import xml.etree.ElementTree as ET

from .common import JdmToolException, get_data_dir


class ServiceException(JdmToolException):
    pass


@dataclass
class DownloadConfig:
    dest_path: pathlib.Path
    size: int | None
    crc32: int | None
    params: dict[str, str]


def get_downloads_dir() -> pathlib.Path:
    path = get_data_dir() / 'downloads'
    path.mkdir(parents=True, exist_ok=True)
    return path

def get_services_path() -> pathlib.Path:
    return get_data_dir() / 'services.xml'


class Service(ABC):
    @abstractmethod
    def get_optional_property(self, name: str, default: str | None = None) -> str | None:
        ...

    @abstractmethod
    def get_media(self) -> list[ET.Element]:
        ...

    @abstractmethod
    def get_databases(self) -> list[DownloadConfig]:
        ...

    @abstractmethod
    def get_sffs(self) -> list[DownloadConfig]:
        ...

    @abstractmethod
    def get_oems(self) -> list[DownloadConfig]:
        ...

    def get_download_paths(self) -> list[pathlib.Path]:
        return [cfg.dest_path for cfg in self.get_databases() + self.get_sffs() + self.get_oems()]

    def get_property(self, name: str) -> str:
        value = self.get_optional_property(name)

        if value is None:
            raise ServiceException(f"Missing {name!r}")

        return value

    def get_fingerprint(self) -> tuple[str, str, str]:
        return (
            self.get_property('unique_service_id'),
            self.get_property('service_code'),
            self.get_property('version'),
        )

    def get_start_date(self) -> datetime:
        return datetime.strptime(self.get_property('version_start_date'), '%Y-%m-%d %H:%M:%S')

    def get_end_date(self) -> datetime:
        return datetime.strptime(self.get_property('version_end_date'), '%Y-%m-%d %H:%M:%S')


class SimpleService(Service):
    DEFAULT_OEM = 'Garmin'

    def __init__(self, xml: ET.Element) -> None:
        super().__init__()
        self._xml = xml

    def get_optional_property(self, name: str, default: str | None = None) -> str | None:
        return self._xml.findtext(f'./{name}', default)

    def get_media(self) -> list[ET.Element]:
        return self._xml.findall('./media')

    @classmethod
    def _check_filename(cls, filename):
        if not filename or '/' in filename or '\\' in filename:
            raise ServiceException(f"Bad filename: {filename!r}")

    def get_database(self) -> DownloadConfig:
        filename = self.get_property('filename')
        self._check_filename(filename)

        crc_str = self.get_optional_property('file_crc')
        if crc_str:
            crc = int(crc_str, 16)
        else:
            crc = None

        return DownloadConfig(
            dest_path=get_downloads_dir() / filename,
            size=int(self.get_property('file_size')),
            crc32=crc,
            params=dict(
                unique_service_id=self.get_property('unique_service_id'),
                service_code=self.get_property('service_code'),
                version=self.get_property('version'),
            ),
        )

    def get_databases(self) -> list[DownloadConfig]:
        return [self.get_database()]

    def get_sffs(self) -> list[DownloadConfig]:
        sff_filenames_str = self.get_optional_property('./oem_garmin_sff_filenames')
        if not sff_filenames_str:
            return []

        unique_service_id = self.get_property('unique_service_id')
        version = self.get_property('version')

        sff_dir = get_downloads_dir() / 'sff' / f'{unique_service_id}_{version}'

        common_params = dict(
            unique_service_id=unique_service_id,
            service_code=self.get_property('service_code'),
            version=version,
            type=self.get_property('oem_garmin_sff_db_type'),
            garmin_sec_id=self.get_property('garmin_sec_id'),
            avionics_id=self.get_property('avionics_id'),
        )

        cfgs: list[DownloadConfig] = []
        sff_filenames = sff_filenames_str.split(',')
        for sff_filename in sff_filenames:
            self._check_filename(sff_filename)
            cfgs.append(DownloadConfig(
                dest_path=sff_dir / sff_filename,
                size=None,
                crc32=None,
                params=dict(
                    **common_params,
                    filename=sff_filename,
                ),
            ))

        return cfgs

    def get_oems(self) -> list[DownloadConfig]:
        size_str = self.get_optional_property('oem_package_filesize')
        version = self.get_property('version')
        oem_package_name = self.get_optional_property('oem_package_name', self.DEFAULT_OEM)

        if size_str is None:
            return []
        else:
            return [DownloadConfig(
                dest_path=get_downloads_dir() / 'oem' / f'{oem_package_name}_{version}.zip',
                size=int(size_str),
                crc32=None,
                params=dict(
                    oem=oem_package_name,
                    version=version,
                ),
            )]


class ChartViewService(Service):
    def __init__(self, subservices: list[SimpleService]) -> None:
        super().__init__()
        self._subservices = subservices

    def get_optional_property(self, name: str, default: str | None = None) -> str | None:
        if name == 'coverage_desc':
            values = [s.get_property(name) for s in self._subservices]
            return ', '.join(values)
        else:
            return self._subservices[0].get_optional_property(name, default)

    def get_media(self):
        return self._subservices[0].get_media()

    def get_databases(self) -> list[DownloadConfig]:
        return [s.get_database() for s in self._subservices]

    def get_sffs(self) -> list[DownloadConfig]:
        return self._subservices[0].get_sffs()

    def get_oems(self) -> list[DownloadConfig]:
        return self._subservices[0].get_oems()


def load_services() -> list[Service]:
    try:
        root = ET.parse(get_services_path())
    except FileNotFoundError:
        raise ServiceException("Need to refresh the services first") from None

    xml_services = root.findall('./service')

    services: list[Service] = []
    chartview_by_sn_version: defaultdict[tuple[str, str], list[SimpleService]] = defaultdict(list)

    for xml_service in xml_services:
        category = xml_service.findtext('./category', '')
        if category in ('1', '10'):
            services.append(SimpleService(xml_service))
        elif category == '8':
            serial_number = xml_service.findtext('./serial_number', '')
            version = xml_service.findtext('./version', '')
            chartview_by_sn_version[(serial_number, version)].append(SimpleService(xml_service))
        elif category == '2':
            # Update to JDM itself; ignore.
            pass
        else:
            raise ServiceException(f"Unsupported service category: {category!r}")

    for subservice_list in chartview_by_sn_version.values():
        services.append(ChartViewService(subservice_list))

    return services
