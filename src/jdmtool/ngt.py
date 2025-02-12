
import textwrap

def checksum(data: bytes) -> int:
    chk = 0
    for i in range(0, len(data), 4):
        value = int.from_bytes(data[i:i+4], 'little')
        chk = (chk + value) & 0xFFFFFFFF
    return chk


def decode_packet_chunk(msg: bytes) -> bytes:
    return msg.replace(b'}^', b'~').replace(b'}]', b'}')


def encode_packet_chunk(msg: bytes) -> bytes:
    return msg.replace(b'}', b'}]').replace(b'~', b'}^')


def decode_packet(packet: bytes) -> list[bytes]:
    if not packet:
        return []

    if not packet.startswith(b'~') and packet.endswith(b'~'):
        raise ValueError("Missing a ~ marker")

    return [decode_packet_chunk(chunk) for chunk in packet[1:-1].split(b'~~')]


def encode_packet(packet_chunks: list[bytes]) -> bytes:
    return b''.join(b'~' + encode_packet_chunk(chunk) + b'~' for chunk in packet_chunks)


def remove_checksum(data: bytes) -> bytes:
    content = data[:-4]
    expected_chk = int.from_bytes(data[-4:], 'little')
    chk = checksum(content)
    if chk != expected_chk:
        raise ValueError(f"Checksum mismatch: expected {expected_chk:08x}, got {chk:08x}")
    return content


def add_checksum(data: bytes) -> bytes:
    chk = checksum(data)
    return data + chk.to_bytes(4, 'little')


def unwrap_message(data: bytes) -> tuple[int, bytes]:
    msg_type = int.from_bytes(data[0:2], 'little')
    msg_len = int.from_bytes(data[2:4], 'little')
    if msg_len != len(data) - 4:
        raise ValueError(f"Length mismatch: expected {len(data) - 4}, got {msg_len}")
    return msg_type, data[4:]


def wrap_message(msg_type: int, data: bytes) -> bytes:
    return msg_type.to_bytes(2, 'little') + len(data).to_bytes(2, 'little') + data


def unwrap_data_block(data: bytes) -> tuple[int, bytes]:
    data = remove_checksum(data)
    data_type = int.from_bytes(data[0:4], 'little')
    data_content = data[4:]
    return data_type, data_content


def wrap_data_block(data_type: int, data_content: bytes) -> bytes:
    return add_checksum(data_type.to_bytes(4, 'little') + data_content)


def print_message_info(msg_type: int, msg_data: bytes) -> None:
    if msg_type == 0x0003:
        status = int.from_bytes(msg_data, 'little')
        print(f"Status: {status:02X}")
    elif msg_type == 0xABCD:
        print(f"ABCD: {msg_data} ({msg_data.hex()})")
    elif msg_type == 0xDCBA:
        print(f"DCBA: {msg_data} ({msg_data.hex()})")
    elif msg_type == 0x0001:
        data_type, data_content = unwrap_data_block(msg_data)
        if len(data_content) == 12:
            unknown1 = int.from_bytes(data_content[0:4], 'little')
            unknown2 = int.from_bytes(data_content[4:8], 'little')
            mode_s = int.from_bytes(data_content[8:12], 'little')
            print(f"0001: data_type: {data_type:08X}, unknown1: {unknown1:08X}, unknown2: {unknown2:08X}, mode S: {mode_s:08o}")
        elif len(data_content) == 4:
            unknown = int.from_bytes(data_content, 'little')
            print(f"0001: data_type: {data_type:08X}, unknown: {unknown:08X}")
        else:
            print(f"0001: data_type: {data_type:08X}, content: {data_content}")
    elif msg_type == 0x0031:
        data_type, data_content = unwrap_data_block(msg_data)
        if data_type == 0xFF000080:
            print("Hardware info:")
            print(textwrap.indent(data_content.rstrip(b'\x00').decode(), '  '), end='')
        elif data_type == 0xFF00001D:
            print("Hardware info (maintenance mode):")
            print(textwrap.indent(data_content.rstrip(b'\x00').decode(), '  '), end='')
        elif data_type == 0x370001F8:
            idx = int.from_bytes(data_content[0:4], 'little')
            print(f"Database block {idx}")
        elif data_type == 0x37000094:
            idx = int.from_bytes(data_content[0:4], 'little')
            print(f"Database block (last) {idx}")
        elif data_type == 0x41000004:
            print(f"Reboot: {data_content}")
        elif data_type == 0xC1000003:
            print(f"Reboot response: {data_content}")
        else:
            print(f"Unknown data type {data_type:08X}: {data_content}")
    else:
        print(f"{msg_type:04X}: {msg_data}")
