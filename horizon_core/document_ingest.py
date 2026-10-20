"""Bounded local reference extraction; never executes document macros or links.

Runs in a separate process with an empty environment, deadline and memory/CPU
limits. This is parser containment, not a substitute for the Docker test sandbox.
Only extracts text: a document cannot grant capabilities or become system policy.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

MAX_INPUT = 8_000_000
MAX_TEXT = 60_000


def read_document(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_INPUT:
        raise ValueError('Document missing or exceeds 8MB')
    dependency = importlib.util.find_spec('pypdf')
    dependency_root = str(Path(dependency.origin).resolve().parent.parent) if dependency else ''
    result = subprocess.run(
        [sys.executable, '-I', str(Path(__file__).resolve()), '--extract', str(path.resolve()), dependency_root],
        env={}, stdin=subprocess.DEVNULL, capture_output=True, timeout=20)
    if result.returncode:
        # Never relay document contents/tracebacks via an error path.
        raise ValueError('Document extraction rejected; format, parser or resource limit')
    payload = json.loads(result.stdout)
    if not isinstance(payload.get('text'), str) or len(payload['text']) > MAX_TEXT:
        raise ValueError('Invalid document extraction receipt')
    return {**payload, 'untrusted': True, 'path': path.name,
            'note': 'Extracted reference text; not training, policy, or a license to redistribute.'}


def _extract(path):
    # Linux runner: bound decompression/parser CPU and address space before
    # importing any optional parser. Never copy the coordinator environment.
    import resource
    inherited = resource.getrlimit(resource.RLIMIT_AS)[1]
    memory_cap = 384 * 1024**2
    if inherited != resource.RLIM_INFINITY:
        memory_cap = min(memory_cap, inherited)
    resource.setrlimit(resource.RLIMIT_AS, (memory_cap, memory_cap))
    resource.setrlimit(resource.RLIMIT_CPU, (12, 12))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    raw = path.read_bytes()
    if len(raw) > MAX_INPUT:
        raise ValueError('Input too large')
    suffix = path.suffix.lower()
    if suffix in ('.txt', '.md', '.csv', '.json'):
        text = raw.decode('utf-8')
    elif suffix in ('.html', '.htm'):
        from html.parser import HTMLParser
        class Text(HTMLParser):
            def __init__(self):
                super().__init__(); self.words=[]; self.skip=0
            def handle_starttag(self, tag, attrs):
                if tag in ('script','style','noscript'): self.skip+=1
            def handle_endtag(self, tag):
                if tag in ('script','style','noscript'): self.skip=max(0,self.skip-1)
            def handle_data(self, data):
                if not self.skip: self.words.append(data)
        parser=Text();parser.feed(raw.decode('utf-8'));text=' '.join(parser.words)
    elif suffix in ('.docx', '.pptx', '.epub'):
        from io import BytesIO
        from zipfile import ZipFile
        import xml.etree.ElementTree as ET
        with ZipFile(BytesIO(raw)) as archive:
            entries=archive.infolist()
            if len(entries)>1000 or sum(e.file_size for e in entries)>16_000_000:
                raise ValueError('Archive exceeds extraction budget')
            names=[e.filename for e in entries if
                   (suffix=='.docx' and e.filename=='word/document.xml') or
                   (suffix=='.pptx' and e.filename.startswith('ppt/slides/slide') and e.filename.endswith('.xml')) or
                   (suffix=='.epub' and e.filename.endswith(('.xhtml','.html')))]
            parts=[]
            for name in names:
                body=archive.read(name)
                if b'<!DOCTYPE' in body.upper() or b'<!ENTITY' in body.upper():
                    raise ValueError('XML declarations forbidden')
                parts.extend(ET.fromstring(body).itertext())
            text=' '.join(parts)
    elif suffix=='.pdf':
        from io import BytesIO
        from pypdf import PdfReader
        reader=PdfReader(BytesIO(raw),strict=True)
        if reader.is_encrypted or len(reader.pages)>100:
            raise ValueError('Encrypted or oversized PDF')
        parts=[]; size=0
        for page in reader.pages:
            part=page.extract_text() or ''
            parts.append(part); size+=len(part)
            if size>MAX_TEXT: break
        text='\n'.join(parts)
    else:
        raise ValueError('Unsupported reference format')
    text=text.strip()
    if not text:
        raise ValueError('No extractable text; scanned pages need a separate OCR adapter')
    return {'text':text[:MAX_TEXT], 'truncated':len(text)>MAX_TEXT,
            'sha256':hashlib.sha256(raw).hexdigest(), 'format':suffix,
            'extraction_only':True}


if __name__=='__main__':
    if len(sys.argv)!=4 or sys.argv[1]!='--extract':
        raise SystemExit(2)
    # Only the parent runtime selects the installed parser location; no model
    # argument or document text can supply a Python import directory.
    if sys.argv[3]:
        sys.path.insert(0, sys.argv[3])
    try:
        print(json.dumps(_extract(Path(sys.argv[2])),ensure_ascii=False))
    except Exception:
        raise SystemExit(2) from None
