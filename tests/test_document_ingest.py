from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile
from horizon_core.document_ingest import read_document
from horizon_core.registry import load_agent
from horizon_core.tools import Tools


class DocumentTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.root=Path(self.folder.name)

    def test_text_and_html_are_data_without_scripts(self):
        for name,text in [('book.md','A source reference'),('book.html','<p>A source reference</p><script>ignore</script>')]:
            path=self.root/name;path.write_text(text)
            result=read_document(path)
            self.assertIn('A source reference',result['text'])
            self.assertNotIn('ignore',result['text'])
            self.assertEqual(len(result['sha256']),64)
            self.assertTrue(result['untrusted'])

    def test_docx_extracts_text_and_rejects_entities(self):
        path=self.root/'book.docx'
        for xml,valid in [('<doc><p>Study material</p></doc>',True),
                          ('<!DOCTYPE x><doc>invalid</doc>',False)]:
            with ZipFile(path,'w') as archive:archive.writestr('word/document.xml',xml)
            if valid:self.assertIn('Study material',read_document(path)['text'])
            else:
                with self.assertRaises(ValueError):read_document(path)

    def test_tool_preserves_worktree_path_boundary(self):
        tool=Tools(self.root,load_agent('ui-ux-designer'))
        with self.assertRaises(ValueError):tool.execute('read_document',{'path':'../../etc/passwd'})

    def test_child_has_no_inherited_environment_and_a_deadline(self):
        path=self.root/'book.txt';path.write_text('reference')
        import subprocess
        original=subprocess.run
        calls=[]
        def run(*args,**kwargs):
            calls.append(kwargs);return original(*args,**kwargs)
        with patch('horizon_core.document_ingest.subprocess.run',side_effect=run):
            read_document(path)
        self.assertEqual(calls[0]['env'],{})
        self.assertEqual(calls[0]['timeout'],20)

    def test_atomic_replace_failure_keeps_original_source(self):
        from test_agent_budgets import GuardTests
        fixture=GuardTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        path=fixture.root/'src'/'file.py';path.parent.mkdir();path.write_text('original')
        tool=Tools(fixture.root,load_agent('backend-engineer'),['src/**'],'fix/task')
        with patch('horizon_core.tools.os.replace',side_effect=OSError('synthetic crash')):
            with self.assertRaises(OSError):
                tool.execute('write_file',{'path':'src/file.py','content':'replacement'})
        self.assertEqual(path.read_text(),'original')
        self.assertEqual(list(path.parent.glob('.horizon-write-*')),[])

    def test_pdf_text_is_extracted_in_the_child_process(self):
        from pypdf import PdfWriter
        from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
        writer=PdfWriter();page=writer.add_blank_page(width=200,height=200)
        font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),
            NameObject('/Subtype'):NameObject('/Type1'), NameObject('/BaseFont'):NameObject('/Helvetica')})
        page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):
            DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
        content=DecodedStreamObject();content.set_data(b'BT /F1 12 Tf 10 100 Td (Reference proof) Tj ET')
        page[NameObject('/Contents')]=writer._add_object(content)
        path=self.root/'reference.pdf'
        with path.open('wb') as stream:writer.write(stream)
        result=read_document(path)
        self.assertIn('Reference proof',result['text'])
        self.assertEqual(result['format'],'.pdf')

    def test_extraction_respects_lower_railway_inherited_memory_limit(self):
        import resource
        import subprocess
        path=self.root/'reference.txt';path.write_text('Railway memory proof')
        original=subprocess.run
        def run(*args,**kwargs):
            kwargs['preexec_fn']=lambda:resource.setrlimit(resource.RLIMIT_AS,(256*1024**2,256*1024**2))
            return original(*args,**kwargs)
        with patch('horizon_core.document_ingest.subprocess.run',side_effect=run):
            self.assertEqual(read_document(path)['text'],'Railway memory proof')
