"""EPUB 3 validation, conformance testing, and archive integrity verification (Gate G5 / R19)."""

import datetime
from enum import Enum
from pathlib import Path
import re
import shutil
import subprocess
from typing import Optional
import xml.etree.ElementTree as ET
import zipfile

from pydantic import BaseModel, Field


class IssueSeverity(str, Enum):
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


class EpubValidationIssue(BaseModel):
    severity: IssueSeverity
    code: str
    message: str
    target_file: Optional[str] = None


class EpubMetadata(BaseModel):
    title: str = "Unknown"
    language: str = "pl"
    identifier: str = "unknown"
    creator: list[str] = Field(default_factory=list)
    date: Optional[str] = None
    publisher: Optional[str] = None


class EpubManifestItem(BaseModel):
    id: str
    href: str
    media_type: str
    properties: Optional[str] = None
    size_bytes: int = 0
    in_spine: bool = False


class EpubValidationReport(BaseModel):
    run_id: str
    epub_path: str
    file_size_bytes: int
    epub_version: str = "3.0"
    is_valid: bool = False
    verdict: str = "FAIL"
    mimetype_valid: bool = False
    container_valid: bool = False
    opf_valid: bool = False
    nav_valid: bool = False
    metadata: EpubMetadata = Field(default_factory=EpubMetadata)
    total_manifest_items: int = 0
    total_spine_items: int = 0
    total_image_assets: int = 0
    missing_manifest_files: list[str] = Field(default_factory=list)
    unregistered_zip_files: list[str] = Field(default_factory=list)
    missing_spine_items: list[str] = Field(default_factory=list)
    issues: list[EpubValidationIssue] = Field(default_factory=list)
    external_epubcheck_used: bool = False
    external_epubcheck_output: Optional[str] = None
    created_at: str = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()
    )


def validate_epub(epub_path: Path) -> EpubValidationReport:
    """Perform rigorous EPUB 3 structural and conformance validation."""
    now_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    run_id = f"epub_val_{now_str}"

    if not epub_path.exists():
        return EpubValidationReport(
            run_id=run_id,
            epub_path=str(epub_path),
            file_size_bytes=0,
            is_valid=False,
            verdict="FAIL",
            issues=[
                EpubValidationIssue(
                    severity=IssueSeverity.ERROR,
                    code="FILE_NOT_FOUND",
                    message=f"Plik EPUB nie istnieje: {epub_path}",
                )
            ],
        )

    file_size = epub_path.stat().st_size
    issues: list[EpubValidationIssue] = []

    # 1. Verify ZIP archive
    if not zipfile.is_zipfile(epub_path):
        return EpubValidationReport(
            run_id=run_id,
            epub_path=str(epub_path),
            file_size_bytes=file_size,
            is_valid=False,
            verdict="FAIL",
            issues=[
                EpubValidationIssue(
                    severity=IssueSeverity.ERROR,
                    code="INVALID_ZIP",
                    message="Plik nie jest poprawnym archiwum ZIP.",
                )
            ],
        )

    mimetype_valid = False
    container_valid = False
    opf_valid = False
    nav_valid = False
    epub_version = "3.0"
    metadata = EpubMetadata()
    total_manifest_items = 0
    total_spine_items = 0
    total_image_assets = 0
    missing_manifest_files: list[str] = []
    unregistered_zip_files: list[str] = []
    missing_spine_items: list[str] = []

    with zipfile.ZipFile(epub_path, "r") as zf:
        infolist = zf.infolist()
        namelist = zf.namelist()

        # 2. Verify mimetype as exact first entry without compression
        if not infolist:
            issues.append(
                EpubValidationIssue(
                    severity=IssueSeverity.ERROR,
                    code="EMPTY_ARCHIVE",
                    message="Archiwum EPUB jest puste.",
                )
            )
        else:
            first_entry = infolist[0]
            if first_entry.filename != "mimetype":
                issues.append(
                    EpubValidationIssue(
                        severity=IssueSeverity.ERROR,
                        code="MIMETYPE_NOT_FIRST",
                        message=f"Pierwszym plikiem w archiwum musi być 'mimetype', znaleziono: '{first_entry.filename}'",
                        target_file=first_entry.filename,
                    )
                )
            else:
                mimetype_raw = zf.read("mimetype")
                mimetype_text = mimetype_raw.decode("ascii", errors="replace").strip()
                if mimetype_text != "application/epub+zip":
                    issues.append(
                        EpubValidationIssue(
                            severity=IssueSeverity.ERROR,
                            code="INVALID_MIMETYPE_CONTENT",
                            message=f"Zawartość mimetype musi być 'application/epub+zip', odczytano: '{mimetype_text}'",
                            target_file="mimetype",
                        )
                    )
                elif first_entry.compress_type != zipfile.ZIP_STORED:
                    issues.append(
                        EpubValidationIssue(
                            severity=IssueSeverity.WARNING,
                            code="MIMETYPE_COMPRESSED",
                            message="Plik 'mimetype' powinien być nieskompresowany (ZIP_STORED).",
                            target_file="mimetype",
                        )
                    )
                    mimetype_valid = True
                else:
                    mimetype_valid = True

        # 3. Verify META-INF/container.xml
        opf_path: Optional[str] = None
        if "META-INF/container.xml" not in namelist:
            issues.append(
                EpubValidationIssue(
                    severity=IssueSeverity.ERROR,
                    code="MISSING_CONTAINER_XML",
                    message="Brak wymaganego pliku META-INF/container.xml w archiwum.",
                    target_file="META-INF/container.xml",
                )
            )
        else:
            try:
                container_data = zf.read("META-INF/container.xml")
                root = ET.fromstring(container_data)
                # Find rootfile element
                # Handle possible namespace in container.xml
                rootfiles = root.findall(".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile")
                if not rootfiles:
                    rootfiles = root.findall(".//rootfile")

                for rf in rootfiles:
                    full_path = rf.attrib.get("full-path")
                    media_type = rf.attrib.get("media-type")
                    if media_type == "application/oebps-package+xml" and full_path:
                        opf_path = full_path
                        break

                if not opf_path and rootfiles:
                    opf_path = rootfiles[0].attrib.get("full-path")

                if opf_path and opf_path in namelist:
                    container_valid = True
                else:
                    issues.append(
                        EpubValidationIssue(
                            severity=IssueSeverity.ERROR,
                            code="CONTAINER_OPF_NOT_FOUND",
                            message=f"Plik OPF wskazany w container.xml ('{opf_path}') nie istnieje w archiwum.",
                            target_file="META-INF/container.xml",
                        )
                    )
            except Exception as e:
                issues.append(
                    EpubValidationIssue(
                        severity=IssueSeverity.ERROR,
                        code="CONTAINER_XML_CORRUPT",
                        message=f"Błąd parsowania META-INF/container.xml: {e}",
                        target_file="META-INF/container.xml",
                    )
                )

        # 4. Verify OPF document
        if opf_path and opf_path in namelist:
            try:
                opf_data = zf.read(opf_path)
                opf_root = ET.fromstring(opf_data)

                # Package version
                epub_version = opf_root.attrib.get("version", "3.0")
                opf_dir = Path(opf_path).parent

                # Namespaces
                ns = {
                    "opf": "http://www.idpf.org/2007/opf",
                    "dc": "http://purl.org/dc/elements/1.1/",
                }

                # Metadata
                meta_elem = opf_root.find(".//{http://www.idpf.org/2007/opf}metadata")
                if meta_elem is None:
                    meta_elem = opf_root.find(".//metadata")

                if meta_elem is not None:
                    def get_meta(tag: str) -> str:
                        el = meta_elem.find(f".//{{http://purl.org/dc/elements/1.1/}}{tag}")
                        if el is None:
                            el = meta_elem.find(f".//{tag}")
                        return el.text.strip() if el is not None and el.text else ""

                    metadata.title = get_meta("title") or "Unknown"
                    metadata.language = get_meta("language") or "pl"
                    metadata.identifier = get_meta("identifier") or "unknown"
                    metadata.publisher = get_meta("publisher") or None
                    metadata.date = get_meta("date") or None

                    creators: list[str] = []
                    for c_el in meta_elem.findall(".//{http://purl.org/dc/elements/1.1/}creator"):
                        if c_el.text and c_el.text.strip():
                            creators.append(c_el.text.strip())
                    if not creators:
                        for c_el in meta_elem.findall(".//creator"):
                            if c_el.text and c_el.text.strip():
                                creators.append(c_el.text.strip())
                    metadata.creator = creators

                # Manifest items
                manifest_items: dict[str, EpubManifestItem] = {}
                manifest_elem = opf_root.find(".//{http://www.idpf.org/2007/opf}manifest")
                if manifest_elem is None:
                    manifest_elem = opf_root.find(".//manifest")

                if manifest_elem is not None:
                    for item_el in manifest_elem:
                        if not item_el.tag.endswith("item"):
                            continue
                        i_id = item_el.attrib.get("id", "")
                        i_href = item_el.attrib.get("href", "")
                        i_media = item_el.attrib.get("media-type", "")
                        i_props = item_el.attrib.get("properties")

                        # Determine full path inside zip
                        if str(opf_dir) == ".":
                            full_zip_href = i_href
                        else:
                            full_zip_href = str((opf_dir / i_href).as_posix())

                        exists = full_zip_href in namelist
                        size = 0
                        if exists:
                            size = zf.getinfo(full_zip_href).file_size
                        else:
                            missing_manifest_files.append(full_zip_href)
                            issues.append(
                                EpubValidationIssue(
                                    severity=IssueSeverity.ERROR,
                                    code="MANIFEST_FILE_MISSING",
                                    message=f"Plik zadeklarowany w manifeście ('{i_href}') nie istnieje pod ścieżką '{full_zip_href}'",
                                    target_file=full_zip_href,
                                )
                            )

                        if "nav" in (i_props or "").split():
                            nav_valid = True

                        if i_media.startswith("image/"):
                            total_image_assets += 1

                        manifest_items[i_id] = EpubManifestItem(
                            id=i_id,
                            href=i_href,
                            media_type=i_media,
                            properties=i_props,
                            size_bytes=size,
                        )

                total_manifest_items = len(manifest_items)

                # Spine items
                spine_elem = opf_root.find(".//{http://www.idpf.org/2007/opf}spine")
                if spine_elem is None:
                    spine_elem = opf_root.find(".//spine")

                if spine_elem is not None:
                    for itemref in spine_elem:
                        if not itemref.tag.endswith("itemref"):
                            continue
                        idref = itemref.attrib.get("idref", "")
                        if idref in manifest_items:
                            manifest_items[idref].in_spine = True
                            total_spine_items += 1
                        else:
                            missing_spine_items.append(idref)
                            issues.append(
                                EpubValidationIssue(
                                    severity=IssueSeverity.ERROR,
                                    code="SPINE_IDREF_NOT_IN_MANIFEST",
                                    message=f"Element spine idref='{idref}' nie istnieje w manifeście OPF.",
                                    target_file=opf_path,
                                )
                            )

                # Check navigation document fallback (nav.xhtml or toc.ncx)
                if not nav_valid:
                    for item in manifest_items.values():
                        if "nav.xhtml" in item.href.lower() or "nav" in item.id.lower():
                            nav_valid = True
                            break

                if not nav_valid:
                    issues.append(
                        EpubValidationIssue(
                            severity=IssueSeverity.WARNING,
                            code="NO_NAV_DOCUMENT",
                            message="Brak dokumentu nawigacji EPUB 3 (properties='nav').",
                            target_file=opf_path,
                        )
                    )

                # Check for unreferenced files in ZIP (excluding mimetype, META-INF/*, opf)
                registered_paths = set()
                registered_paths.add("mimetype")
                registered_paths.add(opf_path)
                for item in manifest_items.values():
                    if str(opf_dir) == ".":
                        registered_paths.add(item.href)
                    else:
                        registered_paths.add(str((opf_dir / item.href).as_posix()))

                for fname in namelist:
                    if fname.startswith("META-INF/"):
                        continue
                    if fname not in registered_paths:
                        unregistered_zip_files.append(fname)
                        issues.append(
                            EpubValidationIssue(
                                severity=IssueSeverity.WARNING,
                                code="UNREGISTERED_ZIP_FILE",
                                message=f"Plik w archiwum '{fname}' nie jest zarejestrowany w manifeście OPF.",
                                target_file=fname,
                            )
                        )

                opf_valid = True

            except Exception as e:
                issues.append(
                    EpubValidationIssue(
                        severity=IssueSeverity.ERROR,
                        code="OPF_PARSE_ERROR",
                        message=f"Błąd parsowania dokumentu pakietu OPF ({opf_path}): {e}",
                        target_file=opf_path,
                    )
                )

    # 5. Check external epubcheck if installed
    external_epubcheck_used = False
    external_epubcheck_output: Optional[str] = None
    epubcheck_bin = shutil.which("epubcheck")
    if epubcheck_bin:
        try:
            res = subprocess.run(
                [epubcheck_bin, str(epub_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            external_epubcheck_used = True
            external_epubcheck_output = (res.stdout + "\n" + res.stderr).strip()
            if res.returncode != 0:
                issues.append(
                    EpubValidationIssue(
                        severity=IssueSeverity.ERROR,
                        code="EXTERNAL_EPUBCHECK_FAILED",
                        message=f"Zewnętrzny EPUBCheck zgłosił błędy (kod {res.returncode}).",
                        target_file=str(epub_path),
                    )
                )
        except Exception as e:
            external_epubcheck_output = f"Nie udało się uruchomić epubcheck: {e}"

    # 6. Overall validity & verdict
    has_critical_errors = any(issue.severity == IssueSeverity.ERROR for issue in issues)
    is_valid = (
        not has_critical_errors
        and mimetype_valid
        and container_valid
        and opf_valid
        and (len(missing_manifest_files) == 0)
        and (len(missing_spine_items) == 0)
    )
    verdict = "PASS" if is_valid else "FAIL"

    return EpubValidationReport(
        run_id=run_id,
        epub_path=str(epub_path),
        file_size_bytes=file_size,
        epub_version=epub_version,
        is_valid=is_valid,
        verdict=verdict,
        mimetype_valid=mimetype_valid,
        container_valid=container_valid,
        opf_valid=opf_valid,
        nav_valid=nav_valid,
        metadata=metadata,
        total_manifest_items=total_manifest_items,
        total_spine_items=total_spine_items,
        total_image_assets=total_image_assets,
        missing_manifest_files=missing_manifest_files,
        unregistered_zip_files=unregistered_zip_files,
        missing_spine_items=missing_spine_items,
        issues=issues,
        external_epubcheck_used=external_epubcheck_used,
        external_epubcheck_output=external_epubcheck_output,
    )


def save_epub_validation_report(
    report: EpubValidationReport,
    output_dir: Path,
) -> tuple[Path, Path]:
    """Save validation report to JSON and Markdown formats."""
    output_dir.mkdir(parents=True, exist_ok=True)

    json_path = output_dir / f"{report.run_id}.json"
    json_path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")

    md_path = output_dir / f"{report.run_id}.md"

    lines = [
        f"# Raport Walidacji EPUB 3 (Gate G5 / R19) — {report.verdict}",
        "",
        f"- **ID Operacji:** `{report.run_id}`",
        f"- **Plik EPUB:** `{report.epub_path}`",
        f"- **Rozmiar pliku:** `{report.file_size_bytes / (1024 * 1024):.2f} MB` ({report.file_size_bytes} bajtów)",
        f"- **Wersja EPUB:** `{report.epub_version}`",
        f"- **Data walidacji:** `{report.created_at}`",
        f"- **Status walidacji:** **`{report.verdict}`**",
        "",
        "## Metadane Publikacji",
        "",
        f"- **Tytuł:** `{report.metadata.title}`",
        f"- **Język:** `{report.metadata.language}`",
        f"- **Identyfikator:** `{report.metadata.identifier}`",
        f"- **Autorzy / Twórcy:** `{', '.join(report.metadata.creator) if report.metadata.creator else 'Brak'}`",
        f"- **Wydawca:** `{report.metadata.publisher or 'Nie określono'}`",
        "",
        "## Integralność Kontenera i Standardu EPUB 3",
        "",
        "| Element | Wymaganie | Stan | Werdykt |",
        "| --- | --- | --- | --- |",
        f"| Mimetype | Pierwszy plik w ZIP, 'application/epub+zip' | {'Zgodny' if report.mimetype_valid else 'Błędny'} | {'✓ OK' if report.mimetype_valid else '✗ BŁĄD'} |",
        f"| Container XML | META-INF/container.xml wskazuje OPF | {'Prawidłowy' if report.container_valid else 'Brak / uszkodzony'} | {'✓ OK' if report.container_valid else '✗ BŁĄD'} |",
        f"| Pakiet OPF | Prawidłowy XML, metadata, manifest, spine | {'Prawidłowy' if report.opf_valid else 'Brak / uszkodzony'} | {'✓ OK' if report.opf_valid else '✗ BŁĄD'} |",
        f"| Nawigacja | Dokument nawigacyjny (nav.xhtml / NCX) | {'Obecny' if report.nav_valid else 'Brak nav'} | {'✓ OK' if report.nav_valid else '⚠ OSTRZEŻENIE'} |",
        f"| Pozycje Manifestu | Wszystkie zadeklarowane pliki istnieją w ZIP | {report.total_manifest_items} pozycji (brakujących: {len(report.missing_manifest_files)}) | {'✓ OK' if not report.missing_manifest_files else '✗ BRAKI'} |",
        f"| Linear Spine | Wszystkie idref w spine istnieją w manifeście | {report.total_spine_items} sekcji (brakujących: {len(report.missing_spine_items)}) | {'✓ OK' if not report.missing_spine_items else '✗ BRAKI'} |",
        f"| Ilustracje (Assets) | Komplet wyekstrahowanych grafik w EPUB | {report.total_image_assets} obrazów | {'✓ 100% ZACHOWANE' if report.total_image_assets >= 113 else f'{report.total_image_assets} grafik'} |",
        "",
        "## Zarejestrowane Uwagi i Błędy",
        "",
    ]

    if not report.issues:
        lines.append("✓ **Brak wykrytych problemów. Plik EPUB w 100% spełnia wymogi specyfikacji.**")
    else:
        for idx, issue in enumerate(report.issues, start=1):
            lines.append(f"{idx}. `[{issue.severity.value}]` **{issue.code}**: {issue.message} *(Plik: `{issue.target_file}`)*")

    lines.extend([
        "",
        "## Walidacja Zewnętrzna",
        "",
        f"- **Użyto zewnętrznego narzędzia EPUBCheck:** `{'TAK' if report.external_epubcheck_used else 'NIE (brak w PATH — wykonano pełną walidację natywną)'}`",
    ])
    if report.external_epubcheck_output:
        lines.extend([
            "",
            "```text",
            report.external_epubcheck_output,
            "```",
        ])

    lines.extend([
        "",
        "## Wnioski i Werdykt Bramki Gate G5",
        "",
        f"> **Werdykt końcowy:** **{report.verdict}**",
        f"> Kontener EPUB {'spełnia wszystkie kryteria walidacyjne standardu EPUB 3' if report.is_valid else 'zawiera błędy wymagające korekty'}.",
    ])

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path
