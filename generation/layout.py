"""Conservative two-column reading-order proposal. Acceptance is versioned review."""

def suggest_reading_order(lines):
    def box(line):
        r=line['region'];return r['x1'],r['y1'],r['x2'],r['y2']
    candidates=[line for line in lines if sum('\u3400'<=c<='\u9fff' for c in line['raw_text'])>=3]
    options=[]
    for n in range(30,71):
        x=n/100;left=sum(box(l)[2]<x-.005 for l in candidates);right=sum(box(l)[0]>x+.005 for l in candidates)
        crossing=len(candidates)-left-right
        if min(left,right)>=3 and crossing<=len(candidates)*.25:options.append((crossing,-min(left,right),abs(x-.5),x))
    if not options:return dict(status='no_column_split_proposed',line_ids=[l['line_id'] for l in lines],requires_review=True)
    split=min(options)[-1]
    spanning=sorted([l for l in lines if box(l)[0]<split<box(l)[2]],key=lambda l:box(l)[1])
    rest=[l for l in lines if l not in spanning];ordered=[]
    def columns(items):
        return sorted(items,key=lambda l:(0 if (box(l)[0]+box(l)[2])/2<split else 1,box(l)[1],box(l)[0]))
    for span in spanning:
        before=[l for l in rest if (box(l)[1]+box(l)[3])/2<box(span)[1]]
        ordered+=columns(before)+[span];rest=[l for l in rest if l not in before]
    ordered+=columns(rest)
    return dict(status='two_column_proposal',split_x=split,line_ids=[l['line_id'] for l in ordered],requires_review=True,
                limitation='Geometric proposal only. Marginal notes, speech balloons, RTL text and complex layouts require manual order.')
