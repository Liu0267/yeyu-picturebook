"""Run in the isolated MLX environment. Inputs and inference stay local."""
import argparse
import hashlib
import json
import time
from pathlib import Path


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('request')
    args=parser.parse_args()
    request_path=Path(args.request)
    request=json.loads(request_path.read_text())
    model_path=Path(request['model_path']).resolve()
    output=Path(request['output_dir']).resolve()
    output.mkdir(parents=True,exist_ok=True)
    start=time.monotonic()
    import mlx.core as mx
    mx.random.seed(42)
    metadata=dict(kind=request['kind'],model_id=request['model_id'],seed=42,
                  model_config_sha256=hashlib.sha256((model_path/'config.json').read_bytes()).hexdigest(),
                  input_sha256=hashlib.sha256(request_path.read_bytes()).hexdigest(),is_mock=False)
    revision=model_path/'.cache/huggingface/download/config.json.metadata'
    if revision.exists():metadata['model_revision']=revision.read_text().splitlines()[0]
    if request['kind']=='vlm':
        from mlx_vlm import load,generate
        from mlx_vlm.prompt_utils import apply_chat_template
        model,processor=load(str(model_path))
        images=request['images']
        metadata['image_sha256']=[hashlib.sha256(Path(image).read_bytes()).hexdigest() for image in images]
        prompt=apply_chat_template(processor,model.config,request['prompt'],num_images=len(images))
        result=generate(model,processor,prompt,image=images,max_tokens=request.get('max_tokens',500),temperature=0.0,verbose=False)
        metadata.update(raw_text=result.text,generation_tokens=result.generation_tokens,prompt_tokens=result.prompt_tokens,
                        peak_memory_gb=result.peak_memory,finish_reason=result.finish_reason)
        text=result.text.strip()
        if text.startswith('```'):
            text='\n'.join(text.splitlines()[1:-1])
        try:metadata['parsed']=json.loads(text)
        except ValueError:metadata['parse_error']='MODEL_OUTPUT_NOT_JSON'
    elif request['kind']=='tts':
        import numpy as np
        import soundfile as sf
        from mlx_audio.tts.utils import load_model
        model=load_model(str(model_path))
        segments=[];sample_rate=None;token_count=0
        for part in model.generate_custom_voice(text=request['text'],speaker=request.get('speaker','Vivian'),
                                               language='Chinese',instruct=request.get('instruction','用温柔、清楚、自然的普通话讲述，语速稍慢。'),
                                               max_tokens=400,temperature=.7,verbose=False):
            segments.append(np.asarray(part.audio))
            sample_rate=part.sample_rate
            token_count+=part.token_count
        if not segments:raise RuntimeError('TTS returned no waveform')
        audio=np.concatenate(segments)
        path=output/'narration.wav'
        sf.write(path,audio,sample_rate,subtype='PCM_16')
        metadata.update(audio_file=path.name,sample_rate=sample_rate,duration_ms=round(len(audio)/sample_rate*1000),
                        audio_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),text=request['text'],
                        speaker=request.get('speaker','Vivian'),instruction=request.get('instruction',''))
        metadata['audio_tokens']=token_count
    else:raise ValueError('unsupported inference kind')
    metadata['elapsed_ms']=round((time.monotonic()-start)*1000)
    metadata['peak_memory_bytes']=mx.get_peak_memory()
    (output/'inference.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2))
    print(json.dumps(dict(status='succeeded',kind=request['kind'],elapsed_ms=metadata['elapsed_ms']),ensure_ascii=False),flush=True)


if __name__=='__main__':main()
