import argparse
import binascii
import configparser
from dataclasses import dataclass
import pathlib
import struct
import typing as T
import zipfile
import zlib

import libscrc


@dataclass
class ChartHeader:
    SIZE = 27
    MAGIC = 0x1000000 + 27

    checksum: int
    num_files: int
    index_offset: int
    db_begin_date: str

    @classmethod
    def from_bytes(cls, data: bytes) -> T.Self:
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
    def from_bytes(cls, data: bytes) -> T.Self:
        name: bytes
        name, offset, size, metadata = struct.unpack('<26s2i6s', data)
        return cls(name.rstrip(b'\x00').decode(), offset, size, metadata)

    def to_bytes(self) -> bytes:
        return struct.pack('<26s2i6s', self.name.encode(), self.offset, self.size, self.metadata)


@dataclass
class ChartSource:
    path: pathlib.Path
    handle: zipfile.ZipFile
    entry_map: T.Dict[str, zipfile.ZipInfo]


class ChartView:
    def __init__(self, zip_list: T.List[pathlib.Path]) -> None:
        self._sources: T.List[ChartSource] = []
        for path in zip_list:
            handle = zipfile.ZipFile(path)
            entry_map = {
                entry.filename.lower(): entry
                for entry in handle.infolist()
            }
            self._sources.append(ChartSource(path, handle, entry_map))

    def close(self):
        for source in self._sources:
            source.handle.close()

    @classmethod
    def find_charts_bin(cls, entry_map: T.Dict[str, zipfile.ZipInfo]) -> zipfile.ZipInfo:
        for name, entry in entry_map.items():
            if name.endswith('.bin'):
                return entry
        raise ValueError("Could not find the charts file!")

    # def verify(self):

    def transfer(self, dest_path: pathlib.Path) -> None:
        config_bytes = self._sources[0].handle.read(self._sources[0].entry_map['charts.ini'])
        cfg = configparser.ConfigParser()
        cfg.read_string(config_bytes.decode())
        db_begin_date = cfg['CHARTS']['Database_Begin_Date']

        charts_bin_dest = dest_path / 'charts.bin'
        with open(charts_bin_dest, 'wb') as charts_bin_fd:
            crc32q = 0

            def write_with_crc(data: bytes):
                nonlocal crc32q
                charts_bin_fd.write(data)
                crc32q = libscrc.crc32_q(data, crc32q)

            chart_fds: T.List[T.IO[bytes]] = []
            headers: T.List[ChartHeader] = []
            all_records: T.List[ChartRecord] = []

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
