"""Small generated PDFs keep required tests independent of private documents/models."""
from pathlib import Path

import pytest


def pytest_addoption(parser):
    parser.addoption('--run-document-integration', action='store_true', default=False,
                     help='Run Docling inference tests on generated PDFs (requires local model weights)')


def pytest_configure(config):
    config.addinivalue_line('markers', 'document_integration: real Docling inference on generated PDFs; opt in with --run-document-integration')


def pytest_collection_modifyitems(config, items):
    if not config.getoption('--run-document-integration'):
        skip = pytest.mark.skip(reason='Docling inference integration: enable --run-document-integration with local model weights')
        for item in items:
            if 'document_integration' in item.keywords:
                item.add_marker(skip)


@pytest.fixture
def make_text_pdf(tmp_path):
    """Write a minimal uncompressed PDF with standard Helvetica, without extra tools."""
    def write(pages: list[list[str]], filename='fixture.pdf') -> Path:
        objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'', b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
        page_ids=[]
        for lines in pages:
            page_id=len(objects)+1
            page_ids.append(page_id)
            commands=['BT /F1 12 Tf 50 730 Td']
            for line in lines:
                escaped=line.replace('\\','\\\\').replace('(','\\(').replace(')','\\)')
                commands += [f'({escaped}) Tj', '0 -20 Td']
            stream=('\n'.join(commands)+'\nET\n').encode('ascii')
            objects.append(f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents {page_id+1} 0 R >>'.encode())
            objects.append(f'<< /Length {len(stream)} >>\nstream\n'.encode()+stream+b'endstream')
        kids=' '.join(f'{i} 0 R' for i in page_ids)
        objects[1]=f'<< /Type /Pages /Count {len(pages)} /Kids [{kids}] >>'.encode()
        data=bytearray(b'%PDF-1.4\n'); offsets=[0]
        for index,obj in enumerate(objects,1):
            offsets.append(len(data)); data.extend(f'{index} 0 obj\n'.encode()+obj+b'\nendobj\n')
        xref=len(data)
        data.extend(f'xref\n0 {len(objects)+1}\n0000000000 65535 f \n'.encode())
        for offset in offsets[1:]: data.extend(f'{offset:010d} 00000 n \n'.encode())
        data.extend(f'trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode())
        target=tmp_path/filename; target.write_bytes(data); return target
    return write


@pytest.fixture
def text_pdf(make_text_pdf):
    return make_text_pdf([['Foreword', 'Add one teaspoon of salt and stir gently into the warm water.'],
                          ['Ingredients', 'Use fresh flour and water to prepare a simple dough.'],
                          ['Baking', 'Bake the prepared loaf until the crust is golden brown.']])
