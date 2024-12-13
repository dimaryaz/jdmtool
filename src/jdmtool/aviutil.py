import argparse
import hashlib
import os
import pathlib
import zlib


# oemdata/JeppExtractor.dat
EXE_SIZE = 352256
EXE_MD5 = '6e6d1a3494aa60827c4e83e0834439e5'

MAGIC_BYTES = b'1.00!AVIDYNE_SFX!'


def debug(input_file: str, extract: bool):
    with open(input_file, 'rb') as fd:
        exe = fd.read(EXE_SIZE)
        md5 = hashlib.md5(exe).hexdigest()
        if md5 != EXE_MD5:
            raise ValueError("Unexpected .exe prefix")

        fd.seek(-0x1000, os.SEEK_END)
        script = fd.read()

        script_offset = len(script)

        def read_bytes(lenght: int):
            nonlocal script_offset
            script_offset -= lenght
            if script_offset < 0:
                raise ValueError("Reading past the beginning of the data")
            return script[script_offset:script_offset+lenght]

        def read_int32():
            return int.from_bytes(read_bytes(4), 'little')

        def read_int8():
            return int.from_bytes(read_bytes(1), 'little')

        def read_string():
            length = read_int32()
            return read_bytes(length).decode("utf-8")

        def read_zero():
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

                message = read_string()
                sn, avidyne_key, chart_code, charts_path, db_begin_date, subscr_code = message.split("\t")
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
