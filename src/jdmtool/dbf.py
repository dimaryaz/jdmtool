from dataclasses import dataclass
import datetime
import struct
from typing import Any, BinaryIO, List, Tuple
try:
    from typing import Self  # type: ignore
except ImportError:
    from typing_extensions import Self  # type: ignore


@dataclass
class DbfHeader:
    SIZE = 32
    VERSION = 3

    info: int
    last_update: datetime.date
    num_records: int
    header_bytes: int
    record_bytes: int

    @classmethod
    def from_bytes(cls, data: bytes) -> Self:
        info, year, month, day, num_records, header_bytes, record_bytes = struct.unpack('<4BIHH20x', data)
        version = info & 0x3
        if version != cls.VERSION:
            raise ValueError(f"Unsupported DBF version: {version}")

        return cls(info, datetime.date(year + 1900, month, day), num_records, header_bytes, record_bytes)

    def to_bytes(self) -> bytes:
        return struct.pack(
            '<4BIHH20x',
            self.info, self.last_update.year - 1900, self.last_update.month, self.last_update.day,
            self.num_records, self.header_bytes, self.record_bytes
        )


@dataclass
class DbfField:
    SIZE = 32

    name: str
    type: str
    length: int

    @classmethod
    def from_bytes(cls, data: bytes) -> Self:
        name, typ, length = struct.unpack('<11sc4xB15x', data)
        return cls(name.rstrip(b'\x00').decode(), typ.decode(), length)

    def to_bytes(self) -> bytes:
        return struct.pack('<11sc4xB15x', self.name.encode(), self.type.encode(), self.length)


class DbfFile:
    @classmethod
    def read_header(cls, fd: BinaryIO) -> Tuple[DbfHeader, List[DbfField]]:
        header = DbfHeader.from_bytes(fd.read(DbfHeader.SIZE))
        num_fields = (header.header_bytes - 33) // 32
        fields = [DbfField.from_bytes(fd.read(DbfField.SIZE)) for _ in range(num_fields)]
        if fd.read(1) != b'\x0D':
            raise ValueError("Missing array terminator")
        return header, fields

    @classmethod
    def write_header(cls, fd: BinaryIO, header: DbfHeader, fields: List[DbfField]) -> None:
        header.header_bytes = len(fields) * 32 + 33
        fd.write(header.to_bytes())
        for field in fields:
            fd.write(field.to_bytes())
        fd.write(b'\x0D')

    @classmethod
    def read_record(cls, fd: BinaryIO, fields: List[DbfField]) -> List[Any]:
        del_marker = fd.read(1).decode()
        if del_marker == '*':
            raise ValueError("Deleted record?")
        elif del_marker != ' ':
            raise ValueError(f"Bad deleted marker: {del_marker!r}")

        values = []
        for field in fields:
            data = fd.read(field.length).decode('latin-1').strip(' ')
            if field.type == 'C':
                value = data
            elif field.type == 'D':
                s = data.strip(' ')
                if s:
                    value = datetime.datetime.strptime(data, '%Y%m%d').date()
                else:
                    value = None
            elif field.type == 'L':
                if len(data) != 1:
                    raise ValueError(f"Incorrect length: {data!r}")
                if data in 'YyTt':
                    value = True
                elif data in 'NnFf':
                    value = False
                elif data == '?':
                    value = None
                else:
                    raise ValueError(f"Incorrect boolean: {data!r}")
            elif field.type in ('M', 'N'):
                value = int(data) if data else None
            else:
                raise ValueError(f"Unsupported field: {field.type}")
            values.append(value)
        return values

    @classmethod
    def write_record(cls, fd: BinaryIO, fields: List[DbfField], values: List[Any]) -> None:
        fd.write(b' ')
        for field, value in zip(fields, values):
            data = None
            if field.type == 'C':
                if value is None:
                    raise ValueError("C type cannot be None")
                data = value
            elif field.type == 'D':
                if value is None:
                    data = ''
                else:
                    assert isinstance(value, datetime.date)
                    data = value.strftime('%Y%m%d')
            elif field.type == 'L':
                data = {
                    True: 'T',
                    False: 'F',
                    None: '?',
                }[value]
            elif field.type in ('M', 'N'):
                # Should be rjust, but that's not what JDM does!
                data = '' if value is None else str(value)
            else:
                raise ValueError(f"Unsupported field: {field.type}")
            fd.write(data.ljust(field.length).encode('latin-1'))


# http://www.manmrk.net/tutorials/database/xbase/dbt.html

@dataclass
class DbtHeader:
    SIZE = 512

    next_free_block: int
    dbf_filename: str
    reserved: int
    block_length: int

    @classmethod
    def from_bytes(cls, data: bytes) -> Self:
        next_free_block, dbf_filename, reserved, block_length = struct.unpack('<I4x8sIH490x', data)
        return cls(next_free_block, dbf_filename.decode('latin-1'), reserved, block_length)

    def to_bytes(self) -> bytes:
        return struct.pack(
            '<I4x8sIH490x',
            self.next_free_block, self.dbf_filename.encode('latin-1'), self.reserved, self.block_length
        )


class DbtFile:
    DBT3_BLOCK_SIZE = 512
    DBT4_BLOCK_START = b'\xFF\xFF\x08\x00'

    @classmethod
    def read_header(cls, fd: BinaryIO) -> DbtHeader:
        fd.seek(0)
        block = fd.read(DbtHeader.SIZE)
        return DbtHeader.from_bytes(block)

    @classmethod
    def write_header(cls, fd: BinaryIO, header: DbtHeader) -> None:
        fd.seek(0)
        fd.write(header.to_bytes())

    @classmethod
    def read_record(cls, fd: BinaryIO, header: DbtHeader, idx: int) -> str:
        if header.block_length:
            fd.seek(header.block_length * idx)
            block_start = fd.read(8)
            if block_start[0:4] != cls.DBT4_BLOCK_START:
                raise ValueError("Invalid dBase IV block")
            length = int.from_bytes(block_start[4:8], 'little')
            data = fd.read(length - len(block_start))
        else:
            fd.seek(cls.DBT3_BLOCK_SIZE * idx)
            blocks = b''
            while True:
                block = fd.read(cls.DBT3_BLOCK_SIZE)
                if not block:
                    raise ValueError("Failed to find field terminator!")
                blocks += block
                if b'\x1a\x1a' in blocks:
                    break
            data = blocks.split(b'\x1a\x1a', 1)[0]

        return data.decode('latin-1')

    @classmethod
    def write_record(cls, fd: BinaryIO, header: DbtHeader, idx: int, data: str) -> int:
        if header.block_length:
            block_length = header.block_length
            total_length = 8 + len(data)
            blocks = cls.DBT4_BLOCK_START + total_length.to_bytes(4, 'little') + data.encode('latin-1')
        else:
            block_length = cls.DBT3_BLOCK_SIZE
            blocks = data.encode('latin-1') + b'\x1a\x1a'

        block_count = -(-len(blocks) // block_length)

        fd.seek(block_length * idx)
        fd.write(blocks.ljust(block_length * block_count, b'\x00'))
        return block_count
