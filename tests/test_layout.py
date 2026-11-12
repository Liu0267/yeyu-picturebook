from generation.layout import suggest_reading_order
from test_pipeline import seed,system


def test_two_columns_read_down_then_across_with_spanning_heading():
    def line(i,x,y,w):return dict(line_id=i,raw_text='合成测试文字',region=dict(x1=x,y1=y,x2=x+w,y2=y+.025))
    lines=[line('heading',.05,.01,.9)]
    for i in range(3):lines += [line('left'+str(i),.05,.2+i*.1,.38),line('right'+str(i),.55,.2+i*.1,.38)]
    proposal=suggest_reading_order(lines)
    assert proposal['status']=='two_column_proposal'
    assert proposal['line_ids']==['heading','left0','left1','left2','right0','right1','right2']
    assert proposal['requires_review'] is True


def test_review_rejects_missing_or_duplicated_reading_order_ids(system):
    client,store=system;b=store.get('book','book_mock');lines=b['pages'][0]['ocr_lines']
    body=dict(expected_revision=1,reviewer='editor',reading_order=['missing'])
    assert client.patch('/v1/pages/book_mock_p001/review',json=body).status_code==422
    body['reading_order']=[l['line_id'] for l in lines]
    assert client.patch('/v1/pages/book_mock_p001/review',json=body).status_code==200
    assert store.get('book','book_mock')['pages'][0]['page_revision']==2
