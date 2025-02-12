# JdmTool

A command-line tool for downloading and transferring Jeppesen databases aiming to be compatible with [Jeppesen Distribution Manager](https://ww2.jeppesen.com/data-solutions/jeppesen-distribution-manager/).

It requires a Jeppesen subscription, and currenty supports the following services:
- NavData for Garmin GNS 400/500 Series
  - Requires a data card programmer device:
    - Jeppesen Skybound G2 USB Adapter (USB ID `0e39:1250`), or
    - Garmin USB Aviation Data Card Programmer (USB ID `091e:0500` / `091e:1300`)
  - Requires a NavData card (16MB WAAS, 8MB, 6MB, 4MB, 3MB, 2MB)
- NavData and Obstacles for Avidyne IFD 400 Series
- NavData for Avidyne EX5000
- (Very experimental) Garmin G1000 support
  - If you try it, please [file a bug](https://github.com/dimaryaz/jdmtool/issues/) to report your results - even just to say "it worked".
  - Services other than Electronic Charts should produce byte-for-byte identical results to JDM, and are expected to work.
  - Electronic Charts _may_ work, but are completely untested! See [more info here](ElectronicCharts.md).


It is mainly tested on Linux, but should work on OS X and Windows.

## Installing

You may want to create a Python virtual environment using e.g. [virtualenvwrapper](https://pypi.org/project/virtualenvwrapper/).

Install the latest `jdmtool` release:

```
pip3 install jdmtool
```

Or install the latest code from GitHub:

```
pip3 install "git+https://github.com/dimaryaz/jdmtool.git#egg=jdmtool"
```

### IFD 400 and G1000

You should install an optional Just-in-Time compiler by running:
```
pip3 install jdmtool[jit]
```

This should significantly improve transfer speeds.

### GNS 400/500

You must install usb library: 
```
pip3 install jdmtool[usb]
```

Make sure you have access to the USB device. 

On Linux, see [Installing udev rules on Linux](udev/README.md). 

On Windows, you will need the WinUSB drivers. You do need the official Skybound/Garmin drivers (though they might still work for you).

## Basic Usage

### Log in

You only need to run this once (unless you change your password).

```
$ jdmtool login
Username: test@example.com
Password: 
Logged in successfully
```

### Refresh the list of available downloads

Run this every time you want to download updates.

```
$ jdmtool refresh
Downloading services...
Downloading keychain...
No updates.
```

### View available downloads

```
$ jdmtool list
ID  Name                                                                    Coverage              Version   Start Date  End Date    Downloaded
 0  Garmin GNS 400/500 Series WAAS - NavData                                Americas              2303      2023-03-23  2023-04-20            
 1  Garmin GNS 400/500 Series WAAS - NavData                                Americas              2304      2023-04-20  2023-05-18            
```

### View detailed info

```
$ jdmtool info 0
Aircraft Manufacturer:        LOCKHEED
Aircraft Model:               SR-71
Aircraft Tail Number:         N12345

Avionics:                     Garmin GNS 400/500 Series WAAS
Coverage:                     Americas
Service Type:                 NavData
Service Code:                 DGRW7253
Service ID:                   12345678
Service Renewal Date:         2024-01-01 00:00:00

Version:                      2303
Version Start Date:           2023-03-23 06:00:00
Version End Date:             2023-04-20 06:00:00

Next Version:                 2304
Next Version Available Date:  2023-04-10 06:00:00
Next Version Start Date:      2023-04-20 06:00:00

File Name:                    dgrw72_2303_eceb0273.bin
File Size:                    8443904
File CRC32:                   eceb0273
Serial Number:                
System ID:                    

Downloads:
  /home/user/.local/share/jdmtool/downloads/dgrw72_2303_eceb0273.bin  (missing)
```

### Download the database

This is optional - the next command will automatically download the database as needed - but can be useful if you want to transfer the database when you are offline.

You can specify a single service ID, multiple IDs separated by commas, `curr` for all services that are current, or `next` for all services that are not yet current.

```
$ jdmtool download 0
Downloading: 100%|█████████████████████████████████████████████████| 8.44M/8.44M [00:03<00:00, 2.15MB/s]
Downloaded to /home/user/.local/share/jdmtool/downloads/dgrw72_2303_eceb0273.bin
```

### Transfer the database to the data card (GNS 400/500)

```
$ jdmtool transfer 0
Found device: Bus 001 Device 052: ID 0e39:1250
Detected data card: 16MB WAAS

Selected service:
  Garmin GNS 400/500 Series WAAS - NavData                              2408    2024-08-08 - 2024-09-05

Transfer to the data card? (y/n) y
Blank checking: 100%|██████████████████████████████████████████████| 8.59M/8.59M [00:06<00:00, 1.33MB/s]
Erasing the database: 100%|████████████████████████████████████████| 8.59M/8.59M [02:15<00:00, 63.1KB/s]
Writing the database: 100%|████████████████████████████████████████| 8.59M/8.59M [04:14<00:00, 40.5KB/s]
Verifying the database: 100%|██████████████████████████████████████| 8.59M/8.59M [01:32<00:00, 92.5KB/s]
Done in: 291.9s.
```

### Transfer the database to the USB drive (IFD 440 or G1000)

You can specify a single service ID, multiple IDs separated by commas, `curr` for all services that are current, or `next` for all services that are not yet current.

Note: the final database file requires the FAT32 volume ID of the USB drive. `jdmtool` will attempt to find it automatically - which requires the destination to be an actual FAT32-formatted device, not any random directory. Alternatively, you may set the volume ID manually using the `--vol-id` parameter.

> Getting the volume ID automatically is currently not supported on Mac OS, so you will need to use the `--vol-id` parameter. You can try [these instructions](https://apple.stackexchange.com/questions/408562/how-can-i-get-the-volume-serial-number-of-a-fat-volume) for finding the volume ID.

```
$ jdmtool transfer 0,1 /run/media/user/USB/
Found volume ID: 1234abcd

Selected services:
  Avidyne IFD 400 Series, Bendix King AeroNav Series - NavData          2405      2024-05-17  2024-06-16
  Avidyne IFD 400 Series, Bendix King AeroNav Series - Obstacles        2405      2024-05-17  2024-06-16

Transfer to /run/media/user/USB/? (y/n) y
Writing to /run/media/user/USB/navdata.dsf: 100%|██████████████████| 32.2M/32.2M [00:10<00:00, 3.18MB/s]
Updating .jdm...
Writing to /run/media/user/USB/obstacles.dsf: 100%|████████████████| 2.24M/2.24M [00:02<00:00, 984kB/s]
Updating .jdm...
Done in: 12.5s
```

### Delete expired downloads

You can delete expired downloads by running `clean`:

```
$ jdmtool clean
Found 1 obsolete downloads (8.2MB total):
  /home/user/.local/share/jdmtool/downloads/dgrw72_2408_d1dc1d8c.bin

Delete? (y/n) y
Deleted.
```

### Extract Garmin databases

Garmin subscriptions are not (yet) supported - however, if you get a hold of a `.taw` or `.awp` file, you can extract its contents using the `extract-taw` command:

```
$ jdmtool extract-taw j500a-us-2413.awp
Database type: 190 (G500)
Year: 24
Cycle: 13
Avionics: 'GNS 430W/530W'
Coverage: 'US Garmin Navigation Database'
Type: ''

Extracting nav.bin... Done
```

You can transfer GNS 400/500 NavData databases to a data card using the `write-database` command (see [here](DataCards.md) for more info). Terrain and obstacles databases are not supported, but may be in the near future.

You can extract other database types, but `jdmtool` cannot do anything with them yet.

## More Information

- [Experimental support for G1000 Electronic Charts](ElectronicCharts.md)
- [Troubleshooting data cards (GNS 400/500)](DataCards.md)

## Bugs

Please [file a bug](https://github.com/dimaryaz/jdmtool/issues/) if you run into problems, or if you have a device/service that is not currently supported.
