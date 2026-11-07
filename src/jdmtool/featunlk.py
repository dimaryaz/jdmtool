from __future__ import annotations

import argparse
import os
import pathlib
import re
import struct
import zipfile
import datetime
import sys

from collections.abc import Callable
from enum import Enum
from io import BytesIO
from typing import Any, IO
from pathlib import Path

from .checksum import feat_unlk_checksum
from .common import JdmToolException
from .taw import TAW_DATABASE_TYPES

FEAT_UNLK = 'feat_unlk.dat'


def decode_volume_id(encoded_vol_id: int) -> int:
    return ~((encoded_vol_id << 1 & 0xFFFFFFFF) | (encoded_vol_id >> 31)) & 0xFFFFFFFF


def encode_volume_id(vol_id: int) -> int:
    return ~((vol_id << 31 & 0xFFFFFFFF) | (vol_id >> 1)) & 0xFFFFFFFF


def truncate_system_id(system_id: int) -> int:
    return (system_id & 0xFFFFFFFF) + (system_id >> 32)

def represent_bytes(bs: bytes) -> str:
    return ''.join(chr(b) if 32 <= b < 128 else f' 0x{b:02X} ' for b in bs.rstrip(b'\x00'))

CONTENT1_LEN = 0x55   # 85
CONTENT2_LEN = 0x338  # 824

SEC_ID_OFFSET = 191

MAGIC1 = 0x1
MAGIC2 = 0x7648329A  # Hard-coded in GrmNavdata.dll
MAGIC3 = 0x6501

NAVIGATION_PREVIEW_START = 129
NAVIGATION_PREVIEW_END = 146

CHUNK_SIZE = 0x8000

# DATABASE / FILE FORMAT SIGNATURES
GARMIN_AVDB_FOOTER_MAGIC = 0xA5DBACE1
UNKNOWN_DB_SIGNATURE_63614030 = 0x63614030
TERRAIN_TRN_SIGNATURE_1 = 0x5EF3B4C7
TERRAIN_9AS_SIGNATURE = 0x03E876E9
TERRAIN_TRN_SIGNATURE_2 = 0xC3276E1F
FBO_GPI_SIGNATURE = 0x726F7040


class UserException(JdmToolException):
    pass


class Feature(Enum):
    NAVIGATION = 0, 0, ['ldr_sys/avtn_db.bin', 'avtn_db.bin', '.System/AVTN/avtn_db.bin']
    CONFIG_ENABLE = 913, 2, []
    TERRAIN = 1826, 3, ['terrain_9as.tdb', 'trn.dat', '.System/AVTN/terrain.tdb', 'terrain.tdb']
    OBSTACLE = 2739, 4, ['terrain.odb', '.System/AVTN/obstacle.odb', 'obstacle.odb']
    APT_TERRAIN = 3652, 5, ['terrain.adb']
    CHARTVIEW = 4565, 6, ['Charts/crcfiles.txt', 'crcfiles.txt']
    SAFETAXI = 5478, 7, ['safetaxi.bin', '.System/AVTN/safetaxi.img', 'safetaxi.img']
    FLITE_CHARTS = 6391, 8, ['fc_tpc/fc_tpc.dat', 'fc_tpc.dat', '.System/AVTN/FliteCharts/fc_tpc.dat']
    BASEMAP = 7304, 10, ['bmap.bin']
    AIRPORT_DIR = 8217, 10, ['apt_dir.gca', 'fbo.gpi']
    AIR_SPORT = 9130, 10, ['air_sport.gpi', 'Poi/air_sport.gpi']
    NAVIGATION_2 = 10043, 10, []
    SECTIONALS = 10956, 10, ['rasters/rasters.xml', 'rasters.xml']  # IFR_VFR_CHARTS
    OBSTACLE2 = 11869, 10, ['standard.odb']
    NAV_DB2 = 12782, 10, ['ldr_sys/nav_db2.bin', 'nav_db2.bin']
    NAV_DB2_STBY = 13695, 10, []
    SYSTEM_COPY = 14608, 11, []
    CONFIG_ENABLE_NO_SERNO = 15521, 2, []
    SAFETAXI2 = 16434, 10, ['safetaxi2.gca']
    BASEMAP2 = 17347, 10, ['bmap2.bin']

    # Unknown Features and Offsets
    # LVL_4_CONFIG = 0, 1, []
    # INSTALLER_UNLOCK = 0, 9, []

    def __init__(self, offset: int, bit: int, filenames: list[str]):
        self.offset = offset
        self.bit = bit
        self.filenames = filenames


FILENAME_TO_FEATURE: dict[str, Feature] = {
    filename: feature
    for feature in Feature
    for filename in feature.filenames
}


def calculate_crc_and_preview_of_file(feature: Feature, filename: pathlib.Path) -> tuple[int, bytes]:
    chk = 0xFFFFFFFF

    with open(filename, 'rb') as fd:
        block = fd.read(CHUNK_SIZE)

        preview = block[NAVIGATION_PREVIEW_START:NAVIGATION_PREVIEW_END]
        while True:
            chk = feat_unlk_checksum(block, chk)
            next_block = fd.read(CHUNK_SIZE)
            if not next_block:
                break
            block = next_block

        if feature != Feature.CHARTVIEW and feature != Feature.CONFIG_ENABLE_NO_SERNO:
            if chk != 0:
                raise ValueError(f"{filename} failed the checksum")
            chk = int.from_bytes(block[-4:], 'little')

    return chk, preview


def copy_with_feat_unlk(
        dest_dir: pathlib.Path, src: IO[bytes], filename: str,
        vol_id: int, security_id: int, system_id: int,
        progress_cb: Callable[[int], Any]
) -> None:
    feature = FILENAME_TO_FEATURE.get(filename)
    if feature is None:
        raise ValueError(f"Unsupported filename: {filename}")

    preview = None
    dest_path = dest_dir / filename
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(dest_dir / filename, 'wb') as dest:
        last_block = block = src.read(CHUNK_SIZE)

        if feature == Feature.NAVIGATION:
            preview = block[NAVIGATION_PREVIEW_START:NAVIGATION_PREVIEW_END]

        chk = 0xFFFFFFFF
        while block:
            last_block = block
            dest.write(block)
            chk = feat_unlk_checksum(block, chk)
            progress_cb(len(block))

            block = src.read(CHUNK_SIZE)

    if chk != 0:
        raise ValueError(f"{filename} failed the checksum: 0x{chk}")

    checksum = int.from_bytes(last_block[-4:], 'little')

    update_feat_unlk(dest_dir, feature, vol_id, security_id, system_id, checksum, preview)


def update_feat_unlk(
        dest_dir: pathlib.Path, feature: Feature, vol_id: int, security_id: int,
        system_id: int, checksum: int, preview: bytes | None
) -> None:
    content1 = BytesIO()

    content1.write(MAGIC1.to_bytes(2, 'little'))
    content1.write(((security_id - SEC_ID_OFFSET + 0x10000) & 0XFFFF).to_bytes(2, 'little'))
    content1.write(MAGIC2.to_bytes(4, 'little'))
    content1.write((1 << feature.bit).to_bytes(4, 'little'))
    content1.write((0).to_bytes(4, 'little'))
    content1.write(encode_volume_id(vol_id).to_bytes(4, 'little'))

    if feature == Feature.NAVIGATION:
        content1.write(MAGIC3.to_bytes(2, 'little'))

    content1.write(checksum.to_bytes(4, 'little'))

    preview_len = NAVIGATION_PREVIEW_END - NAVIGATION_PREVIEW_START
    if feature == Feature.NAVIGATION:
        assert preview is not None and len(preview) == preview_len, preview
        content1.write(preview)
    else:
        content1.write(b'\x00' * preview_len)

    content1.write(b'\x00' * (CONTENT1_LEN - len(content1.getbuffer()) - 4))

    chk1 = feat_unlk_checksum(bytes(content1.getbuffer()))
    content1.write(chk1.to_bytes(4, 'little'))
    assert len(content1.getbuffer()) == CONTENT1_LEN, len(content1.getbuffer())

    content2 = BytesIO()
    
    content2.write((0).to_bytes(4, 'little'))

    content2.write(truncate_system_id(system_id).to_bytes(4, 'little'))

    content2.write(b'\x00' * (CONTENT2_LEN - len(content2.getbuffer()) - 4))

    chk2 = feat_unlk_checksum(bytes(content2.getbuffer()))
    content2.write(chk2.to_bytes(4, 'little'))
    assert len(content2.getbuffer()) == CONTENT2_LEN, len(content2.getbuffer())

    chk3 = feat_unlk_checksum(content1.getvalue() + content2.getvalue())

    feat_unlk = dest_dir / FEAT_UNLK

    # JDM makes the file read-only, so make it writable again (if it exists)
    try:
        if hasattr(os, 'chflags'):
            # On MacOS, also clear the "IMMUTABLE" flag *before* the chmod.
            os.chflags(feat_unlk, 0) # type: ignore
        feat_unlk.chmod(0o644)
    except OSError:
        pass

    # Open the file in read+write mode - but make sure it exists first.
    # Why is there no mode that accomplishes both of these in one call?
    with open(feat_unlk, 'ab'):
        pass
    with open(feat_unlk, 'r+b') as out:
        out.seek(feature.offset)
        out.write(content1.getbuffer())
        out.write(content2.getbuffer())
        out.write(chk3.to_bytes(4, 'little'))

    # Make it read-only just to be consistent with JDM.
    # (Don't bother with the "IMMUTABLE" flag on MacOS - not worth the potential problems with it.)
    try:
        feat_unlk.chmod(0o444)
    except OSError:
        pass


def display_all_content_of_feat_unlk(featunlk: pathlib.Path, show_missing=False) -> None:
    for feature in Feature:
        display_content_of_feat_unlk(featunlk, feature, show_missing)


def display_content_of_feat_unlk(featunlk: pathlib.Path, feature: Feature, show_missing=False) -> None:
    """ Feature_Fields
0x000 |------|------|------|------|------|------|------|------|------|------|------|------|------|------|------|------|
      | 0x01 | 0x00 |   SEC_ID    | 0x9A | 0x32 | 0x48 | 0x76 |       FEATURE BIT 1       |       FEATURE BIT 2       |
      |------|------|------|------|------|------|------|------|------|------|------|------|------|------|------|------|

NAV DB:
0x010 |------|------|------|------|------|------|------|------|------|------|------|------|------|------|------|------|
      |         VOLUME ID         | PREV |  REV |          FILE CRC         |                  DATE                   |
0x020 |------|------|------|------|------|------|------|------|------|------|------|------|------|------|------|------|
      |                                       DATE                                 |          RESERVED                |
      |------|------|------|------|------|------|------|------|------|------|------|------|------|------|------|------|

OTHER DB:
0x010 |------|------|------|------|------|------|------|------|------|------|------|------|------|------|------|------|
      |         VOLUME ID         |          FILE CRC         |                   RESERVED                            |
0x020 |------|------|------|------|------|------|------|------|------|------|------|------|------|------|------|------|
      |                                                    RESERVED                                                   |
      |------|------|------|------|------|------|------|------|------|------|------|------|------|------|------|------|


0x030 |------|------|------|------|------|------|------|------|------|------|------|------|------|------|------|------|
      | RES  |        CARD SERIAL        |                              RESERVED                                      |
0x040 |------|------|------|------|------|------|------|------|------|------|------|------|------|------|------|------|
      |                                                    RESERVED                                                   |
0x050 |------|------|------|------|------|------|------|------|------|------|------|------|------|------|------|------|
      |      |        CRC BLOCK 1        |  UNIT COUNT |   RESERVED  |         SYSTEM ID 1       |     SYSTEM ID 2    |
0x060 |------|------|------|------|------|------|------|------|------|------|------|------|------|------|------|------|
      |  ..  |         SYSTEM ID 3       |        SYSTEM ID 4        |         SYSTEM ID 5       |     SYSTEM ID 6    |
      |------|------|------|------|------|------|------|------|------|------|------|------|------|------|------|------|
        ...
0x370 |------|------|------|------|------|------|------|------|------|------|------|------|------|------|------|------|
      |  ..  |         SYSTEM ID 199     |        SYSTEM ID 200      |          RESERVED         |      RESERVED      |
0x380 |------|------|------|------|------|------|------|------|------|------|------|------|------|------|------|------|
      |  ..  |         RESERVED          |        RESERVED           |          CRC BLOCK 2      |      FULL CRC      |
0x390 |------|------|------|------|------|------|------|------|------|------|------|------|------|------|------|------|
      |  ..  |
      |------|
    """
    print(f"\n---- {feature.name} ----")

    with open(featunlk, 'rb') as fd:
        fd.seek(feature.offset)

        content1_bytes = fd.read(CONTENT1_LEN)
        if all(b == 0 for b in content1_bytes):
            print("* No content")
            return
        chk1 = feat_unlk_checksum(content1_bytes)
        if chk1 != 0:
            raise ValueError("Content1 failed the checksum")

        content2_bytes = fd.read(CONTENT2_LEN)
        chk2 = feat_unlk_checksum(content2_bytes)
        if chk2 != 0:
            raise ValueError("Content2 failed the checksum")

        overall_chk = fd.read(4)
        chk3 = feat_unlk_checksum(content2_bytes + overall_chk, 0)
        if chk3 != 0:
            raise ValueError(f"Content failed the checksum: {chk3:08x}")

    content1 = BytesIO(content1_bytes[:-4])

    magic = int.from_bytes(content1.read(2), 'little')
    if magic != MAGIC1:
        raise ValueError(f"Unexpected magic number: 0x{magic:04X}")

    security_id = (int.from_bytes(content1.read(2), 'little') + SEC_ID_OFFSET) & 0xFFFF
    device_model_val = TAW_DATABASE_TYPES.get(security_id, "Unknown")
    print(f"* garmin_sec_id: {security_id}, device_model: ({device_model_val})")

    magic = int.from_bytes(content1.read(4), 'little')
    if magic != MAGIC2:
        raise ValueError(f"Unexpected magic number: 0x{magic:08X}")

    file_feature_bit = int.from_bytes(content1.read(4), 'little')
    if file_feature_bit != 1 << feature.bit:
        raise ValueError(f"Incorrect bit: file: {file_feature_bit:04x}, expected: {1 << feature.bit:04x}")

    if not all(b == 0 for b in content1.read(4)):
        raise ValueError("Expected zeros")

    vol_id = decode_volume_id(int.from_bytes(content1.read(4), 'little'))
    print(f"* Volume ID: {vol_id:08X}")

    if feature == Feature.NAVIGATION:
        magic = int.from_bytes(content1.read(2), 'little')
        if magic != MAGIC3:
            raise ValueError(f"Unexpected magic number: 0x{magic:04X}")

    expected_chk = int.from_bytes(content1.read(4), 'little')
    expected_preview = content1.read(17)

    if feature != Feature.NAVIGATION and not all(b == 0 for b in expected_preview):
        if show_missing:
            print("- Expected zeros in preview content")

    if feature == Feature.CONFIG_ENABLE_NO_SERNO and feature.filenames == []:
        vpath = featunlk.parent.joinpath("./cfg/")
        if vpath.is_dir():
            for file in os.listdir(vpath):
                file1 = vpath / file
                if show_missing:
                    print(f'* File option : "{file1}"')
                feature.filenames.append(featunlk.parent.joinpath(file1))

    for filename in feature.filenames:
        dat_file = featunlk.parent.joinpath(filename)
        if dat_file.is_file():
            crc, preview = calculate_crc_and_preview_of_file(feature, dat_file)

            # wrong file
            if crc != expected_chk:
                print(
                    f'- {dat_file} exists, but has wrong CRC, '
                    f'CRC (0x{crc:08X}), Expected Chk on featunlk : '
                    f'{hex(expected_chk)}'
                )
                continue

            print(f'* {dat_file} has correct CRC (0x{crc:08X})')

            if feature == Feature.NAVIGATION and expected_preview != preview:
                raise ValueError("Preview data mismatch")

            display_content_of_dat_file(feature, dat_file)
            break
    else:
        print('- Unknown Filename or CRC not found in files')
        print('* Expected Chk: ', hex(expected_chk))

    # OFFSET 0x2B
    byte = content1.tell()
    if not all(b == 0 for b in content1.read(8)):
        if show_missing:
            print("- Expected zeros in the content but got: ", [hex(x) for x in content1_bytes[byte:byte + 8]])
        else:
            print("- Expected zeros in the content")

    # OFFSET 0x33
    card_id = int.from_bytes(content1.read(4), 'little')
    if card_id != 0:
        print(f'* Card ID: 0x{card_id:08x}')

    byte = content1.tell()
    if not all(b == 0 for b in content1.read()):
        if show_missing:
            print("- Expected zeros in the content but got: ", [hex(x) for x in content1_bytes[byte:-4]])
        else:
            print("- Expected zeros in the content")

    # start CONTENT2
    content2 = BytesIO(content2_bytes[:-4])
    unit_count = int.from_bytes(content2.read(2), 'little')
    content2.read(2)
    system_id = int.from_bytes(content2.read(4), 'little')

    byte = content2.tell()
    content2.seek(byte - 4)
    b = 1
    c = 0
    while b != 0:
        b = int.from_bytes(content2.read(4), 'little')
        if b != 0:
            if c == 0:
                print("* Allocated to truncated avionics_id:", end='')
            print(f" 0x{b:08X}({b})", end='')
            c += 1
        elif c != 0:
            print()

    if unit_count != 0:
        print(f'* Still allowed onto {unit_count}/{unit_count + c} systems')
    else:
        possible_system_ids = [system_id - i | i << 32 for i in range(1, 4)]
        print(
            f"  (Possible values1: "
            f"{', '.join(f'{v:X}' for v in possible_system_ids)}, ...)"
        )

# ---------------------------------------------------------------------------
# Terminal/date display helpers
# ---------------------------------------------------------------------------

_ANSI_GREEN = '\033[92m'
_ANSI_RESET = '\033[0m'
_DATE_FORMAT = '%d-%b-%Y'
_DATETIME_FORMAT = '%d-%b-%Y %H:%M:%S'


def _format_date(value: datetime.date) -> str:
    return value.strftime(_DATE_FORMAT).upper()


def _format_datetime(value: datetime.datetime) -> str:
    return value.strftime(_DATETIME_FORMAT).upper()


def _print_effective(text: str) -> None:
    print(f'{_ANSI_GREEN}{text}{_ANSI_RESET}')


def _print_date_window(
    effective: datetime.date,
    expires: datetime.date,
    *,
    label: str = 'Effective',
) -> None:
    _print_effective(
        f'** {label} {_format_date(effective)} to {_format_date(expires)}'
    )


def _print_date(
    label: str,
    value: datetime.date,
    *,
    green: bool = False,
) -> None:
    text = f'** {label}: {_format_date(value)}'
    if green:
        _print_effective(text)
    else:
        print(text)


def _print_datetime(
    label: str,
    value: datetime.datetime,
    *,
    green: bool = True,
) -> None:
    text = f'** {label}: {_format_datetime(value)}'
    if green:
        _print_effective(text)
    else:
        print(text)


def _print_generated(
    value: datetime.datetime,
) -> None:
    _print_datetime('Generated', value)


def _print_creation_date(
    value: datetime.datetime,
) -> None:
    _print_datetime('Creation Date', value)


def _print_update_month_year(month: int, year: int) -> None:
    print(f'** Update: {month:02d}/{year:04d}')


def _date_plus_days(
    value: datetime.date,
    days: int,
) -> datetime.date:
    return value + datetime.timedelta(days=days)


def _print_disables(value: datetime.date) -> None:
    _print_date('Disables', value, green=True)


def _print_date_source(source: str) -> None:
    print(f'** Date source: {source}')


def _print_date_block(
    *,
    effective: datetime.date | None = None,
    expires: datetime.date | None = None,
    generated: datetime.datetime | None = None,
    creation: datetime.datetime | None = None,
    release: datetime.date | None = None,
    disables: datetime.date | None = None,
    source: str,
) -> None:
    """Print one logical date block followed by exactly one source line."""
    printed = False

    if effective is not None and expires is not None:
        _print_date_window(effective, expires)
        printed = True
    elif effective is not None:
        _print_date('Effective', effective, green=True)
        printed = True

    if generated is not None:
        _print_generated(generated)
        printed = True

    if creation is not None:
        _print_creation_date(creation)
        printed = True

    if release is not None:
        _print_date('Release Date', release, green=True)
        printed = True

    if disables is not None:
        _print_date('Disables', disables, green=True)
        printed = True

    if printed:
        _print_date_source(source)


def _print_derived_date_window(
    effective: datetime.date,
    expires: datetime.date,
    *,
    source: str,
) -> None:
    _print_date_window(effective, expires)
    _print_date_source(f'derived from {source}')


def _print_cycle_derived_date_block(
    *,
    cycle: str,
    decoder: Callable[
        [str],
        tuple[datetime.date, datetime.date] | None,
    ],
    source_label: str,
    generated: datetime.datetime | None = None,
    generated_source: str | None = None,
) -> tuple[datetime.date, datetime.date] | None:
    date_window = decoder(cycle)

    source_parts: list[str] = []
    effective = expires = None

    if date_window is not None:
        effective, expires = date_window
        source_parts.append(f'validity derived from {source_label}')

    if generated is not None and generated_source:
        source_parts.append(generated_source)

    if effective is not None or generated is not None:
        _print_date_block(
            effective=effective,
            expires=expires,
            generated=generated,
            source='; '.join(source_parts),
        )

    return date_window


def _print_release_based_date_block(
    *,
    creation: datetime.datetime | None = None,
    release: datetime.date | None = None,
    source_prefix: str | None = None,
) -> None:
    """Print one date block for Garmin as-needed/non-expiring databases."""
    source_parts: list[str] = []

    if creation is not None:
        _print_creation_date(creation)
        source_parts.append('Creation Date embedded')

    if release is not None:
        _print_date('Release Date', release, green=True)
        source_parts.append('Release Date embedded')

    _print_effective('** Effective: UPON RELEASE')
    print('** Expires: N/A')

    if source_prefix:
        source_parts.insert(0, source_prefix)
    source_parts.append('validity release-based')

    _print_date_source('; '.join(source_parts))


def _print_as_needed_database_dates() -> None:
    """
    Garmin Basemap/Terrain family:
    publication as needed, effective upon release, no expiration.
    """
    _print_release_based_date_block()


# ---------------------------------------------------------------------------
# Database date decoding helpers
# ---------------------------------------------------------------------------

_GARMIN_56DAY_ANCHOR = datetime.date(2007, 1, 18)
_GARMIN_56DAY_STEP = datetime.timedelta(days=56)

_AIRAC_2501_DATE = datetime.date(2025, 1, 23)
_AIRAC_STEP = datetime.timedelta(days=28)


def _year_from_yy(yy: int) -> int:
    return 2000 + yy if yy <= 79 else 1900 + yy


def _require_size(
    data: bytes,
    offset: int,
    size: int,
    *,
    label: str,
) -> None:
    if offset < 0 or size < 0 or offset + size > len(data):
        raise ValueError(
            f'{label}: need {size} byte(s) at 0x{offset:X}, '
            f'buffer size is 0x{len(data):X}'
        )


def _read_u8(
    data: bytes,
    offset: int,
    *,
    label: str = 'u8',
) -> int:
    _require_size(data, offset, 1, label=label)
    return data[offset]


def _read_u16le(
    data: bytes,
    offset: int,
    *,
    label: str = 'u16le',
) -> int:
    _require_size(data, offset, 2, label=label)
    return struct.unpack_from('<H', data, offset)[0]


def _read_ascii(
    data: bytes,
    offset: int,
    size: int,
    *,
    label: str = 'ASCII',
) -> str:
    _require_size(data, offset, size, label=label)
    return represent_bytes(data[offset:offset + size]).strip()


def _make_date(year: int, month: int, day: int) -> datetime.date:
    try:
        return datetime.date(year, month, day)
    except ValueError as exc:
        raise ValueError(
            f'Invalid date: year={year}, month={month}, day={day}'
        ) from exc


def _make_datetime(
    year: int,
    month: int,
    day: int,
    hour: int = 0,
    minute: int = 0,
    second: int = 0,
) -> datetime.datetime:
    try:
        return datetime.datetime(
            year, month, day, hour, minute, second
        )
    except ValueError as exc:
        raise ValueError(
            'Invalid datetime: '
            f'year={year}, month={month}, day={day}, '
            f'hour={hour}, minute={minute}, second={second}'
        ) from exc


def _decode_56day_cycle(
    cycle: str,
) -> tuple[datetime.date, datetime.date] | None:
    cycle = cycle.strip().upper().replace('\x00', '')
    match = re.fullmatch(r'(\d{2})([SDB])(\d{1,2})', cycle)
    if not match:
        return None

    year = _year_from_yy(int(match.group(1)))
    issue = int(match.group(3))

    d = _GARMIN_56DAY_ANCHOR
    while d.year < year:
        d += _GARMIN_56DAY_STEP
    while (d - _GARMIN_56DAY_STEP).year >= year:
        d -= _GARMIN_56DAY_STEP
    while d.year < year:
        d += _GARMIN_56DAY_STEP

    dates = []
    while d.year == year:
        dates.append(d)
        d += _GARMIN_56DAY_STEP

    if not 1 <= issue <= len(dates):
        return None

    effective = dates[issue - 1]
    return effective, effective + _GARMIN_56DAY_STEP


def _first_airac_date(year: int) -> datetime.date:
    d = _AIRAC_2501_DATE

    if year >= 2025:
        while d.year < year:
            d += _AIRAC_STEP
        while (d - _AIRAC_STEP).year == year:
            d -= _AIRAC_STEP
    else:
        while d.year > year:
            d -= _AIRAC_STEP
        while (d - _AIRAC_STEP).year == year:
            d -= _AIRAC_STEP

    return d


def _decode_airac_cycle(
    cycle: str,
) -> tuple[datetime.date, datetime.date] | None:
    cycle = cycle.strip()
    match = re.fullmatch(r'(\d{2})(\d{2})', cycle)
    if not match:
        return None

    year = _year_from_yy(int(match.group(1)))
    issue = int(match.group(2))

    if not 1 <= issue <= 14:
        return None

    effective = _first_airac_date(year) + (issue - 1) * _AIRAC_STEP
    if effective.year != year:
        return None

    return effective, effective + _AIRAC_STEP


def _decode_bbh_date(data: bytes, offset: int) -> datetime.date:
    _require_size(data, offset, 4, label='BBH date')
    month, day, year = struct.unpack_from('<BBH', data, offset)
    return _make_date(year, month, day)


def _decode_hhh_date(
    data: bytes,
    offset: int,
    *,
    order: str = 'dmy',
) -> datetime.date:
    _require_size(data, offset, 6, label='HHH date')
    a, b, c = struct.unpack_from('<HHH', data, offset)

    if order == 'dmy':
        day, month, year = a, b, c
    elif order == 'mdy':
        month, day, year = a, b, c
    else:
        raise ValueError(f'Unsupported HHH date order: {order}')

    return _make_date(year, month, day)


def _decode_yyy_b_b_date(data: bytes, offset: int) -> datetime.date:
    _require_size(data, offset, 4, label='YMD date')
    year = struct.unpack_from('<H', data, offset)[0]
    month, day = struct.unpack_from('<BB', data, offset + 2)
    return _make_date(year, month, day)


def _decode_date_window(
    data: bytes,
    effective_offset: int,
    expires_offset: int,
    decoder: Callable[[bytes, int], datetime.date] = _decode_bbh_date,
) -> tuple[datetime.date, datetime.date]:
    return (
        decoder(data, effective_offset),
        decoder(data, expires_offset),
    )


def _decode_terrain_common_metadata(
    header_bytes: bytes,
    footer_bytes: bytes,
    footer2_bytes: bytes,
) -> dict[str, str]:
    result: dict[str, str] = {}

    if len(header_bytes) >= 0x90:
        result['name'] = _read_ascii(
            header_bytes, 0x58, 0x1E, label='Terrain name'
        )
        result['part_number'] = _read_ascii(
            header_bytes, 0x78, 0x0C, label='Terrain part number'
        )
        result['version'] = _read_ascii(
            header_bytes, 0x86, 0x04, label='Terrain version'
        )
        result['database_version'] = _read_ascii(
            header_bytes, 0x8C, 0x04, label='Terrain database version'
        )

    if len(footer2_bytes) >= 5:
        result['cycle_footer2'] = _read_ascii(
            footer2_bytes, 0x01, 4, label='Terrain cycle footer2'
        )

    if len(footer_bytes) >= 8:
        result['cycle_footer'] = _read_ascii(
            footer_bytes, 0x04, 4, label='Terrain cycle footer'
        )

    if len(footer_bytes) >= 0xC0:
        result['type_footer'] = _read_ascii(
            footer_bytes, 0x98, 0x28, label='Terrain type footer'
        )

    return result


def _decode_dskimg_metadata(
    feature: Feature,
    header_bytes: bytes,
    footer_bytes: bytes,
) -> dict[str, object]:
    _require_size(
        header_bytes,
        0,
        0x89,
        label='DSKIMG header',
    )

    if header_bytes[0x10:0x16] != b'DSKIMG':
        raise ValueError('No DSKIMG file')

    if header_bytes[0x41:0x47] != b'GARMIN':
        raise ValueError(
            f"DSKIMG vendor is not GARMIN: "
            f"{header_bytes[0x41:0x47]!r}"
        )

    map_major = _read_u8(
        header_bytes, 0x08, label='DSKIMG map major'
    )
    map_minor = _read_u8(
        header_bytes, 0x09, label='DSKIMG map minor'
    )
    update_month = _read_u8(
        header_bytes, 0x0A, label='DSKIMG update month'
    )
    update_year = 1900 + _read_u8(
        header_bytes, 0x0B, label='DSKIMG update year'
    )
    name = _read_ascii(
        header_bytes, 0x49, 0x12, label='DSKIMG name'
    )

    if feature == Feature.BASEMAP2:
        cycle = _read_ascii(
            footer_bytes, 0x04, 4, label='BASEMAP2 cycle'
        )
        description = _read_ascii(
            footer_bytes, 0x1F, 25, label='BASEMAP2 description'
        )
    elif feature == Feature.BASEMAP:
        cycle = (
            _read_ascii(
                header_bytes, 0x5B, 2, label='BASEMAP cycle prefix'
            )
            + _read_ascii(
                header_bytes, 0x65, 2, label='BASEMAP cycle suffix'
            )
        ).strip()
        description = _read_ascii(
            footer_bytes, 0x1F, 25, label='BASEMAP description'
        )
    elif feature == Feature.SAFETAXI:
        cycle = _read_ascii(
            header_bytes, 0x59, 4, label='SAFETAXI cycle'
        )
        description = _read_ascii(
            header_bytes, 0x65, 30, label='SAFETAXI description'
        )
    else:
        raise ValueError(
            f'Unsupported DSKIMG feature: {feature.name}'
        )

    creation_dt = _decode_dskimg_creation_date(header_bytes)

    result: dict[str, object] = {
        'map_version': f'{map_major}.{map_minor}',
        'update_month': update_month,
        'update_year': update_year,
        'name': name,
        'cycle': cycle,
        'description': description,
        'created': creation_dt,
    }

    if _read_u16le(
        header_bytes, 0x83, label='DSKIMG software marker'
    ) == 0xDEAD:
        software_major = _read_u8(
            header_bytes, 0x85, label='DSKIMG software major'
        )
        software_minor = _read_u8(
            header_bytes, 0x86, label='DSKIMG software minor'
        )
        software_release = _read_u16le(
            header_bytes, 0x87, label='DSKIMG software release'
        )
        result['creation_software'] = (
            f'{software_major}.{software_minor}',
            software_release,
        )

    return result


def _decode_dskimg_creation_date(
    header_bytes: bytes,
) -> datetime.datetime:
    _require_size(
        header_bytes,
        0x39,
        7,
        label='DSKIMG creation datetime',
    )
    year = struct.unpack_from('<H', header_bytes, 0x39)[0]
    month, day, hour, minute, second = struct.unpack_from(
        '<BBBBB', header_bytes, 0x3B
    )
    return _make_datetime(year, month, day, hour, minute, second)


def _decode_mmddyyyy_date(data: bytes) -> datetime.date:
    if len(data) != 10:
        raise ValueError(
            f'MM/DD/YYYY date must be exactly 10 bytes, got {len(data)}'
        )
    try:
        parsed = datetime.datetime.strptime(
            data.decode('ascii'), '%m/%d/%Y'
        )
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError(
            f'Invalid MM/DD/YYYY date bytes: {data!r}'
        ) from exc
    return _make_date(parsed.year, parsed.month, parsed.day)


def _decode_airac17_block(
    data: bytes,
    offset: int,
) -> dict[str, object]:
    """Decode Garmin 17-byte AIRAC block: cycle, effective, expires, generated."""
    _require_size(data, offset, 17, label='AIRAC17 block')

    cycle = struct.unpack_from('<H', data, offset)[0]
    effective, expires = _decode_date_window(
        data, offset + 2, offset + 6
    )
    generated_date = _decode_bbh_date(data, offset + 10)
    hour, minute, second = struct.unpack_from('<BBB', data, offset + 14)

    return {
        'cycle': cycle,
        'effective': effective,
        'expires': expires,
        'generated': _make_datetime(
            generated_date.year,
            generated_date.month,
            generated_date.day,
            hour, minute, second,
        ),
    }


def _decode_navigation_dates(header_bytes: bytes) -> dict[str, object]:
    if len(header_bytes) < 0x93:
        raise ValueError('Navigation header is too short')

    result = _decode_airac17_block(header_bytes, 0x81)
    result['revision'] = chr(header_bytes[0x92])
    return result


def _decode_db_magic_footer(footer_bytes: bytes) -> dict[str, object]:
    _require_size(
        footer_bytes,
        0,
        0x1B,
        label='GARMIN_AVDB_FOOTER_MAGIC footer',
    )

    if int.from_bytes(
        footer_bytes[0:4], 'little'
    ) != GARMIN_AVDB_FOOTER_MAGIC:
        raise ValueError('Invalid GARMIN_AVDB_FOOTER_MAGIC footer')

    cycle = footer_bytes[4:8].decode(
        'ascii', errors='replace'
    ).rstrip('\x00 ')

    effective, expires = _decode_date_window(
        footer_bytes, 0x08, 0x0C
    )

    generated_date = _decode_bbh_date(footer_bytes, 0x14)
    g_hour, g_minute, g_second = struct.unpack_from(
        '<BBB', footer_bytes, 0x18
    )

    return {
        'cycle': cycle,
        'effective': effective,
        'expires': expires,
        'generated': _make_datetime(
            generated_date.year,
            generated_date.month,
            generated_date.day,
            g_hour,
            g_minute,
            g_second,
        ),
    }


def _decode_db_magic_metadata(
    footer_bytes: bytes,
) -> dict[str, object]:
    dates = _decode_db_magic_footer(footer_bytes)

    result = dict(dates)
    result['description_1'] = _read_ascii(
        footer_bytes, 0x20, 0x0B, label='DB footer description 1'
    )
    result['description_2'] = _read_ascii(
        footer_bytes, 0x2B, 0x14, label='DB footer description 2'
    )

    if len(footer_bytes) >= 0xAC:
        result['description_3'] = _read_ascii(
            footer_bytes, 0x98, 0x14, label='DB footer description 3'
        )
    else:
        result['description_3'] = ''

    return result


def _display_db_magic_footer_dates(
    footer_bytes: bytes,
    *,
    show_generated: bool = True,
    show_descriptions: bool = False,
) -> dict[str, object]:
    db_dates = _decode_db_magic_metadata(footer_bytes)
    print(f"** Cycle: {db_dates['cycle']}")
    _print_date_block(
        effective=db_dates['effective'],
        expires=db_dates['expires'],
        generated=(
            db_dates['generated']
            if show_generated
            else None
        ),
        source='embedded',
    )

    if show_descriptions:
        for key in (
            'description_1',
            'description_2',
            'description_3',
        ):
            value = db_dates.get(key)
            if value:
                print(f'** {value}')

    return db_dates


def _display_airac17_dates(
    data: bytes,
    offset: int,
    *,
    revision: str | None = None,
) -> dict[str, object]:
    result = _decode_airac17_block(data, offset)
    if revision is not None:
        print(f'** Revision: {revision}')
    print(f"** Cycle: {result['cycle']}")
    _print_date_block(
        effective=result['effective'],
        expires=result['expires'],
        generated=result['generated'],
        source='embedded',
    )
    return result


def _decode_grmrec_timestamp(
    header_bytes: bytes,
) -> datetime.datetime | None:
    pos = header_bytes.find(b'GRMREC01')
    if pos < 0 or pos + 12 > len(header_bytes):
        return None

    raw = int.from_bytes(header_bytes[pos + 8:pos + 12], 'little')
    stored = datetime.datetime(1970, 1, 1) + datetime.timedelta(seconds=raw)

    try:
        return stored.replace(year=stored.year + 20) - datetime.timedelta(days=1)
    except ValueError:
        return (
            stored.replace(year=stored.year + 20, day=28)
            - datetime.timedelta(days=1)
        )


def _find_airport_directory_cycle(data: bytes) -> str | None:
    for pattern in (
        rb'Directory Data\s+(\d{2}D\d{1,2})',
        rb'\b(\d{2}D\d{1,2})\b',
    ):
        match = re.search(pattern, data, re.IGNORECASE)
        if match:
            return match.group(1).decode('ascii').upper()

    return None


def _find_air_sport_cycle(data: bytes) -> str | None:
    for pattern in (
        rb'airsport-data-(\d{4})',
        rb'Air Sport Data\s+(\d{4})',
    ):
        match = re.search(pattern, data, re.IGNORECASE)
        if match:
            return match.group(1).decode('ascii')

    return None


def _julian_day_to_date(value: float) -> datetime.date:
    j = int(value + 0.5)
    l = j + 68569
    n = (4 * l) // 146097
    l = l - (146097 * n + 3) // 4
    i = (4000 * (l + 1)) // 1461001
    l = l - (1461 * i) // 4 + 31
    j2 = (80 * l) // 2447
    day = l - (2447 * j2) // 80
    l = j2 // 11
    month = j2 + 2 - 12 * l
    year = 100 * (n - 49) + i + l
    return _make_date(year, month, day)


def _parse_chartview_ini_dates(
    data: bytes,
) -> tuple[datetime.date, datetime.date] | None:
    text = data.decode('ascii', errors='ignore')

    begin = re.search(
        r'(?im)^\s*Database_Begin_Date\s*=\s*([0-9.]+)',
        text,
    )
    end = re.search(
        r'(?im)^\s*Database_End_Date\s*=\s*([0-9.]+)',
        text,
    )

    if not begin or not end:
        return None

    return (
        _julian_day_to_date(float(begin.group(1))),
        _julian_day_to_date(float(end.group(1))),
    )


def _display_metadata_error(
    feature: Feature,
    exc: Exception,
    *,
    label: str | None = None,
) -> None:
    suffix = f' [{label}]' if label else ''
    print(
        f'** Metadata decode warning [{feature.name}]{suffix}: {exc}'
    )


def _safe_metadata(
    feature: Feature,
    label: str,
    decoder: Callable[[], Any],
) -> Any | None:
    try:
        return decoder()
    except (
        ValueError,
        struct.error,
        UnicodeDecodeError,
        OverflowError,
    ) as exc:
        _display_metadata_error(feature, exc, label=label)
        return None


def display_content_of_dat_file(
    feature: Feature,
    dat_file: pathlib.Path,
):
    header_bytes = footer_bytes = footer2_bytes = b''

    if feature == Feature.SAFETAXI2 and zipfile.is_zipfile(dat_file):
        with zipfile.ZipFile(dat_file, 'r') as zip_fp:
            with zip_fp.open('safetaxi2.bin') as fd:
                header_bytes = fd.read(0x200)
                fd.seek(-0x102, os.SEEK_END)
                footer_bytes = fd.read(0x102)
    elif (
        feature != Feature.CHARTVIEW
        and feature != Feature.CONFIG_ENABLE_NO_SERNO
    ):
        with open(dat_file, 'rb') as fd:
            header_bytes = fd.read(0x200)
            fd.seek(-0x102, os.SEEK_END)
            footer_bytes = fd.read(0x102)
            fd.seek(-0x1F2, os.SEEK_END)
            footer2_bytes = fd.read(0x1F2)

    elif feature == Feature.CONFIG_ENABLE_NO_SERNO:
        with open(dat_file, 'rt') as fd:
            header_bytes = fd.read()

    if feature in (Feature.NAVIGATION, Feature.NAV_DB2):
        parts = [
            represent_bytes(x).strip()
            for x in header_bytes[0x9F:0xEF].split(b'\0')
        ]

        if len(parts) >= 3:
            region, year, man = parts[:3]
            print(f'** Region: {region}')
            print(f'** {year}')
            print(f'** {man}')

        nav_dates = _safe_metadata(
            feature,
            'AIRAC17',
            lambda: _decode_navigation_dates(header_bytes),
        )
        if nav_dates is not None:
            _safe_metadata(
                feature,
                'AIRAC17 display',
                lambda: _display_airac17_dates(
                    header_bytes,
                    0x81,
                    revision=nav_dates['revision'],
                ),
            )

    elif feature == Feature.OBSTACLE:
        footer_magic = int.from_bytes(
            footer_bytes[0:4], 'little'
        )

        # Ancien format Garmin : dates HHH explicites dans le header.
        if header_bytes[0x30:0x3A] == b'Garmin Ltd':
            print('** ' + represent_bytes(header_bytes[0x30:0x3A]))

            cycle = represent_bytes(
                footer2_bytes[0x01:0x05]
            ).strip()
            print(f'** Cycle: {cycle}')

            obstacle_window = _safe_metadata(
                feature,
                'embedded validity',
                lambda: (
                    _decode_hhh_date(
                        header_bytes, 0x10, order='dmy'
                    ),
                    _decode_hhh_date(
                        header_bytes, 0x92, order='dmy'
                    ),
                ),
            )

            if obstacle_window is not None:
                effective, expires = obstacle_window
                _print_date_block(
                    effective=effective,
                    expires=expires,
                    source='embedded',
                )

        # Format Garmin AVDB : cycle + dates explicites dans le footer.
        elif footer_magic == GARMIN_AVDB_FOOTER_MAGIC:
            print(
                f'** Footer magic = GARMIN_AVDB_FOOTER_MAGIC: '
                f'0x{footer_magic:08X}'
            )

            _safe_metadata(
                feature,
                'GARMIN_AVDB_FOOTER_MAGIC',
                lambda: _display_db_magic_footer_dates(
                    footer_bytes,
                    show_generated=True,
                    show_descriptions=True,
                ),
            )

        # Autres anciens formats : cycle Garmin de 56 jours.
        else:
            cycle = represent_bytes(
                footer_bytes[0x04:0x08]
            ).strip()

            print(f'** Cycle: {cycle}')

            date_window = _decode_56day_cycle(cycle)

            if date_window is not None:
                effective, expires = date_window
                _print_date_block(
                    effective=effective,
                    expires=expires,
                    source='derived from 56-day cycle',
                )
            else:
                print(
                    f"** Effective: unable to decode "
                    f"56-day cycle {cycle!r}"
                )

            description_1 = represent_bytes(
                footer_bytes[0x20:0x2B]
            ).strip()
            description_2 = represent_bytes(
                footer_bytes[0x2B:0x3F]
            ).strip()
            description_3 = represent_bytes(
                footer_bytes[0x98:0xAC]
            ).strip()

            for value in (
                description_1,
                description_2,
                description_3,
            ):
                if value:
                    print(f'** {value}')    
                elif feature in (Feature.TERRAIN, Feature.APT_TERRAIN):
                    file_signature = int.from_bytes(
                        header_bytes[0:4], 'little'
                    )

                    terrain_meta = _safe_metadata(
                        feature,
                        'terrain metadata',
                        lambda: _decode_terrain_common_metadata(
                            header_bytes,
                            footer_bytes,
                            footer2_bytes,
                        ),
                    ) or {}

                    release_date = None

                    if file_signature == TERRAIN_TRN_SIGNATURE_1:
                        print(
                            f"TERRAIN_TRN_SIGNATURE_1: "
                            f"0x{file_signature:08X}"
                        )
                        if terrain_meta.get('type_footer'):
                            print(f"** Type: {terrain_meta['type_footer']}")
                        if terrain_meta.get('cycle_footer'):
                            print(f"** Cycle: {terrain_meta['cycle_footer']}")
                        print(
                            '** '
                            + represent_bytes(
                                footer_bytes[0x49:0x85]
                            ).replace(" 0x00", "")
                        )

                    elif file_signature == TERRAIN_9AS_SIGNATURE:
                        print(
                            f"TERRAIN_9AS_SIGNATURE: "
                            f"0x{file_signature:08X}"
                        )

                        terrain_name = terrain_meta.get('name', '')
                        cycle = terrain_meta.get('cycle_footer2', '')
                        part_number = terrain_meta.get('part_number', '')
                        version = terrain_meta.get('version', '')
                        db_version = terrain_meta.get('database_version', '')

                        if terrain_name:
                            print(f'** Type: {terrain_name}')
                        if cycle:
                            print(f'** Cycle: {cycle}')
                        if part_number:
                            print(f'** Part Number: {part_number}')
                        if version:
                            print(f'** Version: {version}')
                        if db_version:
                            print(f'** Database Version: {db_version}')

                        # The 9AS format contains a release/update date at 0x10.
                        # It is retained as metadata, but Terrain has no expiry window.
                        release_date = _safe_metadata(
                            feature,
                            'release date',
                            lambda: _decode_hhh_date(
                                header_bytes, 0x10, order='mdy'
                            ),
                        )

                    elif file_signature == TERRAIN_TRN_SIGNATURE_2:
                        print(
                            f"TERRAIN_TRN_SIGNATURE_2: "
                            f"0x{file_signature:08X}"
                        )
                        if terrain_meta.get('type_footer'):
                            print(f"** Type: {terrain_meta['type_footer']}")
                        if terrain_meta.get('cycle_footer'):
                            print(f"** Cycle: {terrain_meta['cycle_footer']}")
                        print(
                            '** '
                            + represent_bytes(
                                footer_bytes[0x49:0x85]
                            ).replace(" 0x00", "")
                        )

                    else:
                        print(
                            f"Other Terrain signature: "
                            f"0x{file_signature:08X}"
                        )
                        print(
                            '** '
                            + represent_bytes(header_bytes[0x58:0x76])
                        )
                        print(
                            '** Cycle: '
                            + represent_bytes(footer2_bytes[0x01:0x05])
                        )
                        print(
                            '** '
                            + represent_bytes(header_bytes[0x78:0x84])
                        )
                        print(
                            '** '
                            + represent_bytes(header_bytes[0x86:0x8A])
                        )
                        print(
                            '** '
                            + represent_bytes(header_bytes[0x8C:0x90])
                        )

                _print_release_based_date_block(
                    release=release_date,
                )

    elif feature in (Feature.OBSTACLE2, Feature.SAFETAXI2):
        footer_magic = int.from_bytes(
            footer_bytes[0:4], 'little'
        )

        if footer_magic != GARMIN_AVDB_FOOTER_MAGIC:
            print('WRONG MAGIC!!')
            print(f"0x{footer_magic:08X}")

        footer_dates = _safe_metadata(
            feature,
            'GARMIN_AVDB_FOOTER_MAGIC',
            lambda: _display_db_magic_footer_dates(
                footer_bytes,
                show_descriptions=True,
            ),
        )

    elif feature == Feature.AIRPORT_DIR:
        footer_magic = int.from_bytes(
            footer_bytes[0:4], 'little'
        )

        if footer_magic == GARMIN_AVDB_FOOTER_MAGIC:
            print(
                f'** Footer magic = GARMIN_AVDB_FOOTER_MAGIC: '
                f'0x{footer_magic:08X}'
            )
            _safe_metadata(
                feature,
                'GARMIN_AVDB_FOOTER_MAGIC',
                lambda: _display_db_magic_footer_dates(
                    footer_bytes
                ),
            )
            print('** ' + represent_bytes(footer_bytes[0x20:0x2B]))
            print('** ' + represent_bytes(footer_bytes[0x2B:0x3F]))
            print('** ' + represent_bytes(footer_bytes[0x98:0xAC]))

        elif b'GRMREC01' in header_bytes:
            cycle = _find_airport_directory_cycle(header_bytes)
            generated = _safe_metadata(
                feature,
                'GRMREC timestamp',
                lambda: _decode_grmrec_timestamp(header_bytes),
            )

            if cycle:
                print(f'** Cycle: {cycle}')
                _print_cycle_derived_date_block(
                    cycle=cycle,
                    decoder=_decode_56day_cycle,
                    source_label='Airport Directory cycle',
                    generated=generated,
                    generated_source=(
                        'Generated derived from GRMREC timestamp'
                        if generated
                        else None
                    ),
                )
            elif generated:
                _print_date_block(
                    generated=generated,
                    source='Generated derived from GRMREC timestamp',
                )

            if not cycle:
                print(
                    f'** Unsupported Airport Directory date encoding '
                    f'(footer magic 0x{footer_magic:08X})'
                )
        else:
            print(
                f'** Unsupported Airport Directory format '
                f'(footer magic 0x{footer_magic:08X})'
            )

    elif feature == Feature.FLITE_CHARTS:
        print('** ' + represent_bytes(header_bytes[0x18:0x24]))
        print('** ' + represent_bytes(header_bytes[0x24:0x38]))
        print('** ' + represent_bytes(header_bytes[0x95:0xA9]))

        _safe_metadata(
            feature,
            'AIRAC17',
            lambda: _display_airac17_dates(
                header_bytes, 0x04
            ),
        )

    elif feature == Feature.CHARTVIEW:
        if (dat_file.parent / 'chartview.hif').is_file():
            with open(
                dat_file.parent / 'chartview.hif',
                'rb',
            ) as fd:
                header_bytes = fd.read(0x200)

            print('** ' + represent_bytes(header_bytes[0x0A:0x13]))
            print(
                '** Cycle: '
                + represent_bytes(header_bytes[0x23:0x2A])
            )

        charts_ini = dat_file.parent / 'charts.ini'
        if charts_ini.is_file():
            with open(charts_ini, 'rb') as fd:
                ini_bytes = fd.read()

            date_window = _safe_metadata(
                feature,
                'charts.ini validity',
                lambda: _parse_chartview_ini_dates(ini_bytes),
            )
            if date_window:
                effective, expires = date_window
                # Garmin ChartView remains viewable for 70 days after
                # the database expiration date, then becomes disabled.
                disables = _date_plus_days(expires, 70)
                _print_date_block(
                    effective=effective,
                    expires=expires,
                    disables=disables,
                    source=(
                        'validity embedded; '
                        'Disables derived from expiration + 70 days'
                    ),
                )

    elif feature in (
        Feature.SAFETAXI,
        Feature.BASEMAP,
        Feature.BASEMAP2,
    ):
        xor_byte = header_bytes[0x00] if header_bytes else 0
        if xor_byte:
            print(f'** XOR BYTE: {xor_byte:02x}')

        meta = _safe_metadata(
            feature,
            'DSKIMG metadata',
            lambda: _decode_dskimg_metadata(
                feature,
                header_bytes,
                footer_bytes,
            ),
        )

        if meta is not None:
            print(f"** MAP Version: {meta['map_version']}")
            _print_update_month_year(
                meta['update_month'],
                meta['update_year'],
            )

            if meta['name']:
                print(f"** {meta['name']}")

            print(f"** Cycle: {meta['cycle']}")

            if meta['description']:
                print(f"** {meta['description']}")

            if feature == Feature.SAFETAXI:
                date_window = _decode_56day_cycle(meta['cycle'])
                if date_window is not None:
                    effective, expires = date_window
                    _print_date_block(
                        effective=effective,
                        expires=expires,
                        creation=meta['created'],
                        source=(
                            'Creation Date embedded; '
                            'validity derived from SafeTaxi cycle'
                        ),
                    )
                else:
                    _print_date_block(
                        creation=meta['created'],
                        source='Creation Date embedded',
                    )
            else:
                # BASEMAP / BASEMAP2 use edition cycles such as
                # 24M1 / 25M1, not a fixed calendar validity cycle.
                _print_release_based_date_block(
                    creation=meta['created'],
                )

            creation_software = meta.get('creation_software')
            if creation_software is not None:
                version, release = creation_software
                print(
                    f'** Creation Software Version: '
                    f'{version} ({release})'
                )

    elif feature == Feature.SECTIONALS:
        cycle = represent_bytes(header_bytes[101:105]).strip()
        print(f'** Cycle: {cycle}')

        # rasters.xml carries an explicit MM/DD/YYYY effective date.
        # Keep that embedded date as the source of truth.
        sectional_effective = _safe_metadata(
            feature,
            'embedded effective date',
            lambda: _decode_mmddyyyy_date(
                header_bytes[171:181]
            ),
        )
        if sectional_effective is not None:
            _print_date_block(
                effective=sectional_effective,
                source='embedded',
            )

        description = represent_bytes(header_bytes[216:237]).strip()
        if description:
            print(f'** {description}')

        # Garmin IFR/VFR Charts normally revise every 28 days.
        # Canadian IFR/VFR Charts revise every 56 days, so no synthetic
        # expiration is printed here until the region is identified
        # unambiguously from an actual database sample.

    elif feature == Feature.AIR_SPORT:
        print('** header_bytes')
        print('** ' + represent_bytes(header_bytes[0x18:0x2A]))
        print('** ' + represent_bytes(header_bytes[0x5A:0x76]))
        print('** ' + represent_bytes(header_bytes[0x7C:0x88]))

        cycle = _find_air_sport_cycle(header_bytes)
        if cycle is None:
            legacy_cycle = represent_bytes(
                header_bytes[0x72:0x76]
            ).strip()
            cycle = (
                legacy_cycle
                if re.fullmatch(r'\d{4}', legacy_cycle)
                else None
            )

        generated = _safe_metadata(
            feature,
            'GRMREC timestamp',
            lambda: _decode_grmrec_timestamp(header_bytes),
        )

        if cycle:
            print(f'** Cycle: {cycle}')
            # AIR_SPORT does not currently expose the embedded AIRAC17
            # structure used by Navigation/FliteCharts. The validity
            # window is derived from the YYnn cycle identifier.
            _print_cycle_derived_date_block(
                cycle=cycle,
                decoder=_decode_airac_cycle,
                source_label='AIRAC cycle',
                generated=generated,
                generated_source=(
                    'Generated derived from GRMREC timestamp'
                    if generated
                    else None
                ),
            )
        elif generated:
            _print_date_block(
                generated=generated,
                source='Generated derived from GRMREC timestamp',
            )

    elif feature == Feature.CONFIG_ENABLE_NO_SERNO:
        debug('** File option contents -------:')
        debug(header_bytes)
        debug('** end file option------------.')

    else:
        print('** UNKNOWN DATA TYPE')
        print(header_bytes)


def cmd_verify(featunlk: str, feature: str | None) -> None:
    if feature is None:
        display_all_content_of_feat_unlk(pathlib.Path(featunlk), True)
    else:
        try:
            feature_typed = Feature[feature]
        except KeyError as e:
            feature_typed = FILENAME_TO_FEATURE.get(feature)

            if feature_typed is None:
                print(f"Unsupported feature: {feature}")
                print()
                print("Supported feature names and file paths:")
                for f in Feature:
                    print(f"  {f.name}: {', '.join(f.filenames)}")
                raise ValueError from e

        display_content_of_feat_unlk(pathlib.Path(featunlk), feature_typed, True)


def main():
    parser = argparse.ArgumentParser(description="Process the contents of a featunlk.dat/feat_unlk.dat file")

    subparsers = parser.add_subparsers(metavar="<command>")
    subparsers.required = True

    verify_p = subparsers.add_parser(
        "verify",
        help="Verify information",
    )
    verify_p.add_argument(
        '-f',
        '--feature',
        help="Only verify info for one specific Feature (by name or file path). "
             "CRC will be checked against file in same folder/subfolder of featunlk.dat/feat_unlk.dat file.",
    )
    verify_p.set_defaults(func=cmd_verify)

    parser.add_argument(
        "featunlk",
        metavar="featunlk.dat",
        help="Path to the featunlk.dat/feat_unlk.dat file",
    )

    args = parser.parse_args()

    kwargs = vars(args)
    func = kwargs.pop('func')

    try:
        func(**kwargs)
    except JdmToolException as ex:
        print(ex)
        return 1

    return 0
