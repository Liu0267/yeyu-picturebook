# 页语 · YeYu — 原作可信互动伴读系统

**[简体中文](README.md) | [English](README_EN.md)**

把一本真实的纸质绘本，变成"会动、会答、可点"的互动读本 —— **每个回答都带可回放的证据**。

拍照 → OCR/图文理解 → 事实抽取 → LLM自动校对 → 分镜 → 本机AI视频生成 → 逐帧审核 → TTS配音 → 发布 → 语音问答/画面点选/证据回放/打断续读。

> ⚠️ 本仓库**不含任何绘本图像或故事文本**（版权原因），提供 Mock 演示数据与一键引导脚本；
> 接入你自己的绘本前，请确认你拥有相应授权。详见 [使用边界](#-使用边界)。

## ✨ 功能特性

- **原作可信视频生成**：事实表驱动分镜，每镜头绑定原书裁片与首尾帧约束；生成后逐帧审核并设**动作门控**（motion gate）——无"实际可感知动作"记录的片段不能发布
- **全本地推理链路**：OCR（Apple Vision / Xiaomi-OCR-0 双引擎）· VLM（Qwen3-VL-4B）· 视频生成（Wan2.2-TI2V-5B）· TTS（Qwen3-TTS）· ASR（mlx-whisper），在 Apple Silicon 上零上传运行；同时提供云端 API 统一适配层（DeepSeek / 豆包 / Seedance / 智谱GLM / Agnes）
- **证据可验证问答**：区分"书里说"（原书事实）与"画面里"（实际成片帧观察）；摘取式短答强制携带 `evidence_ids`，支持一键回放到书页或视频时间点；证据不足时明确拒答
- **两级触控识别**：L1 已审对象框匹配（<10ms）→ L2 按需截帧 VLM 定位命名 → 写回缓存；点击后可追问"它是谁"
- **打断续读状态机**：`READING / PAUSED / LISTENING / THINKING / SPEAKING / RESUMING` 显式状态机；提问冻结播放位置，旧播报经 seq+AbortController 取消，回答朗读结束自动恢复阅读
- **中文检索**：SQLite FTS5 unicode61 的 2-gram 化 + BM25 + 时间窗过滤（"刚才"≤t、"后来">t）
- **全链留痕可复现**：九节点流水线每步写入 RunRecord（输入哈希 / 模型版本 / 参数 / 资产哈希 / 完整日志）

## 🏗️ 架构

```
┌─────────────┐   REST    ┌──────────────────┐   REST   ┌───────────────────────┐
│ React 前端   │ ────────► │ B端交互服务 :8801 │ ───────► │ A端多Agent编排 :8765   │
│ 书架/播放器/  │           │ 状态机·问答·触控   │          │ Ingest→OCR→理解→分镜   │
│ 语音/点选    │           │ FTS5检索·本地ASR  │          │ →视频→TTS→帧校验→发布  │
└─────────────┘           └──────────────────┘          └───────────────────────┘
        本地模型层：Apple Vision · Xiaomi-OCR-0 · Qwen3-VL · Wan2.2 · Qwen3-TTS · mlx-whisper
        存储层：state.sqlite（版本化事实/资产/RunRecord） · interaction.sqlite（FTS5）
```

## 📦 安装

**依赖**：Python 3.12+ · Node.js 18+ · FFmpeg · [uv](https://docs.astral.sh/uv/)（推荐）

```bash
# 1. Python 依赖
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements.models.txt

# 2. 下载模型（约17GB，Apple Silicon 为 MLX 量化版）
python scripts/download_all_models.py --directory models
# 国内加速：export HF_ENDPOINT=https://hf-mirror.com
# Wan 视频模型（pinned SHA256）：python scripts/download_wan_models.py --directory models

# 3. 前端构建
cd web && npm install && npm run build && cd ..

# 4. 生成 Mock 演示数据（不含任何真实绘本）
python scripts/bootstrap_mock.py
```

## 🚀 使用示例

```bash
# 一键启动（A端 :8765 + 交互服务 :8801，自动打开浏览器）
./启动演示.sh          # macOS: ./启动演示.command

# 或分别启动
python -m uvicorn generation.app:app --host 127.0.0.1 --port 8765 &
python -m uvicorn interaction.app:app --host 127.0.0.1 --port 8801
```

打开 `http://127.0.0.1:8801` 后：

| 操作 | 说明 |
|---|---|
| 书架 → 播放 | 书页式播放器，仅播放 `approved` 片段，字幕/进度/状态徽章 |
| 点选画面 | L1 对象框命中直接命名；未命中区域截帧走 VLM 定位 |
| 语音提问 | 按住说话（本地 whisper）→ 摘取式短答 → 朗读 → 自动恢复阅读 |
| 证据回放 | 回答附 evidence 徽标，跳书页原图或视频时间点 |
| 导入绘本 | 上传 → OCR → 行级校对（版本递增）→ 理解 → 生成 → 发布 |

**API 示例**（问答，带证据）：

```bash
curl -X POST http://127.0.0.1:8801/v1/turns -H 'Content-Type: application/json' \
  -H 'X-Reading-Session: <session_id>' -d '{
    "seq": 1, "turn_id": "t1",
    "input": {"kind": "text", "text": "猫向主人要了什么？"},
    "snapshot": {"book_id": "mock_cat", "asset_version": 1, "media_time_ms": 0}
  }'
# → {"answer_text": "书里说：猫向主人要一双靴子、一件衣服和一只大口袋。",
#    "status": "answered", "evidence": [{"id": "mock_fact02", ...}]}
```

**评测**（自测口径）：

```bash
python evaluation/interaction/make_qa_dataset.py
python evaluation/interaction/run_qa_eval.py dev      # 自动题集回归基准
python evaluation/interaction/touch_benchmark.py      # 触控命中/拒识
python evaluation/interaction/latency_report.py       # P50/P95
```

## 📁 目录结构

```
generation/    A端编排与生成（workflow·providers·wan_local·frame_audit）
interaction/   B端交互服务（qa·indexer·touch·speech·session_store）
web/           React 18 + TypeScript 前端
shared/        OpenAPI 合同（31路由）+ 16个 JSON Schema（AB端契约测试）
scripts/       模型下载 / Mock引导 / 合同导出 / 快照
docs/          模型清单(SHA256)·架构与复现边界·验收文档
evaluation/    问答题集生成与评测·触控基准·延迟报告
```

## 🔒 使用边界

- 本仓库**不包含、不分发**任何真实绘本的页面图像、OCR 文本或由此生成的视频 —— 版权归原权利人
- `data/mock/` 为自制合成数据（`is_mock=True` 隔离），用于验证全链路协议
- 接入真实绘本前请自行确认授权范围；生成内容请遵守各模型许可证与版权法
- 模型权重均为公开开源版本（Apache-2.0 / MIT），清单与 SHA256 见 `docs/model_manifest.json`

## 🤝 贡献指南

欢迎 Issue / PR：

1. Fork → 建 feature 分支（`git checkout -b feat/xxx`）
2. 提交前：`npx tsc -b`（前端类型检查）+ `python -m pytest`（后端测试）
3. 新增功能请附带对应评测（evaluation/），或至少说明验证方式
4. 遵守 [使用边界](#-使用边界)：不提交任何受版权保护的绘本内容
5. commit message 用 conventional commits（`feat:` `fix:` `docs:` …）

## 📄 许可证

代码 [MIT](LICENSE)；各模型与第三方组件按其自身许可证（见 LICENSE 附录与 `docs/model_manifest.json`）。
