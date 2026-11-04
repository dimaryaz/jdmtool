from __future__ import annotations

import argparse
import hashlib
import os
import pathlib
import zlib
from datetime import datetime, timedelta, timezone

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
TOOL_VERSION = '1.5'


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
    *,
    chart_product_code: str,
    subscriptions: list[tuple[str, bool]] | None = None,
) -> None:
    if len(chart_product_code) != 3 or not chart_product_code.isascii() or not chart_product_code.isdigit():
        raise ValueError("Invalid chart_product_code")
    subscriptions = subscriptions or [(subscr_code, False)]
    expected = {'vfrcharts.bin' if vfr else 'charts.bin' for code, vfr in subscriptions}
    if not expected.issubset({path.name.lower() for path in charts_files}):
        raise ValueError("Missing IFR/VFR binary")
    current_offset = fd.tell()

    charts_records: List[FileRecord] = []
    for charts_file in charts_files:
        orig_size, compressed_size = _append_file(fd, charts_file, progress_cb)
        charts_records.append(FileRecord(
            ({'charts.bin': r'\Extractor\AviCharts.bin',
              'vfrcharts.bin': r'\Extractor\AviVFRCharts.bin'}.get(
                  charts_file.name.lower(), f'\\Charts\\{charts_file.name}')),
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

    # Two reserved words precede the base section in the native JDM package.
    write_int32(0)
    write_int32(0)

    end_section(0, "CMax Data Update")

    for index in reversed(range(len(subscriptions))):
        code, is_vfr = subscriptions[index]
        binary = 'AviVFRCharts.bin' if is_vfr else 'AviCharts.bin'
        info = '\t'.join([
            serial_number.replace('-', ''), avidyne_key, chart_product_code,
            'D:\\AviData\\JeppView\\DATA\\' + binary, db_begin_date, code + ' ',
        ])
        write_string(info)
        write_int32(int(is_vfr))
    write_int32(len(subscriptions))
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


def format_julian_date(value):
    """Keep the Julian day number and append its UTC calendar date."""
    try:
        date = datetime(2000, 1, 1, tzinfo=timezone.utc) + timedelta(days=float(value) - 2451544.5)
        return f"{value} ({date:%d/%m/%Y})"
    except (TypeError, ValueError, OverflowError):
        return f"{value} (invalid date)"


def debug(input_file: str, extract: bool):
    with open(input_file, 'rb') as fd:
        exe = fd.read(JEPP_EXTRACTOR_SIZE)
        md5 = hashlib.md5(exe).hexdigest()
        if md5 != JEPP_EXTRACTOR_MD5:
            raise ValueError("Unexpected .exe prefix")

        # Read backwards directly from the file. The script may exceed 4 KiB.
        fd.seek(0, os.SEEK_END)
        script_offset = fd.tell()

        def read_bytes(length: int) -> bytes:
            nonlocal script_offset
            if length < 0 or length > script_offset:
                raise ValueError("Reading past the beginning of the data")
            script_offset -= length
            fd.seek(script_offset)
            data = fd.read(length)
            if len(data) != length:
                raise ValueError(f"Truncated file at offset {script_offset}")
            return data

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
        chart_files = set()
        subscription_count = None

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

                    basename = pathlib.PurePosixPath(src.replace('\\', '/')).name.lower()
                    if basename in {'avicharts.bin', 'avivfrcharts.bin'}:
                        chart_files.add(basename)
                    if basename == 'sbscrips.dbf' and len(uncompressed_content) >= 32:
                        dbf = uncompressed_content
                        count = int.from_bytes(dbf[4:8], 'little')
                        header_size = int.from_bytes(dbf[8:10], 'little')
                        record_size = int.from_bytes(dbf[10:12], 'little')
                        if header_size >= 32 and record_size >= 1 and header_size + count * record_size <= len(dbf):
                            subscription_count = sum(dbf[header_size + i * record_size] != 0x2a for i in range(count))

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
                reserved = read_int32()
                version_count = read_int32()
                if reserved != 0:
                    raise ValueError(f"Unexpected version reserved value: {reserved}")
                if not 1 <= version_count <= 1024:
                    raise ValueError(f"Invalid version record count: {version_count}")
                print(f"Version records: {version_count}; reserved: {reserved}")

                for _ in range(version_count):
                    record_id = read_int32()
                    info = read_string()
                    values = info.split("\t")
                    if len(values) != 6:
                        raise ValueError(f"Version record {record_id}: expected 6 fields, got {len(values)}")
                    sn, avidyne_key, chart_code, charts_path, db_begin_date, subscr_code = values
                    print(
                        f"Record {record_id}: serial={sn}, product={chart_code}, "
                        f"file={charts_path}, begin={format_julian_date(db_begin_date)}, "
                        f"code={subscr_code.strip()}"
                    )


            else:
                raise ValueError("Unknown section type")

            print()

        read_zero()
        if chart_files == {'avicharts.bin', 'avivfrcharts.bin'}:
            print("Chart layout: IFR and VFR stored in separate files")
        elif len(chart_files) == 1:
            print("Chart layout: one chart file; combined IFR/VFR cannot be confirmed from the file count")
        else:
            print("Chart layout: indeterminate (expected chart files not found)")
        if subscription_count is not None:
            print(f"Active subscription records: {subscription_count}")


def main():
    parser = argparse.ArgumentParser(description="Read the payload of AviUtility.exe (IFR/VFR)")
    parser.add_argument("--version", action="version", version="aviutil " + TOOL_VERSION)
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
