# JdmTool

A command-line tool for downloading and transferring Jeppesen databases aiming to be compatible with [Jeppesen Distribution Manager](https://ww2.jeppesen.com/data-solutions/jeppesen-distribution-manager/).

It requires a Jeppesen subscription, and currenty supports the following services:
- NavData for Garmin GNS 400/500 Series
  - Requires a Skybound data card programmer (USB ID `0e39:1250`)
  - Requires a 16MB NavData WAAS card or a 4MB NavData non-WAAS card
    - If you have an 8MB data card, please [file a bug](https://github.com/dimaryaz/jdmtool/issues/)!
- NavData and Obstacles for Avidyne IFD 400 Series
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

Make sure you have access to the USB device. On Linux, you should copy `udev/50-garmin.rules` to `/etc/udev/rules.d/` and possibly reload the rules.

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
  Garmin GNS 400/500 Series WAAS - NavData

Transfer to the data card? (y/n) y
Erasing the database: 100%|████████████████████████████████████████| 8.59M/8.59M [02:15<00:00, 63.1KB/s]
Writing the database: 100%|████████████████████████████████████████| 8.59M/8.59M [04:14<00:00, 40.5KB/s]
Verifying the database: 100%|██████████████████████████████████████| 8.59M/8.59M [01:32<00:00, 92.5KB/s]
Writing new metadata: {2303~12345678}
Done
```

### Transfer the database to the USB drive (IFD 440 or G1000)

You can specify a single service ID, or multiple IDs separated by commas.

Note: the final database file requires the FAT32 volume ID of the USB drive. `jdmtool` will attempt to find it automatically - which requires the destination to be an actual FAT32-formatted device, not any random directory. Alternatively, you may set the volume ID manually using the `--vol-id` parameter.

> Getting the volume ID automatically is currently not supported on Mac OS, so you will need to use the `--vol-id` parameter. You can try [these instructions](https://apple.stackexchange.com/questions/408562/how-can-i-get-the-volume-serial-number-of-a-fat-volume) for finding the volume ID.

```
$ jdmtool transfer 0,1 /run/media/user/USB/
Found volume ID: 1234abcd

Selected services:
  Avidyne IFD 400 Series, Bendix King AeroNav Series - NavData
  Avidyne IFD 400 Series, Bendix King AeroNav Series - Obstacles

Transfer to /run/media/user/USB/? (y/n) y
Writing to /run/media/user/USB/navdata.dsf: 100%|██████████████████| 38.0M/38.0M [00:15<00:00, 2.49MB/s]
Updating .jdm...
Done
```

## More Information

- [Experimental support for G1000 Electronic Charts](ElectronicCharts.md)
- [Troubleshooting Skybound data cards (GNS 400/500)](Skybound.md)

## Bugs

Please [file a bug](https://github.com/dimaryaz/jdmtool/issues/) if you run into problems, or if you have a device/service that is not currently supported.
