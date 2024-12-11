import argparse
import hashlib
import os
import pathlib
import zlib

from dataclasses import dataclass
from typing import BinaryIO, Callable, List, Tuple


# oemdata/JeppExtractor.dat
JEPP_EXTRACTOR_SRC_NAME = 'JeppExtractor.dat'
JEPP_EXTRACTOR_DEST_NAME = 'AviUtility.exe'
JEPP_EXTRACTOR_SIZE = 352256
JEPP_EXTRACTOR_MD5 = '6e6d1a3494aa60827c4e83e0834439e5'

# oemdata/AviUtil.DAT
AVI_UTIL_SRC_NAME = 'AviUtil.DAT'
AVI_UTIL_DEST_NAME = 'AviUtil.dat'
AVI_UTIL_SIZE = 221184
AVI_UTIL_MD5 = '36866252e662a551fdb44f980358c0f5'

MAGIC_BYTES = b'1.00!AVIDYNE_SFX!'


@dataclass
class FileRecord:
    src: str
    is_font: bool
    compressed_size: int
    orig_size: int
    offset: int


CHUNK_SIZE = 0x8000


def _append_file(out: BinaryIO, src: pathlib.Path, progress_cb: Callable[[int], None]) -> Tuple[int, int]:
    obj = zlib.compressobj()

    orig_size = compressed_size = 0
    with open(src, 'rb') as src_fd:
        while True:
            block = src_fd.read(CHUNK_SIZE)
            if not block:
                break

            compressed_block = obj.compress(block)
            out.write(compressed_block)
            compressed_size += len(compressed_block)

            orig_size += len(block)
            progress_cb(len(block))

    compressed_block = obj.flush()
    out.write(compressed_block)
    compressed_size += len(compressed_block)

    return orig_size, compressed_size


def append_jepp_extractor_payload(
    fd: BinaryIO,
    charts_files: List[pathlib.Path],
    fonts_files: List[pathlib.Path],
    subscr_code: str,
    db_begin_date: str,
    avidyne_key: str,
    serial_number: str,
    progress_cb: Callable[[int], None],
) -> None:
    current_offset = fd.tell()

    charts_records: List[FileRecord] = []
    for charts_file in charts_files:
        orig_size, compressed_size = _append_file(fd, charts_file, progress_cb)
        charts_records.append(FileRecord(
            '\\Extractor\\AviCharts.bin' if charts_file.name == 'charts.bin' else f'\\Charts\\{charts_file.name}',
            False,
            compressed_size,
            orig_size,
            current_offset
        ))

        current_offset += compressed_size

    fonts_records: List[FileRecord] = []
    for fonts_file in fonts_files:
        orig_size, compressed_size = _append_file(fd, fonts_file, progress_cb)
        fonts_records.append(FileRecord(
            f'\\Fonts\\{fonts_file.name}',
            True,
            compressed_size,
            orig_size,
            current_offset
        ))

        current_offset += compressed_size

    def write_int32(n: int) -> None:
        fd.write(n.to_bytes(4, 'little'))

    def write_int8(n: int) -> None:
        fd.write(n.to_bytes(1, 'little'))

    def write_string(s: str) -> None:
        data = s.encode("utf-8")
        fd.write(data)
        write_int32(len(data))

    section_count = 0

    def end_section(section_type: int, message: str) -> None:
        nonlocal section_count
        write_string(message)
        write_int8(section_type)
        write_int32(0)
        section_count += 1

    write_int32(0)

    end_section(0, "CMax Data Update")

    info = '\t'.join([
        serial_number.replace('-', ''),
        avidyne_key,
        subscr_code[5:8],
        'D:\\AviData\\JeppView\\DATA\\AviCharts.bin',
        db_begin_date,
        subscr_code,
    ])
    write_string(info)

    # Unknown
    write_int32(0)
    write_int32(1)
    write_int32(0)

    end_section(4, "JeppViewVersion")

    write_string("D:\\AviData\\JeppView\\")
    end_section(2, "Delete old files")

    write_string("D:\\AviData\\JeppViewTMP\\")
    end_section(2, "Delete temp files")

    for charts_record in charts_records:
        write_int32(charts_record.offset)
        write_int32(charts_record.orig_size)
        write_int32(charts_record.compressed_size)
        write_int32(charts_record.is_font)
        write_string(charts_record.src)

    write_int32(len(charts_records))
    write_string("D:\\AviData\\JeppViewTMP\\DATA\\")
    end_section(1, "Copying CMax Data")

    for fonts_record in fonts_records:
        write_int32(fonts_record.offset)
        write_int32(fonts_record.orig_size)
        write_int32(fonts_record.compressed_size)
        write_int32(fonts_record.is_font)
        write_string(fonts_record.src)

    write_int32(len(fonts_records))
    write_string("D:\\AviData\\JeppViewTMP\\Fonts\\")
    end_section(1, "Copying CMax Font")

    write_int32(section_count)
    fd.write(MAGIC_BYTES)


def debug(input_file: str, extract: bool):
    with open(input_file, 'rb') as fd:
        exe = fd.read(JEPP_EXTRACTOR_SIZE)
        md5 = hashlib.md5(exe).hexdigest()
        if md5 != JEPP_EXTRACTOR_MD5:
            raise ValueError("Unexpected .exe prefix")

        fd.seek(-0x1000, os.SEEK_END)
        script = fd.read()

        script_offset = len(script)

        def read_bytes(lenght: int) -> bytes:
            nonlocal script_offset
            script_offset -= lenght
            if script_offset < 0:
                raise ValueError("Reading past the beginning of the data")
            return script[script_offset:script_offset+lenght]

        def read_int32() -> int:
            return int.from_bytes(read_bytes(4), 'little')

        def read_int8() -> int:
            return int.from_bytes(read_bytes(1), 'little')

        def read_string() -> str:
            length = read_int32()
            return read_bytes(length).decode("utf-8")

        def read_zero() -> None:
            z = read_int32()
            if z != 0:
                raise ValueError(f"Expected 0, but got {z}")

        data = read_bytes(len(MAGIC_BYTES))
        if data != MAGIC_BYTES:
            raise ValueError(f"Unexpected magic bytes: {data}")

        section_count = read_int32()

        for _ in range(section_count):
            read_zero()

            section_type = read_int8()
            print(f"Section type: {section_type}")

            message = read_string()
            print(f"Message: {message}")

            if section_type == 0:
                pass

            elif section_type == 1:
                dest = read_string()
                print(f"Destination path: {dest}")

                file_count = read_int32()
                print(f"Number of files: {file_count}")

                for _ in range(file_count):
                    src = read_string()
                    is_font = read_int32()
                    compressed_size = read_int32()
                    orig_size = read_int32()
                    offset = read_int32()

                    fd.seek(offset)
                    compressed_content = fd.read(compressed_size)
                    uncompressed_content = zlib.decompress(compressed_content)
                    if len(uncompressed_content) != orig_size:
                        raise ValueError(f"Unexpected size: expected {orig_size}, got {len(uncompressed_content)}")

                    content_hash = hashlib.md5(uncompressed_content).hexdigest()

                    print(f"  {src:<30} {is_font} {orig_size:>10} {content_hash}")

                    if extract:
                        dest = pathlib.Path(src.replace('\\', '/').lstrip('/'))
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        dest.write_bytes(uncompressed_content)

            elif section_type == 2:
                path = read_string()
                print(f"Path: {path}")

            elif section_type == 4:
                a = read_int32()
                b = read_int32()
                c = read_int32()
                print(f"Unknown values: {a}, {b}, {c}")

                info = read_string()
                sn, avidyne_key, chart_code, charts_path, db_begin_date, subscr_code = info.split("\t")
                print(f"Serial number: {sn[:4]}-{sn[4:8]}-{sn[8:12]}-{sn[12:]}")
                print(f"Avidyne key: {avidyne_key}")
                print(f"Chart product code: {chart_code}")
                print(f"Chart file: {charts_path}")
                print(f"Database begin date: {db_begin_date}")
                print(f"Subscription code: {subscr_code}")

            else:
                raise ValueError("Unknown section type")

            print()

        read_zero()


def main():
    parser = argparse.ArgumentParser(description="Read the payload of AviUtility.exe")
    parser.add_argument(
        '-x',
        '--extract',
        action='store_true',
        help="Extract the files into the current directory",
    )
    parser.add_argument(
        "path",
        help="Path to the AviUtility.exe file",
    )
    args = parser.parse_args()

    debug(args.path, args.extract)


if __name__ == '__main__':
    main()
