"""Transparent small-sample OCR evaluation; no invented whole-book accuracy."""
import unicodedata


def normalized_chinese(text):
    return ''.join(c for c in unicodedata.normalize('NFKC',text) if '\u3400'<=c<='\u9fff')


def edit_distance(reference,hypothesis):
    row=list(range(len(hypothesis)+1))
    for i,a in enumerate(reference,1):
        new=[i]
        for j,b in enumerate(hypothesis,1):new.append(min(new[-1]+1,row[j]+1,row[j-1]+(a!=b)))
        row=new
    return row[-1]


def evaluate_ocr(samples):
    rows=[]
    for sample in samples:
        reference=normalized_chinese(sample['reference_text']);hypothesis=normalized_chinese(sample['recognized_text'])
        if not reference:raise ValueError('empty ground truth')
        errors=edit_distance(reference,hypothesis)
        rows.append(dict(sample_id=sample['sample_id'],page_id=sample['page_id'],method=sample['method'],reference_chars=len(reference),
                         edit_errors=errors,cer=errors/len(reference),reference_text=sample['reference_text'],recognized_text=sample['recognized_text']))
    n=sum(r['reference_chars'] for r in rows);errors=sum(r['edit_errors'] for r in rows)
    return dict(sample_count=len(rows),reference_characters=n,edit_errors=errors,micro_cer=errors/n if n else None,
                normalization='NFKC; retain CJK unified ideographs only; exclude punctuation, digits and pinyin',
                reference_source='assistant source-image transcription, no independent human double review',
                scope='selected excerpts; not whole-book accuracy, not random sample, no confidence interval',samples=rows)
