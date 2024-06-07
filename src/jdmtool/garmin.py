import binascii
from enum import Enum
from io import BytesIO
import pathlib
import sys
from typing import BinaryIO, Callable, Dict, List, Optional


FEAT_UNLK = 'feat_unlk.dat'


# From "objdump -s --start-address=0x10028108 --stop-address=0x10028508 plugins/oem_garmin/GrmNavdata.dll"
# Jeppesen Distribution Manager Version 3.14.0 (Build 60)
LOOKUP_TABLE: List[int] = [int.from_bytes(binascii.a2b_hex(v), 'little') for v in b'''
    00000000 96300777 2c610eee ba510999
    19c46d07 8ff46a70 35a563e9 a395649e
    3288db0e a4b8dc79 1ee9d5e0 88d9d297
    2b4cb609 bd7cb17e 072db8e7 911dbf90
    6410b71d f220b06a 4871b9f3 de41be84
    7dd4da1a ebe4dd6d 51b5d4f4 c785d383
    56986c13 c0a86b64 7af962fd ecc9658a
    4f5c0114 d96c0663 633d0ffa f50d088d
    c8206e3b 5e10694c e44160d5 727167a2
    d1e4033c 47d4044b fd850dd2 6bb50aa5
    faa8b535 6c98b242 d6c9bbdb 40f9bcac
    e36cd832 755cdf45 cf0dd6dc 593dd1ab
    ac30d926 3a00de51 8051d7c8 1661d0bf
    b5f4b421 23c4b356 9995bacf 0fa5bdb8
    9eb80228 0888055f b2d90cc6 24e90bb1
    877c6f2f 114c6858 ab1d61c1 3d2d66b6
    9041dc76 0671db01 bc20d298 2a10d5ef
    8985b171 1fb5b606 a5e4bf9f 33d4b8e8
    a2c90778 34f9000f 8ea80996 18980ee1
    bb0d6a7f 2d3d6d08 976c6491 015c63e6
    f4516b6b 62616c1c d8306585 4e0062f2
    ed95066c 7ba5011b c1f40882 57c40ff5
    c6d9b065 50e9b712 eab8be8b 7c88b9fc
    df1ddd62 492dda15 f37cd38c 654cd4fb
    5861b24d ce51b53a 7400bca3 e230bbd4
    41a5df4a d795d83d 6dc4d1a4 fbf4d6d3
    6ae96943 fcd96e34 468867ad d0b860da
    732d0444 e51d0333 5f4c0aaa c97c0ddd
    3c710550 aa410227 10100bbe 86200cc9
    25b56857 b3856f20 09d466b9 9fe461ce
    0ef9de5e 98c9d929 2298d0b0 b4a8d7c7
    173db359 810db42e 3b5cbdb7 ad6cbac0
    2083b8ed b6b3bf9a 0ce2b603 9ad2b174
    3947d5ea af77d29d 1526db04 8316dc73
    120b63e3 843b6494 3e6a6d0d a85a6a7a
    0bcf0ee4 9dff0993 27ae000a b19e077d
    44930ff0 d2a30887 68f2011e fec20669
    5d5762f7 cb676580 71366c19 e7066b6e
    761bd4fe e02bd389 5a7ada10 cc4add67
    6fdfb9f9 f9efbe8e 43beb717 d58eb060
    e8a3d6d6 7e93d1a1 c4c2d838 52f2df4f
    f167bbd1 6757bca6 dd06b53f 4b36b248
    da2b0dd8 4c1b0aaf f64a0336 607a0441
    c3ef60df 55df67a8 ef8e6e31 79be6946
    8cb361cb 1a8366bc a0d26f25 36e26852
    95770ccc 03470bbb b9160222 2f260555
    be3bbac5 280bbdb2 925ab42b 046ab35c
    a7ffd7c2 31cfd0b5 8b9ed92c 1daede5b
    b0c2649b 26f263ec 9ca36a75 0a936d02
    a906099c 3f360eeb 85670772 13570005
    824abf95 147ab8e2 ae2bb17b 381bb60c
    9b8ed292 0dbed5e5 b7efdc7c 21dfdb0b
    d4d2d386 42e2d4f1 f8b3dd68 6e83da1f
    cd16be81 5b26b9f6 e177b06f 7747b718
    e65a0888 706a0fff ca3b0666 5c0b0111
    ff9e658f 69ae62f8 d3ff6b61 45cf6c16
    78e20aa0 eed20dd7 5483044e c2b30339
    612667a7 f71660d0 4d476949 db776e3e
    4a6ad1ae dc5ad6d9 660bdf40 f03bd837
    53aebca9 c59ebbde 7fcfb247 e9ffb530
    1cf2bdbd 8ac2baca 3093b353 a6a3b424
    0536d0ba 9306d7cd 2957de54 bf67d923
    2e7a66b3 b84a61c4 021b685d 942b6f2a
    37be0bb4 a18e0cc3 1bdf055a 8def022d
'''.split()]


# Note: appending the checksum to the content makes the overall checksum become 0.
def feat_unlk_checksum(data: bytes, value: int = 0xFFFFFFFF) -> int:
    for b in data:
        x = b ^ (value & 0xFF)
        value >>= 8
        value ^= LOOKUP_TABLE[x]
    return value


try:
    import numpy as np
    from numba import jit

    LOOKUP_TABLE = np.array(LOOKUP_TABLE)
    feat_unlk_checksum = jit(nopython=True, nogil=True)(feat_unlk_checksum)
except ImportError as ex:
    print("Using a slow checksum implementation; consider installing jdmtool[jit]")


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


FILENAME_TO_FEATURE: Dict[str, Feature] = {
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
        system_id: int, checksum: int, preview: Optional[str]
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
