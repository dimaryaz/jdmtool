"""EX500/EX5000: native IFR/VFR tables, using only supplied subscriptions."""
from dataclasses import dataclass
import configparser
import html
import datetime
import io
import pathlib
import re
import struct
import zipfile

from .checksum import crc32q_checksum
from .dbf import DbtFile
from .chartview import ChartView, ChartHeader
import tempfile


class RawDbf:
    def __init__(self, data):
        if len(data) < 33:
            raise ValueError('Truncated DBF')
        self.count = struct.unpack_from('<I', data, 4)[0]
        self.header_size, self.record_size = struct.unpack_from('<HH', data, 8)
        if self.header_size < 33 or self.record_size < 1 or self.header_size + self.count * self.record_size > len(data):
            raise ValueError('Invalid DBF sizes')
        self.header = bytearray(data[:self.header_size])
        self.fields = {}
        offset = 1
        for p in range(32, self.header_size - 1, 32):
            name = data[p:p+11].split(b'\0')[0].decode('ascii')
            length = data[p+16]
            self.fields[name] = (offset, length)
            offset += length
        if offset != self.record_size:
            raise ValueError('Invalid DBF record size')
        self.rows = [bytearray(data[p:p+self.record_size])
                     for p in range(self.header_size, self.header_size+self.count*self.record_size, self.record_size)
                     if data[p:p+1] == b' ']

    def get(self, row, name):
        offset, length = self.fields[name]
        return bytes(row[offset:offset+length]).decode('latin1').strip(' ')

    def set(self, row, name, value):
        offset, length = self.fields[name]
        value = str(value).encode('latin1')
        if len(value) > length:
            raise ValueError('Value too long for DBF field ' + name)
        row[offset:offset+length] = value.ljust(length, b' ')

    def encode(self, rows, eof=False, date=None):
        header = self.header.copy()
        struct.pack_into('<I', header, 4, len(rows))
        if date is not None:
            header[1:4] = bytes((date.year-1900, date.month, date.day))
        return bytes(header) + b''.join(rows) + (b'\x1a' if eof else b'')


def code_to_key(code):
    if not re.fullmatch(r'[0-9]{3}[A-Z]{2}[0-9]{5}', code):
        raise ValueError('Invalid subscription code: ' + code)
    end = int(code[5:])
    value = ((int(code[:3]) << 8) | ((ord(code[3])-65) << 20)
             | ((ord(code[4])-65) << 16) | ((end & 0xffffff00) << 16) | (end & 255)) & 0xffffffff
    key = 0
    for i in range(8):
        shift = 2*(i+1)
        key |= ((value & (3 << (2*i))) << shift) & 0xffffffff
        key |= (value & (0xc0000000 >> (2*i))) >> shift
    return str(key & 0xffffffff)


def encoded_serial(serial):
    """JDM NUM representation of the service's unchanged serial number."""
    serial = serial.replace('-', '').upper().encode('ascii')
    if len(serial) != 16:
        raise ValueError('Expected a 16-character Avidyne serial number')
    return bytes((byte ^ b'\x18\x4c\x2b\x7a'[i % 4]) or 0x80 for i, byte in enumerate(serial))


@dataclass
class Ex5000Metadata:
    charts_files: list
    fonts_files: list
    db_begin_date: str
    subscriptions: list  # ordered (code, is_vfr), IFR before VFR
    airport_report: pathlib.Path = None


def _notams(read, base, airports, countries):
    table = RawDbf(read(base + '.dbf'))
    names = list(table.fields)
    country_field, airport_field, memo_field = names[0], names[2], names[3]
    selected = []
    output = io.BytesIO()
    with io.BytesIO(read(base + '.dbt')) as source:
        header = DbtFile.read_header(source)
        next_memo = 1
        for row in table.rows:
            airport = table.get(row, airport_field)
            if (airport in airports) if airport else (table.get(row, country_field) in countries):
                memo = DbtFile.read_record(source, header, int(table.get(row, memo_field)))
                table.set(row, memo_field, next_memo)
                next_memo += DbtFile.write_record(output, header, next_memo, memo)
                selected.append(row)
        output.seek(0, 2)
        DbtFile.write_header(output, header)
    selected.sort(key=lambda r: (table.get(r, country_field), table.get(r, airport_field) or '\xff'))
    return table.encode(selected), output.getvalue()


def write_airport_report(service, charts_files, fonts_files, destination):
    optional = getattr(service, 'get_optional_property', lambda name, default=None: default)
    if optional('oem_subsetreport_cardwrite', '0') != '1':
        return None
    airports = {}
    for path in charts_files:
        if path.name not in ('airports.dbf', 'vfrapts.dbf'):
            continue
        table = RawDbf(path.read_bytes())
        name_field = 'ARPT_NAME' if 'ARPT_NAME' in table.fields else 'ARPT_NAM_N'
        city_field = 'CITY' if 'CITY' in table.fields else 'CITY_NAM_N'
        for row in table.rows:
            ident = table.get(row, 'F5_6')
            airports.setdefault(ident, (table.get(row, name_field), table.get(row, city_field), table.get(row, 'J5_3')))
    def escape(value):
        return html.escape(str(value), quote=True)
    rows = []
    for ident, (name, city, country) in sorted(airports.items()):
        location = ', '.join(value for value in (city, country) if value)
        rows.append('<tr><th scope="row">' + escape(ident) + '</th><td>' + escape(name)
                    + '</td><td>' + escape(location) + '</td><td>Yes</td></tr>')
    aircraft = '-'.join(value for value in (optional('oracle_aircraft_tail_number', ''),
                                           optional('oracle_aircraft_model', '')) if value)
    size = sum(path.stat().st_size for path in charts_files + fonts_files)
    values = {
        '@@TOTAL@@': str(len(airports)), '@@SIZE@@': f'{size / (1024*1024):.1f} MB',
        '@@ROWS@@': '\n'.join(rows), '@@AIRCRAFT@@': escape(aircraft),
        '@@CYCLE@@': escape(optional('display_version', '')),
        '@@AVIONICS@@': escape(optional('avionics', '')),
        '@@COVERAGE@@': escape(optional('coverage_desc', '')),
        '@@CREATED@@': datetime.datetime.now(datetime.timezone.utc).strftime('%d-%b-%Y %H:%M:%S'),
    }
    report = AIRPORT_REPORT_TEMPLATE
    for token, value in values.items():
        report = report.replace(token, value)
    path = pathlib.Path(destination) / 'AirportReport.html'
    path.write_text(report, encoding='utf-8')
    return path


def prepare_ex5000(service, destination, progress_cb=lambda size: None):
    """Prepare at most one coverage per family; reject ambiguous input explicitly."""
    charts = pathlib.Path(destination) / 'Charts'
    fonts = pathlib.Path(destination) / 'Fonts'
    charts.mkdir(parents=True, exist_ok=True)
    fonts.mkdir(parents=True, exist_ok=True)
    product = service.get_property('chart_product_code')
    serial = service.get_property('serial_number')
    if not re.fullmatch(r'[0-9]{3}', product):
        raise ValueError('Invalid chart_product_code')
    inputs = []
    for database in service.get_databases():
        with zipfile.ZipFile(database.dest_path) as archive:
            binaries = [p for p in archive.namelist()
                        if re.fullmatch(r'[0-9]{3}[A-Z]{2}[0-9]{5}_(VFR)?Charts\.bin', pathlib.PurePosixPath(p).name, re.I)]
            for binary in binaries:
                name = pathlib.PurePosixPath(binary).name
                inputs.append((name[:10].upper(), '_vfrcharts.bin' in name.lower(), database.dest_path, binary))
    inputs = sorted(set(inputs), key=lambda item: (item[1], item[0]))
    if not inputs or len({is_vfr for _, is_vfr, _, _ in inputs}) != len(inputs):
        raise ValueError('EX5000 expects one IFR and/or one VFR coverage; duplicate families require explicit selection')
    outputs = {}
    shared = {}
    all_subscriptions = []
    all_coverage = []
    subscription_table = coverage_table = None
    combine = getattr(service, 'get_optional_property', lambda name, default: default)('combine_ifr_vfr', '0') == '1'
    print('EX5000 mode: ' + ('IFR/VFR combined' if combine else 'IFR/VFR separate'), flush=True)
    selected_airports = {False: set(), True: set()}
    begin_date = None
    link_table = None
    ifr_airport_info = None
    for code, is_vfr, archive_path, binary in inputs:
        with zipfile.ZipFile(archive_path) as archive:
            entries = {}
            for name in archive.namelist():
                if name.endswith('/'):
                    continue
                base = pathlib.PurePosixPath(name).name.lower()
                if base not in entries or '/' not in name:
                    entries[base] = name
            def read(name):
                try:
                    return archive.read(entries[name.lower()])
                except KeyError:
                    raise ValueError(f'Missing {name} in {archive_path}') from None
            config_data = read('charts.ini')
            cfg = configparser.ConfigParser()
            cfg.read_string(config_data.decode())
            current_date = cfg['CHARTS']['Database_Begin_Date']
            if begin_date is not None and current_date != begin_date:
                raise ValueError('IFR and VFR cycles do not have the same begin date')
            begin_date = current_date
            shared['charts.ini'] = config_data
            subs = RawDbf(read('sbscrips.dbf'))
            candidates = [r for r in subs.rows if subs.get(r, 'CODE') == code
                          and subs.get(r, 'PROD') == product]
            if not candidates:
                raise ValueError(f'{code}: missing product {product} in the subscription catalog')
            # Source catalog NUM values are not the OEM serial. JDM personalizes
            # the selected CODE/PROD row for the serial already supplied by the service.
            row = candidates[0].copy()
            offset, length = subs.fields['NUM']
            if length < 16:
                raise ValueError('NUM field is too short')
            row[offset:offset+length] = encoded_serial(serial).ljust(length, b' ')
            matches = [row]
            all_subscriptions.extend(matches)
            subscription_table = subscription_table or subs
            coverage = RawDbf(read('coverags.dbf'))
            key = code_to_key(code)
            selected = [r for r in coverage.rows if coverage.get(r, 'KEY') == key]
            if not selected:
                raise ValueError(f'{code}: no coverage rows for KEY {key}')
            all_coverage.extend(selected)
            coverage_table = coverage_table or coverage
            airports = {coverage.get(r, 'F5_6') for r in selected}
            selected_airports[is_vfr] = airports
            airport_name = 'vfrapts.dbf' if is_vfr else 'airports.dbf'
            chart_name = 'vfrchrts.dbf' if is_vfr else 'charts.dbf'
            notam_base = 'vfrntms' if is_vfr else 'notams'
            chart_table = RawDbf(read(chart_name))
            rows = [r for r in chart_table.rows if chart_table.get(r, 'F5_6') in airports]
            rows.sort(key=lambda r: chart_table.get(r, 'F5_6'))
            chart_indices = {}
            for index, row in enumerate(rows, 1):
                chart_indices.setdefault(chart_table.get(row, 'F5_6'), index)
            outputs[chart_name] = chart_table.encode(rows)
            airport_table = RawDbf(read(airport_name))
            airport_rows = [r for r in airport_table.rows if airport_table.get(r, 'F5_6') in airports]
            if {airport_table.get(r, 'F5_6') for r in airport_rows} != airports:
                raise ValueError(f'{code}: coverage includes airports missing from {airport_name}')
            if not is_vfr:
                links = RawDbf(read('chrtlink.dbf'))
                link_table = links
                link_rows = [r for r in links.rows if links.get(r, 'F5_6') in airports]
                link_indices = {}
                for index, row in enumerate(link_rows, 1):
                    link_indices.setdefault(links.get(row, 'F5_6'), index)
                shared['chrtlink.dbf'] = links.encode(link_rows)
            for row in airport_rows:
                ident = airport_table.get(row, 'F5_6')
                airport_table.set(row, 'CHRT_IDX', chart_indices.get(ident, ''))
                if not is_vfr:
                    airport_table.set(row, 'CLNK_IDX', link_indices.get(ident, ''))
            airport_rows.sort(key=lambda r: airport_table.get(r, 'F5_6'))
            outputs[airport_name] = airport_table.encode(airport_rows)
            if not is_vfr:
                ifr_airport_info = (airport_table, airport_rows)
            countries = {airport_table.get(r, 'J5_3') for r in airport_rows}
            outputs[notam_base+'.dbf'], outputs[notam_base+'.dbt'] = _notams(read, notam_base, airports, countries)
            binary_name = 'vfrcharts.bin' if is_vfr else 'charts.bin'
            # JDM orders physical chart blocks by index even for one coverage.
            # Reuse the existing writer per family, never merge IFR with VFR.
            if not combine:
                with tempfile.TemporaryDirectory(dir=destination, prefix='ex5000_bin_') as work:
                    with ChartView([archive_path]) as cv:
                        with archive.open(binary) as header_fd:
                            native_date = ChartHeader.from_bytes(header_fd.read(ChartHeader.SIZE)).db_begin_date
                        cv.process_charts_bin(pathlib.Path(work), native_date, progress_cb)
                    (pathlib.Path(work)/'charts.bin').replace(charts/binary_name)
            for name in ['jeppesen.tfl', 'jeppesen.tls', 'lssdef.tcl']:
                value = read(name)
                if name in shared and shared[name] != value:
                    raise ValueError('IFR/VFR shared file differs: ' + name)
                shared[name] = value
            for name in ('country.dbf','ctypes.dbf','regions.dat','state.dbf'):
                if name in entries and name not in shared:
                    shared[name] = read(name)
            for member in archive.namelist():
                if member.lower().startswith('fonts/') and not member.endswith('/'):
                    target = fonts/pathlib.PurePosixPath(member).name.lower()
                    value = archive.read(member)
                    if target.exists() and target.read_bytes() != value:
                        raise ValueError('Conflicting IFR/VFR font: ' + target.name)
                    target.write_bytes(value)
    if combine:
        # Reuse the existing combined ChartView pipeline, but with the exact
        # CODE/KEY selections rather than guessed subscriptions.
        archives = list(dict.fromkeys(item[2] for item in inputs))
        with ChartView(archives) as cv:
            cv.process_charts_bin(charts, begin_date, progress_cb)
            chart_indices = cv.process_charts(selected_airports[False], selected_airports[True], charts)
            link_indices = cv.process_chartlink(selected_airports[False], selected_airports[True], charts)
            countries_ifr, countries_vfr = cv.process_airports(
                selected_airports[False], selected_airports[True], chart_indices, link_indices, charts)
            cv.process_notams(selected_airports[False], selected_airports[True], countries_ifr, countries_vfr, charts)
        shared['chrtlink.dbf'] = (charts/'chrtlink.dbf').read_bytes()
        outputs = {name: (charts/name).read_bytes() for name in
                   ('airports.dbf', 'charts.dbf', 'notams.dbf', 'notams.dbt')}
    else:
        # The IFR CLNK_IDX follows the shared IFR+VFR subset, even though the
        # EX5000 package does not copy chrtlink.dbf itself.
        if link_table is not None:
            union_airports = {coverage_table.get(row, 'F5_6') for row in all_coverage}
            link_rows = [row for row in link_table.rows if link_table.get(row, 'F5_6') in union_airports]
            indices = {}
            for index, row in enumerate(link_rows, 1):
                indices.setdefault(link_table.get(row, 'F5_6'), index)
            shared['chrtlink.dbf'] = link_table.encode(link_rows)
            table, rows = ifr_airport_info
            for row in rows:
                table.set(row, 'CLNK_IDX', indices.get(table.get(row, 'F5_6'), ''))
            outputs['airports.dbf'] = table.encode(rows)
    outputs['sbscrips.dbf'] = subscription_table.encode(all_subscriptions, eof=True, date=datetime.datetime.now(datetime.timezone.utc).date())
    outputs['coverags.dbf'] = coverage_table.encode(all_coverage)
    for name in ('charts.ini','jeppesen.tfl','jeppesen.tls','lssdef.tcl'):
        outputs[name] = shared[name]
    crc_names = ['airports.dbf','charts.dbf','charts.ini','chrtlink.dbf','country.dbf','coverags.dbf',
                 'ctypes.dbf','jeppesen.tfl','jeppesen.tls','lssdef.tcl','notams.dbf','notams.dbt',
                 'regions.dat','sbscrips.dbf','state.dbf','vfrapts.dbf','vfrchrts.dbf','vfrntms.dbf','vfrntms.dbt']
    checksums = {**shared, **outputs}
    outputs['crcfiles.txt'] = ''.join(f'{name},0x{crc32q_checksum(checksums[name]):08x}\r\n'
                                    for name in crc_names if name in checksums).encode('ascii')
    for name, data in outputs.items():
        (charts/name).write_bytes(data)
    binary_names = ['charts.bin'] if combine else ['vfrcharts.bin' if is_vfr else 'charts.bin' for _, is_vfr, _, _ in inputs]
    chart_paths = [charts/name for name in sorted(set(outputs) | set(binary_names))]
    versions = [(inputs[0][0], False)] if combine else [(code, is_vfr) for code, is_vfr, _, _ in inputs]
    font_paths = sorted(fonts.iterdir())
    report = write_airport_report(service, chart_paths, font_paths, destination)
    return Ex5000Metadata(chart_paths, font_paths, begin_date, versions, report)


AIRPORT_REPORT_TEMPLATE = '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Strict//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-strict.dtd">\n<html lang="en">\n<head>\n<meta charset="UTF-8">\n<meta http-equiv="X-UA-Compatible" content="IE=edge">\n<meta name="viewport" content="width=device-width, initial-scale=1">\n<meta name="subscriptionairportstotal" content="@@TOTAL@@">\n<meta name="subsetted_dataset_size" content="@@SIZE@@">\n<title>JDM Subset Report</title>\n</head>\n<body>\n<header>\n  <div class="bg-light p-3">\n    <div class="container-fluid">\n      <h2 class="text-center mb-4">JDM Charts - Included/Excluded Airports</h2>\n      <div class="row">\n        <div class="col-6 col-sm-3">\n          <h5 class="text-right">Aircraft:</h5>\n        </div>\n        <div class="col-6 col-sm-2"> @@AIRCRAFT@@ </div>\n        <div class="col-6 col-sm-4">\n          <h5 class="text-right">Current Cycle:</h5>\n        </div>\n        <div class="col-6 col-sm-3"> @@CYCLE@@</div>\n      </div>\n      <div class="row">\n        <div class="col-6  col-sm-3 col-md-3">\n          <h5 class="text-right">Avionics:</h5>\n        </div>\n        <div class="col-6  col-sm-2 col-md-3"> @@AVIONICS@@ </div>\n        <div class="col-6 col-sm-4 col-md-3">\n          <h5 class="text-right">Subset Applied:</h5>\n        </div>\n        <div class="col-6 col-sm-3 col-md-3">no</div>\n      </div>\n      <div class="row">\n        <div class="col-6 col-sm-3 col-md-3">\n          <h5 class="text-right">Coverage:</h5>\n        </div>\n        <div class="col-6 col-sm-2 col-md-3">@@COVERAGE@@</div>\n        <div class="col-6 col-sm-4 col-md-3">\n          <h5 class="text-right">Runway Filter:</h5>\n        </div>\n        <div class="col-6 col-sm-3 col-md-3">0 </div>\n      </div>\n      <div class="row">\n        <div class="col-6 col-sm-3 col-md-3">\n          <h5 class="text-right">Included Airports:</h5>\n        </div>\n        <div class="col-6 col-sm-2 col-md-3"> </div>\n      </div>\n    </div>\n  </div>\n</header>\n<section>\n  <div class="container-fluid">\n    <div>&nbsp;</div>\n  </div>\n</section>\n<div class="card">\n  <div class="card-header d-flex pr-0">\n    <h6 class="card-title mb-0">@@TOTAL@@ Included / 0 Excluded</h6>\n    <div class="card-actions ml-auto py-0">\n      <span class="align-self-center mb-1 mr-5">Created: @@CREATED@@ UTC</span>\n    </div>\n  </div>\n  <div class="table-responsive">\n    <table class="table">\n      <thead>\n        <tr>\n          <th scope="col"><span class="aptListHdr">ICAO </span></th>\n          <th scope="col"><span class="aptListHdr">Airport Name </span></th>\n          <th scope="col"><span class="aptListHdr">Location </span></th>\n          <th scope="col" nowrap=""><span class="aptListHdr">Included? </span></th>\n        </tr>\n      </thead>\n      <tbody>\n@@ROWS@@\n</tbody>\n    </table>\n  </div>\n  <hr class="my-0 w-100">\n</div>\n<footer class="text-center">\n  <hr>\n  <div class="container">\n    <div class="row">\n      <div class="col-12">\n        <p>Copyright © 2020 JEPPESEN. All Rights Reserved.</p>\n      </div>\n    </div>\n  </div>\n</footer>\n<style>\nbody {\n  text-align: left;\n  text-align: start;\n  background-color: white;\n  color: rgba(0, 0, 0, 0.87);\n  font-family: Roboto, -apple-system, BlinkMacSystemFont, "Segoe UI", "Helvetica Neue", Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol";\n  font-size: 0.875rem;\n  -moz-osx-font-smoothing: grayscale;\n  -webkit-font-smoothing: antialiased;\n  font-weight: 400;\n  line-height: 1.828572;\n  margin: 0;\n}\nhtml {\n  box-sizing: border-box;\n  font-family: sans-serif;\n  line-height: 1.15;\n  -webkit-text-size-adjust: 100%;\n  -moz-text-size-adjust: 100%;\n  -ms-text-size-adjust: 100%;\n  text-size-adjust: 100%;\n  -ms-overflow-style: scrollbar;\n  -webkit-tap-highlight-color: transparent;\n}\n\n*,\n*::after,\n*::before {\n  box-sizing: inherit;\n}\n\nh2,\nh5,\nh6 {\n  color: inherit;\n  font-family: inherit;\n  margin-top: 0;\n  margin-bottom: 0.5rem;\n}\nh2 {\n  font-size: 2.125rem;\n  font-weight: 400;\n  letter-spacing: 0;\n  line-height: 1.176471;\n}\nh5 {\n  font-size: 1.0rem;\n  font-weight: 400;\n  letter-spacing: 0.04em;\n  line-height: 1.5;\n}\nh6 {\n  font-size: 0.875rem;\n  font-weight: 500;\n  letter-spacing: 0;\n  line-height: 1.428572;\n}\n.container-fluid {\n  margin-right: auto;\n  margin-left: auto;\n  padding-right: 20px;\n  padding-left: 20px;\n  width: 100%;\n}\n.row {\n  display: -ms-flexbox;\n  display: flex;\n  -ms-flex-wrap: wrap;\n  flex-wrap: wrap;\n  margin-right: -20px;\n  margin-left: -20px;\n}\n.mb-4 {\n  margin-bottom: 1.5rem !important;\n}\n.col-6, .col-12, .col-sm-2, .col-sm-3, .col-sm-4, .col-sm-8, .col-md-3, .col-md-6, .col-lg-5 {\n  min-height: 1px;\n  padding-right: 20px;\n  padding-left: 20px;\n  position: relative;\n  width: 100%;\n}\n.col-6 {\n  -ms-flex: 0 0 50%;\n  flex: 0 0 50%;\n  max-width: 50%;\n}\n.col-sm-2 {\n  -ms-flex: 0 0 16.666667%;\n  flex: 0 0 16.666667%;\n  max-width: 16.666667%;\n}\n.col-sm-3 {\n  -ms-flex: 0 0 25%;\n  flex: 0 0 25%;\n  max-width: 25%;\n}\n.col-sm-4 {\n  -ms-flex: 0 0 33.333333%;\n  flex: 0 0 33.333333%;\n  max-width: 33.333333%;\n}\n.col-sm-8 {\n  -ms-flex: 0 0 66.666667%;\n  flex: 0 0 66.666667%;\n  max-width: 66.666667%;\n}\n.col-md-3 {\n  -ms-flex: 0 0 25%;\n  flex: 0 0 25%;\n  max-width: 25%;\n}\n.col-md-6 {\n  -ms-flex: 0 0 50%;\n  flex: 0 0 50%;\n  max-width: 50%;\n}\n.col-lg-5 {\n  -ms-flex: 0 0 41.666667%;\n  flex: 0 0 41.666667%;\n  max-width: 41.666667%;\n}\n\n.text-center {\n  text-align: center !important;\n}\n.text-right {\n  text-align: right !important;\n}\na.bg-light:active, a.bg-light:focus, a.bg-light:hover {\n  background-color: #e0e0e0 !important;\n}\n\n.bg-light {\n  background-color: #f5f5f5 !important;\n}\n.p-3 {\n  padding: 1rem !important;\n}\n.my-3 {\n  margin-top: 1rem !important;\n  margin-bottom: 1rem !important;\n}\n.mb-0 {\n  margin-bottom: 0 !important;\n}\n.card {\n  border-radius: 2px;\n  background-color: #ffffff;\n  box-shadow: 0 0 4px 0 rgba(0, 0, 0, 0.14), 0 3px 4px 0 rgba(0, 0, 0, 0.12), 0 1px 5px 0 rgba(0, 0, 0, 0.2);\n  display: -ms-flexbox;\n  display: flex;\n  -ms-flex-direction: column;\n  flex-direction: column;\n  min-width: 0;\n  position: relative;\n  word-wrap: break-word;\n}\n.card-header {\n  border-bottom: 1px solid rgba(0, 0, 0, 0.12);\n  margin-bottom: 0;\n  padding: 1rem 1rem;\n}\n.card-title {\n  font-size: 1.5rem;\n  font-weight: 400;\n  letter-spacing: 0;\n  line-height: 1.333334;\n  margin-bottom: 1rem;\n}\n.py-0 {\n  padding-top: 0 !important;\n  padding-bottom: 0 !important;\n}\n.mr-5 {\n  margin-right: 3rem !important;\n}\n.mb-5 {\n  margin-bottom: 3rem !important;\n}\n.ml-auto {\n  margin-left: auto !important;\n}\n\n.d-flex {\n  display: -ms-flexbox !important;\n  display: flex !important;\n}\n\n.table-responsive {\n  display: block;\n  overflow-x: auto;\n  width: 100%;\n  -ms-overflow-style: -ms-autohiding-scrollbar;\n  user-select: none;\n}\ntable {\n  border-collapse: collapse;\n}\n\nth {\n  text-align: left;\n  text-align: start;\n}\n\n.table {\n  background-color: #ffffff;\n  border: 0;\n  margin-bottom: 1rem;\n  max-width: 100%;\n  width: 100%;\n}\n\n.table td,\n.table th {\n  border-top: 1px solid #e1e1e1;\n  line-height: 1.428572;\n  padding-right: 1.75rem;\n  padding-left: 1.75rem;\n  vertical-align: top;\n}\n\n.table td:first-child,\n.table th:first-child {\n  padding-left: 1.5rem;\n}\n\n.table td:last-child,\n.table th:last-child {\n  padding-right: 1.5rem;\n}\n\n.table tbody {\n  color: rgba(0, 0, 0, 0.87);\n}\n\n.table tbody td,\n.table tbody th {\n  font-size: 0.8125rem;\n  font-weight: 400;\n  height: 3rem;\n  padding-top: 0.919643rem;\n  padding-bottom: 0.919643rem;\n}\n.table thead {\n  color: rgba(0, 0, 0, 0.54);\n}\n\n.table thead td,\n.table thead th {\n  font-size: 0.75rem;\n  font-weight: 500;\n  height: 3.5rem;\n  padding-top: 1.214285rem;\n  padding-bottom: 1.214285rem;\n}\n.aptListHdr {\n  font-weight: 900;\n  font-size: large;\n}\n</style>\n</body></html>\n'
