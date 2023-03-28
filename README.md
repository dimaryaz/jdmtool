# JdmTool

A command-line tool for programming Garmin aviation data cards aiming to be compatible with [Jeppesen DistributionManager](https://ww2.jeppesen.com/data-solutions/jeppesen-distribution-manager/).

It requires:
- A GNS 430/530 data card programmer (USB ID `0e39:1250`)
- A 16MB data card

Currently, it has only been tested on Linux with GNS 430 and a Jeppesen NavData database.

## Installing

You may want to create a Python virtual environment using e.g. [virtualenvwrapper](https://pypi.org/project/virtualenvwrapper/).

Install `jdmtool`:

```
git clone https://github.com/dimaryaz/jdmtool
cd jdmtool
pip3 install .
```

Make sure you have access to the USB device. On Linux, you should copy `udev/50-garmin.rules` to `/etc/udev/rules.d/`.

## Usage

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

```
$ jdmtool read-metadata
Found device: Bus 001 Device 045: ID 0e39:1250
Database metadata: {2303~12345678}
```

### Write the metadata (should probably keep the same format):

```
$ jdmtool write-metadata '{2303~12345678}'
Found device: Bus 001 Device 045: ID 0e39:1250
Done
```

### Read the current database from the data card:

```
$ jdmtool read-database db.bin
Found device: Bus 001 Device 044: ID 0e39:1250
Reading the database: 100%|█████████████████████████████████████████████████████████████████████████████| 8.59M/8.59M [01:33<00:00, 91.6KB/s]
Truncating the file...
Done
```

You should now have the database in `db.bin`:

```
$ file db.bin
db.bin: DOS/MBR boot sector, code offset 0x3c+2, OEM-ID "GARMIN10", sectors/cluster 8, FAT  1, root entries 512, sectors 32768 (volumes <=32 MB), sectors/FAT 16, sectors/track 63, heads 255, hidden sectors 63, serial number 0x1102, label: "GARMIN AT  ", FAT (16 bit)
```

### Write a new database to the data card:

This will do some sanity checks to make sure the file is in fact a Garmin database. If it rejects your file, please file a bug to let me know.

```
$ jdmtool write-database dgrw72_2303_eceb0273.bin
Found device: Bus 001 Device 045: ID 0e39:1250
Erasing the database: 100%|█████████████████████████████████████████████████████████████████████████████| 8.59M/8.59M [02:15<00:00, 63.1KB/s]
Writing the database: 100%|█████████████████████████████████████████████████████████████████████████████| 8.59M/8.59M [04:14<00:00, 40.5KB/s]
Done
```

After it is done, you may want to run `jdmtool write-metadata '{...-...}'` to save the new cycle number in the metadata.


## Bugs

This has only been tested with a single card reader and two cards, so chances are, it won't work correctly for others. Please file a bug if you run into problems.
