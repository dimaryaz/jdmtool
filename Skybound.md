## Advanced Features for Skybound Data Cards (GNS 400/500)

These mainly exist for troubleshooting. You should not normally need them.

### Check that the tool can detect the device and the data card:

```
$ jdmtool detect
Found device: Bus 001 Device 049: ID 0e39:1250
Firmware version: 20071203
Card inserted:
  IID: 0x1004100
  Unknown identifier: 0x38001000
```

("Unknown identifier" likely contains the information about what type of card this is, but
I don't have enough information to decode it.)


### Read the metadata (should contain the cycle and the service ID):

JDM seems to only write it to 16MB cards. Not clear if it's actually used for anything.

```
$ jdmtool read-metadata
Found device: Bus 001 Device 045: ID 0e39:1250
Detected data card: 16MB WAAS
Database metadata: {2303~12345678}
```

### Write the metadata (should probably keep the same format):

JDM seems to only write it to 16MB cards. Not clear if it's actually used for anything.

```
$ jdmtool write-metadata '{2303~12345678}'
Found device: Bus 001 Device 045: ID 0e39:1250
Detected data card: 16MB WAAS
Done
```

### Read the current database from the data card:

```
$ jdmtool read-database db.bin
Found device: Bus 001 Device 044: ID 0e39:1250
Detected data card: 16MB WAAS
Reading the database: 100%|████████████████████████████████████████| 8.59M/8.59M [01:33<00:00, 91.6KB/s]
Truncating the file...
Done
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
Erasing the database: 100%|████████████████████████████████████████| 8.59M/8.59M [02:15<00:00, 63.1KB/s]
Writing the database: 100%|████████████████████████████████████████| 8.59M/8.59M [04:14<00:00, 40.5KB/s]
Verifying the database: 100%|██████████████████████████████████████| 8.59M/8.59M [01:32<00:00, 92.5KB/s]
Done
```
