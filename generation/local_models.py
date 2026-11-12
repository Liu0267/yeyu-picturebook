"""Subprocess boundary keeps MLX models out of the FastAPI process and releases memory per job."""
import json
import os
import subprocess
from pathlib import Path
from PIL import Image
from .providers import ProviderError,ProviderNotConfigured


PAGE_PROMPT='''你是绘本图文校对助手。只依据当前图片，不使用故事常识补全。
请返回严格JSON，不要Markdown，不要思考过程：
{"visible_objects":[{"name":"物体或角色","description":"可见外观","bbox":[0.0,0.0,1.0,1.0]}],"explicit_text":"图片里能确定读出的中文正文，忽略拼音；看不清就留空", "visual_summary":"只描述实际可见内容", "uncertainties":["不确定之处"]}
bbox是左上原点的0到1坐标。原文里的物品若没有在画面清楚呈现，不列为可见对象。不要把原文中的动作当作图片中正在发生的动作。不得声称已审核通过。'''


def run_local(kind,output_dir,**inputs):
    python=os.getenv('YU_MODEL_PYTHON')
    model=os.getenv('YU_VLM_MODEL_DIR' if kind=='vlm' else 'YU_TTS_MODEL_DIR')
    if not python or not model or not (Path(model)/'config.json').is_file():
        raise ProviderNotConfigured('本地模型环境或权重目录未配置。')
    output=Path(output_dir).resolve()
    output.mkdir(parents=True,exist_ok=True)
    model_id='mlx-community/Qwen3-VL-4B-Instruct-4bit' if kind=='vlm' else 'mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-4bit'
    if kind=='vlm':
        images=[]
        for index,path in enumerate(inputs['images']):
            with Image.open(path) as image:
                image=image.convert('RGB')
                image.thumbnail((1280,1280))
                target=output/f'vlm_input_{index}.jpg'
                image.save(target,quality=94)
                images.append(str(target))
        inputs['images']=images
    request=dict(kind=kind,model_path=str(Path(model).resolve()),model_id=model_id,output_dir=str(output),**inputs)
    request_path=output/'request.json'
    request_path.write_text(json.dumps(request,ensure_ascii=False,indent=2))
    env=dict(os.environ,HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_IMPLICIT_TOKEN='1',TOKENIZERS_PARALLELISM='false')
    for key in ('AGNES_API_KEY','YU_AGNES_KEY_FILE'):env.pop(key,None)
    script=Path(__file__).resolve().parents[1]/'scripts/local_inference.py'
    try:
        result=subprocess.run([python,str(script),str(request_path)],capture_output=True,text=True,env=env,timeout=900)
    except subprocess.TimeoutExpired:
        raise ProviderError('LOCAL_MODEL_TIMEOUT','本地推理超时，已结束子进程。',True)
    (output/'process.log').write_text(result.stdout+'\n'+result.stderr)
    if result.returncode:
        raise ProviderError('LOCAL_MODEL_FAILED','本地模型推理失败，检查本地process.log。')
    return json.loads((output/'inference.json').read_text())


def understand_page(image_path,output_dir):
    return run_local('vlm',output_dir,images=[str(Path(image_path).resolve())],prompt=PAGE_PROMPT+'最多列出4个主要对象，每个外观描述不超过20字。',max_tokens=800)


def synthesize(text,output_dir):
    return run_local('tts',output_dir,text=text,speaker='Vivian',instruction='用温柔、清楚、自然的普通话给小朋友讲故事，语速稍慢，不额外添加文字。')
