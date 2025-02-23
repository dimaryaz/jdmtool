from __future__ import annotations

import argparse
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from functools import wraps
from getpass import getpass
from importlib import metadata as importlib_metadata
from io import TextIOWrapper
import json
import os
import pathlib
import shutil
import sys
import time
from typing import Any, TYPE_CHECKING
import zipfile

import psutil
import tqdm

from .common import JdmToolException, get_data_dir
from .config import get_config, get_config_file
from .const import GRM_FEAT_KEY
from .data_card.common import ProgrammingDevice, ProgrammingException
from .service import Service, ServiceException, SimpleService, get_downloads_dir, load_services


if TYPE_CHECKING:
    from .downloader import Downloader


class CardType(Enum):
    SD = 2
    DATA_CARD = 7


DOT_JDM = '.jdm'
DOT_JDM_MAX_FH_SIZE = 100 * 1024 * 1024  # fh calculated up to 100MB

LDR_SYS = 'ldr_sys'

DETAILED_INFO_MAP = [
    ("Aircraft Manufacturer", "oracle_aircraft_manufacturer"),
    ("Aircraft Model", "oracle_aircraft_model"),
    ("Aircraft Tail Number", "oracle_aircraft_tail_number"),
    (None, None),
    ("Avionics", "avionics"),
    ("Service Type", "service_type"),
    ("Coverage", "coverage_desc"),
    ("Service Renewal Date", "service_renewal_date"),
    (None, None),
    ("Service Code", "service_code"),
    ("Service ID", "unique_service_id"),
    ("Serial Number", "serial_number"),
    ("System ID", "avionics_id"),
    (None, None),
    ("Version", "display_version"),
    ("Version Start Date", "version_start_date"),
    ("Version End Date", "version_end_date"),
    (None, None),
    ("Next Version", "next_display_version"),
    ("Next Version Available Date", "next_version_avail_date"),
    ("Next Version Start Date", "next_version_start_date"),
]


class UserException(JdmToolException):
    pass


class IdPreset(Enum):
    CURRENT = 'curr'
    NEXT = 'next'


def default_confirm_impl(prompt: str) -> None:
    prompt = input(f"{prompt} (y/n) ")
    if prompt.lower() != 'y':
        raise UserException("Cancelled")


PROMPT_CTX = ContextVar("prompt_func", default=default_confirm_impl)


def confirm(prompt: str) -> None:
    PROMPT_CTX.get()(prompt)


def with_usb(f: Callable):
    @wraps(f)
    def wrapper(*args, **kwargs):
        from .data_card.detect import open_programming_device

        with open_programming_device() as dev:
            f(dev, *args, **kwargs)

    return wrapper


def with_data_card(f: Callable):
    @wraps(f)
    @with_usb
    def wrapper(dev: ProgrammingDevice, *args, **kwargs):
        if not dev.has_card():
            raise ProgrammingException("Card is missing!")

        dev.init_data_card()
        print(f"Detected data card: {dev.get_card_name()}")

        f(dev, *args, **kwargs)

    return wrapper


def _find_obsolete_downloads(services: list[Service]) -> tuple[list[pathlib.Path], int]:
    good_downloads = set(f for s in services for f in s.get_download_paths())

    obsolete_downloads: list[pathlib.Path] = []
    total_size = 0

    for path in get_downloads_dir().rglob('*'):
        if not path.is_file():
            continue
        if path not in good_downloads:
            obsolete_downloads.append(path)
            total_size += path.stat().st_size

    return obsolete_downloads, total_size


def _load_services_by_ids(ids: list[int] | IdPreset) -> list[Service]:
    if not ids:
        raise UserException("Need at least one download ID")

    all_services = load_services()

    if isinstance(ids, list):
        try:
            services = [all_services[id] for id in ids]
        except IndexError:
            raise UserException("Invalid service ID") from None
    else:
        services: list[Service] = []
        now = datetime.now()
        for service in all_services:
            if ids is IdPreset.CURRENT:
                if service.get_start_date() <= now <= service.get_end_date():
                    services.append(service)
            elif ids is IdPreset.NEXT:
                if now < service.get_start_date():
                    services.append(service)

        if not services:
            raise UserException("Did not match any services")

    return services


def cmd_login() -> None:
    from .downloader import Downloader

    downloader = Downloader()

    username = input("Username: ")
    password = getpass("Password: ")

    downloader.login(username, password)
    print("Logged in successfully")


def cmd_refresh() -> None:
    from .downloader import Downloader

    try:
        old_services = load_services()
    except ServiceException:
        old_services = []

    downloader = Downloader()
    print("Downloading services...")
    downloader.refresh()
    print("Downloading keychain...")
    downloader.refresh_keychain()

    new_services = load_services()

    if [s.get_fingerprint() for s in old_services] != [s.get_fingerprint() for s in new_services]:
        print()
        print("Found updates!")
        print()
        _list(new_services)
    else:
        print("No updates.")

    obsolete_downloads, total_size = _find_obsolete_downloads(new_services)

    if obsolete_downloads:
        print()
        print(
            f"Found {len(obsolete_downloads)} obsolete downloads ({total_size / 2**20:.1f}MB total); "
            "run `jdmtool clean` to delete them."
        )


def _list(services: list[Service]) -> None:
    config = get_config()
    header_style = config.get("list", "header_style", fallback="")
    odd_row_style = config.get("list", "odd_row_style", fallback="")
    even_row_style = config.get("list", "even_row_style", fallback="")

    row_format = "\033[{}m{:>2}  {:<70}  {:<25}  {:<8}  {:<10}  {:<10}  {:<10}\033[0m"

    print(row_format.format(header_style, "ID", "Name", "Coverage", "Version", "Start Date", "End Date", "Downloaded"))
    for idx, service in enumerate(services):
        avionics: str = service.get_property('avionics')
        service_type: str = service.get_property('service_type')
        name = f'{avionics} - {service_type}'
        coverage: str = service.get_property('coverage_desc')
        if len(coverage) > 25:
            coverage = coverage[:24] + '…'
        version: str = service.get_property('display_version')
        start_date: str = service.get_property('version_start_date').split()[0]
        end_date: str = service.get_property('version_end_date').split()[0]

        downloaded = all(f.exists() for f in service.get_download_paths())

        style = even_row_style if idx % 2 == 0 else odd_row_style

        print(row_format.format(style, idx, name, coverage, version, start_date, end_date, 'Y' if downloaded else ''))


def cmd_list() -> None:
    services = load_services()
    _list(services)


def cmd_info(id: int) -> None:
    services = load_services()
    if id < 0 or id >= len(services):
        raise UserException("Invalid download ID")

    service = services[id]

    for desc, path in DETAILED_INFO_MAP:
        if desc is None:
            print()
        else:
            value = service.get_optional_property(path, "")
            print(f'{desc+":":<30}{value}')

    download_paths = service.get_download_paths()

    print()
    print("Downloads:")
    for f in download_paths:
        status = '' if f.exists() else '  (missing)'
        print(f'  {f}{status}')


def _download(downloader: Downloader, service: Service) -> None:
    databases = service.get_databases()
    sffs = service.get_sffs()
    oems = service.get_oems()

    for database in databases:
        if database.dest_path.exists():
            print(f"Skipping {database.dest_path}: already exists")
            continue

        database.dest_path.parent.mkdir(parents=True, exist_ok=True)

        with tqdm.tqdm(desc=f"Downloading {database.dest_path.name}", total=database.size, unit='B', unit_scale=True) as t:
            downloader.download_database(database.params, database.dest_path, database.crc32, t.update)

        print(f"Downloaded to {database.dest_path}")

    for sff in sffs:
        if sff.dest_path.exists():
            print(f"Skipping {sff.dest_path}: already exists")
            continue

        sff.dest_path.parent.mkdir(parents=True, exist_ok=True)

        print(f'Downloading {sff.dest_path.name}...')
        downloader.download_sff(sff.params, sff.dest_path)
        print(f"Downloaded to {sff.dest_path}")

    for oem in oems:
        if oem.dest_path.exists():
            print(f"Skipping {oem.dest_path}: already exists")
            continue

        oem.dest_path.parent.mkdir(parents=True, exist_ok=True)

        print(f'Downloading {oem.dest_path.name}...')
        downloader.download_oem(oem.params, oem.dest_path)
        print(f"Downloaded to {oem.dest_path}")


def cmd_download(ids: list[int] | IdPreset) -> None:
    from .downloader import Downloader

    services = _load_services_by_ids(ids)
    downloader = Downloader()

    for service in services:
        _download(downloader, service)


@dataclass
class DotJdmConfig:
    sh_size: int
    files: list[pathlib.Path]


def update_dot_jdm(service: Service, path: pathlib.Path, config: DotJdmConfig) -> None:
    from .checksum import crc32q_checksum

    try:
        with open(path / DOT_JDM, encoding='utf-8') as fd:
            data = json.load(fd)
    except (OSError, ValueError):
        data = {}

    # Calculate new file hashes
    file_info: list[dict[str, Any]] = []
    file_path_set: set[str] = set()

    for f in config.files:
        size = f.stat().st_size

        with open(f, 'rb') as fd:
            sh = crc32q_checksum(fd.read(config.sh_size))
            if size <= DOT_JDM_MAX_FH_SIZE:
                fh = sh
                while True:
                    chunk = fd.read(0x8000)
                    if not chunk:
                        break
                    fh = crc32q_checksum(chunk, fh)
            else:
                fh = None

        rel_file_path = str(f.relative_to(path))
        file_info.append({
            "fp": rel_file_path,
            "fs": size,
            "sh": f"{sh:08x}",
            "fh": f"{fh:08x}" if fh is not None else "",
        })
        file_path_set.add(rel_file_path)

    # Drop any services that have overlapping files
    ss = []
    for existing_service in data.get("ss") or []:
        for f in existing_service["f"]:
            if f["fp"] in file_path_set:
                break
        else:
            ss.append(existing_service)

    # Write the new .jdm
    gsi = service.get_optional_property('garmin_system_ids')
    ss.append({
        "a": service.get_property('avionics'),
        "c": service.get_property('customer_number'),
        "cd": service.get_property('coverage_desc'),
        "date_label_override": service.get_property('date_label_override'),
        "dv": service.get_property('display_version'),
        "f": file_info,
        "fid": service.get_optional_property('fleet_ids', ""),
        "filter_applied": "no",
        "gsi": f"0x{gsi}" if gsi else "",
        "jvsn": service.get_optional_property('serial_number', ""),
        "ndv": service.get_optional_property('next_display_version', ""),
        "nvad": service.get_optional_property('next_version_avail_date', ""),
        "nvsd": service.get_optional_property('next_version_start_date', ""),
        "oatn": service.get_property('oracle_aircraft_tail_number'),
        "pi": service.get_property('product_item'),
        "s": service.get_property('service_type'),  # ?
        "sc": service.get_property('coverage_desc'),
        "u": service.get_property('unique_service_id'),
        "uv": service.get_property('unique_service_id') + '_' + service.get_property('version'),
        "v": service.get_property('version'),
        "ved": service.get_property('version_end_date'),
        "vsd": service.get_property('version_start_date'),
    })

    data = {
        "ss": ss,
        "ver": "1.1",
        "z": "DEADBEEF",
    }

    data_str = json.dumps(data, separators=(',', ':'), sort_keys=True)
    z = crc32q_checksum(data_str.encode())
    data["z"] = f"{z:08x}"

    with open(path / DOT_JDM, 'w', encoding='utf-8') as fd:
        json.dump(data, fd, separators=(',', ':'), sort_keys=True)


def get_device_volume_id(path: pathlib.Path) -> int:
    partition = next((p for p in psutil.disk_partitions() if pathlib.Path(p.mountpoint) == path), None)
    if partition is None:
        raise UserException(f"Could not find the device name for {path}")

    if psutil.LINUX:
        import pyudev  # type: ignore  pylint: disable=import-error

        if partition.fstype != 'vfat':
            raise UserException(f"Wrong filesystem: {partition.fstype}")

        ctx = pyudev.Context()
        devices = list(ctx.list_devices(subsystem='block', DEVNAME=partition.device))
        if not devices:
            raise JdmToolException(f"Could not find the device for {partition.device}")

        volume_id_str = devices[0].properties['ID_FS_UUID'].replace('-', '')
        if len(volume_id_str) != 8:
            raise JdmToolException(f"Unexpected volume ID: {volume_id_str}")

        return int(volume_id_str, 16)
    elif psutil.WINDOWS:
        import win32api  # type: ignore  pylint: disable=import-error

        if partition.fstype != 'FAT32' and partition.fstype != 'FAT':
            raise UserException(f"Wrong filesystem: {partition.fstype}")

        return win32api.GetVolumeInformation(str(path))[1] & 0xFFFFFFFF
    elif psutil.MACOS:
        raise UserException("Volume IDs not yet supported; enter it manually using --vol-id")
    else:
        raise UserException("OS not supported")


def _format_service_name(service: Service, now: datetime) -> str:
    avionics = service.get_property('avionics')
    service_type = service.get_property('service_type')
    name = f'{avionics} - {service_type}'

    version = service.get_property('display_version')
    start = service.get_start_date()
    end = service.get_end_date()
    if now > end:
        note = "  \033[1;31m(EXPIRED)\033[0m"
    elif now < start:
        note = "  \033[1m(not valid yet)\033[0m"
    else:
        note = ''

    return f'{name:<70}{version:<8}{start.date()} - {end.date()}{note}'


def _transfer_avidyne_e2(service: Service, path: pathlib.Path, volume_id: int) -> DotJdmConfig:
    from .avidyne import SFXFile, SecurityContext

    databases = service.get_databases()
    assert len(databases) == 1
    database_path = databases[0].dest_path

    dot_jdm = DotJdmConfig(0x8000, [])

    tail_drm = service.get_optional_property("oem_avidyne_taildrm_enabled", "") == "1"
    dsf_dir = pathlib.PurePosixPath("tail" if tail_drm else ".")

    fleet_ids_str = service.get_optional_property("fleet_ids", "")
    fleet_ids = [fid.rstrip() for fid in fleet_ids_str.split(",")] if fleet_ids_str else []

    def _dsf_filter(path_str: str):
        # Look for files ending with dsf.txt, even not preceeded by a dot, or anything at all.
        path = pathlib.PurePosixPath(path_str)
        return path.parent == dsf_dir and path.name.endswith('dsf.txt')

    with zipfile.ZipFile(database_path) as database_zip:
        dsf_txt_files = [f for f in database_zip.infolist() if _dsf_filter(f.filename)]
        if not dsf_txt_files:
            raise JdmToolException("Did not find a dsf.txt file")
        if len(dsf_txt_files) > 1:
            raise JdmToolException(f"Found multiple dsf.txt files: {dsf_txt_files}")
        dsf_txt_file = dsf_txt_files[0]

        with database_zip.open(dsf_txt_file) as dsf_bytes:
            with TextIOWrapper(dsf_bytes) as dsf_txt:
                script = SFXFile.parse_script(dsf_dir, dsf_txt)

        dsf_name = pathlib.PurePosixPath(dsf_txt_file.filename).name[:-4].lower()
        if not dsf_name.endswith('.dsf'):
            dsf_name += '.dsf'

        ctx = SecurityContext(service.get_property('display_version'), volume_id, 2, fleet_ids)

        dest = path / dsf_name
        total = script.total_progress(database_zip)
        with tqdm.tqdm(desc=f"Writing to {dest}", total=total, unit='B', unit_scale=True) as t:
            with open(dest, 'wb') as dsf_fd:
                script.run(dsf_fd, database_zip, ctx, t.update)

        dot_jdm.files.append(dest)

    return dot_jdm


def _transfer_avidyne_basic(service: Service, path: pathlib.Path, _: int) -> DotJdmConfig:
    databases = service.get_databases()
    assert len(databases) == 1
    database_path = databases[0].dest_path

    dot_jdm = DotJdmConfig(0x2000, [])

    with zipfile.ZipFile(database_path) as database_zip:
        infolist = database_zip.infolist()
        for info in infolist:
            print(f"Extracting {info.filename}...")
            database_zip.extract(info, path)
            dot_jdm.files.append(path / info.filename)

    return dot_jdm


def _transfer_g1000_basic(service: Service, path: pathlib.Path, volume_id: int) -> DotJdmConfig:
    from .g1000 import copy_with_feat_unlk, FILENAME_TO_FEATURE

    databases = service.get_databases()
    assert len(databases) == 1
    database_path = databases[0].dest_path

    dot_jdm = DotJdmConfig(0x2000, [])
    security_id = int(service.get_property('garmin_sec_id'))
    system_id = int(service.get_property('avionics_id'), 16)

    with zipfile.ZipFile(database_path) as database_zip:
        infolist = database_zip.infolist()
        for info in infolist:
            info.filename = info.filename.replace('\\', '/')  # 🤦‍
            if info.filename not in FILENAME_TO_FEATURE:
                raise UserException(f"Unexpected filename: {info.filename}! Please file a bug.")

            target = path / info.filename
            with tqdm.tqdm(desc=f"Extracting {info.filename}...", total=info.file_size, unit='B', unit_scale=True) as t:
                with database_zip.open(info) as src_fd:
                    copy_with_feat_unlk(path, src_fd, info.filename, volume_id, security_id, system_id, t.update)

            dot_jdm.files.append(target)

    return dot_jdm


def _transfer_g1000_chartview(service: Service, path: pathlib.Path, volume_id: int) -> DotJdmConfig:
    from .chartview import ChartView
    from .g1000 import Feature, feat_unlk_checksum, update_feat_unlk

    charts_path = path / 'Charts'
    charts_path.mkdir(exist_ok=True)

    charts_files: list[str] = []

    zip_files = [d.dest_path for d in service.get_databases()]
    with ChartView(zip_files) as cv:
        print("Processing charts.ini...")
        charts_files.append('charts.ini')
        db_begin_date = cv.process_charts_ini(charts_path)

        charts_bin_size = cv.get_charts_bin_size()
        charts_files.append('charts.bin')
        with tqdm.tqdm(desc="Processing charts.bin", total=charts_bin_size, unit='B', unit_scale=True) as t:
            filenames_by_chart = cv.process_charts_bin(charts_path, db_begin_date, t.update)

        print("Reading airports...")
        ifr_charts_by_airport = cv.get_charts_by_airport(False)
        vfr_charts_by_airport = cv.get_charts_by_airport(True)

        airports_by_key = cv.get_airports_by_key()

        ifr_airports: set[str] = set()
        vfr_airports: set[str] = set()

        for (code, is_vfr), filenames in filenames_by_chart.items():
            print(f"Guessing subscription for code {code}...")

            subscription_airports = vfr_airports if is_vfr else ifr_airports
            charts_by_airport = vfr_charts_by_airport if is_vfr else ifr_charts_by_airport

            subscription_charts = {filename.split('.')[0].upper() for filename in filenames}

            airports = {
                airport for airport, charts in charts_by_airport.items()
                if subscription_charts.issuperset(charts)
            }

            matches = []
            for key, key_airports in airports_by_key.items():
                if key_airports.issuperset(airports):
                    matches.append((key, key_airports))
            if not matches:
                raise ValueError("Failed to find any matching subscriptions!")

            matches.sort(key=lambda match: len(match[1]))
            best_subscription, best_airports = matches[0]
            subscription_airports.update(best_airports)
            print(f"Best match: {best_subscription}, {len(best_airports)} airports")

        print("Processing charts.dbf...")
        charts_files.append('charts.dbf')
        charts = cv.process_charts(ifr_airports, vfr_airports, charts_path)

        print("Processing chrtlink.dbf...")
        charts_files.append('chrtlink.dbf')
        chartlink = cv.process_chartlink(ifr_airports, vfr_airports, charts_path)

        print("Processing airports.dbf...")
        charts_files.append('airports.dbf')
        ifr_countries, vfr_countries = cv.process_airports(
            ifr_airports, vfr_airports, charts, chartlink, charts_path)

        print("Processing notams.dbf + notams.dbt...")
        charts_files.extend(['notams.dbf', 'notams.dbt'])
        cv.process_notams(ifr_airports, vfr_airports, ifr_countries, vfr_countries, charts_path)

        for filename in ChartView.FILES_TO_COPY:
            print(f"Extracting {filename}...")
            charts_files.append(filename)
            cv.extract_file(filename, charts_path)

        print("Extracting fonts...")
        fonts_files = cv.extract_fonts(path)

        print("Processing crcfiles.txt...")
        charts_files.append('crcfiles.txt')
        cv.process_crcfiles(charts_path)

    security_id = int(service.get_property('garmin_sec_id'))
    system_id = int(service.get_property('avionics_id'), 16)

    crcfiles = (charts_path / 'crcfiles.txt').read_bytes()
    chk = feat_unlk_checksum(crcfiles)

    update_feat_unlk(path, Feature.CHARTVIEW, volume_id, security_id, system_id, chk, None)

    for oem in service.get_oems():
        with zipfile.ZipFile(oem.dest_path) as oem_zip:
            for entry in oem_zip.infolist():
                print(f"Extracting {entry.filename}...")
                oem_zip.extract(entry, charts_path)

    fonts_files.sort()
    charts_files.sort()
    dot_jdm_files = [path / f for f in fonts_files] + [charts_path / f for f in charts_files]

    return DotJdmConfig(0x2000, dot_jdm_files)


def _transfer_sd_card(services: list[Service], path: pathlib.Path, vol_id_override: str | None) -> None:
    transfer_funcs: list[Callable[[Service, pathlib.Path, int], DotJdmConfig]] = []

    for service in services:
        if isinstance(service, SimpleService):
            if service.get_optional_property("oem_avidyne_e2") == '1':
                transfer_func = _transfer_avidyne_e2
            elif service.get_optional_property("oem_avidyne") == '1':
                transfer_func = _transfer_avidyne_basic
            elif service.get_optional_property("oem_garmin") == '1':
                transfer_func = _transfer_g1000_basic
            else:
                raise UserException("This service is not yet supported")
        else:
            if service.get_optional_property("oem_garmin") == '1':
                print()
                print("WARNING: Electronic Charts support is very experimental!")
                print("Transferred files may not completely match JDM, and may not even be correct.")
                print("Please report your results at https://github.com/dimaryaz/jdmtool/issues.")
                print()
                transfer_func = _transfer_g1000_chartview
            else:
                raise UserException("Unexpected service type")
        transfer_funcs.append(transfer_func)

    if path.is_block_device():
        raise UserException(f"{path} is a device file; need the directory where the SD card is mounted")

    if not path.is_dir():
        raise UserException(f"{path} is not a directory")

    if isinstance(path, pathlib.PosixPath):
        if not path.is_mount():
            print(f"WARNING: {path} appears to be a normal directory, not a device.")
    else:
        if path.parent != path:
            print(f"WARNING: {path} appears to be a directory, not a drive.")
        elif not path.root:
            path = path / '/'

    if vol_id_override is not None:
        try:
            vol_id_override = vol_id_override.replace('-', '')
            if len(vol_id_override) != 8:
                raise ValueError()
            volume_id = int(vol_id_override, 16)
        except ValueError:
            raise UserException("Volume ID must be 8 hex digits long") from None
        print(f"Using a manually-provided volume ID: {volume_id:08x}")
    else:
        volume_id = get_device_volume_id(path)
        print(f"Found volume ID: {volume_id:08x}")

    print()
    print("Selected services:")
    now = datetime.now()
    for service in services:
        print("  " + _format_service_name(service, now))
    print()
    confirm(f"Transfer to {path}?")

    if not all(f.exists() for s in services for f in s.get_download_paths()):
        from .downloader import Downloader

        downloader = Downloader()
        for service in services:
            _download(downloader, service)

    start = time.perf_counter()

    for service, transfer_func in zip(services, transfer_funcs):
        dot_jdm_config = transfer_func(service, path, volume_id)

        sffs = service.get_sffs()
        for sff in sffs:
            sff_target = path / sff.dest_path.name
            print(f"Copying {sff.dest_path} to {sff_target}...")
            shutil.copyfile(sff.dest_path, sff_target)

        if sffs:
            keychain_source = get_data_dir() / GRM_FEAT_KEY
            (path / LDR_SYS).mkdir(exist_ok=True)
            keychain_target = path / LDR_SYS / GRM_FEAT_KEY
            print(f"Copying {keychain_source} to {keychain_target}...")
            shutil.copyfile(keychain_source, keychain_target)

        print("Updating .jdm...")
        update_dot_jdm(service, path, dot_jdm_config)

    print(f"Done in: {time.perf_counter() - start:.1f}s.")


@with_data_card
def _transfer_data_card(dev: ProgrammingDevice, service: Service, full_erase: bool) -> None:
    databases = service.get_databases()
    assert len(databases) == 1, databases

    card_size_min = int(service.get_property('media/card_size_min'))
    card_size_max = int(service.get_property('media/card_size_max'))

    if not card_size_min <= dev.get_total_size() <= card_size_max:
        print()
        print(
            f"WARNING: This service requires a data card between "
            f"{card_size_min // 2**20}MB and {card_size_max // 2**20}MB, "
            f"but yours is {dev.get_total_size() // 2**20}MB!"
        )
        print()

    print()
    print("Selected service:")
    print("  " + _format_service_name(service, datetime.now()))
    print()
    confirm("Transfer to the data card?")

    if not all(f.exists() for f in service.get_download_paths()):
        from .downloader import Downloader

        downloader = Downloader()
        _download(downloader, service)

    path = databases[0].dest_path

    start = time.perf_counter()

    _write_database(dev, str(path), full_erase)

    print(f"Done in: {time.perf_counter() - start:.1f}s.")


def cmd_transfer(
    ids: list[int] | IdPreset,
    device: str | None,
    no_download: bool,
    vol_id: str | None,
    full_erase: bool,
) -> None:
    services = _load_services_by_ids(ids)

    if no_download and not all(f.exists() for s in services for f in s.get_download_paths()):
        raise UserException("Need to download the data, but --no-download was specified")

    card_types = set(int(service.get_property('media/card_type')) for service in services)
    if len(card_types) != 1:
        raise UserException("Cannot mix SD card and programmer device services")
    card_type = CardType(card_types.pop())

    if card_type is CardType.SD:
        if not device:
            raise UserException("This database requires a path to an SD card")

        if full_erase:
            raise UserException("--full-erase only makes sense for data cards")

        _transfer_sd_card(services, pathlib.Path(device), vol_id)
    elif card_type is CardType.DATA_CARD:
        if device:
            raise UserException("This database requires a programmer device and does not support paths")
        if len(services) != 1:
            raise UserException("Cannot transfer multiple programmer device services at the same time")

        if vol_id:
            raise UserException("--vol-id only makes sense for SD cards / USB drives")

        _transfer_data_card(services[0], full_erase)  # pylint: disable=no-value-for-parameter


def cmd_clean() -> None:
    try:
        services = load_services()
    except ServiceException:
        print("WARNING: Did not find any services. Will be cleaning all downloads!")
        print()
        services = []

    obsolete_downloads, total_size = _find_obsolete_downloads(services)

    if obsolete_downloads:
        print(f"Found {len(obsolete_downloads)} obsolete downloads ({total_size / 2**20:.1f}MB total):")
        for path in obsolete_downloads:
            print(f"  {path}")

        print()
        confirm("Delete?")

        for path in obsolete_downloads:
            path.unlink()

        print("Deleted.")
    else:
        print("No obsolete downloads found.")


@with_usb
def cmd_detect(dev: ProgrammingDevice, verbose: bool) -> None:
    firmware = dev.get_firmware_description()
    print(f"Firmware version: {firmware}")
    if dev.has_card():
        if verbose:
            # Print IIDs first, even if it's an unsupported card.
            print(f"Chip IIDs: {' '.join(f'0x{iid:08x}' for iid in dev.get_chip_iids())}")
        # Then (try to) initialize the card and print the info.
        dev.init_data_card()
        print(f"Card type: {dev.get_card_name()}, {dev.get_card_description()}")
    else:
        print("No card")


@with_data_card
def cmd_read_database(dev: ProgrammingDevice, path: str, full_card: bool) -> None:
    start = time.perf_counter()

    with open(path, 'w+b') as fd:
        with tqdm.tqdm(desc="Reading the database", total=dev.get_total_size(), unit='B', unit_scale=True) as t:
            for block in dev.read_blocks(0, dev.get_total_sectors()):
                if not full_card and block == b'\xFF' * ProgrammingDevice.BLOCK_SIZE:
                    # Garmin card has no concept of size of the data,
                    # so we stop when we see a completely empty block.
                    break

                fd.write(block)
                t.update(len(block))

    print(f"Done in: {time.perf_counter() - start:.1f}s.")


def _write_database(dev: ProgrammingDevice, path: str, full_erase: bool) -> None:
    max_size = dev.get_total_size()

    with open(path, 'rb') as fd:
        size = os.fstat(fd.fileno()).st_size

        if size > max_size:
            raise ProgrammingException(f"Database file is too big: {size}! The maximum size is {max_size}.")

        def _read_block():
            return fd.read(ProgrammingDevice.BLOCK_SIZE).ljust(ProgrammingDevice.BLOCK_SIZE, b'\xFF')

        sectors_required = -(-size // ProgrammingDevice.SECTOR_SIZE)
        total_size = sectors_required * ProgrammingDevice.SECTOR_SIZE

        if full_erase:
            sectors_to_erase = dev.get_total_sectors()
        else:
            # Erase an extra sector just to be safe.
            sectors_to_erase = min(sectors_required + 1, dev.get_total_sectors())
        total_erase_size = sectors_to_erase * ProgrammingDevice.SECTOR_SIZE

        sector_is_blank = [True] * sectors_to_erase

        with tqdm.tqdm(desc="Blank checking", total=total_erase_size, unit='B', unit_scale=True) as t:
            for sector_idx in range(sectors_to_erase):
                for i, block in enumerate(dev.read_blocks(sector_idx, 1)):
                    if block != b'\xff' * ProgrammingDevice.BLOCK_SIZE:
                        sector_is_blank[sector_idx] = False
                        t.update(ProgrammingDevice.BLOCK_SIZE * (ProgrammingDevice.BLOCKS_PER_SECTOR - i))
                        break

                    t.update(ProgrammingDevice.BLOCK_SIZE)

        # Data card can only write by changing 1s to 0s (effectively doing a bit-wise AND with
        # the existing contents), so all data needs to be "erased" first to reset everything to 1s.
        with tqdm.tqdm(desc="Erasing the database", total=total_erase_size, unit='B', unit_scale=True) as t:
            for sector_idx in range(sectors_to_erase):
                if not sector_is_blank[sector_idx]:
                    for _ in dev.erase_sectors(sector_idx, 1):
                        pass
                t.update(ProgrammingDevice.SECTOR_SIZE)

        with tqdm.tqdm(desc="Writing the database", total=total_size, unit='B', unit_scale=True) as t:
            for _ in dev.write_blocks(0, sectors_required, _read_block):
                t.update(ProgrammingDevice.BLOCK_SIZE)

        fd.seek(0)

        with tqdm.tqdm(desc="Verifying the database", total=total_size, unit='B', unit_scale=True) as t:
            for card_block in dev.read_blocks(0, sectors_required):
                file_block = _read_block()

                if card_block != file_block:
                    raise ProgrammingException("Verification failed!")

                t.update(len(file_block))

@with_data_card
def cmd_write_database(dev: ProgrammingDevice, path: str, full_erase: bool) -> None:
    confirm(f"Transfer {path} to the data card?")

    start = time.perf_counter()

    try:
        _write_database(dev, path, full_erase)
    except IOError as ex:
        raise ProgrammingException(f"Could not read the database file: {ex}")

    print(f"Done in: {time.perf_counter() - start:.1f}s.")


def _clear_card(dev: ProgrammingDevice) -> None:
    sectors_to_erase = dev.get_total_sectors()
    total_erase_size = sectors_to_erase * ProgrammingDevice.SECTOR_SIZE

    with tqdm.tqdm(desc="Erasing the database", total=total_erase_size, unit='B', unit_scale=True) as t:
        for _ in dev.erase_sectors(0, sectors_to_erase):
            t.update(ProgrammingDevice.SECTOR_SIZE)


@with_data_card
def cmd_clear_card(dev: ProgrammingDevice) -> None:
    confirm("Clear all bytes on the data card?")

    start = time.perf_counter()

    _clear_card(dev)

    print(f"Done in: {time.perf_counter() - start:.1f}s.")


def cmd_config_file() -> None:
    print(get_config_file())


def cmd_extract_taw(input_file: str, verbose: bool, list_only: bool) -> None:
    from .taw import TAW_DATABASE_TYPES, TAW_REGION_PATHS, parse_taw_metadata, read_taw_header, read_taw_sections

    input_file_path = pathlib.Path(input_file)

    debug = print if verbose else lambda *_: None

    with open(input_file_path, 'rb') as fd_in:
        sqa1, meta_bytes, sqa2 = read_taw_header(fd_in)
        debug(f"SQA1: {sqa1}")
        debug(f"SQA2: {sqa2}")

        try:
            m = parse_taw_metadata(meta_bytes)
            database_type_name = TAW_DATABASE_TYPES.get(m.database_type, "Unknown")
            print(f"Database type: {m.database_type:x} ({database_type_name})")
            print(f"Year: {m.year}")
            print(f"Cycle: {m.cycle}")
            print(f"Avionics: {m.avionics!r}")
            print(f"Coverage: {m.coverage!r}")
            print(f"Type: {m.type.upper()!r}")
        except ValueError as ex:
            print(ex)

        print()

        databases: list[tuple[str, int]] = []
        for s in read_taw_sections(fd_in):
            debug(f"Section start: {s.sect_start:x}")
            debug(f"Section size: {s.sect_size:x}")

            dest_path = TAW_REGION_PATHS.get(s.region)
            debug(f"Region: {s.region:02x} ({dest_path or 'unknown'})")
            debug(f"Unknown: {s.unknown}")
            debug(f"Database start: {s.data_start}")
            debug(f"Database size: {s.data_size}")

            if dest_path:
                output_file = pathlib.PurePosixPath(dest_path).name
            else:
                output_file = f"region_{s.region:02x}.bin"

            databases.append((output_file, s.data_size))

            if not list_only:
                print(f"Extracting {output_file}... ", end='')
                assert fd_in.tell() == s.data_start
                block_size = 0x1000
                with open(output_file, 'wb') as fd_out:
                    for offset in range(0, s.data_size, block_size):
                        block = fd_in.read(min(s.data_size - offset, block_size))
                        fd_out.write(block)
                print("Done")
            debug()

        tail = fd_in.read()
        debug(f"Tail: {tail}")

        if list_only:
            debug()
            print(f"{len(databases)} database(s):")
            for database, database_size in databases:
                print(f"{database_size:>10} {database}")


def _parse_ids(ids: str) -> list[int] | IdPreset:
    try:
        return IdPreset(ids)
    except ValueError:
        return [int(s) for s in ids.split(',')]


def main():
    parser = argparse.ArgumentParser(description="Download and transfer Jeppesen databases")

    parser.add_argument('--version', action='version', version=importlib_metadata.version('jdmtool'))

    parser.add_argument(
        '-y', '--assume-yes',
        action='store_true',
        help="Disable confirmations; assume the answer is 'yes' for everything",
    )

    subparsers = parser.add_subparsers(metavar="<command>")
    subparsers.required = True

    login_p = subparsers.add_parser(
        "login",
        help="Log into Jeppesen",
    )
    login_p.set_defaults(func=cmd_login)

    refresh_p = subparsers.add_parser(
        "refresh",
        help="Refresh the list available downloads",
    )
    refresh_p.set_defaults(func=cmd_refresh)

    list_p = subparsers.add_parser(
        "list",
        help="Show the (cached) list of available downloads",
    )
    list_p.set_defaults(func=cmd_list)

    info_p = subparsers.add_parser(
        "info",
        help="Show detailed info about the download",
    )
    info_p.add_argument(
        "id",
        help="ID of the download",
        type=int,
    )
    info_p.set_defaults(func=cmd_info)

    download_p = subparsers.add_parser(
        "download",
        help="Download the data",
    )
    download_p.add_argument(
        "ids",
        help="Comma-separated list of service IDs, 'curr' for all current versions, or 'next' for all future versions",
        type=_parse_ids,
    )
    download_p.set_defaults(func=cmd_download)

    transfer_p = subparsers.add_parser(
        "transfer",
        help="Transfer the downloaded database to a USB drive or a Skybound data card",
    )
    transfer_p.add_argument(
        "--no-download",
        help="Don't automatically downloaded missing databases",
        action="store_true",
    )
    transfer_p.add_argument(
        "--vol-id",
        help="FAT32 Volume ID (e.g., 1234-ABCD)",
        type=str,
    )
    transfer_p.add_argument(
        "-f", "--full-erase",
        action="store_true",
        help="Erase the whole card, regardless of the database size (only for data cards)",
    )
    transfer_p.add_argument(
        "ids",
        help="Comma-separated list of service IDs, 'curr' for all current versions, or 'next' for all future versions",
        type=_parse_ids,
    )
    transfer_p.add_argument(
        "device",
        help="SD card directory (for Avidyne/G1000 databases)",
        type=str,
        nargs='?',
    )
    transfer_p.set_defaults(func=cmd_transfer)

    clean_p = subparsers.add_parser(
        "clean",
        help="Delete downloaded files that are not used by any current service",
    )
    clean_p.set_defaults(func=cmd_clean)

    detect_p = subparsers.add_parser(
        "detect",
        help="Detect a card programmer device",
    )
    detect_p.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Increase output verbosity",
    )
    detect_p.set_defaults(func=cmd_detect)

    read_database_p = subparsers.add_parser(
        "read-database",
        help="Read the database from a data card and write to a file",
    )
    read_database_p.add_argument(
        "path",
        help="File to write the database to",
    )
    read_database_p.add_argument(
        "-f", "--full-card",
        action="store_true",
        help="Read the full contents of the card instead of stopping at the first empty block",
    )
    read_database_p.set_defaults(func=cmd_read_database)

    write_database_p = subparsers.add_parser(
        "write-database",
        help="Write the database to a data card",
    )
    write_database_p.add_argument(
        "path",
        help="Database file, e.g. dgrw72_2302_742ae60e.bin",
    )
    write_database_p.add_argument(
        "-f", "--full-erase",
        action="store_true",
        help="Erase the whole card, regardless of the database size",
    )
    write_database_p.set_defaults(func=cmd_write_database)

    clear_card_p = subparsers.add_parser(
        "clear-card",
        help="Clear all bytes on a data card",
    )
    clear_card_p.set_defaults(func=cmd_clear_card)

    config_file_p = subparsers.add_parser(
        "config-file",
        help="Print the path of config.ini",
    )
    config_file_p.set_defaults(func=cmd_config_file)

    extract_taw_p = subparsers.add_parser(
        "extract-taw",
        help="Extract the database from a Garmin .awp or .taw file",
    )
    extract_taw_p.add_argument(
        "input_file",
        help="Input .awp or .taw file",
    )
    extract_taw_p.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Increase output verbosity",
    )
    extract_taw_p.add_argument(
        "-l", "--list-only",
        action="store_true",
        help="List databases without extracting",
    )
    extract_taw_p.set_defaults(func=cmd_extract_taw)

    args = parser.parse_args()

    kwargs = vars(args)
    func = kwargs.pop('func')

    if kwargs.pop('assume_yes'):
        PROMPT_CTX.set(lambda _: None)

    try:
        func(**kwargs)
    except JdmToolException as ex:
        print(ex)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
