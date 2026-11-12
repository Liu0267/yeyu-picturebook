"""Per-run measurements and denominators, including failed and cancelled tasks."""
import collections,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from generation.store import Store
root=Path(__file__).resolve().parents[1];s=Store(root/'data');rows=[]
for run in s.all('run'):
    if run['is_mock']:continue
    receipt=root/'data/runs'/run['run_id']/'provider_state.json'
    provider=json.loads(receipt.read_text()) if receipt.exists() else {}
    rows.append(dict(run_id=run['run_id'],step=run['step'],status=run['status'],model_version=run['model_version'],attempts=run['attempts'],
                     input_hash=run['input_hash'],usage=run['usage'],error=run['error'],
                     native_profile=provider.get('request') if provider.get('provider')=='local_wan' else None,
                     has_video_task_id=bool(provider.get('video_id')),known_accepted_cloud_tasks=int(bool(provider.get('video_id')))))
report=dict(scope='all non-Mock registered runs; diagnostics with separate scratch receipts are reported separately',runs=len(rows),
            status_counts=dict(collections.Counter(r['status'] for r in rows)),records=rows,
            denominators='Different models/frame counts/prompts/profiles are not pooled as a single speed or quality benchmark.',
            cost_scope='Local external video API charge=0 for local inference only; device energy/amortization not measured. Agnes charges remain unknown without account bill. Built-in image_gen price not reported.')
(root/'evaluation/run_measurements.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(dict(runs=len(rows),status_counts=report['status_counts']),ensure_ascii=False))
