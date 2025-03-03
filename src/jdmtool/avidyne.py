from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from collections.abc import Callable, Mapping
import re
from typing import BinaryIO, TextIO
try:
    from typing import Self  # type: ignore
except ImportError:
    from typing_extensions import Self  # type: ignore
import sys
import zlib
from zipfile import ZipFile

from .checksum import sfx_checksum


def read_u32(fd: BinaryIO) -> int:
    return int.from_bytes(fd.read(4), 'big')


def read_bytes(fd: BinaryIO) -> bytes:
    str_len = read_u32(fd)
    s = fd.read(str_len)
    if len(s) != str_len:
        raise ValueError("Unexpected EOF")
    return s


def read_string(fd: BinaryIO) -> str:
    return read_bytes(fd).decode()


def write_u32(fd: BinaryIO, v: int) -> None:
    fd.write(v.to_bytes(4, 'big'))


def write_bytes(fd: BinaryIO, data: bytes) -> None:
    write_u32(fd, len(data))
    fd.write(data)


def write_string(fd: BinaryIO, data: str) -> None:
    write_bytes(fd, data.encode())


@dataclass
class SectionContext:
    header: str
    bitmask: int
    conditional_info: str | None
    param: str


@dataclass
class SecurityContext:
    cycle: str
    volume_id: int
    remaining_transfers: int


@dataclass
class SFXSection(ABC):
    ctx: SectionContext

    SECTION_ID = -1

    @classmethod
    @abstractmethod
    def debug(cls, fd: BinaryIO) -> None:
        ...

    @classmethod
    @abstractmethod
    def parse_script(cls, fd: TextIO, ctx: SectionContext) -> Self:
        ...

    @abstractmethod
    def total_progress(self, zipfile: ZipFile) -> int:
        ...

    @abstractmethod
    def run(self, out: BinaryIO, zipfile: ZipFile, ctx: SecurityContext, progress_cb: Callable[[int], None]) -> None:
        ...


@dataclass
class SFXScriptSection(SFXSection):
    start_message: str
    security: bool

    SECTION_ID = 0

    @classmethod
    def debug(cls, fd: BinaryIO) -> None:
        msg = read_string(fd)
        print("Message:", msg)

        security = fd.read(1)[0]
        print("Security enabled:", security)

        if security:
            unknown = fd.read(1)[0]
            print("Unknown value:", unknown)

            cycle = read_string(fd)
            print("Cycle:", cycle)

            volume_id = read_u32(fd)
            print(f"Card volume ID: {volume_id:08x}")
            remaining_transfers = read_u32(fd)
            print("Remaining transfers:", remaining_transfers)
            padding = fd.read(32 * remaining_transfers)
            if padding != b'\xaa' * 32 * remaining_transfers:
                raise ValueError(f"Unexpected padding: {padding}")

    @classmethod
    def parse_script(cls, fd: TextIO, ctx: SectionContext) -> Self:
        blank = next(fd).strip()
        if blank:
            raise ValueError(f"Unexpected content: {blank!r}")
        start_message = next(fd).strip()
        security = not next(fd).strip().startswith('0')
        return SFXScriptSection(ctx, start_message, security)

    def total_progress(self, zipfile: ZipFile) -> int:
        return 0

    def run(self, out: BinaryIO, zipfile: ZipFile, ctx: SecurityContext, progress_cb: Callable[[int], None]) -> None:
        write_string(out, self.start_message)
        out.write(self.security.to_bytes(1, 'big'))

        if self.security:
            out.write(b'\x03')
            write_string(out, ctx.cycle)
            write_u32(out, ctx.volume_id)
            write_u32(out, ctx.remaining_transfers)
            out.write(b'\xaa' * 32 * ctx.remaining_transfers)


@dataclass
class SFXCopySection(SFXSection):
    mode: int
    files: list[str]

    SECTION_ID = 1

    @classmethod
    def debug(cls, fd: BinaryIO) -> None:
        file_count = read_u32(fd)
        print("File count:", file_count)
        mode = read_u32(fd)
        print(f'Mode: {mode:04o}')

        for _ in range(file_count):
            filename = read_string(fd)
            print("Filename:", filename)
            unknown = read_u32(fd)
            print("Unknown value:", unknown)

            size = read_u32(fd)
            print("Uncompressed size:", size)

            compressed_contents = read_bytes(fd)
            contents = zlib.decompress(compressed_contents)
            if len(contents) != size:
                raise ValueError(f"Unexpected size: {len(contents)}; expected: {size}")

            expected_checksum = read_u32(fd)
            calculated_checksum = sfx_checksum(contents)
            if calculated_checksum != expected_checksum:
                raise ValueError(f"Unexpected checksum: {calculated_checksum:08x}; expected {expected_checksum:08x}")
            print(f"Checksum: {calculated_checksum:08x}")

    @classmethod
    def parse_script(cls, fd: TextIO, ctx: SectionContext) -> Self:
        mode_str = next(fd).strip()
        mode = int(mode_str, 8)
        files = []
        for line in fd:
            line = line.strip()
            if not line:
                break
            files.append(line)

        return SFXCopySection(ctx, mode, files)

    def total_progress(self, zipfile: ZipFile) -> int:
        return sum(zipfile.getinfo(filename).file_size for filename in self.files)

    def run(self, out: BinaryIO, zipfile: ZipFile, ctx: SecurityContext, progress_cb: Callable[[int], None]) -> None:
        write_u32(out, len(self.files))
        write_u32(out, self.mode)

        for filename in self.files:
            write_string(out, filename.rsplit('/')[-1])
            write_u32(out, 3)

            contents = zipfile.read(filename)
            write_u32(out, len(contents))
            compressed_contents = zlib.compress(contents)
            write_u32(out, len(compressed_contents))

            out.write(compressed_contents)

            checksum = sfx_checksum(contents)
            write_u32(out, checksum)

            progress_cb(len(contents))


@dataclass
class SFXMessageBoxSection(SFXSection):
    has_proceed: bool
    has_cancel: bool
    message: str

    SECTION_ID = 14

    @classmethod
    def debug(cls, fd: BinaryIO) -> None:
        has_proceed, has_cancel = fd.read(2)
        print("Has proceed:", has_proceed)
        print("Has cancel:", has_cancel)
        message = read_string(fd)
        print('Message:', message)

    @classmethod
    def parse_script(cls, fd: TextIO, ctx: SectionContext) -> Self:
        has_proceed = not next(fd).strip().startswith('0')
        has_cancel = not next(fd).strip().startswith('0')
        message_parts = []
        for line in fd:
            line = line.rstrip('\n')
            if line == '~MsgEnd~':
                break
            message_parts.append(line)
        return SFXMessageBoxSection(ctx, has_proceed, has_cancel, ''.join(message_parts))

    def total_progress(self, zipfile: ZipFile) -> int:
        return 0

    def run(self, out: BinaryIO, zipfile: ZipFile, ctx: SecurityContext, progress_cb: Callable[[int], None]) -> None:
        out.write(self.has_proceed.to_bytes(1, 'big'))
        out.write(self.has_cancel.to_bytes(1, 'big'))
        write_string(out, self.message)


SECTION_CLASSES: list[SFXSection] = [SFXScriptSection, SFXCopySection, SFXMessageBoxSection]
SECTION_BY_ID: Mapping[int, SFXSection] = { cls.SECTION_ID: cls for cls in SECTION_CLASSES }


@dataclass
class SFXFile:
    MAGIC_HEADER = b'!AVIDYNE_SFX!'
    MAGIC_FOOTER = 0x03040506

    VERSION_1_05 = '1.05'
    VERSION_3_07 = '3.07'

    SECTION_RE = re.compile(r'(\d{1,2})\s+(.+?)( ~Conditional.*)?')
    CONDITIONAL_RE = re.compile(r'(\d):(\d):(\d)\t(.+\t.+\t.+\t.+)')

    version: str
    sections: list[SFXSection]

    @classmethod
    def debug(cls, fd: BinaryIO) -> None:
        magic = fd.read(len(cls.MAGIC_HEADER))
        if magic != cls.MAGIC_HEADER:
            raise ValueError("Incorrect magic number")

        ver = fd.read(4).decode()
        print("Version:", ver)

        num_sections = read_u32(fd)
        for _ in range(num_sections):
            print()
            unknown = read_u32(fd)
            print("Unknown value:", unknown)
            section_header = read_string(fd)
            print('Header:', section_header)

            if ver == cls.VERSION_3_07:
                bitmask = read_u32(fd)
                print(f"Bitmask: {bitmask}")

                conditional = read_u32(fd)
                print(f"Conditional: {conditional}")

                if conditional:
                    condition_info = read_string(fd)
                    print(f"Condition info: {condition_info}")

            elif ver != cls.VERSION_1_05:
                raise ValueError(f"Unexpected version: {ver}")

            param = read_string(fd)
            print('Param:', param)

            section_type = fd.read(1)[0]
            print('Section type:', section_type)

            section_cls = SECTION_BY_ID.get(section_type)
            if section_cls is None:
                raise ValueError(f"Unsupported section type: {section_type}")

            section_cls.debug(fd)

        footer = read_u32(fd)
        if footer != cls.MAGIC_FOOTER:
            raise ValueError(f"Unexpected footer: {footer:08x}")

    @classmethod
    def parse_script(cls, fd: TextIO) -> Self:
        version = cls.VERSION_1_05
        sections = []

        for line in fd:
            line = line.strip()
            if not line or line.startswith(';'):
                continue

            m = cls.SECTION_RE.fullmatch(line)
            if not m:
                raise ValueError(f"Could not parse line: {line!r}")

            section_type = int(m.group(1))
            header = m.group(2)
            conditional = m.group(3) is not None

            bitmask = 7
            conditional_info = None

            if conditional:
                version = cls.VERSION_3_07
                m = cls.CONDITIONAL_RE.fullmatch(next(fd).strip())
                if m:
                    bitmask = (
                        bool(int(m.group(1))) |
                        bool(int(m.group(2))) << 2 |
                        bool(int(m.group(3))) << 1
                    )
                    conditional_info = m.group(4)
                else:
                    bitmask = 0

            param = next(fd).strip()

            ctx = SectionContext(header, bitmask, conditional_info, param)

            section_cls = SECTION_BY_ID.get(section_type)
            if section_cls is None:
                raise ValueError(f"Unsupported section type: {section_type}")

            section = section_cls.parse_script(fd, ctx)
            sections.append(section)

        return SFXFile(version, sections)

    def total_progress(self, zipfile: ZipFile) -> int:
        return sum(section.total_progress(zipfile) for section in self.sections)

    def run(self, out: BinaryIO, zipfile: ZipFile, ctx: SecurityContext, progress_cb: Callable[[int], None]) -> None:
        out.write(self.MAGIC_HEADER)
        out.write(self.version.encode())

        write_u32(out, len(self.sections))
        for idx, section in enumerate(self.sections):
            write_u32(out, 0)
            if idx == 0:
                write_string(out, f"{ctx.cycle} {section.ctx.header}")
            else:
                write_string(out, section.ctx.header)

            if self.version == self.VERSION_3_07:
                write_u32(out, section.ctx.bitmask)
                write_u32(out, section.ctx.conditional_info is not None)
                if section.ctx.conditional_info:
                    write_string(out, section.ctx.conditional_info)

            write_string(out, section.ctx.param)
            out.write(section.SECTION_ID.to_bytes(1, 'big'))

            section.run(out, zipfile, ctx, progress_cb)

        write_u32(out, self.MAGIC_FOOTER)


def main():
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} input.dsf")
        return

    with open(sys.argv[1], 'rb') as fd:
        SFXFile.debug(fd)


if __name__ == '__main__':
    main()
