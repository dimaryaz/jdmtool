# Changelog

## 0.3 (2024-07-28)
- (Very experimental, possibly incomplete) Support for Garmin G1000 Electronic Charts
- Automatically download databases when transferring
- Transfer multiple databases at once
- A few UI improvements

## 0.2 (2024-06-04)
- (Very experimental) Support for Garmin G1000, except for Electronic Charts
- Optional JIT - improves transfer speed for IFD 400 and G1000

## 0.1 (2024-05-25)
- Support for Avidyne IFD 400 series
- Support for 4MB non-WAAS data cards for GNS 400/500 series
- Possibly fix accessing the Skybound device when it has an existing driver

## 0.0.4 (2024-04-12)
- Unbreak Python versions 3.7, 3.8, 3.9, 3.10

## 0.0.3 (2024-03-24)
- Fix refreshing services: the server started requiring the `cov_check` parameter
- Download sff files and keychain (though not used yet)
- Add a helper script for inspecting chartview files

## 0.0.2 (2023-04-10)
- Fix a crash when downloading files without crc32
- Improve UI

## 0.0.1 (2023-03-29)
- First release
- Supports programming GNS 430W
