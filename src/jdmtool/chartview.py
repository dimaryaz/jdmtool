import argparse
import binascii
import configparser
from dataclasses import dataclass
import datetime
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


@dataclass
class DbfHeader:
    SIZE = 32
    VERSION = 3

    last_update: datetime.date
    num_records: int
    header_bytes: int
    record_bytes: int

    @classmethod
    def from_bytes(cls, data: bytes):
        version, year, month, day, num_records, header_bytes, record_bytes = struct.unpack('<4BIHH20x', data)
        if version != cls.VERSION:
            raise ValueError(f"Unsupported DBF version: {version}")

        return cls(datetime.date(year + 1900, month, day), num_records, header_bytes, record_bytes)

    def to_bytes(self):
        return struct.pack(
            '<4BIHH20x',
            self.VERSION, self.last_update.year - 1900, self.last_update.month, self.last_update.day,
            self.num_records, self.header_bytes, self.record_bytes
        )


@dataclass
class DbfField:
    SIZE = 32

    name: str
    type: str
    length: int

    @classmethod
    def from_bytes(cls, data: bytes):
        name, typ, length = struct.unpack('<11sc4xB15x', data)
        return cls(name.decode(), typ.decode(), length)

    def to_bytes(self):
        return struct.pack('<11sc4xB15x', self.name.encode(), self.type.encode(), self.length)


class ChartView:
    CHARTS_INI = 'charts.ini'
    CHARTLINK = 'chrtlink.dbf'

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
        cfg = self._process_charts_ini(dest_path)
        filenames = self._process_charts_bin(cfg, dest_path)
        filename_set = set(filename.lower()[:-4] for filename in filenames)
        self._process_chartlink(filename_set, dest_path)


    def _process_charts_ini(self, dest_path: pathlib.Path) -> configparser.ConfigParser:
        config_bytes = self._sources[0].handle.read(self._sources[0].entry_map[self.CHARTS_INI])
        cfg = configparser.ConfigParser()
        cfg.read_string(config_bytes.decode())
        (dest_path / self.CHARTS_INI).write_bytes(config_bytes)

        return cfg

    def _process_charts_bin(self, cfg, dest_path: pathlib.Path) -> T.List[str]:
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

        return [record.name for record in all_records]

    @classmethod
    def _read_dbf_header(cls, fd: T.IO[bytes]) -> T.Tuple[DbfHeader, T.List[DbfField]]:
        header = DbfHeader.from_bytes(fd.read(DbfHeader.SIZE))
        num_fields = (header.header_bytes - 33) // 32
        fields = [DbfField.from_bytes(fd.read(DbfField.SIZE)) for _ in range(num_fields)]
        if fd.read(1) != b'\x0D':
            raise ValueError("Missing array terminator")
        return header, fields

    @classmethod
    def _write_dbf_header(cls, fd: T.IO[bytes], header: DbfHeader, fields: T.List[DbfField]) -> None:
        header.header_bytes = len(fields) * 32 + 33
        fd.write(header.to_bytes())
        for field in fields:
            fd.write(field.to_bytes())
        fd.write(b'\x0D')

    @classmethod
    def _read_dbf_record(cls, fd: T.IO[bytes], fields: T.List[DbfField]) -> T.List[T.Any]:
        del_marker = fd.read(1).decode()
        if del_marker != ' ':
            raise ValueError("Deleted field?")
        values = []
        for field in fields:
            data = fd.read(field.length)
            if field.type == 'C':
                values.append(data.decode('latin-1').rstrip(' '))
            elif field.type == 'N':
                values.append(int(data))
            else:
                raise ValueError(f"Unsupported field: {field.type}")
        return values

    @classmethod
    def _write_dbf_record(cls, fd: T.IO[bytes], fields: T.List[DbfField], values: T.List[T.Any]) -> None:
        fd.write(b' ')
        for field, value in zip(fields, values):
            if field.type == 'C':
                fd.write(value.encode('latin-1').ljust(field.length, b' '))
            elif field.type == 'N':
                fd.write(str(value).encode('latin-1').rjust(field.length, b' '))
            else:
                raise ValueError(f"Unsupported field: {field.type}")

    def _process_chartlink(self, filenames: T.Set[str], dest_path: pathlib.Path):
        with self._sources[0].handle.open(self._sources[0].entry_map[self.CHARTLINK]) as fd:
            header, fields = self._read_dbf_header(fd)
            records = []
            for _ in range(header.num_records):
                record = self._read_dbf_record(fd, fields)
                if record[3].lower() in filenames or record[4].lower() in filenames:
                    records.append(record)

        header.num_records = len(records)

        with open(dest_path / self.CHARTLINK, 'wb') as fd:
            self._write_dbf_header(fd, header, fields)
            for record in records:
                self._write_dbf_record(fd, fields, record)


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
