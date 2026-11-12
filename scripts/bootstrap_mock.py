"""Rebuild synthetic test book and an unreviewed playable mock clip in a NEW data directory."""
import argparse
import hashlib
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from generation.ingest import page_record
from generation.media import make_mock
from generation.models import BookManifest, Entity, Event, Fact, SourceRef
from generation.store import Store
from generation.workflow import execute_run, grounded_shots, new_run


def bootstrap(store):
    if store.get('book', 'book_mock'):
        return store.get('book', 'book_mock')
    video = make_mock(store.root/'mock')
    page = page_record(store, 'book_mock', 1, video.parent/'mock.png', 'synthetic:fixture:1', is_mock=True)
    page.review_status = 'reviewed'
    ref = SourceRef(ref_id='mock_ref1', page_id=page.page_id, kind='original_image', locator='synthetic:fixture:1',
                    region=dict(x1=.3,y1=.24,x2=.7,y2=.76))
    fact = Fact(fact_id='mock_fact1', page_id=page.page_id, claim='画面中央有一本展开的书。', kind='visual_observation',
                source_ref_ids=[ref.ref_id], status='reviewed', reviewed_by='synthetic_fixture_author')
    book = BookManifest(book_id='book_mock', title='合成测试书（MOCK）', is_mock=True,
                        rights_status='team_synthetic_fixture', source_filename='mock.png',
                        source_sha256=hashlib.sha256((video.parent/'mock.png').read_bytes()).hexdigest(),
                        pages=[page], source_refs=[ref], facts=[fact],
                        entities=[Entity(entity_id='mock_entity1', name='书', description='矩形展开的书', source_ref_ids=[ref.ref_id])],
                        events=[Event(event_id='mock_event1', sequence=1, description='书出现在中央', fact_ids=[fact.fact_id], entity_ids=['mock_entity1'])])
    store.put('book', book.book_id, book)
    shot = grounded_shots(book)[0]
    shot.status='approved'
    store.put('shot', shot.shot_id, shot)
    run = new_run('video_generation', dict(shot=shot.model_dump()), True)
    execute_run(store, run)
    return book.model_dump()


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--data-dir', default=str(Path(__file__).resolve().parents[1]/'data'))
    args=p.parse_args()
    bootstrap(Store(args.data_dir))
    print('Mock book ready. Clip awaits frame review; use include_pending=true to inspect.')
