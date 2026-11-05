import json,time,hashlib,platform,shutil
from pathlib import Path
from src.cleaner import clean_markdown_text
from src.auditor import audit_markdown_content
from src.block_validator import extract_culinary_measurements
from src.benchmark import normalize_text_for_metrics,compute_cer
root=Path.cwd(); manifest=json.loads(Path('benchmarks/fixtures/test_pages_manifest.json').read_text())
results=[]; total=0; changed=[]; flags=[]
for file in sorted(Path('benchmarks/fixtures/ground_truth').glob('*.json')):
 d=json.loads(file.read_text()); image=Path(d['source_image_path']); rows=[]
 for b in d['blocks']:
  if b['block_type'] in ['header_footer','separator']:continue
  text=b['text'];total+=1;out=clean_markdown_text(text)
  audit=audit_markdown_content(text)
  if audit.total_anomalies: flags.append({'page':d['page_no'],'block':b['block_id'],'issues':[a.sample_tokens for a in audit.anomalies]})
  if normalize_text_for_metrics(text)!=normalize_text_for_metrics(out):
   changed.append({'page':d['page_no'],'block':b['block_id'],'cer':compute_cer(text,out),'before':text[:160],'after':out[:160],'measurements_changed':extract_culinary_measurements(text)!=extract_culinary_measurements(out)})
 results.append({'page':d['page_no'],'blocks':len(d['blocks']),'quantities':sum(len(b.get('critical_quantities',[])) for b in d['blocks']),'image_exists':image.exists(),'image_hash_matches':image.exists() and hashlib.sha256(image.read_bytes()).hexdigest()==d['source_image_sha256'],'bbox_blocks':sum(b.get('bbox') is not None for b in d['blocks'])})
probes=['Add 1/2 tsp salt.','1/2 tsp salt','1/4 cup flour','Vitamin B and D are useful.','## TABLE OF CONTENTS','Bake at 180°C for 20 minutes.','Mix 1 1/2 cups flour.','| Salt | 1/2 tsp |','![Photo](assets/image.png)']
checks=[{'input':s,'output':clean_markdown_text(s).strip(),'measurements_changed':extract_culinary_measurements(s)!=extract_culinary_measurements(clean_markdown_text(s))} for s in probes]
mds=[]
for f in Path('output/markdown').glob('*.md'):
 txt=f.read_text();start=time.perf_counter();a=audit_markdown_content(txt);audit=time.perf_counter()-start; start=time.perf_counter();clean=clean_markdown_text(txt);elapsed=time.perf_counter()-start
 mds.append({'file':str(f),'bytes':f.stat().st_size,'lines':len(txt.splitlines()),'audit_seconds':audit,'anomalies':a.total_anomalies,'missing_chapters':len(a.missing_chapters),'clean_seconds':elapsed,'clean_changes_content':txt!=clean})
r={'date':'2026-11-05','scope':'read-only and in-memory; no extraction','ground_truth':results,'annotated_dev_pages':sum(x['page'] in [p['pdf_page'] for p in manifest['dev_pages']] for x in results),'annotated_holdout_pages':sum(x['page'] in [p['pdf_page'] for p in manifest['holdout_pages']] for x in results),'manifest_dev_pages':len(manifest['dev_pages']),'manifest_holdout_pages':len(manifest['holdout_pages']),'auditor_flagged_reference_blocks':flags,'onnxruntime_available_providers':__import__('onnxruntime').get_available_providers(),'cleaner_reference_blocks':total,'cleaner_changed_reference_blocks':changed,'cleaner_probes':checks,'production_markdown':mds,'epubcheck_on_path':shutil.which('epubcheck'),'pandoc_on_path':shutil.which('pandoc'),'python':platform.python_version()}
Path('docs/measurements/quality-baseline-2026-11-05.json').write_text(json.dumps(r,indent=2,ensure_ascii=False));print(json.dumps(r,ensure_ascii=False))
