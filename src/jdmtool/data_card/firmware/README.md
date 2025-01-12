# Firmware Data

Garmin firmware was captured using WireShark, converted to Python using [usbrply](https://github.com/JohnDMcMaster/usbrply), and saved into binary files by mocking out `controlWrite`:

```python
import struct

firmware = []

def controlWrite(_type, req, addr, _idx, data):
    if req == 0xA0:
        firmware.append((addr, data))

### usbrply code

...
# Generated from packet 39/40
controlWrite(0x40, 0xA0, 0x08F5, 0x0000, b"\x00\x01\x02\x02\x03\x03\x04\x04\x05\x05")
...

### end of usbrply code

with open('firmware.bin', 'wb') as fd:
    for addr, data in firmware:
        fd.write(struct.pack('<HH', addr, len(data)))
        fd.write(data)

```

Only the `0xA0` requests contain the firmware:

```python
controlWrite(0x40, 0xA0, ..., 0x0000, b"...")
```

Address `0xE600` appears to indicate beginning and end of firmware segments, but for our purposes, it doesn't matter.

## grmn0500.dat

Captured while the Programmer Device has an ID of `091e:0500`. It comes in two segments; both appear inside of the .rdata section of `grmn0500.sys` from the Garmin USB drivers.

## grmn1300.dat

Captured after the Programmer Device reconnects and gets an ID of `091e:1300`. The firmware does **not** seem to be in any of the USB driver files; it's not clear where it comes from.
