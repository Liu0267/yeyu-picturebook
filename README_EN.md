# YeYu — Trustworthy Interactive Picture-Book Companion

**[简体中文](README.md) | [English](README_EN.md)**

Turn a real physical picture book into an interactive read-along that can **move, answer, and be touched** — with **verifiable, replayable evidence behind every answer**.

Photo → OCR / page understanding → fact extraction → LLM auto-review → storyboard → on-device AI video generation → frame-by-frame audit → TTS narration → publish → voice Q&A / touch recognition / evidence replay / interrupt & resume.

> ⚠️ This repository **contains no copyrighted picture-book images or story text**. A synthetic Mock dataset and bootstrap scripts are provided instead. Make sure you have proper authorization before ingesting your own books. See [Usage Boundaries](#-usage-boundaries).

## ✨ Features

- **Faithful-to-source video generation**: fact-driven storyboards; every shot is bound to the original page crop with first/last-frame constraints; generated frames are audited frame-by-frame with a **motion gate** — clips without a verifiable "perceptible motion" record cannot be published
- **Fully local inference**: OCR (Apple Vision / Xiaomi-OCR-0 dual engine) · VLM (Qwen3-VL-4B) · video generation (Wan2.2-TI2V-5B) · TTS (Qwen3-TTS) · ASR (mlx-whisper) — zero upload on Apple Silicon; plus a unified cloud-API adapter layer (DeepSeek / Doubao / Seedance / Zhipu GLM / Agnes)
- **Evidence-verified Q&A**: separates "the book says" (book facts) from "the video shows" (observed frames); extractive short answers always carry `evidence_ids` with one-tap replay to the page or video timestamp; explicit refusal when evidence is insufficient
- **Two-tier touch recognition**: L1 audited bounding-box hit (<10ms) → L2 on-demand frame capture + VLM grounding → cached; follow up with "who is this?"
- **Interrupt-and-resume state machine**: explicit `READING / PAUSED / LISTENING / THINKING / SPEAKING / RESUMING`; questions freeze the playback position, old replies are cancelled via seq + AbortController, and reading resumes automatically after the answer is spoken
- **Chinese retrieval**: SQLite FTS5 with 2-gram tokenization + BM25 + time-window filters ("just now" ≤ t, "later" > t)
- **Reproducible by design**: every step of the nine-node pipeline writes a RunRecord (input hash / model version / params / asset hash / full logs)

## 🏗️ Architecture

```
┌─────────────┐   REST    ┌──────────────────┐   REST   ┌───────────────────────┐
│ React client │ ────────► │ Interaction :8801 │ ───────► │ Orchestration :8765   │
│ shelf/player/│           │ state machine·QA  │          │ Ingest→OCR→understand │
│ voice/touch  │           │ FTS5·local ASR    │          │ →video→TTS→audit→pub  │
└─────────────┘           └──────────────────┘          └───────────────────────┘
   Local models: Apple Vision · Xiaomi-OCR-0 · Qwen3-VL · Wan2.2 · Qwen3-TTS · mlx-whisper
   Storage: state.sqlite (versioned facts/assets/RunRecord) · interaction.sqlite (FTS5)
```

## 📦 Install

**Prerequisites**: Python 3.12+ · Node.js 18+ · FFmpeg · [uv](https://docs.astral.sh/uv/) (recommended)

```bash
# 1. Python dependencies
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements.models.txt

# 2. Download models (~17GB, MLX quantized for Apple Silicon)
python scripts/download_all_models.py --directory models
# Mirror acceleration: export HF_ENDPOINT=https://hf-mirror.com
# Wan video models (pinned SHA256): python scripts/download_wan_models.py --directory models

# 3. Build the frontend
cd web && npm install && npm run build && cd ..

# 4. Bootstrap Mock demo data (contains no real picture books)
python scripts/bootstrap_mock.py
```

## 🚀 Usage

```bash
# One-command start (A-side :8765 + interaction :8801, opens the browser)
./启动演示.sh          # or ./启动演示.command on macOS

# Or start manually
python -m uvicorn generation.app:app --host 127.0.0.1 --port 8765 &
python -m uvicorn interaction.app:app --host 127.0.0.1 --port 8801
```

Open `http://127.0.0.1:8801`:

| Action | What happens |
|---|---|
| Shelf → Play | Page-style player; only `approved` clips; subtitles / progress / state badge |
| Tap the video | L1 bounding-box hit names the object directly; otherwise frame capture goes to VLM grounding |
| Hold-to-talk | Local whisper ASR → extractive answer → spoken → reading resumes |
| Evidence replay | Answers carry evidence badges — jump to the page image or video timestamp |
| Ingest a book | Upload → OCR → line-level review (versioned) → understanding → generate → publish |

**API example** (Q&A with evidence):

```bash
curl -X POST http://127.0.0.1:8801/v1/turns -H 'Content-Type: application/json' \
  -H 'X-Reading-Session: <session_id>' -d '{
    "seq": 1, "turn_id": "t1",
    "input": {"kind": "text", "text": "What did the cat ask its master for?"},
    "snapshot": {"book_id": "mock_cat", "asset_version": 1, "media_time_ms": 0}
  }'
# → {"answer_text": "...", "status": "answered", "evidence": [{"id": "mock_fact02", ...}]}
```

**Evaluation** (self-reported regression baseline):

```bash
python evaluation/interaction/make_qa_dataset.py
python evaluation/interaction/run_qa_eval.py dev
python evaluation/interaction/touch_benchmark.py
python evaluation/interaction/latency_report.py
```

## 📁 Repository layout

```
generation/    A-side orchestration & generation (workflow·providers·wan_local·frame_audit)
interaction/   B-side interaction service (qa·indexer·touch·speech·session_store)
web/           React 18 + TypeScript frontend
shared/        OpenAPI contract (31 routes) + 16 JSON Schemas (cross-side contract tests)
scripts/       model download / Mock bootstrap / contract export / snapshots
docs/          model manifest (SHA256)·architecture & reproducibility·acceptance docs
evaluation/    QA dataset generation & eval·touch benchmark·latency report
```

## 🔒 Usage boundaries

- This repository **does not include or distribute** any real picture-book page images, OCR text, or videos derived from them — all rights belong to the original rights holders
- `data/mock/` is synthetic data we generated (`is_mock=True` isolation) for protocol verification
- Verify your authorization scope before ingesting real books; respect each model's license and copyright law
- Model weights are public open-source releases (Apache-2.0 / MIT); manifest and SHA256 in `docs/model_manifest.json`

## 🤝 Contributing

Issues and PRs are welcome:

1. Fork → feature branch (`git checkout -b feat/xxx`)
2. Before committing: `npx tsc -b` (frontend types) + `python -m pytest` (backend tests)
3. New features should ship with an evaluation (evaluation/) or at least describe the verification method
4. Respect [Usage Boundaries](#-usage-boundaries): never commit copyrighted picture-book content
5. Conventional commits (`feat:` `fix:` `docs:` …)

## 📄 License

Code [MIT](LICENSE); models and third-party components follow their own licenses (see the LICENSE appendix and `docs/model_manifest.json`).
