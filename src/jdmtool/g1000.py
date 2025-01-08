from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from io import BytesIO
import pathlib
import sys
from typing import BinaryIO

from .checksum import feat_unlk_checksum


FEAT_UNLK = 'feat_unlk.dat'


def decode_volume_id(encoded_vol_id: int) -> int:
    return ~((encoded_vol_id << 1 & 0xFFFFFFFF) | (encoded_vol_id >> 31)) & 0xFFFFFFFF

def encode_volume_id(vol_id: int) -> int:
    return ~((vol_id << 31 & 0xFFFFFFFF) | (vol_id >> 1)) & 0xFFFFFFFF

def truncate_system_id(system_id: int) -> int:
    return (system_id & 0xFFFFFFFF) + (system_id >> 32)


CONTENT1_LEN = 85
CONTENT2_LEN = 824

SEC_ID_OFFSET = 191

MAGIC1 = 0x1
MAGIC2 = 0x7648329A  # Hard-coded in GrmNavdata.dll
MAGIC3 = 0x6501

NAVIGATION_PREVIEW_START = 129
NAVIGATION_PREVIEW_END = 146

CHUNK_SIZE = 0x8000


class Feature(Enum):
    NAVIGATION = 0, 0
    TERRAIN = 1826, 3
    APT_TERRAIN = 3652, 5
    CHARTVIEW = 4565, 6
    SAFETAXI = 5478, 7
    BASEMAP = 7304, 10
    AIRPORT_DIR = 8217, 10
    AIR_SPORT = 9130, 10
    OBSTACLE2 = 11869, 10
    NAV_DB2 = 12782, 10
    SAFETAXI2 = 16434, 10
    BASEMAP2 = 17347, 10

    def __init__(self, offset, bit):
        self.offset = offset
        self.bit = bit


FILENAME_TO_FEATURE: dict[str, Feature] = {
    'apt_dir.gca': Feature.AIRPORT_DIR,
    'bmap.bin': Feature.BASEMAP,
    'bmap2.bin': Feature.BASEMAP2,
    'ldr_sys/avtn_db.bin': Feature.NAVIGATION,
    'ldr_sys/nav_db2.bin': Feature.NAV_DB2,
    'safetaxi.bin': Feature.SAFETAXI,
    'safetaxi2.gca': Feature.SAFETAXI2,
    'standard.odb': Feature.OBSTACLE2,
    'terrain_9as.tdb': Feature.TERRAIN,
    'terrain.odb': Feature.TERRAIN,
    'trn.dat': Feature.TERRAIN,
    "air_sport.gpi": Feature.AIR_SPORT,
    "avtn_db.bin": Feature.NAVIGATION,
    "crcfiles.txt": Feature.CHARTVIEW,
    "fbo.gpi": Feature.AIRPORT_DIR,
    "nav_db2.bin": Feature.NAV_DB2,
    "terrain.adb": Feature.APT_TERRAIN,
}


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
        system_id: int, checksum: int, preview: str | None
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
        assert len(preview) == preview_len, preview
        content1.write(preview)
    else:
        content1.write(b'\x00' * preview_len)

    content1.write(b'\x00' * (CONTENT1_LEN - len(content1.getbuffer()) - 4))

    chk1 = feat_unlk_checksum(content1.getbuffer())
    content1.write(chk1.to_bytes(4, 'little'))
    assert len(content1.getbuffer()) == CONTENT1_LEN, len(content1.getbuffer())

    content2 = BytesIO()
    content2.write((0).to_bytes(4, 'little'))

    content2.write(truncate_system_id(system_id).to_bytes(4, 'little'))

    content2.write(b'\x00' * (CONTENT2_LEN - len(content2.getbuffer()) - 4))

    chk2 = feat_unlk_checksum(content2.getbuffer())
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


def verify_feat_unlk(featunlk: pathlib.Path, path: pathlib.Path) -> None:
    feature = FILENAME_TO_FEATURE.get(path.name)
    if feature is None:
        raise ValueError(f"Unsupported filename: {path.name}")

    with open(featunlk, 'rb') as fd:
        fd.seek(feature.offset)

        content1_bytes = fd.read(CONTENT1_LEN)
        if all(b == 0 for b in content1_bytes):
            raise ValueError("No content")
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
    print(f"garmin_sec_id: {security_id}")

    magic = int.from_bytes(content1.read(4), 'little')
    if magic != MAGIC2:
        raise ValueError(f"Unexpected magic number: 0x{magic:08X}")

    expected_bit_value = int.from_bytes(content1.read(4), 'little')
    if expected_bit_value != 1 << feature.bit:
        raise ValueError(f"Incorrect bit: expected {expected_bit_value:04x}, got {1 << feature.bit:04x}")

    if not all(b == 0 for b in content1.read(4)):
        raise ValueError("Expected zeros")

    vol_id = decode_volume_id(int.from_bytes(content1.read(4), 'little'))
    print(f"Volume ID: {vol_id:08X}")

    if feature == Feature.NAVIGATION:
        magic = int.from_bytes(content1.read(2), 'little')
        if magic != MAGIC3:
            raise ValueError(f"Unexpected magic number: 0x{magic:04X}")

    expected_chk = int.from_bytes(content1.read(4), 'little')
    expected_preview = content1.read(17)

    if feature != Feature.NAVIGATION:
        if not all(b == 0 for b in expected_preview):
            raise ValueError("Expected zeros in the content")

    with open(path, 'rb') as fd:
        block = fd.read(CHUNK_SIZE)

        if feature == Feature.NAVIGATION:
            if expected_preview != block[129:146]:
                raise ValueError("Preview data mismatch")
        else:
            if not all(b == 0 for b in expected_preview):
                raise ValueError("Expected zeros in the content")

        chk = 0xFFFFFFFF
        while True:
            chk = feat_unlk_checksum(block, chk)
            next_block = fd.read(CHUNK_SIZE)
            if not next_block:
                break
            block = next_block

        if feature == Feature.CHARTVIEW:
            file_chk = chk
        else:
            if chk != 0:
                raise ValueError(f"{path} failed the checksum")
            file_chk = int.from_bytes(block[-4:], 'little')

    if file_chk != expected_chk:
        raise ValueError(
            f"Incorrect checksum for {path}: expected {expected_chk:08x}, got {file_chk:08x}"
        )

    if not all(b == 0 for b in content1.read()):
        raise ValueError("Expected zeros in the content")

    content2 = BytesIO(content2_bytes[:-4])

    if not all(b == 0 for b in content2.read(4)):
        raise ValueError("Expected zeros in the content2")

    system_id = int.from_bytes(content2.read(4), 'little')
    print(f"Truncated avionics_id: {system_id:08X}")
    possible_system_ids = [system_id - i | i << 32 for i in range(1, 4)]
    print(f"  (Possible values: {', '.join(f'{v:X}' for v in possible_system_ids)}, ...)")

    if not all(b == 0 for b in content2.read()):
        raise ValueError("Expected zeros in the content2")


def main():
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} featunlk path")
        return

    _, featunlk, path = sys.argv
    verify_feat_unlk(pathlib.Path(featunlk), pathlib.Path(path))

if __name__ == '__main__':
    main()
