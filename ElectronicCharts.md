## Electronic Charts

### Experimental Support

Electronic Charts are significantly more complex than other databases, and are not fully reverse-engineered yet. jdmtool produces _almost_ identical results to JDM, but not exactly.

If you are willing to try it, please make sure you have a backup plan - i.e., you can update the database using the official JDM.

If the update goes through, please make sure the expected data is there: approach plates, airport information, NOTAMs, etc.

Please [file a bug](https://github.com/dimaryaz/jdmtool/issues/) to report any problems.

### Technical Info

Unlike other databases that are simply copied to the SD card / USB drive, Electronic Charts require a lot of pre-processing.

Zip files downloaded from Jeppesen contain the following files:

- `[CODE]_Charts.bin` or `[CODE]_VFRCharts.bin`: archives containing actual IFR or VFR charts. The file names and contents depend on the subscription. You can use the `chartview` tool to inspect them (though they are in some unknown proprietary format).
- `sbscrips.dbf`: contains binary data, `CODE` values for filenames above, and some integers. It presumably describes each subscription - but the actual meaning of the data is unknown.
- `regions.dat`: a binary file containing a bunch of records that are related to the subscriptions above. It likely describes the shape of a subscription's region.
- `coverags.dbf`: list of airports by coverage ID. It is not known where coverage IDs are coming from - but presumably from the two files above.
- `charts.ini`: version numbers.
- `charts.dbf` and `vfrchrts.dbf`: worldwide list of IFR and VFR charts with some metadata - in particular, file names in the archives above and airport codes.
- `chrtlink.dbf`: worldwide list of... routes? Each route contains an airport name and a chart name.
- `airports.dbf` and `vfrapts.dbf`: worldwide list of airports, with coordinates, countries, and other info.
- `notams.dbf`, `notams.dbt`, `vfrntms.dbf`, `vfrntms.dbt`: worldwide list of NOTAMs.
- `countries.dbf`: list of country codes and names - doesn't seem to be used for anything?
- `state.dbf`: list of US states - also not used?
- `ctypes.dbf`: chart types; does not need any pre-processing.
- `jeppesen.tfl`, `jeppesen.tls`, `lssdef.tcl`: unknown - but they don't need any pre-processing.
- `crcfiles.txt`: checksums of the files above.
- `Fonts/...`: font files; don't need any pre-processing.

`.dbf` is a somewhat standard format; you can open those files in e.g. LibreOffice.

The official JDM software performs roughly the following steps:
- Combine all `...Charts.bin` files from all Zips into a single `charts.bin`
- Filter the rest of the files so they only contain data for the user's subscription
- Update `crcfiles.txt` with new checksums
  - It includes filtered `regions.dat`, etc. - even though they won't be copied
- Copy the files (except subscription info) to the USB drive
- Create the copy-protection file `featunlk.dat` based on `crcfiles.txt`
- Update `.jdm`

Unfortunately, I don't know how JDM determines the coverage in order to do the filtering. I also don't know if filtering is even important.

Therefore, jdmtool instead performs the following:
- Combine all `...Charts.bin` files from all Zips into a single `charts.bin`
- For each of the original `...Charts.bin` files:
  - Get the list of charts in the file
  - Find the airports of those charts
  - Find the smallest subscription containing those airports
    - It may not be an exact match: a subscription may contain closed airports that have metadata but no charts
- Filter the `.dbf` files based on the subscription guesses above
- Update `crcfiles.txt` with new checksums
  - It will _not_ include checksums for `regions.dat` and the other subscription-related files - however, since they are not copied to the USB drive, I cannot imagine that their checksums could be used for anything
- Copy the files (except subscription info) to the USB drive
- Create the copy-protection file `featunlk.dat` based on `crcfiles.txt`
- Update `.jdm`

Assuming the subscription guessing logic works correctly, jdmtool should produce the same files as the official JDM. However, if you compare the results, you will see the following differences:
- Some `.dbf` files contain the date when they were created; if you run jdmtool and JDM on different days, you will get slightly different files. (Also, it's not clear which timezone JDM uses.)
- JDM has questionable logic and bugs when creating some of the `.dbf` files; jdmtool _mostly_ follows that logic, but not 100%.
- `crcfiles.txt` will be different due to missing checksums for `regions.dat`, etc.
- `featunlk.dat` contains the checksum of `crcfiles.txt`, so will be different.
- `.jdm` contains checkums of all of the above, so will also be different.
