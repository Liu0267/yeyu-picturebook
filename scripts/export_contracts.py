import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from generation import models
from generation.app import app

root=Path(__file__).resolve().parents[1]
definitions={}
for name in ('BookManifest','ClipAsset','FrameEvidence','RunRecord','ShotSpec','NarrationAsset','PageObservation','CompilationAsset','CompilationCreate','CompilationReview','BookContentReview','ObservationReview','PreprocessCreate','ClipReview','PipelineCreate'):
    schema=getattr(models,name).model_json_schema()
    (root/'shared/schemas'/f'{name}.schema.json').write_text(json.dumps(schema, ensure_ascii=False, indent=2))
    definitions.update(schema.get('$defs',{}))
    definitions[name]=schema
(root/'shared/contracts/openapi.json').write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2))

# TypeScript projection of the same schemas, no extra generator dependency.
def ts(schema):
    if '$ref' in schema:return schema['$ref'].split('/')[-1]
    if 'enum' in schema:return ' | '.join(json.dumps(x,ensure_ascii=False) for x in schema['enum'])
    if 'const' in schema:return json.dumps(schema['const'],ensure_ascii=False)
    if 'anyOf' in schema:return ' | '.join(ts(s) for s in schema['anyOf'])
    kind=schema.get('type')
    if kind=='array':
        if 'prefixItems' in schema:return '['+', '.join(ts(s) for s in schema['prefixItems'])+']'
        return f'Array<{ts(schema["items"])}>'
    if kind=='object':
        if not schema.get('properties'):
            extra=schema.get('additionalProperties',{})
            return 'Record<string, '+(ts(extra) if isinstance(extra,dict) else 'unknown')+'>'
        required=schema.get('required',[])
        return '{\n'+''.join(f'  {key}{"" if key in required else "?"}: {ts(value)};\n' for key,value in schema['properties'].items())+'}'
    return {'string':'string','integer':'number','number':'number','boolean':'boolean','null':'null'}.get(kind,'unknown')

types='// Generated from Pydantic contracts by scripts/export_contracts.py.\n'
types+='// Times: milliseconds. Regions: normalized top-left x1/y1/x2/y2.\n\n'
types+='\n\n'.join('export type '+name+' = '+ts(schema)+';' for name,schema in sorted(definitions.items()))
types+='\n\nexport type APIEnvelope<T> = { request_id: string; status: "ok" | "accepted" | "error"; data: T | null; error: ErrorInfo | null; warnings: string[] };\n'
(root/'shared/contracts/types.ts').write_text(types)
