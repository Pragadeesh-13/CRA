# Complaint Resolution Agent (CRA) — Troy

An autonomous AI-powered complaint resolution system built with Python (FastAPI, AsyncIO, PyTorch, Transformers) and an interactive web interface. The platform classifies incoming customer complaints, retrieves historical precedent resolutions from vector storage, extracts critical entities, routes tickets to target departments, and streams real-time formal resolution assessments via **Troy**, the Lead Autonomous Complaint Resolution Agent.

---

## Overview

When a customer complaint ticket is submitted, CRA Troy executes a unified intelligence pipeline:

1. **Category Classification**: Fine-tuned **DistilBERT** classifier determines complaint intent across multiple categories with calibrated confidence scores.
2. **Precedent Retrieval**: **Sentence-BERT (SBERT)** semantic vector search performs memory-mapped dot product similarity search across over 248,000 historical resolved tickets.
3. **Entity Extraction**: Rule-based and regular expression parsing identifies order IDs, reference numbers, dates, monetary amounts, and product names.
4. **Automated Department Routing & SLA Prediction**: Predicts target department assignment (Billing, Shipping, Technical Support, Hardware, etc.) and resolution time window.
5. **Troy's Resolution Assessment**: Troy streams a structured, empathetic, and actionable resolution plan directly to the agent console in real-time.

---

## System Architecture

```
Complaint-Resolution-Agent/
├── app/
│   ├── auth/           # AES-256-GCM encrypted credential vault and Firebase auth
│   ├── cra/            # CRA Machine Learning inference engine (DistilBERT, SBERT vector search)
│   ├── http/           # Server-Sent Events (SSE) broadcast engine
│   ├── prompts/        # Complaint title, summary, and Troy resolution prompt templates
│   ├── providers/      # OpenRouter, OpenAI, Anthropic, Groq, LM Studio, and Mock drivers
│   ├── reference/      # Reference reasoning and output streaming architecture
│   ├── simulation/     # Troy orchestrator and session management
│   ├── utils/          # Thought buffer parsing, content sanitization, and JSON extraction
│   ├── config.py       # Pydantic settings schema and environment parsing
│   └── main.py         # FastAPI application exposing REST and SSE streaming endpoints
├── CRA_Models/         # Trained model assets (DistilBERT weights, vector embeddings, XGBoost models)
├── public/             # Single-page web interface (Dark/Light theme, live SSE streaming)
├── tests/              # Pytest automated test suite
├── run.py              # Application launch entry point
└── requirements.txt    # Project runtime dependencies
```

---

## Key Features

- **End-to-End Machine Learning Pipeline**: Integrated DistilBERT category classifier, SBERT vector similarity search, and entity extractors.
- **Multi-Provider LLM Integration**: Pluggable provider architecture supporting local models via **LM Studio** (`http://127.0.0.1:1235/v1`), OpenRouter, OpenAI, Anthropic, and Groq.
- **Real-Time Token Streaming**: Server-Sent Events (SSE) stream tokens directly into Troy's resolution console with live auto-scroll.
- **Secure Key Management**: Client-provided API credentials are protected via AES-256-GCM encryption with per-session isolation (`KeyVault`).
- **Flexible Authentication**: Configurable authentication supporting standard credentials, optional guest modes, and Firebase OAuth.
- **Theme-Aware Interface**: Modern responsive UI with dark/light themes, glowing AI orb canvas, and ticket feed.

---

## Getting Started

### Prerequisites

- Python 3.11 or higher (verified on Python 3.14)
- Git
- (Optional) [LM Studio](https://lmstudio.ai/) for running local LLMs (e.g. `qwen/qwen3.5-9b` on port 1235)

### Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/Pragadeesh-13/CRA.git
   cd CRA
   ```

2. Create and activate a virtual environment:
   ```bash
   python -m venv .venv
   # Windows:
   .venv\Scripts\activate
   # Linux / macOS:
   source .venv/bin/activate
   ```

3. Install required dependencies:
   ```bash
   pip install -r requirements.txt
   ```

### Configuration

Create a `.env` file based on `.env.example`:
```bash
cp .env.example .env
```

| Variable | Description | Default |
|---|---|---|
| `PORT` | HTTP server port | `3000` |
| `LLM_PROVIDER` | LLM service provider (`lmstudio`, `openrouter`, `openai`, `anthropic`, `groq`) | `lmstudio` |
| `LLM_MODEL` | Model identifier string (e.g., `qwen/qwen3.5-9b`, `gpt-4o`) | `qwen/qwen3.5-9b` |
| `LLM_API_KEY` | Server-level default API key (not required for LM Studio) | `""` |
| `AUTH_MODE` | Authentication level (`optional`, `required`, `disabled`) | `optional` |
| `PUBLIC_URL` | Base public URL for the server instance | `http://localhost:3000` |

### Running the Application

Launch the server using the entry point script:
```bash
python run.py
```

Once running, navigate to [http://localhost:3000](http://localhost:3000) (or the port displayed in terminal) in your browser.

---

## Testing

Execute the test suite using `pytest`:
```bash
pytest
```

The test suite covers:
- CRA model inference and intelligence reporting
- Thought buffer token streaming and tag extraction
- Sanitization and content filtering
- Session security and credential encryption
- REST API and Server-Sent Event streaming endpoints
- Troy complaint resolution pipeline execution

---

## License

This project is licensed under the MIT License.
