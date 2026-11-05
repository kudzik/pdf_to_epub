"""End-to-end gate checks with controlled candidates; never invokes OCR or private PDFs."""
import json
from pathlib import Path

import pytest

from src.extraction_adapter import ExtractedBlock, ExtractedPage, BlockProvenance, compute_file_sha256, save_extracted_page
from src.ground_truth import PageGroundTruth, GroundTruthBlock, CriticalQuantity, GroundTruthTable, GroundTruthTableCell
from src.ocr_modes import CandidateRunManifest, CandidateRunConfig, OcrEngineMode
from src.gate_g2_evaluation import run_gate_g2_holdout_evaluation, save_gate_g2_report


@pytest.fixture
def gate_fixture(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    pdf = tmp_path / 'fixture.pdf'
    pdf.write_bytes(b'source identity fixture; converter is replaced')
    sha = compute_file_sha256(pdf)
    image = tmp_path / 'scan.png'
    image.write_bytes(b'annotated scan fixture')
    truth = tmp_path / 'truth'
    truth.mkdir()
    pages = list(range(11, 21))
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'book_name': 'fixture', 'source_pdf_path': str(pdf), 'source_pdf_sha256': sha,
        'total_pages_in_pdf': 100, 'dev_pages': [{'pdf_page':p,'layout_type':'prose_single_column','chapter':'Dev','description':'fixture'} for p in [*range(25,43),54,58]], 'holdout_pages': [{'pdf_page': p, 'layout_type': 'recipe_two_column', 'chapter': 'Recipes', 'description': 'fixture', 'has_tables': True} for p in pages]}))
    texts = ['Add 1 tsp salt and 2 cups flour to the bowl.', 'Bake at 180°C for 20 minutes until the crust is golden.', '| Ingredient | Amount |\n| --- | --- |\n| Sugar | 3 tsp |']
    for page in pages:
        reference = PageGroundTruth(page_no=page, layout_type='recipe_two_column', source_image_path=str(image), source_image_sha256=compute_file_sha256(image), metadata={'source_pdf_sha256': sha},
            blocks=[GroundTruthBlock(block_id=f'b{i}', reading_order=i+1, block_type='table' if i==2 else 'paragraph', text=text,
                critical_quantities=[CriticalQuantity(value='1', unit='tsp', ingredient_or_context='salt', raw_token='1 tsp salt')] if i==0 else []) for i,text in enumerate(texts)],
            tables=[GroundTruthTable(table_id='table', reading_order=3, num_rows=2, num_cols=2, cells=[GroundTruthTableCell(row=r+1,col=c+1,text=value) for r,row in enumerate([['Ingredient','Amount'],['Sugar','3 tsp']]) for c,value in enumerate(row)])])
        (truth / f'page_{page:03d}.json').write_text(reference.model_dump_json())
    mutation = {'kind': 'none', 'called': False}

    def convert(**kwargs):
        mutation['called'] = True
        run_id=kwargs['run_id']
        directory=kwargs['output_base_dir'] / run_id
        actual=pages[:-1] if mutation['kind']=='missing_page' else pages
        for page in actual:
            blocks=[]
            for i,original in enumerate(texts):
                text=original
                if page==11:
                    if mutation['kind']=='unit': text=text.replace('1 tsp','1 tbsp')
                    if mutation['kind']=='context_swap': text=text.replace('1 tsp salt and 2 cups flour','2 cups salt and 1 tsp flour')
                    if mutation['kind']=='temperature': text=text.replace('180°C','180°F')
                    if mutation['kind']=='ingredient': text=text.replace(' and 2 cups flour','')
                    if mutation['kind']=='cell_swap': text=text.replace('| Sugar | 3 tsp |','| 3 tsp | Sugar |')
                order=3-i if page==11 and mutation['kind']=='columns' else i+1
                blocks.append(ExtractedBlock(block_id=f'b{i}',page_no=page,reading_order=order,block_type='table' if i==2 else 'paragraph',text=text,
                    provenance=BlockProvenance(run_id=run_id,engine='fixture',engine_version='1',source_pdf_path=str(pdf),source_pdf_sha256=sha,page_no=page)))
            save_extracted_page(ExtractedPage(run_id=run_id,page_no=page,width_pt=600,height_pt=800,blocks=blocks),directory/'pages')
        if mutation['kind']=='production':
            protected=tmp_path/'output'/'markdown'/'another_book.md'
            protected.parent.mkdir(parents=True,exist_ok=True)
            protected.write_text('Unexpected production write')
        if mutation['kind']=='reference_after':
            (truth/'page_011.json').write_text('{}')
        durations={p:1.0 for p in actual}
        if mutation['kind']=='missing_duration': durations.pop(11)
        if mutation['kind']=='slow': durations[11]=60
        config=CandidateRunConfig(run_id=run_id,mode=OcrEngineMode.RAPID_OCR,pages=pages,source_pdf_path=str(pdf),source_pdf_sha256=sha,output_dir=str(directory),docling_version='fixture')
        return CandidateRunManifest(config=config,pages_converted=actual,total_pages=len(actual),total_blocks=len(actual)*3,page_durations=durations)

    monkeypatch.setattr('src.gate_g2_evaluation.run_candidate_conversion',convert)
    def run():
        return run_gate_g2_holdout_evaluation(pdf,manifest,tmp_path/'candidates',run_id='test',ground_truth_dir=truth)
    return run, mutation, truth, manifest, tmp_path


def test_exact_control_passes(gate_fixture):
    run, mutation, *_=gate_fixture
    report=run()
    assert mutation['called']
    assert report.verdict=='PASS'
    assert all(report.criteria_checklist.values())
    assert report.evidence[11]['table_match'] is True


@pytest.mark.parametrize('kind,criterion', [('context_swap','2_measure_and_temp_invariants'),('unit','2_measure_and_temp_invariants'),('temperature','2_measure_and_temp_invariants'),('ingredient','2_measure_and_temp_invariants'),('cell_swap','3_table_and_text_stability'),('columns','1_no_column_interleaving'),('missing_page','5_holdout_10_pages_completed'),('slow','4_latency_under_30s_limit'),('production','6_production_truth_preserved'),('reference_after','3_table_and_text_stability')])
def test_corrupt_candidate_cannot_pass(gate_fixture,kind,criterion):
    run,mutation,*_=gate_fixture
    mutation['kind']=kind
    report=run()
    assert report.verdict=='FAIL'
    assert report.criteria_checklist[criterion] is False


def test_missing_references_do_not_consume_holdout(gate_fixture):
    run,mutation,truth,_,root=gate_fixture
    (truth/'page_011.json').unlink()
    report=run()
    assert report.verdict=='NOT_EVALUATED'
    assert not mutation['called']
    json_path,md_path=save_gate_g2_report(report,root/'reports')
    assert 'NOT_EVALUATED' in md_path.read_text()
    assert 'Zezwolono' not in md_path.read_text()
    assert json.loads(json_path.read_text())['criteria_checklist']['2_measure_and_temp_invariants'] is None


def test_missing_duration_cannot_be_zero_latency(gate_fixture):
    run,mutation,*_=gate_fixture
    mutation['kind']='missing_duration'
    report=run()
    assert report.verdict=='NOT_EVALUATED'
    assert report.p95_duration_s is None
    assert report.criteria_checklist['4_latency_under_30s_limit'] is None


@pytest.mark.parametrize('damage',['source_hash','reference_hash','image_hash'])
def test_provenance_is_required_before_conversion(gate_fixture,damage):
    run,mutation,truth,manifest,_=gate_fixture
    target=manifest if damage=='source_hash' else truth/'page_011.json'
    data=json.loads(target.read_text())
    if damage=='source_hash': data['source_pdf_sha256']='wrong'
    elif damage=='reference_hash': data['metadata']['source_pdf_sha256']='wrong'
    else: data['source_image_sha256']='wrong'
    target.write_text(json.dumps(data))
    assert run().verdict=='NOT_EVALUATED'
    assert not mutation['called']


def test_cli_returns_nonzero_for_unmeasured_gate(gate_fixture):
    from typer.testing import CliRunner
    from src.pipeline import app
    _, mutation, _, manifest, root = gate_fixture
    result = CliRunner().invoke(app, ['gate-g2-evaluate', '--input-pdf', str(root/'fixture.pdf'),
                                     '--manifest', str(manifest), '--output-dir', str(root/'reports')])
    assert result.exit_code == 2, result.output
    assert 'NOT_EVALUATED' in result.output
    assert not mutation['called']
    assert len(list((root/'reports').glob('*.json'))) == 1
