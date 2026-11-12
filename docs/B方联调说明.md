# 给开发者B：v0.3.0联调说明

本轮完成接口协议草案，尚未获得B方实际确认。你可以立即使用合成MOCK书、可播放MP4、任务查询及实际视频抽帧推进页面和播放器。

## 启动和数据合同

按项目根目录 `README_generation.md` 启动API与工作进程。基地址为 `http://127.0.0.1:8765`。默认允许React/Vite的 `http://localhost:5173` 和 `http://127.0.0.1:5173`，换端口时通过 `YU_CORS_ORIGINS` 设置。

- `shared/contracts/openapi.json`：实际服务导出的路由合同。
- `shared/contracts/types.ts`：同一Pydantic Schema生成的TypeScript类型。
- `shared/schemas/*.schema.json`：BookManifest、ShotSpec、ClipAsset、FrameEvidence、RunRecord。
- `shared/fixtures/`：MOCK书、视频、片段、帧及运行记录。

API JSON统一为 `request_id / status / data / error / warnings`。`status`是请求状态；具体任务进度看 `data.status`。媒体接口成功时直接返回媒体二进制，失败仍返回JSON错误结构。没有百分比进度或SSE，先用轮询。

## 最短联调路径

1. `GET /v1/books` 读书单，选 `book_mock`。
2. `GET /v1/books/book_mock/manifest` 显示书页与来源。
3. `GET /v1/books/book_mock/clips` 得到一条已审核MOCK片段和3条实际抽帧。
4. 视频地址拼成 `/v1/assets/{asset_id}`；帧图拼成 `/v1/assets/{image_asset_id}`。不要把asset_id当文件路径。
5. 点击/检索坐标以原始视频内容区为准；本样例是640×360。坐标为左上原点、0—1归一化的 `x1,y1,x2,y2`，播放器黑边不计入。
6. 查询新任务：`POST /v1/clips`，然后轮询 `GET /v1/runs/{run_id}`。新片段会停在 `needs_review`，审核通过后才出现在默认片段列表。

```ts
import type { APIEnvelope, BookManifest, ClipAsset, FrameEvidence } from './types';

const base = 'http://127.0.0.1:8765';
const result: APIEnvelope<BookManifest> = await fetch(
  `${base}/v1/books/book_mock/manifest`
).then(r => r.json());
if (result.status === 'error') throw new Error(result.error?.message);

const published: APIEnvelope<{clips: ClipAsset[]; frame_evidence: FrameEvidence[]}> =
  await fetch(`${base}/v1/books/book_mock/clips`).then(r => r.json());
const clip = published.data?.clips[0];
const videoUrl = clip ? `${base}/v1/assets/${clip.asset_id}` : undefined;
```

## 接口与审核

| 接口 | 用途 / 状态码 |
|---|---|
| `POST /v1/books` | 导入data/inbox中的文件；202返回book_id/run_id |
| `GET /v1/books/{id}/manifest` | 当前书籍版本及证据 |
| `PATCH /v1/pages/{id}/review` | expected_revision/reviewer/corrections/reviewed_fact_ids；409表示并发冲突 |
| `GET/POST /v1/books/{id}/storyboard` | 读分镜 / 按已核对事实创建规则式草稿 |
| `PATCH /v1/shots/{id}/review` | expected_page_revision/reviewer；依赖页与事实须已审核 |
| `POST /v1/clips` | shot_id/mode/idempotency_key；202，不同步等待视频 |
| `GET /v1/runs/{id}` | queued/running/needs_review/succeeded/failed/cancelled/expired |
| `POST /v1/runs/{id}/cancel` | 仅允许取消queued |
| `POST /v1/runs/{id}/retry` | 仅failed且retryable且未达到两次上限 |
| `GET /v1/books/{id}/clips` | 默认只返回approved片段和reviewed帧 |
| `PATCH /v1/clips/{id}/review` | 核对实际帧后提交审核；不接受只拿生成提示词填观察 |
| `GET /v1/assets/{id}` | 二进制媒体，支持Range与ETag |

`include_pending=true`用于A/B开发校对，会包含待审核、失败、过期记录，不能直接混入正常观看列表。

校对原页后，页版本和书版本递增；该页所有旧片段/帧标为expired，响应返回 `invalidated_clip_ids`。B清除这些clip_id对应的检索和触控缓存，重新拉取manifest及片段列表。其他书页已通过的片段不受此次页级校对影响。没有推送事件，先在写入响应及后续轮询中处理。

同一镜头生成新版本也会令旧片段和旧证据过期；B以 `clip_id + asset_version` 作为索引键。时间码 `t_ms`、`duration_ms` 都是毫秒。

视频供应商明确队列拒绝用`AGNES_QUEUE_FULL`表示，可能受理但无video_id用`SUBMISSION_UNCERTAIN`表示；这两种停止状态不可由B自动创建新任务规避。任务usage保留提交次数、已知受理数及provider_error_code。

常见错误码：NOT_FOUND、VALIDATION_ERROR、VERSION_CONFLICT、IDEMPOTENCY_CONFLICT、STALE_INPUT、REVIEW_REQUIRED、FRAME_REVIEW_REQUIRED、MOCK_SCOPE_MISMATCH、PROVIDER_NOT_CONFIGURED、RETRY_NOT_ALLOWED。

## 本轮数据的真实性边界

`book_mock`、其3秒视频和3条帧证据都是 `is_mock=true`。视频来自合成图的静态编码，画面内写有MOCK；仅用于协议和播放器联调，没有旁白和音轨。它不计入真实AI视频生成验收。

四本真实书的书页、OCR、24条事实和9个草稿分镜已在A的完整本地开发包里。独立B方联调包只含合成素材，启动后不应期待出现四本书。B可以用完整包联调真实书页，当前A完整包内`GET /v1/books/book_cat/clips`可读取1条本地真实配音成片及3条已审核实际帧。

本轮未实现B方的ASR、问答、检索、触控或业务播放器。A已在本机接通真实VLM和TTS；Agnes鉴权对照通过，但排障与恢复请求仍被队列拒绝；现已通过本机Wan2.2完成首段真实成片。B方独立包只含Mock，不含模型权重、私有配置和四本原图。

完整A包新增 `/v1/pages/{id}/understandings` 和 `/v1/books/{id}/narrations`，可读取实际VLM草稿与WAV；推理入口及实测见 `模型接入实测.md`。VLM两次样例都有语义错误，均为needs_review，不能混入已确认原书事实或已审核视频证据。工程测试和小样推理不代表比赛正确率、视频质量、交互延迟或正式赛题验收通过。

## v0.3真实片段接入

A完整包的`book_cat`当前发布片段为`clip_3a59f686e1c34517a82d53c45ca327c3`，`asset_version=2`，`provenance=model_generated`、`is_mock=false`。媒体为512×384、8fps，成片6250ms；前4125ms来自Wan生成，末尾2125ms为配音定格补尾。3条实际帧时间码为0、3125、6125ms，其中末帧在定格区，不得把它描述为新的动态事件。审核来源是助手实际帧观察，未冒充人工终审。

`audio_asset_id`、`subtitle_asset_id`和`source_video_asset_id`分别提供真实WAV、WebVTT和原始模型视频。规范视频没有烧入字幕栏；`examples/首段本地AI成片/`中的字幕预览增加了96像素展示栏，仅供观看，B仍以规范视频512×384内容区及对应哈希、时间码构建索引。不要把字幕预览的512×480尺寸用于触控坐标。

低帧率视频现在按实际PTS提取证据，`t_ms`不保证为整数秒。片段经过配音会生成新版本并使原片和旧帧过期，B继续以`clip_id+asset_version`刷新缓存。独立B包仍只有合成Mock，可直接开始联调。
