import argparse
import binascii
from collections import defaultdict
import configparser
from dataclasses import dataclass
import datetime
import pathlib
import struct
from typing import Any, Dict, IO, List, Optional, Set
try:
    from typing import Self  # type: ignore
except ImportError:
    from typing_extensions import Self  # type: ignore
import zipfile
import zlib

import libscrc

from .dbf import DbfField, DbfFile, DbfHeader


@dataclass
class ChartHeader:
    SIZE = 27
    MAGIC = 0x1000000 + 27

    checksum: int
    num_files: int
    index_offset: int
    db_begin_date: str

    @classmethod
    def from_bytes(cls, data: bytes) -> Self:
        db_begin_date: bytes
        checksum, magic, num_files, index_offset, db_begin_date = struct.unpack('<4i11s', data)

        if magic != cls.MAGIC:
            raise ValueError("Invalid file")

        return cls(checksum, num_files, index_offset, db_begin_date.rstrip(b'\x00').decode())

    def to_bytes(self) -> bytes:
        return struct.pack(
            '<4i11s',
            self.checksum, self.MAGIC, self.num_files,
            self.index_offset, self.db_begin_date.encode(),
        )


@dataclass
class ChartRecord:
    SIZE = 40

    name: str
    offset: int
    size: int
    metadata: bytes

    @classmethod
    def from_bytes(cls, data: bytes) -> Self:
        name: bytes
        name, offset, size, metadata = struct.unpack('<26s2i6s', data)
        return cls(name.rstrip(b'\x00').decode(), offset, size, metadata)

    def to_bytes(self) -> bytes:
        return struct.pack('<26s2i6s', self.name.encode(), self.offset, self.size, self.metadata)


@dataclass
class ChartSource:
    path: pathlib.Path
    handle: zipfile.ZipFile
    entry_map: Dict[str, zipfile.ZipInfo]


class ChartView:
    def __init__(self, zip_list: List[pathlib.Path]) -> None:
        self._sources: List[ChartSource] = []
        for path in zip_list:
            handle = zipfile.ZipFile(path)
            entry_map = {
                entry.filename.lower(): entry
                for entry in handle.infolist()
            }
            self._sources.append(ChartSource(path, handle, entry_map))

    def close(self) -> None:
        for source in self._sources:
            source.handle.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, type, value, traceback) -> None:
        self.close()

    @classmethod
    def find_charts_bin(cls, entry_map: Dict[str, zipfile.ZipInfo]) -> zipfile.ZipInfo:
        for name, entry in entry_map.items():
            if name.endswith('.bin'):
                return entry
        raise ValueError("Could not find the charts file!")

    def process_charts_ini(self, dest_path: pathlib.Path) -> str:
        config_bytes = self._sources[0].handle.read(self._sources[0].entry_map['charts.ini'])
        cfg = configparser.ConfigParser()
        cfg.read_string(config_bytes.decode())
        (dest_path / 'charts.ini').write_bytes(config_bytes)

        return cfg['CHARTS']['Database_Begin_Date']

    def process_charts_bin(self, dest_path: pathlib.Path, db_begin_date: str) -> Dict[str, List[str]]:
        filenames: Dict[str, List[str]] = {}

        charts_bin_dest = dest_path / 'charts.bin'
        with open(charts_bin_dest, 'wb') as charts_bin_fd:
            crc32q = 0

            def write_with_crc(data: bytes):
                nonlocal crc32q
                charts_bin_fd.write(data)
                crc32q = libscrc.crc32_q(data, crc32q)

            chart_fds: List[IO[bytes]] = []
            headers: List[ChartHeader] = []
            all_records: List[ChartRecord] = []

            total_size = 0
            total_files = 0

            try:
                for source in self._sources:
                    chart_fd = source.handle.open(self.find_charts_bin(source.entry_map))
                    chart_fds.append(chart_fd)
                    header = ChartHeader.from_bytes(chart_fd.read(ChartHeader.SIZE))
                    headers.append(header)

                    total_size += header.index_offset - ChartHeader.SIZE
                    total_files += header.num_files

                new_header = ChartHeader(0, total_files, total_size + ChartHeader.SIZE, db_begin_date)
                new_header_bytes = new_header.to_bytes()
                charts_bin_fd.write(new_header_bytes[:4])
                write_with_crc(new_header_bytes[4:])

                total_offset = ChartHeader.SIZE

                for chart_fd, header in zip(chart_fds, headers):
                    chart_fd.seek(header.index_offset)
                    records = [
                        ChartRecord.from_bytes(chart_fd.read(ChartRecord.SIZE))
                        for _ in range(header.num_files)
                    ]
                    all_records.extend(records)
                    filenames[chart_fd.name] = [record.name for record in records]

                    for record in records:
                        assert 0 < record.size < 0x1000000, record.size
                        chart_fd.seek(record.offset)
                        contents = chart_fd.read(record.size)
                        record.offset = total_offset
                        total_offset += record.size
                        write_with_crc(contents)

                all_records.sort(key=lambda record: record.name)

                for record in all_records:
                    write_with_crc(record.to_bytes())

                charts_bin_fd.seek(0)
                charts_bin_fd.write(crc32q.to_bytes(4, 'little'))
            finally:
                for chart_fd in chart_fds:
                    chart_fd.close()

        return filenames

    def get_airports_by_filename(self) -> Dict[str, str]:
        airports: Dict[str, str] = {}
        for chart_filename in ['charts.dbf', 'vfrchrts.dbf']:
            with self._sources[0].handle.open(self._sources[0].entry_map[chart_filename]) as fd:
                header, fields = DbfFile.read_header(fd)
                for _ in range(header.num_records):
                    record = DbfFile.read_record(fd, fields)
                    airports[record[1]] = record[0]
        return airports

    def get_airports_by_key(self) -> Dict[int, Set[str]]:
        result: defaultdict[int, Set[str]] = defaultdict(set)
        with self._sources[0].handle.open(self._sources[0].entry_map['coverags.dbf']) as fd:
            header, fields = DbfFile.read_header(fd)
            for _ in range(header.num_records):
                record = DbfFile.read_record(fd, fields)
                result[int(record[0])].add(record[1])
        return result


    def process_charts(self, ifr_airports: Set[str], vfr_airports: Set[str], dest_path: pathlib.Path) -> Dict[str, int]:
        records: List[List[Any]] = []
        header: Optional[DbfHeader] = None
        fields: Optional[List[DbfField]] = None
        for name, airports in (('charts.dbf', ifr_airports), ('vfrchrts.dbf', vfr_airports)):
            with self._sources[0].handle.open(self._sources[0].entry_map[name]) as fd:
                header, fields = DbfFile.read_header(fd)
                if airports:
                    for _ in range(header.num_records):
                        record = DbfFile.read_record(fd, fields)
                        if record[0] in airports:
                            records.append(record)

        records.sort(key=lambda r: r[0])

        assert header is not None
        assert fields is not None

        header.num_records = len(records)
        header.last_update = datetime.date.today()

        indexes: Dict[str, int] = {}

        with open(dest_path / 'charts.dbf', 'wb') as fd:
            DbfFile.write_header(fd, header, fields)
            for idx, record in enumerate(records):
                DbfFile.write_record(fd, fields, record)
                indexes.setdefault(record[0], idx + 1)

        return indexes

    def process_chartlink(self, ifr_airports: Set[str], vfr_airports: Set[str], dest_path: pathlib.Path) -> Dict[str, int]:
        with self._sources[0].handle.open(self._sources[0].entry_map['chrtlink.dbf']) as fd:
            header, fields = DbfFile.read_header(fd)
            records = []
            for _ in range(header.num_records):
                record = DbfFile.read_record(fd, fields)
                if record[0] in ifr_airports or record[0] in vfr_airports:
                    records.append(record)

        header.num_records = len(records)

        indexes: Dict[str, int] = {}

        with open(dest_path / 'chrtlink.dbf', 'wb') as fd:
            DbfFile.write_header(fd, header, fields)
            for idx, record in enumerate(records):
                DbfFile.write_record(fd, fields, record)
                indexes.setdefault(record[0], idx + 1)

        return indexes

    def process_airports(
            self, ifr_airports: Set[str], vfr_airports: Set[str],
            chart: Dict[str, int], chartlink: Dict[str, int], dest_path: pathlib.Path
    ) -> None:
        records: Dict[str, List[Any]] = {}

        with self._sources[0].handle.open(self._sources[0].entry_map['airports.dbf']) as fd:
            header, fields = DbfFile.read_header(fd)
            assert len(fields) == 26, fields

            if ifr_airports:
                for _ in range(header.num_records):
                    record = DbfFile.read_record(fd, fields)
                    if record[0] in ifr_airports:
                        record[-2] = chart.get(record[0])
                        record[-1] = chartlink.get(record[0])
                        records[record[0]] = record

        with self._sources[0].handle.open(self._sources[0].entry_map['vfrapts.dbf']) as fd:
            vfr_header, vfr_fields = DbfFile.read_header(fd)
            assert len(vfr_fields) == 28, vfr_fields

            if vfr_airports:
                header.last_update = datetime.date.today()

                for _ in range(vfr_header.num_records):
                    record = DbfFile.read_record(fd, vfr_fields)
                    if record[0] in vfr_airports and not record[0] in records:
                        del record[1]  # F5_6_TYPE
                        del record[14]  # SUP_SVCS
                        record.insert(16, 'N')  # PRECISION
                        del record[18]  # NVFR
                        del record[18]  # PPR
                        record[-1] = chart.get(record[0])
                        record.append(chartlink.get(record[0]))

                        records[record[0]] = record

        header.num_records = len(records)

        with open(dest_path / 'airports.dbf', 'wb') as fd:
            DbfFile.write_header(fd, header, fields)
            for record in sorted(records.values()):
                DbfFile.write_record(fd, fields, record)


def main() -> int:
    parser = argparse.ArgumentParser(description="Read the contents of a charts.bin file")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        '-l',
        '--list',
        action='store_true',
        help="List the files",
    )
    group.add_argument(
        '-x',
        '--extract',
        action='store_true',
        help="Extract the files into the current directory",
    )
    parser.add_argument(
        "path",
        help="Path to the chart.bin file",
    )

    args = parser.parse_args()

    with open(args.path, 'rb') as charts_fd:
        header = ChartHeader.from_bytes(charts_fd.read(ChartHeader.SIZE))
        charts_fd.seek(header.index_offset)
        records = [
            ChartRecord.from_bytes(charts_fd.read(ChartRecord.SIZE))
            for _ in range(header.num_files)
        ]

        for record in records:
            assert 0 < record.size < 0x1000000, record.size

            if args.extract:
                print(record.name)
                charts_fd.seek(record.offset)
                contents = charts_fd.read(record.size)
                uncompresed = zlib.decompress(contents)
                with open(record.name, 'wb') as fd:
                    fd.write(uncompresed)

            elif args.list:
                metadata = binascii.hexlify(record.metadata).decode()
                print(f"{record.name:<26}  {record.size:>8}  {metadata:<12}")
