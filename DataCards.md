## Advanced Features for Data Cards (GNS 400/500)

These mainly exist for troubleshooting. You should not normally need them.

### Check that the tool can detect the device and the data card

#### Skybound G2:

```
$ jdmtool detect
Found a Skybound device at Bus 001 Device 053: ID 0e39:1250
Firmware version: 20071203 (G2 Black)
Card type: 16MB WAAS (silver), 4 chips of 4MB
```

Note that a black-label adapter will be able to detect and read orange-label cards, but not write to them.

#### Garmin Data Card Programmmer:

```
$ jdmtool detect
Found an un-initialized Garmin device at Bus 001 Device 054: ID 091e:0500
Writing stage 1 firmware...
Re-scanning devices...
Found at Bus 001 Device 055: ID 091e:1300
Writing stage 2 firmware...
Re-scanning devices...
Found at Bus 001 Device 056: ID 091e:1300
Firmware version: Aviation Card Programmer Ver 3.05 Apr 01 2024 08:42:10
Card type: 16MB WAAS (silver), 4 chips of 4MB
```

jdmtool needs to update the adapter's firmware, twice, every time it is plugged in.

### Read the current database from the data card:

```
$ jdmtool read-database db.bin
Found device: Bus 001 Device 044: ID 0e39:1250
Detected data card: 16MB WAAS
Reading the database: 100%|████████████████████████████████████████| 8.59M/8.59M [01:33<00:00, 91.6KB/s]
Done in: 93.1s.
```

You should now have the database in `db.bin`.

WAAS databases appear to have a DOS boot sector:

```
$ file db.bin
db.bin: DOS/MBR boot sector, code offset 0x3c+2, OEM-ID "GARMIN10", sectors/cluster 8, FAT  1, root entries 512, sectors 32768 (volumes <=32 MB), sectors/FAT 16, sectors/track 63, heads 255, hidden sectors 63, serial number 0x1102, label: "GARMIN AT  ", FAT (16 bit)
```

(Non-WAAS databases don't seem to have it.)

The created file may not match the original downloaded file exactly. There is no way to know the size of the database on the data card, so either `db.bin` or the original file will likely contain extra `\xFF` bytes at the end.

### Write a new database to the data card:

```
$ jdmtool write-database dgrw72_2303_eceb0273.bin
Found device: Bus 001 Device 045: ID 0e39:1250
Detected data card: 16MB WAAS
Transfer dgrw72_2303_eceb0273.bin to the data card? (y/n) y
Blank checking: 100%|██████████████████████████████████████████████| 8.59M/8.59M [00:06<00:00, 1.33MB/s]
Erasing the database: 100%|████████████████████████████████████████| 8.59M/8.59M [02:15<00:00, 63.1KB/s]
Writing the database: 100%|████████████████████████████████████████| 8.59M/8.59M [04:14<00:00, 40.5KB/s]
Verifying the database: 100%|██████████████████████████████████████| 8.59M/8.59M [01:32<00:00, 92.5KB/s]
Done in: 291.9s.
```
