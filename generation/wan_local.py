"""Local Wan TI2V inference through stable-diffusion.cpp; no uploads."""
import hashlib
import io
import json
import subprocess
import time
from pathlib import Path

from PIL import Image
from .providers import ProviderError,VideoResult
from .store import digest


class LocalWanVideoProvider:
    requires_external_upload=False
    model='Wan2.2-TI2V-5B-Q4_K_S+TAEHV-9055eee'

    def __init__(self,executable,model_dir,decoder="tae"):
        self.executable=Path(executable).resolve()
        self.model_dir=Path(model_dir).resolve()
        if decoder not in ('tae','native'):raise ValueError('decoder must be tae or native')
        self.decoder=decoder
        if decoder=='native':self.model='Wan2.2-TI2V-5B-Q4_K_S+VAE-ee6f4a4' 

    def generate(self,shot,reference_path,output_dir,run_id):
        output=Path(output_dir);output.mkdir(parents=True,exist_ok=True)
        decoder_file='taew2_2.safetensors' if self.decoder=='tae' else 'split_files/vae/wan2.2_vae.safetensors'
        files=[self.model_dir/name for name in ('Wan2.2-TI2V-5B-Q4_K_S.gguf','umt5-xxl-encoder-Q4_K_M.gguf',decoder_file)]
        if not self.executable.is_file() or not all(p.is_file() for p in files):
            raise ProviderError('LOCAL_VIDEO_NOT_CONFIGURED','缺少本机Wan运行程序或权重；未上传素材。')
        with Image.open(reference_path) as image:
            r=shot['reference_region'];image=image.convert('RGB')
            crop=image.crop((round(r['x1']*image.width),round(r['y1']*image.height),round(r['x2']*image.width),round(r['y2']*image.height)))
            crop.thumbnail((1440,1440))
            data=io.BytesIO();crop.save(data,format='JPEG',quality=93)
        reference=data.getvalue()
        width,height=512,384
        fps=8;frames=4*round(shot['target_duration_ms']/1000*fps/4)+1
        image_path=output/'input.png'
        with Image.open(io.BytesIO(reference)) as prepared:
            data=io.BytesIO()
            prepared.resize((width,height),Image.Resampling.LANCZOS).save(data,format='PNG')
        prepared_bytes=data.getvalue()
        request=dict(model=self.model,reference_sha256=hashlib.sha256(reference).hexdigest(),
                     input_sha256=hashlib.sha256(prepared_bytes).hexdigest(),
                     runtime_sha256=hashlib.file_digest(self.executable.open('rb'),'sha256').hexdigest(),
                     width=width,height=height,frames=frames,fps=fps,steps=20,seed=42,
                     prompt=shot['prompt'],negative_prompt=shot['negative_prompt'])
        end_bytes=None
        if shot.get('_end_reference_path'):
            end_source=Path(shot['_end_reference_path'])
            with Image.open(end_source) as end_image:
                data=io.BytesIO();end_image.convert('RGB').resize((width,height),Image.Resampling.LANCZOS).save(data,format='PNG')
            end_bytes=data.getvalue()
            request.update(end_source_sha256=hashlib.sha256(end_source.read_bytes()).hexdigest(),end_input_sha256=hashlib.sha256(end_bytes).hexdigest())
        if self.decoder=='native':request.update(decoder='native',native_vae_sha256=hashlib.file_digest(files[2].open('rb'),'sha256').hexdigest())
        fingerprint=digest(request);state_path=output/'provider_state.json'
        state=json.loads(state_path.read_text()) if state_path.exists() else {}
        video=output/'generated.mp4'
        if state and state['input_hash']!=fingerprint:
            raise ProviderError('PROVIDER_INPUT_CHANGED','本地视频输入已变化，请创建新任务。')
        if state.get('status')=='completed' and video.is_file():
            if hashlib.sha256(video.read_bytes()).hexdigest()!=state['output_sha256']:
                raise ProviderError('LOCAL_VIDEO_HASH_MISMATCH','本地视频文件与已完成记录不一致。')
            return VideoResult(video,run_id,self.model,dict(state['usage'],reused_completed_output=True))
        if state:
            raise ProviderError('LOCAL_VIDEO_RESTART_REQUIRED','已有本地生成记录，先核查运行日志；未自动重算。')
        (output/'reference.jpg').write_bytes(reference)
        image_path.write_bytes(prepared_bytes)
        if end_bytes is not None:(output/'end_input.png').write_bytes(end_bytes)
        state=dict(provider='local_wan',run_id=run_id,model=self.model,input_hash=fingerprint,request=request,
                   status='running',external_calls=0)
        def save():state_path.write_text(json.dumps(state,ensure_ascii=False,indent=2))
        save();raw=output/'generated.avi'
        command=[str(self.executable),'-M','vid_gen','--diffusion-model',str(files[0]),'--t5xxl',str(files[1]),'--taesd' if self.decoder=='tae' else '--vae',str(files[2]),
                 '-i',str(image_path.resolve()),'-p',shot['prompt'],'-n',shot['negative_prompt'],'-W',str(width),'-H',str(height),
                 '--video-frames',str(frames),'--fps',str(fps),'--steps','20','--cfg-scale','5.0','--sampling-method','euler',
                 '--flow-shift','3.0','--seed','42','--offload-to-cpu','--mmap','--backend','diffusion=metal,vae=metal,te=cpu',
                 '--auto-fit','off','--conv-direct','off','--flash-attn','on','--batched-cfg','off',
                 '-t','6','-o',str(raw.resolve())]
        if self.decoder=='native':command+=['--temporal-tiling','--vae-tiling','--vae-tile-size','256x256','--extra-tiling-args','temporal_tile_frames=4,temporal_decode_frames=1']
        if end_bytes is not None:command+=['--end-img',str((output/'end_input.png').resolve())]
        started=time.monotonic()
        try:
            with (output/'generation.log').open('w') as log:
                # native VAE全解码路径：编码~220s+T5~95s+采样~900s+分块解码可达30-60min，
                # 2400s会在解码阶段耗尽（run_b421 TimeoutExpired实证），放宽到5400s。
                result=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,timeout=5400)
            if result.returncode or not raw.is_file():
                raise RuntimeError('native generation failed')
            subprocess.run(['ffmpeg','-nostdin','-v','error','-y','-i',str(raw),'-an','-c:v','libx264','-crf','18',
                            '-pix_fmt','yuv420p','-movflags','+faststart',str(video)],check=True,timeout=120)
        except (subprocess.SubprocessError,OSError,RuntimeError) as exc:
            state.update(status='failed',error_type=type(exc).__name__);save()
            raise ProviderError('LOCAL_VIDEO_FAILED','本地视频生成未完成，保留日志；未自动重算或上传。') from None
        usage=dict(external_calls=0,external_api_cost=0,local_inference_ms=round((time.monotonic()-started)*1000),
                   requested_frames=frames,fps=fps,width=width,height=height)
        state.update(status='completed',output_sha256=hashlib.sha256(video.read_bytes()).hexdigest(),usage=usage);save()
        return VideoResult(video,run_id,self.model,usage)
