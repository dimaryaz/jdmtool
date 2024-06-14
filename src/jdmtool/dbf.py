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

    last_update: datetime.date
    num_records: int
    header_bytes: int
    record_bytes: int

    @classmethod
    def from_bytes(cls, data: bytes) -> Self:
        version, year, month, day, num_records, header_bytes, record_bytes = struct.unpack('<4BIHH20x', data)
        if version != cls.VERSION:
            raise ValueError(f"Unsupported DBF version: {version}")

        return cls(datetime.date(year + 1900, month, day), num_records, header_bytes, record_bytes)

    def to_bytes(self) -> bytes:
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
            data = fd.read(field.length).decode('latin-1')
            if field.type == 'C':
                values.append(data.rstrip(' '))
            elif field.type == 'D':
                values.append(data)
            elif field.type == 'N':
                s = data.strip(' ')
                values.append(int(s) if s else None)
            else:
                raise ValueError(f"Unsupported field: {field.type}")
        return values

    @classmethod
    def write_record(cls, fd: BinaryIO, fields: List[DbfField], values: List[Any]) -> None:
        fd.write(b' ')
        for field, value in zip(fields, values):
            data = None
            if field.type == 'C':
                if value is None:
                    raise ValueError("C type cannot be None")
                data = value.ljust(field.length, ' ')
            elif field.type == 'D':
                if value is None:
                    raise ValueError("D type cannot be None")
                data = value
            elif field.type == 'N':
                # Why is it not rjust???
                data = ('' if value is None else str(value)).ljust(field.length, ' ')
            else:
                raise ValueError(f"Unsupported field: {field.type}")
            fd.write(data.encode('latin-1'))
