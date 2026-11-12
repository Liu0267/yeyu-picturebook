"""Export honest coverage and open review work without turning drafts into source facts."""
import collections,csv,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from generation.store import Store
root=Path(__file__).resolve().parents[1];s=Store(root/'data');rows=[];summary=[]
for b in s.all('book'):
    if b['is_mock']:continue
    for p in b['pages']:
        obs=[o for o in s.all('understanding') if o['page_id']==p['page_id'] and o['page_revision']==p['page_revision']]
        facts=[f for f in b['facts'] if f['page_id']==p['page_id']]
        rows.append(dict(book_id=b['book_id'],title=b['title'],page_id=p['page_id'],file_page=p['sequence'],printed_page=p['printed_page'] or '',page_revision=p['page_revision'],
                         original_image_asset_id=p['image_asset_id'],ocr_engine=p['ocr_engine'],ocr_lines=len(p['ocr_lines']),ocr_low_confidence_lines=sum(l['confidence'] is not None and l['confidence']<.7 for l in p['ocr_lines']),
                         reviewed_facts=sum(f['status']=='reviewed' for f in facts),fact_ids='|'.join(f['fact_id'] for f in facts),
                         model_observations=len(obs),reviewed_observations=sum(o['status']=='reviewed' for o in obs),schema_passed_observations=sum(o['schema_status']=='passed' for o in obs),independent_human_review='pending'))
    subset=[r for r in rows if r['book_id']==b['book_id']]
    summary.append(dict(book_id=b['book_id'],pages=len(subset),pages_with_model_drafts=sum(r['model_observations']>0 for r in subset),pages_with_reviewed_facts=sum(r['reviewed_facts']>0 for r in subset),facts=len(b['facts']),entities=len(b['entities']),events=len(b['events']),whole_book_accuracy='not measured',independent_human_review='pending'))
folder=root/'evaluation';folder.mkdir(exist_ok=True)
with (folder/'page_review_queue.csv').open('w',encoding='utf-8-sig',newline='') as f:
    writer=csv.DictWriter(f,fieldnames=rows[0].keys());writer.writeheader();writer.writerows(rows)
(folder/'content_coverage.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
# A separate blank instrument for actual people; never insert generated scores.
with (folder/'human_audiovisual_review_blank.csv').open('w',encoding='utf-8-sig',newline='') as f:
    writer=csv.writer(f);writer.writerow(['reviewer_id','blind_sample_code','character_identity_1to5','source_facts_1to5','style_1to5','visible_motion_1to5','temporal_continuity_1to5','voice_clarity_1to5','subtitle_sync_1to5','pass_or_fail','timestamped_issues'])
(folder/'coverage_scope.md').write_text('原始素材共115文件页。page_review_queue.csv展示每页OCR、当前版本模型草稿及来源事实覆盖；Schema通过仅代表结构合法。来源事实由助手对照选定原页整理，不等于全书准确率或独立人工审核。空白视听表留给实际评阅者填写，禁止将机器/助手自评填成真人盲评。\n')
print(json.dumps(summary,ensure_ascii=False))
