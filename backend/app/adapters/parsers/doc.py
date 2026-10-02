"""Legacy OLE DOC conversion bridge. Production admission remains gated."""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import time
from xml.etree import ElementTree as ET
from backend.app.adapters.parsers.native import DocxParser, MAX_INPUT
from backend.app.domain.parsers import ParserError

OLE_MAGIC = bytes.fromhex('d0cf11e0a1b11ae1')
MAX_DISK = 32 * 1024 * 1024
MAX_LOG = 16 * 1024


def validate_ole(raw: bytes) -> None:
    # Container admission only, not a new CFB or Word content parser. The forced
    # MS Word 97 importer must subsequently recognize a supported Word document.
    if len(raw) > MAX_INPUT:
        raise ParserError('DOC_INPUT_LIMIT')
    if (len(raw) < 512 or raw[:8] != OLE_MAGIC or raw[8:24] != bytes(16)
            or raw[28:30] != b'\xfe\xff' or raw[32:34] != b'\x06\x00'
            or (int.from_bytes(raw[26:28], 'little'), int.from_bytes(raw[30:32], 'little')) not in {(3, 9), (4, 12)}
            or len(raw) % (1 << int.from_bytes(raw[30:32], 'little'))):
        raise ParserError('DOC_OLE_SIGNATURE_UNSUPPORTED')


def write_profile(profile: Path) -> None:
    user = profile / 'user'
    user.mkdir(parents=True)
    ns = 'http://openoffice.org/2001/registry'
    ET.register_namespace('oor', ns)
    tree = ET.Element('{'+ns+'}items')
    settings = {
        '/org.openoffice.Office.Common/Security/Scripting': {
            'DisableMacrosExecution': 'true', 'MacroSecurityLevel': '3',
            'DisableActiveContent': 'true', 'BlockUntrustedRefererLinks': 'true',
            'DisableOLEAutomation': 'true'},
        '/org.openoffice.Office.Writer/Content/Update': {
            'Link': '2', 'Field': 'false', 'Chart': 'false'},
    }
    for path, values in settings.items():
        item = ET.SubElement(tree, 'item', {'{'+ns+'}path': path})
        for name, value in values.items():
            prop = ET.SubElement(item, 'prop', {'{'+ns+'}name': name,
                '{'+ns+'}op': 'fuse', '{'+ns+'}finalized': 'true'})
            ET.SubElement(prop, 'value').text = value
    ET.ElementTree(tree).write(user / 'registrymodifications.xcu', encoding='utf-8', xml_declaration=True)


@dataclass(frozen=True)
class ConvertedDocx:
    data: bytes
    converter_version: str


class LibreOfficeDocConverter:
    def __init__(self, *, binary: Path, temp_root: Path, timeout: float = 30,
                 isolation_verified: bool = False):
        if not 0 < timeout <= 60:
            raise ValueError('conversion timeout must be within 60 seconds')
        self.binary = Path(binary)
        self.temp_root = Path(temp_root)
        self.timeout = timeout
        # Trusted operator configuration, never inferred from headless/version.
        # Requires externally verified filesystem/network/process isolation.
        self.isolation_verified = isolation_verified

    def _run(self, arguments: list[str], directory: Path) -> bytes:
        if os.name != 'posix':
            raise ParserError('DOC_PROCESS_ISOLATION_UNSUPPORTED')
        log = directory / 'process.log'
        process = None
        try:
            with log.open('wb') as stream:
                process = subprocess.Popen(arguments, shell=False, cwd=directory,
                    stdin=subprocess.DEVNULL, stdout=stream, stderr=stream,
                    start_new_session=True, close_fds=True,
                    env={'HOME': str(directory / 'home'), 'TMPDIR': str(directory),
                         'XDG_CACHE_HOME': str(directory / 'cache'),
                         'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'SAL_USE_VCLPLUGIN': 'svp'})
                start = time.monotonic()
                while True:
                    if log.stat().st_size > MAX_LOG or sum(p.stat().st_size for p in directory.rglob('*') if p.is_file()) > MAX_DISK:
                        raise ParserError('DOC_CONVERSION_OUTPUT_LIMIT')
                    if (directory / 'source.docx').exists() and (directory / 'source.docx').stat().st_size > MAX_INPUT:
                        raise ParserError('DOC_CONVERSION_OUTPUT_LIMIT')
                    if time.monotonic() - start > self.timeout:
                        raise ParserError('DOC_CONVERSION_TIMEOUT')
                    if process.poll() is not None:
                        break
                    time.sleep(0.05)
                if process.wait(timeout=1) != 0:
                    raise ParserError('DOC_CONVERSION_FAILED')
            return log.read_bytes()
        except (OSError, subprocess.SubprocessError) as exc:
            raise ParserError('DOC_CONVERSION_FAILED') from exc
        finally:
            if process is not None:
                # Also kills surviving children of a successfully exited launcher.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()

    def convert(self, raw: bytes) -> ConvertedDocx:
        if not self.isolation_verified:
            raise ParserError('DOC_CONVERSION_SAFETY_NOT_VERIFIED')
        validate_ole(raw)
        if not self.binary.is_absolute() or not self.binary.is_file() or not self.temp_root.is_absolute() or not self.temp_root.is_dir():
            raise ParserError('DOC_CONVERSION_RUNTIME_UNAVAILABLE')
        try:
            with tempfile.TemporaryDirectory(prefix='rag-doc-', dir=self.temp_root) as name:
                directory = Path(name)
                profile = directory / 'profile'
                write_profile(profile)
                version_log = self._run([str(self.binary), '--version'], directory)
                match = re.search(rb'LibreOffice ([0-9]+(?:\.[0-9]+){2,3})', version_log)
                if not match:
                    raise ParserError('DOC_CONVERSION_VERSION_UNAVAILABLE')
                (directory / 'source.doc').write_bytes(raw)
                self._run([str(self.binary), '--headless', '--nologo', '--nodefault',
                    '--norestore', '--unaccept=all', f'-env:UserInstallation={profile.as_uri()}',
                    '--infilter=MS Word 97', '--convert-to', 'docx:Office Open XML Text',
                    '--outdir', str(directory), str(directory / 'source.doc')], directory)
                output = directory / 'source.docx'
                if not output.is_file() or output.is_symlink():
                    raise ParserError('DOC_CONVERSION_FAILED')
                with output.open('rb') as stream:
                    data = stream.read(MAX_INPUT + 1)
                if len(data) > MAX_INPUT:
                    raise ParserError('DOC_CONVERSION_OUTPUT_LIMIT')
                if not data.startswith(b'PK\x03\x04'):
                    raise ParserError('DOC_CONVERSION_FAILED')
                return ConvertedDocx(data, 'LibreOffice/' + match[1].decode('ascii'))
        except OSError as exc:
            raise ParserError('DOC_CONVERSION_FAILED') from exc


class DocParser:
    def __init__(self, *, python: Path | None = None, converter=None):
        self.python = python
        self.converter = converter

    def parse(self, path: Path, document_id: str, version_id: str):
        try:
            if path.is_symlink():
                raise ParserError('DOC_SOURCE_UNREADABLE')
            with path.open('rb') as stream:
                raw = stream.read(MAX_INPUT + 1)
        except OSError as exc:
            raise ParserError('DOC_SOURCE_UNREADABLE') from exc
        validate_ole(raw)
        if self.converter is None:
            raise ParserError('DOC_CONVERSION_UNSUPPORTED')
        converted = self.converter.convert(raw)
        if not isinstance(converted, ConvertedDocx) or not converted.data or len(converted.data) > MAX_INPUT or not converted.data.startswith(b'PK\x03\x04'):
            raise ParserError('DOC_CONVERSION_FAILED')
        # Reuse the established DOCX admission/worker/schema/table chain.
        # Fixed temporary name; no user filename or external pathname enters LO.
        temp_root = getattr(self.converter, 'temp_root', None)
        with tempfile.TemporaryDirectory(prefix='rag-docx-', dir=temp_root) as name:
            staged = Path(name) / 'converted.docx'
            staged.write_bytes(converted.data)
            try:
                parsed = DocxParser(python=self.python).parse(staged, document_id, version_id)
            except ParserError as exc:
                # Retain explicit unsupported content codes such as HMERGE.
                raise ParserError('DOC_CONVERSION_FAILED:' + str(exc)) from exc
        lineage = {'original_format': 'ole-doc', 'original_sha256': hashlib.sha256(raw).hexdigest(),
            'converted_format': 'docx', 'converted_sha256': hashlib.sha256(converted.data).hexdigest(),
            'converter_version': converted.converter_version, 'parser_version': parsed.parser_version,
            'adapter_version': 'doc/lo-docx-v1', 'coordinate_basis': 'converted-docx-not-original-pagination'}
        warnings = tuple(dict.fromkeys((*parsed.parse_warnings, 'DOC_CONVERTED_LAYOUT_UNVERIFIED')))
        # Rendering offsets and cell origins belong to the converted DOCX.
        # Original DOC identity is recorded exclusively in conversion_lineage.
        tables = [t.model_copy(update={'conversion_lineage': lineage}) for t in parsed.tables]
        locators = [loc.model_copy(update={'conversion_lineage': lineage,
            'parse_status': 'partial', 'parse_warnings': warnings}) for loc in parsed.source_locators]
        return parsed.model_copy(update={'title': path.stem, 'media_type': 'application/msword',
            'tables': tables, 'source_locators': locators, 'parser_version': 'doc/lo-docx-v1',
            'parse_status': 'partial', 'parse_warnings': warnings, 'conversion_lineage': lineage})
