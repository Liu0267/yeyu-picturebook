"""Local PDF/EPUB/image ingestion. Preserve file pages, not guessed printed pagination."""
import hashlib
import html.entities
import json
import posixpath
import re
import subprocess
import zipfile
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree as ET

import pymupdf
from PIL import Image, ImageStat
from .models import BookManifest, OCRLine, Page, Region


def read_xml(raw):
    raw = re.sub(rb'&([A-Za-z]+);', lambda m: ('&#%d;' % html.entities.name2codepoint[m[1].decode()]).encode()
                 if m[1].decode() in html.entities.name2codepoint else m[0], raw)
    return ET.fromstring(raw)


def local_name(element):
    return element.tag.split('}')[-1]


def extract(source, target):
    """Returns one record per PDF page or unique image per EPUB spine document."""
    source, target = Path(source), Path(target)
    target.mkdir(parents=True, exist_ok=True)
    pages = []
    suffix = source.suffix.lower()
    if suffix == '.pdf':
        with pymupdf.open(source) as doc:
            for i, page in enumerate(doc, 1):
                path = target / f'{i:03}.png'
                page.get_pixmap(dpi=150, alpha=False).save(str(path))
                pages.append((path, f'pdf:page:{i}', page.get_text()))
    elif suffix == '.epub':
        with zipfile.ZipFile(source) as archive:
            container = read_xml(archive.read('META-INF/container.xml'))
            opf = next(x.attrib['full-path'] for x in container.iter() if local_name(x) == 'rootfile')
            package = read_xml(archive.read(opf))
            items = {x.attrib['id']: posixpath.normpath(posixpath.join(posixpath.dirname(opf), unquote(x.attrib['href'])))
                     for x in package.iter() if local_name(x) == 'item'}
            for item in (x for x in package.iter() if local_name(x) == 'itemref'):
                chapter = items[item.attrib['idref']]
                tree = read_xml(archive.read(chapter))
                body = next((x for x in tree.iter() if local_name(x) == 'body'), tree)
                text = '\n'.join(dict.fromkeys(x.text.strip() for x in body.iter()
                                 if local_name(x) in ('span', 'p') and x.text and x.text.strip()))
                images = dict.fromkeys(posixpath.normpath(posixpath.join(posixpath.dirname(chapter), unquote(x.attrib['src'])))
                                       for x in body.iter() if local_name(x) == 'img' and x.get('src'))
                for member in images:
                    path = target / f'{len(pages)+1:03}{Path(member).suffix.lower()}'
                    path.write_bytes(archive.read(member))
                    pages.append((path, f'epub:{chapter}#{member}', text))
    elif suffix in ('.png', '.jpg', '.jpeg'):
        path = target / ('001' + suffix)
        path.write_bytes(source.read_bytes())
        pages.append((path, 'image:1', ''))
    else:
        raise ValueError('supported formats: PDF, EPUB, PNG, JPEG')
    if not pages:
        raise ValueError('no image pages found')
    return pages


def page_record(store, book_id, sequence, path, locator, text_layer='', ocr=None, seen=None, is_mock=False):
    with Image.open(path) as im:
        width, height = im.size
        gray = im.convert('L').resize((128, 128))
        contrast_std = ImageStat.Stat(gray).stddev[0]
    flags = []
    sha = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    if seen is not None:
        if sha in seen:
            flags.append('exact_duplicate_image')
        seen.add(sha)
    if width > height * 1.25:
        flags.append('possible_spread_keep_unsplit')
    if min(width, height) < 600:
        flags.append('small_image')
    if contrast_std < 8:
        flags.append('low_global_contrast_check_original')
    page_id = f'{book_id}_p{sequence:03}'
    from .quality import inspect_page
    quality=inspect_page(path);quality.update(page_id=page_id,page_revision=1)
    store.put('page_quality',page_id,quality)
    flags=list(dict.fromkeys(flags+quality['quality_flags']))
    lines = []
    engine = 'not_run'
    if text_layer.strip():
        engine = 'embedded_text_unaligned'
        lines = [OCRLine(line_id=f'{page_id}_l{i:03}', raw_text=t, region=Region())
                 for i, t in enumerate(text_layer.splitlines(), 1) if t.strip()]
        flags.append('embedded_text_bbox_is_whole_page')
    elif ocr and not ocr.get('error'):
        engine = ocr['engine']
        for i, line in enumerate(ocr['lines'], 1):
            coords = [min(1, max(0, float(x))) for x in line['bbox']]
            if coords[0] >= coords[2] or coords[1] >= coords[3]:
                continue
            lines.append(OCRLine(line_id=f'{page_id}_l{i:03}', raw_text=line['text'],
                                 confidence=line['confidence'], region=Region(**dict(zip(('x1','y1','x2','y2'), coords)))))
        if any(x.confidence < 0.7 for x in lines):
            flags.append('low_confidence_text')
    else:
        flags.append('ocr_failed' if ocr else 'ocr_not_run')
    if not lines:
        flags.append('no_text_found_check_original')
    else:
        flags.append('reading_order_unverified')
    return Page(page_id=page_id, sequence=sequence, image_asset_id=store.asset(path, 'image/png' if Path(path).suffix=='.png' else 'image/jpeg', is_mock),
                width=width, height=height, source_locator=locator, ocr_engine=engine,
                ocr_lines=lines, quality_flags=flags)


def ingest(store, source, book_id, title, vision_executable=None):
    source = Path(source)
    pages = extract(source, store.root / 'books' / book_id / 'pages' / 'raw')
    ocr_records = {}
    if vision_executable:
        result = subprocess.run([str(vision_executable), *(str(p) for p, _, text in pages if not text.strip())],
                                capture_output=True, text=True, timeout=1800, check=True)
        (store.root / 'books' / book_id / 'ocr_raw.jsonl').write_text(result.stdout)
        for line in result.stdout.splitlines():
            record = json.loads(line)
            ocr_records[record['path']] = record
    seen = set()
    manifest = BookManifest(book_id=book_id, title=title, source_filename=source.name,
                            source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                            pages=[page_record(store, book_id, i, p, loc, text, ocr_records.get(str(p)), seen)
                                   for i, (p, loc, text) in enumerate(pages, 1)])
    store.put('book', book_id, manifest)
    return manifest

