"""VLM observations are advisory; unresolved findings require actual-frame review."""
import json
from .local_models import run_local
from .models import PageObservation


def audit_clip(store,clip,output):
    book=store.get('book',clip['book_id']);shot=store.get('shot',clip['shot_id'])
    facts=[dict(fact_id=f['fact_id'],claim=f['claim'],kind=f['kind']) for f in book['facts'] if f['fact_id'] in shot['fact_ids']]
    frames=[f for f in store.all('frame') if f['clip_id']==clip['clip_id'] and f['asset_version']==clip['asset_version']]
    prompt='''请只观察当前实际视频帧，返回严格JSON：
{"visible_objects":[{"name":"名称","description":"可见外观","bbox":[0,0,1,1]}],"explicit_text":"确实能读出的字或留空","visual_summary":"实际画面","uncertainties":["不确定处"],"potential_conflicts":["与原书有明确冲突才写；没有则空列表"]}
最多4个对象。不允许把给定原书事实照抄为可见观察。没有出现某个物体不自动等于矛盾；正文叙述与画面瞬间不一定同时发生。不得声称审核通过。原书参照仅用于对照：'''+json.dumps(facts,ensure_ascii=False)
    observations=[];findings=[];elapsed=0;peak=0
    for f in frames:
        result=run_local('vlm',output/f['frame_id'],images=[str(store.asset_path(f['image_asset_id']))],prompt=prompt,max_tokens=650)
        elapsed+=result['elapsed_ms'];peak=max(peak,result['peak_memory_bytes'])
        parsed=result.get('parsed');status='passed'
        try:
            if not isinstance(parsed,dict) or not isinstance(parsed.get('potential_conflicts'),list):raise ValueError()
            PageObservation.model_validate({k:v for k,v in parsed.items() if k!='potential_conflicts'})
            conflicts=parsed['potential_conflicts']
            if not all(isinstance(c,str) for c in conflicts):raise ValueError()
        except ValueError:status='needs_correction';conflicts=['模型输出不符合约定结构，须直接核对实际帧。']
        for n,text in enumerate(conflicts,1):findings.append(dict(finding_id=f['frame_id']+f'_finding{n}',frame_id=f['frame_id'],text=text,status='open'))
        observations.append(dict(frame_id=f['frame_id'],t_ms=f['t_ms'],raw_text=result['raw_text'],parsed=parsed,schema_status=status))
    return dict(clip_id=clip['clip_id'],asset_version=clip['asset_version'],observations=observations,findings=findings,status='needs_review',
                model_id=result['model_id'] if frames else None,usage=dict(local_inference_ms=elapsed,peak_memory_bytes=peak,external_calls=0))
