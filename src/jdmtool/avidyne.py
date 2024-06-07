from abc import ABC, abstractmethod
import binascii
from dataclasses import dataclass
import re
from typing import BinaryIO, Callable, List, Mapping, Optional, TextIO
try:
    from typing import Self  # type: ignore
except ImportError:
    from typing_extensions import Self  # type: ignore
import sys
import zlib
from zipfile import ZipFile


# From "objdump -s --start-address=0xA049C0 --stop-address=0xA04DC0 jdm.exe"
# Jeppesen Distribution Manager Version 3.14.0 (Build 60)
LOOKUP_TABLE: List[int] = [int.from_bytes(binascii.a2b_hex(v), 'little') for v in b'''
    00000000 b71dc104 6e3b8209 d926430d
    dc760413 6b6bc517 b24d861a 0550471e
    b8ed0826 0ff0c922 d6d68a2f 61cb4b2b
    649b0c35 d386cd31 0aa08e3c bdbd4f38
    70db114c c7c6d048 1ee09345 a9fd5241
    acad155f 1bb0d45b c2969756 758b5652
    c836196a 7f2bd86e a60d9b63 11105a67
    14401d79 a35ddc7d 7a7b9f70 cd665e74
    e0b62398 57abe29c 8e8da191 39906095
    3cc0278b 8bdde68f 52fba582 e5e66486
    585b2bbe ef46eaba 3660a9b7 817d68b3
    842d2fad 3330eea9 ea16ada4 5d0b6ca0
    906d32d4 2770f3d0 fe56b0dd 494b71d9
    4c1b36c7 fb06f7c3 2220b4ce 953d75ca
    28803af2 9f9dfbf6 46bbb8fb f1a679ff
    f4f63ee1 43ebffe5 9acdbce8 2dd07dec
    77708634 c06d4730 194b043d ae56c539
    ab068227 1c1b4323 c53d002e 7220c12a
    cf9d8e12 78804f16 a1a60c1b 16bbcd1f
    13eb8a01 a4f64b05 7dd00808 cacdc90c
    07ab9778 b0b6567c 69901571 de8dd475
    dbdd936b 6cc0526f b5e61162 02fbd066
    bf469f5e 085b5e5a d17d1d57 6660dc53
    63309b4d d42d5a49 0d0b1944 ba16d840
    97c6a5ac 20db64a8 f9fd27a5 4ee0e6a1
    4bb0a1bf fcad60bb 258b23b6 9296e2b2
    2f2bad8a 98366c8e 41102f83 f60dee87
    f35da999 4440689d 9d662b90 2a7bea94
    e71db4e0 500075e4 892636e9 3e3bf7ed
    3b6bb0f3 8c7671f7 555032fa e24df3fe
    5ff0bcc6 e8ed7dc2 31cb3ecf 86d6ffcb
    8386b8d5 349b79d1 edbd3adc 5aa0fbd8
    eee00c69 59fdcd6d 80db8e60 37c64f64
    3296087a 858bc97e 5cad8a73 ebb04b77
    560d044f e110c54b 38368646 8f2b4742
    8a7b005c 3d66c158 e4408255 535d4351
    9e3b1d25 2926dc21 f0009f2c 471d5e28
    424d1936 f550d832 2c769b3f 9b6b5a3b
    26d61503 91cbd407 48ed970a fff0560e
    faa01110 4dbdd014 949b9319 2386521d
    0e562ff1 b94beef5 606dadf8 d7706cfc
    d2202be2 653deae6 bc1ba9eb 0b0668ef
    b6bb27d7 01a6e6d3 d880a5de 6f9d64da
    6acd23c4 ddd0e2c0 04f6a1cd b3eb60c9
    7e8d3ebd c990ffb9 10b6bcb4 a7ab7db0
    a2fb3aae 15e6fbaa ccc0b8a7 7bdd79a3
    c660369b 717df79f a85bb492 1f467596
    1a163288 ad0bf38c 742db081 c3307185
    99908a5d 2e8d4b59 f7ab0854 40b6c950
    45e68e4e f2fb4f4a 2bdd0c47 9cc0cd43
    217d827b 9660437f 4f460072 f85bc176
    fd0b8668 4a16476c 93300461 242dc565
    e94b9b11 5e565a15 87701918 306dd81c
    353d9f02 82205e06 5b061d0b ec1bdc0f
    51a69337 e6bb5233 3f9d113e 8880d03a
    8dd09724 3acd5620 e3eb152d 54f6d429
    7926a9c5 ce3b68c1 171d2bcc a000eac8
    a550add6 124d6cd2 cb6b2fdf 7c76eedb
    c1cba1e3 76d660e7 aff023ea 18ede2ee
    1dbda5f0 aaa064f4 738627f9 c49be6fd
    09fdb889 bee0798d 67c63a80 d0dbfb84
    d58bbc9a 62967d9e bbb03e93 0cadff97
    b110b0af 060d71ab df2b32a6 6836f3a2
    6d66b4bc da7b75b8 035d36b5 b440f7b1
'''.split()]


def sfx_checksum(data: bytes) -> int:
    value = 0
    for b in data:
        x = (value & 0x00FFFFFF) << 8
        value >>= 24
        value = b ^ x ^ LOOKUP_TABLE[value]
    return int(value)  # Handle the numpy case


try:
    import numpy as np
    from numba import jit

    LOOKUP_TABLE = np.array(LOOKUP_TABLE)
    sfx_checksum = jit(nopython=True, nogil=True)(sfx_checksum)
except ImportError as ex:
    print("Using a slow checksum implementation; consider installing jdmtool[jit]")


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
    conditional_info: Optional[str]
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
    files: List[str]

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


SECTION_CLASSES: List[SFXSection] = [SFXScriptSection, SFXCopySection, SFXMessageBoxSection]
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
    sections: List[SFXSection]

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
                print(f"Bismask: {bitmask}")

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
