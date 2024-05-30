import argparse 
from enum import Enum
from functools import wraps
from getpass import getpass
from io import TextIOWrapper
import json
import os
import pathlib
import shutil
import typing as T
import zipfile

import libscrc
import psutil
import tqdm
import usb1

from .skybound import SkyboundDevice, SkyboundException
from .downloader import Downloader, DownloaderException, GRM_FEAT_KEY
from .service import Service, ServiceException, SimpleService, get_data_dir, load_services


CARD_TYPE_SD = 2
CARD_TYPE_GARMIN = 7

DOT_JDM = '.jdm'

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


def with_usb(f: T.Callable):
    @wraps(f)
    def wrapper(*args, **kwargs):
        with usb1.USBContext() as usbcontext:
            try:
                usbdev = usbcontext.getByVendorIDAndProductID(SkyboundDevice.VID, SkyboundDevice.PID)
                if usbdev is None:
                    raise SkyboundException("Device not found")

                print(f"Found device: {usbdev}")
                handle = usbdev.open()
            except usb1.USBError as ex:
                raise SkyboundException(f"Could not open: {ex}")

            handle.setAutoDetachKernelDriver(True)
            with handle.claimInterface(0):
                handle.resetDevice()
                dev = SkyboundDevice(handle)
                dev.init()
                try:
                    f(dev, *args, **kwargs)
                finally:
                    dev.set_led(False)

    return wrapper


def with_data_card(f: T.Callable):
    @wraps(f)
    @with_usb
    def wrapper(dev: SkyboundDevice, *args, **kwargs):
        if not dev.has_card():
            raise SkyboundException("Card is missing!")

        dev.select_page(0)
        dev.before_read()

        # TODO: Figure out the actual meaning of the iid and the "unknown" value.
        iid = dev.get_iid()
        if iid == 0x01004100:
            # 16MB WAAS card
            print("Detected data card: 16MB WAAS")
            dev.set_memory_layout(SkyboundDevice.MEMORY_LAYOUT_16MB)
        elif iid == 0x0100ad00:
            # 4MB non-WAAS card
            print("Detected data card: 4MB non-WAAS")
            dev.set_memory_layout(SkyboundDevice.MEMORY_LAYOUT_4MB)
        else:
            raise SkyboundException(
                f"Unknown data card IID: 0x{iid:08x} (possibly 8MB non-WAAS?). Please file a bug!"
            )

        f(dev, *args, **kwargs)

    return wrapper


def _loop_helper(dev, i):
    dev.set_led(i % 2 == 0)
    if not dev.has_card():
        raise SkyboundException("Data card has disappeared!")


def cmd_login() -> None:
    downloader = Downloader()

    username = input("Username: ")
    password = getpass("Password: ")

    downloader.login(username, password)
    print("Logged in successfully")

def cmd_refresh() -> None:
    downloader = Downloader()
    print("Downloading services...")
    downloader.refresh()
    print("Downloading keychain...")
    downloader.refresh_keychain()
    print("Success")

def cmd_list() -> None:
    services = load_services()

    row_format = "{:>2}  {:<70}  {:<25}  {:<8}  {:<10}  {:<10}  {:<10}"

    header = row_format.format("ID", "Name", "Coverage", "Version", "Start Date", "End Date", "Downloaded")
    print(f'\033[1m{header}\033[0m')
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

        print(row_format.format(idx, name, coverage, version, start_date, end_date, 'Y' if downloaded else ''))

def cmd_info(id: int) -> None:
    services = load_services()
    if id < 0 or id >= len(services):
        raise DownloaderException("Invalid download ID")

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

def cmd_download(id: int) -> None:
    downloader = Downloader()

    services = load_services()
    if id < 0 or id >= len(services):
        raise DownloaderException("Invalid download ID")

    service = services[id]

    databases = service.get_databases()
    sffs = service.get_sffs()
    oems = service.get_oems()

    for database in databases:
        if database.dest_path.exists():
            print(f"Skipping {database.dest_path}: already exists")
            continue

        database.dest_path.parent.mkdir(parents=True, exist_ok=True)

        with tqdm.tqdm(desc=f"Downloading {database.dest_path.name}", total=database.size, unit='B', unit_scale=True) as t:
            def _update(n: int) -> None:
                t.update(n)

            downloader.download_database(database.params, database.dest_path, database.crc32, _update)

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


def update_dot_jdm(service: Service, path: pathlib.Path, files: T.List[pathlib.Path], sh_size: int) -> None:
    try:
        with open(path / DOT_JDM) as fd:
            data = json.load(fd)
    except Exception as e:
        data = {}

    # Calculate new file hashes
    file_info: T.List[T.Dict[str, T.Any]] = []
    file_path_set: T.Set[str] = set()

    for f in files:
        with open(f, 'rb') as fd:
            sh = libscrc.crc32_q(fd.read(sh_size))
            fh = sh
            while True:
                chunk = fd.read(0x8000)
                if not chunk:
                    break
                fh = libscrc.crc32_q(chunk, fh)

        rel_file_path = str(f.relative_to(path))
        file_info.append({
            "fp": rel_file_path,
            "fs": f.stat().st_size,
            "sh": f"{sh:08x}",
            "fh": f"{fh:08x}",
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
        "jvsn": "",
        "ndv": service.get_property('next_display_version'),
        "nvad": service.get_property('next_version_avail_date'),
        "nvsd": service.get_property('next_version_start_date'),
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
    z = libscrc.crc32_q(data_str.encode())
    data["z"] = f"{z:08x}"

    with open(path / DOT_JDM, 'w') as fd:
        json.dump(data, fd, separators=(',', ':'), sort_keys=True)


def get_device_volume_id(path: pathlib.Path) -> int:
    partition = next((p for p in psutil.disk_partitions() if pathlib.Path(p.mountpoint) == path), None)
    if partition is None:
        raise DownloaderException(f"Could not find the device name for {path}")

    if psutil.LINUX:
        import pyudev  # type: ignore

        if partition.fstype != 'vfat':
            raise DownloaderException(f"Wrong filesystem: {partition.fstype}")

        ctx = pyudev.Context()
        devices = list(ctx.list_devices(subsystem='block', DEVNAME=partition.device))
        if not devices:
            raise DownloaderException(f"Could not find the device for {partition.device}")

        volume_id_str = devices[0]['ID_FS_UUID'].replace('-', '')
        if len(volume_id_str) != 8:
            raise DownloaderException(f"Unexpected volume ID: {volume_id_str}")

        return int(volume_id_str, 16)
    elif psutil.WINDOWS:
        import win32api  # type: ignore

        if partition.fstype != 'FAT32':
            raise DownloaderException(f"Wrong filesystem: {partition.fstype}")

        return win32api.GetVolumeInformation(str(path))[1]
    elif psutil.MACOS:
        raise DownloaderException("Volume IDs not yet supported; enter it manually using --vol-id")
    else:
        raise DownloaderException("OS not supported")


def _transfer_sd_card(service: Service, path: pathlib.Path, vol_id_override: T.Optional[str]) -> None:
    class TransferType(Enum):
        AVIDYNE = 1
        GARMIN_BASIC = 2

    if isinstance(service, SimpleService):
        if service.get_optional_property("oem_avidyne_e2") == '1':
            transfer_type = TransferType.AVIDYNE
        elif service.get_optional_property("oem_garmin") == '1':
            transfer_type = TransferType.GARMIN_BASIC
        else:
            raise DownloaderException("This service is not yet supported")
    else:
        raise DownloaderException("Electronic Charts are not yet supported")

    if not isinstance(service, SimpleService):
        raise DownloaderException("Unexpected service")

    if not all(f.exists() for f in service.get_download_paths()):
        raise DownloaderException("Need to download it first")

    databases = service.get_databases()

    if path.is_block_device():
        raise DownloaderException(f"{path} is a device file; need the directory where the SD card is mounted")

    if not path.is_dir():
        raise DownloaderException(f"{path} is not a directory")

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
            raise DownloaderException(f"Volume ID must be 8 hex digits long")
        print(f"Using a manually-provided volume ID: {volume_id:08x}")
    else:
        volume_id = get_device_volume_id(path)
        print(f"Found volume ID: {volume_id:08x}")

    avionics = service.get_property('avionics')
    service_type = service.get_property('service_type')
    name = f'{avionics} - {service_type}'
    prompt = input(f"Transfer {name!r} to {path}? (y/n) ")
    if prompt.lower() != 'y':
        raise DownloaderException("Cancelled")

    dot_jdm_files: T.List[pathlib.Path] = []
    sh_size = 0

    if transfer_type == TransferType.AVIDYNE:
        from .avidyne import SFXFile, SecurityContext

        assert len(databases) == 1
        database_path = databases[0].dest_path

        sh_size = 0x8000

        with zipfile.ZipFile(database_path) as database_zip:
            # Look for files ending with dsf.txt, even not preceeded by a dot, or anything at all.
            dsf_txt_files = [f for f in database_zip.infolist() if '/' not in f.filename and f.filename.endswith('dsf.txt')]
            if not dsf_txt_files:
                raise DownloaderException("Did not find a dsf.txt file")
            if len(dsf_txt_files) > 1:
                raise DownloaderException(f"Found multiple dsf.txt files: {dsf_txt_files}")
            dsf_txt_file = dsf_txt_files[0]

            with database_zip.open(dsf_txt_file, ) as dsf_bytes:
                with TextIOWrapper(dsf_bytes) as dsf_txt:
                    script = SFXFile.parse_script(dsf_txt)

            dsf_name = dsf_txt_file.filename[:-4].lower()
            if not dsf_name.endswith('.dsf'):
                dsf_name += '.dsf'

            ctx = SecurityContext(service.get_property('display_version'), volume_id, 2)

            dest = path / dsf_name
            total = script.total_progress(database_zip)
            with tqdm.tqdm(desc=f"Writing to {dest}", total=total, unit='B', unit_scale=True) as t:
                with open(dest, 'wb') as dsf_fd:
                    script.run(dsf_fd, database_zip, ctx, t.update)

            dot_jdm_files.append(dest)

    elif transfer_type == TransferType.GARMIN_BASIC:
        assert len(databases) == 1
        database_path = databases[0].dest_path

        sh_size = 0x2000
        security_id = int(service.get_property('garmin_sec_id'))
        system_id = int(service.get_property('avionics_id'), 16)

        with zipfile.ZipFile(database_path) as database_zip:
            from .garmin import copy_with_feat_unlk, FILENAME_TO_FEATURE

            infolist = database_zip.infolist()
            for info in infolist:
                info.filename = info.filename.replace('\\', '/')  # 🤦‍
                if info.filename not in FILENAME_TO_FEATURE:
                    raise DownloaderException(f"Unexpected filename: {info.filename}! Please file a bug.")

                target = path / info.filename
                with tqdm.tqdm(desc=f"Extracting {info.filename}...", total=info.file_size, unit='B', unit_scale=True) as t:
                    with database_zip.open(info) as src_fd:
                        copy_with_feat_unlk(path, src_fd, info.filename, volume_id, security_id, system_id, t.update)

                dot_jdm_files.append(target)

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

    else:
        # TODO
        assert False

    print("Updating .jdm...")
    update_dot_jdm(service, path, dot_jdm_files, sh_size)


@with_data_card
def _transfer_garmin(dev: SkyboundDevice, service: Service) -> None:
    databases = service.get_databases()
    assert len(databases) == 1, databases

    version = service.get_property('version')
    unique_service_id = service.get_property('unique_service_id')

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

    path = databases[0].dest_path
    if not path.exists():
        raise DownloaderException("Need to download it first")

    prompt = input(f"Transfer {path} to the data card? (y/n) ")
    if prompt.lower() != 'y':
        raise DownloaderException("Cancelled")

    _clear_metadata(dev)
    _write_database(dev, str(path))

    # TODO: 4MB cards don't seem to have metadata?
    if dev.memory_layout == dev.MEMORY_LAYOUT_16MB:
        new_metadata = f'{{{version}~{unique_service_id}}}'  # E.g. {2303~12345678}
        print(f"Writing new metadata: {new_metadata}")
        _write_metadata(dev, new_metadata)


def cmd_transfer(id: int, device: T.Optional[str], vol_id: T.Optional[str]) -> None:
    services = load_services()
    if id < 0 or id >= len(services):
        raise DownloaderException("Invalid download ID")

    service = services[id]

    card_type = int(service.get_property('media/card_type'))

    if card_type == CARD_TYPE_SD:
        if not device:
            raise DownloaderException("This database requires a path to an SD card")

        _transfer_sd_card(service, pathlib.Path(device), vol_id)
    elif card_type == CARD_TYPE_GARMIN:
        if device:
            raise DownloaderException("This database requires a programmer device and does not support paths")

        if vol_id:
            raise DownloaderException("--vol-id only makes sense for SD cards / USB drives")

        _transfer_garmin(service)

    print("Done")

@with_usb
def cmd_detect(dev: SkyboundDevice) -> None:
    version = dev.get_version()
    print(f"Firmware version: {version}")
    if dev.has_card():
        print("Card inserted:")
        dev.select_page(0)
        dev.before_read()
        iid = dev.get_iid()
        print(f"  IID: 0x{iid:08x}")
        unknown = dev.get_unknown()
        print(f"  Unknown identifier: 0x{unknown:08x}")
    else:
        print("No card")

@with_data_card
def cmd_read_metadata(dev: SkyboundDevice) -> None:
    dev.before_read()
    dev.select_page(dev.get_total_pages() - 1)
    blocks = []
    for i in range(SkyboundDevice.BLOCKS_PER_PAGE):
        _loop_helper(dev, i)
        blocks.append(dev.read_block())
    value = b''.join(blocks).rstrip(b"\xFF").decode()
    print(f"Database metadata: {value}")

def _clear_metadata(dev: SkyboundDevice) -> None:
    dev.before_write()
    dev.select_page(dev.get_total_pages() - 1)
    dev.erase_page()

def _write_metadata(dev: SkyboundDevice, metadata: str) -> None:
    dev.before_write()
    page = metadata.encode().ljust(SkyboundDevice.PAGE_SIZE, b'\xFF')

    dev.select_page(dev.get_total_pages() - 1)

    # Data card can only write by changing 1s to 0s (effectively doing a bit-wise AND with
    # the existing contents), so all data needs to be "erased" first to reset everything to 1s.
    dev.erase_page()

    for i in range(SkyboundDevice.BLOCKS_PER_PAGE):
        _loop_helper(dev, i)

        block = page[i*SkyboundDevice.BLOCK_SIZE:(i+1)*SkyboundDevice.BLOCK_SIZE]

        dev.write_block(block)

@with_data_card
def cmd_write_metadata(dev: SkyboundDevice, metadata: str) -> None:
    _write_metadata(dev, metadata)
    print("Done")

@with_data_card
def cmd_read_database(dev: SkyboundDevice, path: str) -> None:
    with open(path, 'w+b') as fd:
        with tqdm.tqdm(desc="Reading the database", total=dev.get_total_size(), unit='B', unit_scale=True) as t:
            dev.before_read()
            for i in range(dev.get_total_pages() * SkyboundDevice.BLOCKS_PER_PAGE):
                _loop_helper(dev, i)

                if i % SkyboundDevice.BLOCKS_PER_PAGE == 0:
                    dev.select_page(i // SkyboundDevice.BLOCKS_PER_PAGE)

                block = dev.read_block()

                if block == b'\xFF' * SkyboundDevice.BLOCK_SIZE:
                    break

                fd.write(block)
                t.update(len(block))

        # Garmin card has no concept of size of the data,
        # so we need to remove the trailing "\xFF"s.
        print("Truncating the file...")
        fd.seek(0, os.SEEK_END)
        pos = fd.tell()
        while pos > 0:
            pos -= SkyboundDevice.BLOCK_SIZE
            fd.seek(pos)
            block = fd.read(SkyboundDevice.BLOCK_SIZE)
            if block != b'\xFF' * SkyboundDevice.BLOCK_SIZE:
                break
        fd.truncate()

    print("Done")

def _write_database(dev: SkyboundDevice, path: str) -> None:
    max_size = dev.get_total_size()

    with open(path, 'rb') as fd:
        size = os.fstat(fd.fileno()).st_size

        if size > max_size:
            raise SkyboundException(f"Database file is too big: {size}! The maximum size is {max_size}.")

        pages_required = min(size // SkyboundDevice.PAGE_SIZE + 3, dev.get_total_pages())
        total_size = pages_required * SkyboundDevice.PAGE_SIZE

        dev.before_write()

        # Data card can only write by changing 1s to 0s (effectively doing a bit-wise AND with
        # the existing contents), so all data needs to be "erased" first to reset everything to 1s.
        with tqdm.tqdm(desc="Erasing the database", total=total_size, unit='B', unit_scale=True) as t:
            for i in range(pages_required):
                _loop_helper(dev, i)
                dev.select_page(i)
                dev.erase_page()
                t.update(SkyboundDevice.PAGE_SIZE)

        with tqdm.tqdm(desc="Writing the database", total=total_size, unit='B', unit_scale=True) as t:
            for i in range(pages_required * SkyboundDevice.BLOCKS_PER_PAGE):
                block = fd.read(SkyboundDevice.BLOCK_SIZE).ljust(SkyboundDevice.BLOCK_SIZE, b'\xFF')

                _loop_helper(dev, i)

                if i % SkyboundDevice.BLOCKS_PER_PAGE == 0:
                    dev.select_page(i // SkyboundDevice.BLOCKS_PER_PAGE)

                dev.write_block(block)
                t.update(len(block))

        fd.seek(0)

        with tqdm.tqdm(desc="Verifying the database", total=total_size, unit='B', unit_scale=True) as t:
            dev.before_read()
            for i in range(pages_required * SkyboundDevice.BLOCKS_PER_PAGE):
                file_block = fd.read(SkyboundDevice.BLOCK_SIZE).ljust(SkyboundDevice.BLOCK_SIZE, b'\xFF')

                _loop_helper(dev, i)

                if i % SkyboundDevice.BLOCKS_PER_PAGE == 0:
                    dev.select_page(i // SkyboundDevice.BLOCKS_PER_PAGE)

                card_block = dev.read_block()

                if card_block != file_block:
                    raise SkyboundException(f"Verification failed! Block {i} is incorrect.")

                t.update(len(file_block))

@with_data_card
def cmd_write_database(dev: SkyboundDevice, path: str) -> None:
    prompt = input(f"Transfer {path} to the data card? (y/n) ")
    if prompt.lower() != 'y':
        raise DownloaderException("Cancelled")

    try:
        _write_database(dev, path)
    except IOError as ex:
        raise SkyboundException(f"Could not read the database file: {ex}")

    print("Done")

def main():
    parser = argparse.ArgumentParser(description="Program a Garmin data card")

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
        "id",
        help="ID of the download",
        type=int,
    )
    download_p.set_defaults(func=cmd_download)

    transfer_p = subparsers.add_parser(
        "transfer",
        help="Transfer the downloaded database to an SD card or a Garmin data card",
    )
    transfer_p.add_argument(
        "--vol-id",
        help="FAT32 Volume ID (e.g., 1234-ABCD)",
        type=str,
    )
    transfer_p.add_argument(
        "id",
        help="ID of the download",
        type=int,
    )
    transfer_p.add_argument(
        "device",
        help="SD card directory (for Avidyne/G1000 databases)",
        type=str,
        nargs='?',
    )
    transfer_p.set_defaults(func=cmd_transfer)

    detect_p = subparsers.add_parser(
        "detect",
        help="Detect a card programmer device",
    )
    detect_p.set_defaults(func=cmd_detect)

    read_metadata_p = subparsers.add_parser(
        "read-metadata",
        help="Read the database metadata",
    )
    read_metadata_p.set_defaults(func=cmd_read_metadata)

    write_metadata_p = subparsers.add_parser(
        "write-metadata",
        help="Write the database metadata",
    )
    write_metadata_p.add_argument(
        "metadata",
        help="Metadata string, e.g. {2303~12345678}",
    )
    write_metadata_p.set_defaults(func=cmd_write_metadata)

    read_database_p = subparsers.add_parser(
        "read-database",
        help="Read the database from the card and write to the file",
    )
    read_database_p.add_argument(
        "path",
        help="File to write the database to",
    )
    read_database_p.set_defaults(func=cmd_read_database)

    write_database_p = subparsers.add_parser(
        "write-database",
        help="Write the database to the card",
    )
    write_database_p.add_argument(
        "path",
        help="Database file, e.g. dgrw72_2302_742ae60e.bin",
    )
    write_database_p.set_defaults(func=cmd_write_database)

    args = parser.parse_args()

    kwargs = vars(args)
    func = kwargs.pop('func')

    try:
        func(**kwargs)
    except DownloaderException as ex:
        print(ex)
        return 1
    except ServiceException as ex:
        print(ex)
        return 1
    except SkyboundException as ex:
        print(ex)
        return 1

    return 0
