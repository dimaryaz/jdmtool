from collections.abc import Generator
from dataclasses import dataclass
from typing import BinaryIO

from .common import JdmToolException


TAW_SEPARATOR = b'\x00\x02\x00\x00\x00Dd\x00\x1b\x00\x00\x00A\xc8\x00'
TAW_MAGIC = b'KpGrd'

TAW_DATABASE_TYPES = {
    0x0091: "GPSMAP196",
    0x00BF: "Gx000",
    0x0104: "GPSMAP296",
    0x0190: "G500",
    0x01F2: "G500H/GPSx75",
    0x0253: "GPSMAP496",
    0x0294: "AERA660",
    0x02E9: "GPSMAP696",
    0x02EA: "G3X",
    0x02F0: "GPS175",
    0x0402: "GtnXi",
    0x0465: "GI275",
    0x0618: "AERA760",
    0x06BF: "G3XT",
    0x0738: "GTR2X5",
    0x07DC: "GTXi",
}

TAW_REGION_PATHS = {
    0x01: "ldr_sys/avtn_db.bin",
    0x02: "ldr_sys/nav_db2.bin",
    0x03: "bmap.bin",
    0x04: "nav.bin",  # fake filename: used for GNS430/500 data cards
    0x05: "bmap2.bin",
    0x0A: "safetaxi.bin",
    0x0B: "safetaxi2.gca",
    0x14: "fc_tpc/fc_tpc.dat",
    0x1A: "rasters/rasters.xml",
    0x21: "terrain.tdb",
    0x22: "terrain.odb",
    0x23: "trn.dat",
    0x24: "FCharts.dat",
    0x25: "Fcharts.fca",
    0x26: "standard.odb",
    0x27: "terrain.odb",
    0x28: "terrain.adb",
    0x32: ".System/AVTN/avtn_db.bin",
    0x33: "Poi/air_sport.gpi",
    0x35: ".System/AVTN/Obstacle.odb",
    0x36: ".System/AVTN/safetaxi.img",
    0x39: ".System/AVTN/FliteCharts/fc_tpc.dat",
    0x3A: ".System/AVTN/FliteCharts/fc_tpc.fca",
    0x4C: "fbo.gpi",
    0x4E: "apt_dir.gca",
    0x4F: "air_sport.gpi",
}


@dataclass
class TawMetadata:
    database_type: int
    year: int
    cycle: int
    avionics: str
    coverage: str
    type: str


@dataclass
class TawSection:
    sect_start: int
    sect_size: int
    region: int
    unknown: int
    data_start: int
    data_size: int


def parse_taw_metadata(metadata: bytes) -> TawMetadata:
    database_type = int.from_bytes(metadata[:2], 'little')

    if metadata[2] == 0x00:
        year = metadata[8]
        cycle = metadata[12]
        text = metadata[16:]
    else:
        year = metadata[4]
        cycle = metadata[6]
        text = metadata[8:]

    parts = text.split(b'\x00')
    if len(parts) != 3:
        raise ValueError(f"Unexpected metadata: {metadata}")

    return TawMetadata(
        database_type=database_type,
        year=year,
        cycle=cycle,
        avionics=parts[0].decode(),
        coverage=parts[1].decode(),
        type=parts[2].decode(),
    )


def read_taw_header(fd: BinaryIO) -> tuple[bytes, bytes, bytes]:
    magic = fd.read(5)
    if magic not in (b'pWa.d', b'wAt.d'):
        raise JdmToolException(f"Unexpected bytes: {magic}")

    sep = fd.read(len(TAW_SEPARATOR))
    if sep != TAW_SEPARATOR:
        raise JdmToolException(f"Unexpected separator bytes: {sep}")

    sqa1 = [s.decode() for s in fd.read(25).split(b'\x00')]

    metadata_len = int.from_bytes(fd.read(4), 'little')

    section_type = fd.read(1)
    if section_type != b'F':
        raise JdmToolException(f"Unexpected section type: {section_type}")

    metadata = fd.read(metadata_len)

    fd.read(4)  # Remaining

    section_type = fd.read(1)
    if section_type != b'R':
        raise JdmToolException(f"Unexpected section type: {section_type}")

    magic = fd.read(len(TAW_MAGIC))
    if magic != TAW_MAGIC:
        raise JdmToolException(f"Got unexpected magic bytes: {magic}")

    sep = fd.read(len(TAW_SEPARATOR))
    if sep != TAW_SEPARATOR:
        raise JdmToolException(f"Unexpected separator bytes: {sep}")

    sqa2 = [s.decode() for s in fd.read(25).split(b'\x00')]

    return sqa1, metadata, sqa2


def read_taw_sections(fd: BinaryIO) -> Generator[TawSection, None, None]:
    while True:
        sect_start = fd.tell()
        sect_size = int.from_bytes(fd.read(4), 'little')

        section_type = fd.read(1)
        if section_type == b'S':
            break
        if section_type != b'R':
            raise JdmToolException(f"Unexpected section type: {section_type}")

        region = int.from_bytes(fd.read(2), 'little')
        unknown = int.from_bytes(fd.read(4), 'little')
        data_size = int.from_bytes(fd.read(4), 'little')
        data_start = fd.tell()

        yield TawSection(
            sect_start=sect_start,
            sect_size=sect_size,
            region=region,
            unknown=unknown,
            data_start=data_start,
            data_size=data_size,
        )

        fd.seek(data_start + data_size)
