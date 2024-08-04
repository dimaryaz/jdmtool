# Firmware Data

## grmn0500.dat

Installed USB Drivers 2.3.1.2 from https://www8.garmin.com/support/download_details.jsp?id=591, then used `objcopy` to extract the data:

```bash
objcopy -O binary -j .data Garmin/USB_Drivers/Aviation_Drivers/amd64/grmn0500.sys grmn0500.dat
```

## grmn1300.dat

You'd think it would be the same as above, but from `grmn1300.sys`... but no, I could not find the firmware in any of the drivers. I had to replay the USB packet capture, and write it to a file in the same format as the above (i.e., `struct.pack('<HHx16sx', len(data), addr, data)`).
