# 页语：A方首批可运行原型

更新：2026-11-12，服务v0.4.0 + B端交互服务v0.1.0 + React前端。AB端已完成整合，本仓库为单仓库交付。

## 一键演示（推荐入口）

双击 `启动演示.command`（演示模式，不占GPU）：自动启动A端API(8765)与B端交互服务(8801)，浏览器打开 `http://127.0.0.1:8801` 即可体验完整系统：书架 → 播放已审核视频 → 点击画面识别对象 → 文字/语音提问（本地mlx-whisper识别+浏览器朗读，回答带可验证证据，回答结束自动恢复阅读）→ 导入绘本 / OCR校对 / 生成进度管理页。

需要生成新视频时另用 `启动本地服务.command`（含GPU worker）。评测：`evaluation/interaction/` 下 `make_qa_dataset.py`（自动题集69题）→ `run_qa_eval.py dev|sealed`（当前dev 29/29、sealed 39/40）→ `touch_benchmark.py`（68次触控100%）→ `latency_report.py`。B端队友接入手册见 `docs/B方联调说明.md`，可运行包见 `outputs/页语_B方联调包_v04.zip`。

## 历史进度（v0.3阶段）

更新：2026-11-10，服务v0.3.0。已完成四本素材导入、本地VLM/TTS接入，以及首段 **真实本地图生视频→配音字幕→实际帧审核→接口发布**。当前结果见 [本地真实视频验收](docs/本地真实视频验收.md)，可直接播放 [带字幕样片](examples/首段本地AI成片/穿靴子的猫_AI演绎_字幕预览.mp4)。

四本书共115个文件页、24条带来源事实、12个角色条目、9个初始分镜草稿。Wan2.2在本机生成《穿靴子的猫》文件页8的33帧视频：512×384、8fps、4.125秒，实测约30分钟。真实Qwen3-TTS旁白合成后为6.25秒，末尾定格补齐2.125秒。动作幅度很小，属于单镜头技术样片，尚待队员试听和演示效果验收。39项工程测试通过。

Agnes鉴权有效，但已执行的排障和恢复请求均未获得video_id；本轮真实成片由本机Wan生成。VLM两份语义有误的草稿仍为待审核，不覆盖原书事实。

先看 [首批推进记录](docs/首批推进记录.md)，交接B方看 [B方联调说明](docs/B方联调说明.md)。

## 本机立即运行

在本文件所在目录，双击 `启动本地服务.command`。它会使用本轮已安装在工作区的Python环境，同时启动API与单个工作进程；关闭终端后两者停止。接口地址为 `http://127.0.0.1:8765`，交互式文档为 `http://127.0.0.1:8765/docs`。

也可以在终端分别运行：

```sh
# 本轮工作区的现成环境：项目目录下执行
../../work/.venv/bin/python -m uvicorn generation.app:app --host 127.0.0.1 --port 8765

# 另开终端，同一目录
../../work/.venv/bin/python -m generation.workflow
```

换电脑或解压开发包后，使用Python 3.12及FFmpeg/FFprobe：

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt
.venv/bin/python scripts/restore_snapshot.py  # 仅首次、数据库为空时执行
.venv/bin/python -m uvicorn generation.app:app --host 127.0.0.1 --port 8765
# 另开终端
.venv/bin/python -m generation.workflow
```

`requirements.txt`是直接依赖，`requirements.lock.txt`固定了本轮安装的完整依赖版本。启动脚本不自动安装软件。快照恢复会验证全部资产SHA256，并保护已有校对和任务数据。

## 验证与最小调用

```sh
# 本机请求绕过可能存在的系统代理
curl --noproxy '*' http://127.0.0.1:8765/v1/books
curl --noproxy '*' http://127.0.0.1:8765/v1/books/book_cat/manifest
curl --noproxy '*' http://127.0.0.1:8765/v1/books/book_mock/clips
curl --noproxy '*' http://127.0.0.1:8765/v1/books/book_cat/clips

# 提交新的MOCK任务；须有工作进程才能消费队列
curl --noproxy '*' -X POST http://127.0.0.1:8765/v1/clips \
  -H 'Content-Type: application/json' \
  -d '{"shot_id":"book_mock_p001_r1_shot1","mode":"mock","idempotency_key":"b-demo-001"}'

# 返回run_id后查询 /v1/runs/{run_id}。
# 新片段初始是needs_review；include_pending=true只供开发校对使用。
```

包内已经审核过的MOCK视频可直接用于播放器联调。每次创建新MOCK版本会使同一镜头的旧版本及证据过期，请用独立幂等键区分不同生成请求。

```sh
.venv/bin/python -m pytest tests -q
.venv/bin/python scripts/validate_snapshot.py
.venv/bin/python scripts/export_contracts.py
```

最新测试结果见 `docs/test_run.txt`、`docs/test_results.xml`；当前接口检查见 `docs/http_smoke_v03.json`（TestClient）。模型原始输出及失败记录见 `docs/模型接入实测.md`，资产检查见 `docs/snapshot_validation.json`。

## 四本书的入口

| 书籍 | 服务ID | 文件页 | 内容与OCR校对入口 |
|---|---|---:|---|
| 好一个埃米尔 | `book_emil` | 36 | [查看](data/books/emil/内容样例与文字校对.md) |
| 长袜子皮皮来了 | `book_pippi` | 27 | [查看](data/books/pippi/内容样例与文字校对.md) |
| 穿靴子的猫 | `book_cat` | 27 | [查看](data/books/cat/内容样例与文字校对.md) |
| 海洋女神 | `book_ocean` | 25 | [查看](data/books/ocean/内容样例与文字校对.md) |

文件页从1计数，包含封面、版权页及跨页图。`printed_page`仅在实际核对过的选定页填写，不能与文件页混用。EPUB按OPF spine排序；《海洋女神》重复封面保留并标记。

每书保留 `pages/raw/`、`pages/ocr.json`、OCR原始JSONL、`pages/quality_report.json`、`manifest.json`、角色和事件表、`storyboard.json`、`style_profile.json`、`references/`。服务端SQLite是后续修改的当前状态，书目录里的JSON是本轮初始快照。

## 导入新文件和使用OCR

把PDF/EPUB/PNG/JPEG放入 `data/inbox/`，通过 `POST /v1/books` 提交 `title`、`source_name`。API只接受此目录内的文件名，不接受电脑绝对路径。导入任务保留文件哈希，排队后文件被改动会失败。

macOS本地OCR采用Apple Vision。编译并给工作进程设置路径：

```sh
mkdir -p .local
swiftc scripts/vision_ocr.swift -o .local/vision_ocr
export YU_VISION_EXECUTABLE="$PWD/.local/vision_ocr"
.venv/bin/python -m generation.workflow
```

没有设置OCR时，扫描页返回 `ocr_not_run`；存在内嵌文字时保留其原始文字，并将边界框标成整页。文字顺序、分栏和注音均待复核。OCR失败保留标记，不用推测原文补齐。本轮未接PaddleOCR，也未实现透视矫正、反光遮挡检测或自动分栏。

## 模型运行与部署

本机私有启动配置已切换为`wan_local`，依照 [本地视频部署](docs/本地视频部署.md) 设置固定运行库和权重路径。换电脑时须另行安装；压缩包不含模型权重、原生程序或私有配置，默认不启动真实供应商。


`generation/providers.py`已实现Agnes 2.5的提交、video_id轮询、下载、输入哈希和恢复记录，默认仍须显式配置供应商。已知任务恢复不重新提交；仅对明确queue_full拒绝有限退避，预算在重启后保留，并受本次上传许可次数限制。提交超时、普通5xx、含未知任务标识的响应均禁止自动重投。当前首帧尝试Data URI；网关对该格式的成功受理尚未验证。未另行上传至公开图床。

选定页和分镜已由助手核对，仍待队员交叉审核；其他书页保持原有待校对状态。整书 `external_upload_allowed`仍为false。最初明确许可绑定单张裁片和一次提交。后续限次排障保留独立run_id与记录；4秒请求此前被拦截，取得用户明确授权后已提交一次，仍被队列拒绝。Pavo网页备用路线未上传，独立站点访问门禁拒绝后未绕过；本轮改用本机Wan。没有扩大到其他书页。提供的书仍是本地开发样本，未记为官方赛事授权数据。

本机启动脚本已读取 `.local/runtime.env`，可使用已安装的MLX环境与权重。换电脑时按 [模型部署说明](docs/模型接入实测.md) 单独安装模型环境；API环境无需安装MLX。VLM观察始终是待审核草稿，不自动覆盖确认事实。

本版只在本机回环地址提供开发服务，资产以ID读取；没有多用户鉴权和公网部署配置。工作进程使用本机文件锁，限一个进程；支持排队取消、中断识别和有限人工重试，尚不支持运行中取消供应商任务、分布式调度或不加区分的真实任务自动重投。

## 技术参考

本轮依照 [FastAPI官方文档](https://fastapi.tiangolo.com/tutorial/background-tasks/)、[PyMuPDF页面接口文档](https://pymupdf.readthedocs.io/en/latest/page.html)及 [Apple Vision文字识别文档](https://developer.apple.com/documentation/vision/recognizing-text-in-images)实现本地原型。为保留任务和重启状态，耗时工作交给独立SQLite工作进程。可继续查阅官方仓库 [FastAPI](https://github.com/fastapi/fastapi) 与 [PyMuPDF](https://github.com/pymupdf/PyMuPDF)。


## 跨平台部署（Windows / Linux / NVIDIA / CPU）

- 启动脚本：macOS用 `启动演示.command`；Linux/WSL用 `启动演示.sh`（bash，等价逻辑）。
- 模型推理路径：Apple Silicon走MLX/Metal（实测最优）；NVIDIA走CUDA；无GPU走CPU（速度换档，功能不变）。
- 云端API双通道：Seedance文生视频/豆包文生图/DeepSeek/智谱GLM统一适配器——云端模式下评审端仅需浏览器。
- 依赖：Python 3.12 + FFmpeg + Node.js（仅构建前端）；详见 2_项目说明书/3.3节部署与运行环境。
