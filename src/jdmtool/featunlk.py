from __future__ import annotations

import argparse
import os
import pathlib
import struct
import zipfile
from collections.abc import Callable
from enum import Enum
from io import BytesIO
from typing import BinaryIO

from .checksum import feat_unlk_checksum
from .taw import TAW_DATABASE_TYPES

FEAT_UNLK = 'feat_unlk.dat'


def decode_volume_id(encoded_vol_id: int) -> int:
    return ~((encoded_vol_id << 1 & 0xFFFFFFFF) | (encoded_vol_id >> 31)) & 0xFFFFFFFF


def encode_volume_id(vol_id: int) -> int:
    return ~((vol_id << 31 & 0xFFFFFFFF) | (vol_id >> 1)) & 0xFFFFFFFF


def truncate_system_id(system_id: int) -> int:
    return (system_id & 0xFFFFFFFF) + (system_id >> 32)


CONTENT1_LEN = 0x55   # 85
CONTENT2_LEN = 0x338  # 824

SEC_ID_OFFSET = 191

MAGIC1 = 0x1
MAGIC2 = 0x7648329A  # Hard-coded in GrmNavdata.dll
MAGIC3 = 0x6501

NAVIGATION_PREVIEW_START = 129
NAVIGATION_PREVIEW_END = 146

CHUNK_SIZE = 0x8000

# DATABASE CONTENT
DB_MAGIC = 0xA5DBACE1
DB_MAGIC2 = 0x63614030


class Feature(Enum):
    NAVIGATION = 0, 0, ['ldr_sys/avtn_db.bin', 'avtn_db.bin', '.System/AVTN/avtn_db.bin']
    CONFIG_ENABLE = 913, 2, []
    TERRAIN = 1826, 3, ['terrain_9as.tdb', 'trn.dat', '.System/AVTN/terrain.tdb']
    OBSTACLE = 2739, 4, ['terrain.odb', '.System/AVTN/obstacle.odb']
    APT_TERRAIN = 3652, 5, ['terrain.adb']
    CHARTVIEW = 4565, 6, ['Charts/crcfiles.txt', 'crcfiles.txt']
    SAFETAXI = 5478, 7, ['safetaxi.bin', '.System/AVTN/safetaxi.img']
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

        if feature != Feature.CHARTVIEW:
            if chk != 0:
                raise ValueError(f"{filename} failed the checksum")
            chk = int.from_bytes(block[-4:], 'little')

    return chk, preview


def copy_with_feat_unlk(
        dest_dir: pathlib.Path, src: BinaryIO, filename: str,
        vol_id: int, security_id: int, system_id: int,
        progress_cb: Callable[[int], None]
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
        raise ValueError(f"{filename} failed the checksum")

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

    # Why is there no mode that accomplishes both of these in one call?
    with open(dest_dir / FEAT_UNLK, 'ab'):
        pass
    with open(dest_dir / FEAT_UNLK, 'r+b') as out:
        out.seek(feature.offset)
        out.write(content1.getbuffer())
        out.write(content2.getbuffer())
        out.write(chk3.to_bytes(4, 'little'))


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
      |         VOLUME ID         | PREV |  REV |   FILE CRC  |                         DATE                          |
0x020 |------|------|------|------|------|------|------|------|------|------|------|------|------|------|------|------|
      |                                DATE                          |                  RESERVED                      |
      |------|------|------|------|------|------|------|------|------|------|------|------|------|------|------|------|

OTHER DB:
0x010 |------|------|------|------|------|------|------|------|------|------|------|------|------|------|------|------|
      |         VOLUME ID         |   FILE CRC  |                         RESERVED                                    |
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
    print(f"* garmin_sec_id: {security_id}, device_model: {device_model_val}")

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

    if feature != Feature.NAVIGATION:
        if not all(b == 0 for b in expected_preview):
            raise ValueError("Expected zeros in the content")

        # read 2 Bytes to be at same offset as Feature.NAVIGATION
        byte = content1.tell()
        if not all(b == 0 for b in content1.read(2)):
            if show_missing:
                print("- Expected zeros in the content but got: ", [hex(x) for x in content1_bytes[byte:byte + 2]])
            else:
                print("- Expected zeros in the content")

    for filename in feature.filenames:
        dat_file = featunlk.parent.joinpath(filename)
        if dat_file.is_file():
            crc, preview = calculate_crc_and_preview_of_file(feature, dat_file)

            # wrong file
            if crc != expected_chk:
                print(f'- {dat_file} exists, but has wrong CRC')
                continue

            print(f'* {dat_file} has correct CRC')

            if feature == Feature.NAVIGATION and expected_preview != preview:
                raise ValueError("Preview data mismatch")

            display_content_of_dat_file(dat_file)
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

    byte = content2.tell()
    if not all(b == 0 for b in content2.read(2)):
        if show_missing:
            print("- Expected zeros in the content2 but got: ", [hex(x) for x in content2_bytes[byte:byte + 2]])
        else:
            print("- Expected zeros in the content2")

    system_id = int.from_bytes(content2.read(4), 'little')

    if unit_count != 0:
        print(f'* Still allowed onto {unit_count} systems')
    else:
        print(f"* Truncated avionics_id: {system_id:08X}")
        possible_system_ids = [system_id - i | i << 32 for i in range(1, 4)]
        print(f"  (Possible values: {', '.join(f'{v:X}' for v in possible_system_ids)}, ...)")

    byte = content2.tell()
    if not all(b == 0 for b in content2.read()):
        if show_missing:
            print("- Expected zeros in the content2 but got: ", [hex(x) for x in content2_bytes[byte: -4]])
        else:
            print("- Expected zeros in the content2")


def display_content_of_dat_file(dat_file: pathlib.Path):
    feature = FILENAME_TO_FEATURE.get(dat_file.name)

    if feature in (Feature.SAFETAXI2, ) and zipfile.is_zipfile(dat_file):
        with zipfile.ZipFile(dat_file, 'r') as zip_fp:
            with zip_fp.open('safetaxi2.bin') as fd:
                header_bytes = fd.read(0x200)
                fd.seek(-0x102, os.SEEK_END)
                footer_bytes = fd.read(0x102)
    else:
        with open(dat_file, 'rb') as fd:
            header_bytes = fd.read(0x200)
            fd.seek(-0x102, os.SEEK_END)
            footer_bytes = fd.read(0x102)

    if feature in (Feature.NAVIGATION, Feature.NAV_DB2):
        (region, year, man, _) = [x.strip() for x in header_bytes[0x9f:0xEF].decode('ascii').split("\0")]
        print(f'** Region: {region}')
        print(f'** {year}')
        print(f'** {man}')

        print('** Revision: ' + chr(header_bytes[0x92]))
        (cycle, f_month, f_day, f_year, t_month, t_day, t_year) = struct.unpack('<HBBHBBH', header_bytes[0x81:0x81+0xa])
        print('** Cycle: ', cycle)
        print(f'** Effective: {f_year}-{f_month:02}-{f_day:02} to {t_year}-{t_month:02}-{t_day:02}')
    elif feature in (Feature.OBSTACLE, ):
        if header_bytes[0x30:0x30+10] == b'Garmin Ltd':
            print('** ' + header_bytes[0x30:0x30+10].decode('ascii'))
            (f_day, f_month, f_year) = struct.unpack('<HHH', header_bytes[0x10:0x10+0x6])
            (t_day, t_month, t_year) = struct.unpack('<HHH', header_bytes[0x92:0x92+0x6])
            print(f'** Effective: {f_year}-{f_month:02}-{f_day:02} to {t_year}-{t_month:02}-{t_day:02}')
    elif feature in (Feature.TERRAIN, Feature.OBSTACLE2, Feature.SAFETAXI2):
        if DB_MAGIC != int.from_bytes(footer_bytes[0:4], 'little'):
            print('WRONG MAGIC!!')
            print(f"0x{int.from_bytes(footer_bytes[0:4], 'little'):08X}")
        print('** ' + footer_bytes[-0x6a:-0x61].decode('ascii') + ' ' +
              footer_bytes[4:8].decode('ascii') + ' ' + '\n** ' + footer_bytes[28:43].decode('ascii') +
              '\n** ' + footer_bytes[43:55].decode('ascii'))
        (f_month, f_day, f_year) = struct.unpack('<BBH', footer_bytes[-0xFA:-0xFA+0x4])
        (t_month, t_day, t_year) = struct.unpack('<BBH', footer_bytes[-0xF6:-0xF6+0x4])
        print(f'** Effective: {f_year}-{f_month:02}-{f_day:02} to {t_year}-{t_month:02}-{t_day:02}')
    elif feature in (Feature.AIRPORT_DIR, ):
        if DB_MAGIC2 != int.from_bytes(footer_bytes[0:4], 'little'):
            print('WRONG MAGIC!!')
            print(f"0x{int.from_bytes(footer_bytes[0:4], 'little'):08X}")
        print('** ' + footer_bytes[-0x6a:-0x50].decode('ascii') + ' ' +
              footer_bytes[4:9].decode('ascii') + ' ' + footer_bytes[28:51].decode('ascii'))
    elif feature in (Feature.FLITE_CHARTS, Feature.CHARTVIEW):
        print('** ' + header_bytes[0x18:0x79].decode('ascii'))
    elif feature in (Feature.SAFETAXI, Feature.BASEMAP, Feature.BASEMAP2):
        xor_byte = header_bytes[0x00]
        if xor_byte:
            print(f'** XOR BYTE: {xor_byte:02x}')

        if header_bytes[16:22] != b'DSKIMG':
            raise ValueError('No DSKIMG file')

        if header_bytes[0x41:0x47] != b'GARMIN':
            print(header_bytes[0x41:0x46])
            raise ValueError('File is not by GARMIN')

        map_version = str(header_bytes[0x08]) + '.' + str(header_bytes[0x09])
        print(f'** MAP Version: {map_version}')

        update_month = int(header_bytes[0x0a])
        update_year = int(header_bytes[0x0b]) + 1900
        print(f'** Update: {update_month}/{update_year}')

        name = header_bytes[0x49:0x49+20].decode('ascii')
        print(f'** {name}')
        description = header_bytes[0x65:0x83].decode('ascii')
        if description.strip():
            print(f'** {description}')
        year = int.from_bytes(header_bytes[0x39:0x39+2], 'little')
        month = int(header_bytes[0x3B])
        day = int(header_bytes[0x3c])
        print(f'** Creation Date: {year}-{month:02}-{day:02}')

        release = int.from_bytes(header_bytes[0x87:0x89], 'little')
        print(f'** Release: {release}')

        if int.from_bytes(header_bytes[0x83:0x85], 'little') == 0xDEAD:
            version = str(header_bytes[0x85]) + '.' + str(header_bytes[0x86])
            release = int.from_bytes(header_bytes[0x87:0x89], 'little')
            print(f'** Creation Software Version: {version} ({release})')
    elif feature in (Feature.AIR_SPORT,):
        print('** header_bytes')
        print('** ' + header_bytes[0x18:0x2A].decode('ascii'))
        print('** ' + header_bytes[0x5A:0x76].decode('ascii'))
        print('** ' + header_bytes[0x7B:0x89].decode('ascii'))

    else:  # Feature.APT_TERRAIN
        print('** UNKNOWN DATA TYPE')
        print(header_bytes)


def main():
    parser = argparse.ArgumentParser(description="Read the contents of a featunlk.dat/feat_unlk.dat file")
    parser.add_argument(
        '-f',
        '--feature',
        help="Only verify info for one specific Feature (by filename). "
             "CRC will be checked against file in same folder/subfolder of featunlk.dat/feat_unlk.dat file.",
    )
    parser.add_argument(
        "featunlk",
        metavar="featunlk.dat",
        help="Path to the featunlk.dat/feat_unlk.dat file",
    )

    args = parser.parse_args()

    if args.feature is None:
        display_all_content_of_feat_unlk(pathlib.Path(args.featunlk), True)
    else:
        path = pathlib.Path(args.feature)
        feature = FILENAME_TO_FEATURE.get(path.name)
        if feature is None:
            raise ValueError(f"Unsupported filename: {path.name}")

        display_content_of_feat_unlk(pathlib.Path(args.featunlk), feature, True)
