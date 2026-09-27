# Vera message engine (magicpin AI challenge)

Production-style **FastAPI** bot with **`compose(category, merchant, trigger, customer?)`**: hybrid Groq + deterministic templates, exposed under **`/v1/*`** for the judge harness.

**First checks for reviewers**

| Check | URL / action |
|--------|----------------|
| Liveness | `GET /v1/healthz` → `status`, uptime, context counts |
| Submission metadata | `GET /v1/metadata` → team, model, approach, contact |

---

## Approach (summary)

- **Hybrid composition:** [Groq](https://console.groq.com) `llama-3.3-70b-versatile` (primary) and `llama-3.1-8b-instant` (fallback), `temperature=0`, fixed **`LLM_SEED`** for repeatability. If the key is missing or the call fails, **`templates.py`** fallbacks run (no API required).
- **Grounding:** **`signals.py`** builds a sorted **facts** object from the four contexts only—no invented numbers, offers, or citations.
- **Playbooks:** **`playbooks.py`** dispatches by `trigger.kind` (send-as, CTA, must-include).
- **Semantic firewall:** **`semantic_compat.py`** + **`hydration.py`** sanitize cross-domain triggers and keep merchant-grounded safe copy when wiring is wrong.
- **Validation:** No URLs, taboo phrases, `send_as` must match scope; failed validation → one LLM repair attempt → template fallback.
- **Replies:** **`conversation.py`** — auto-reply streaks, hostile opt-out, intent commitment, off-topic decline.

## Tradeoffs

| Choice | Why |
|--------|-----|
| Groq | Fast inference; good instruction following |
| Template fallback | Keeps `/v1/tick` reliable within judge time limits |
| In-memory store | Matches brief; no Redis in starter repo |

---

## Environment variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `LLM_API_KEY` | For LLM path | Groq API key (omit locally to force templates) |
| `LLM_PROVIDER` | No | Default `groq` |
| `LLM_MODEL` / `LLM_FALLBACK_MODEL` | No | Model ids |
| `LLM_SEED` | No | Integer seed for reproducibility |
| `LLM_TIMEOUT_SECONDS` | No | Per-call timeout (default in code) |
| `BOT_TEAM_NAME` | No | Shown on `/v1/metadata` |
| `BOT_TEAM_MEMBERS` | No | Comma-separated or JSON array |
| `BOT_MODEL` | No | Label in metadata (defaults to `LLM_MODEL`) |
| `BOT_APPROACH` | No | Short description in metadata |
| `BOT_CONTACT_EMAIL` | Recommended | Contact in metadata |
| `BOT_VERSION` | No | Service version string |
| `BOT_SUBMITTED_AT` | No | ISO timestamp; if unset, server time is used |
| `PORT` | On Render | Injected by platform; optional locally (e.g. `8080`) |

Copy **`.env.example`** → **`.env`** and edit. **Never commit secrets** (`.env` is gitignored).

---

## Local setup

```powershell
cd magicpin-ai-challenge
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
# Edit .env — set LLM_API_KEY for the LLM path
```

### Run locally

```powershell
uvicorn bot:app --host 0.0.0.0 --port 8080
```

**Public URL (e.g. ngrok):** `ngrok http 8080` — give the judge the **`https://…` base** (they call `https://host/v1/…`).

---

## Deploy on Render

1. Push this repo to GitHub and create a **Web Service** from the repo (or use **Blueprint** with `render.yaml`).
2. **Runtime:** Docker (`Dockerfile` at repo root). **`render.yaml`** sets `healthCheckPath: /v1/healthz`.
3. In the Render dashboard, create an environment variable **`LLM_API_KEY`** (secret) — Blueprint uses `sync: false` so it is not stored in the YAML.
4. Set **`BOT_CONTACT_EMAIL`** (and adjust `BOT_TEAM_*` if needed) to real values in the dashboard.
5. After deploy, confirm **`GET https://<service>/v1/healthz`** and **`GET …/v1/metadata`** return 200.

**Notes**

- **Free tier:** cold starts can add latency to the first request after idle; health checks help the service stay discoverable.
- **Port:** the image uses **`PORT`** from the environment (`CMD` expands `${PORT:-8080}`).

---

## Dataset & `submission.jsonl`

Offline artifact for the challenge: one **JSON object per line**, UTF-8, no trailing commas.

**Build steps**

```powershell
python scripts/expand_dataset.py
python scripts/build_submission.py
```

This writes **`submission.jsonl`** at the repo root (default: **30** lines from `expanded/test_pairs.json`). **`build_submission.py`** runs **`compose_message(..., use_llm=False)`** so the file is reproducible without an API key.

**Line shape** (each line is a JSON object)

| Field | Description |
|--------|-------------|
| `test_id` | Pair id from expanded dataset |
| `body` | Message body |
| `cta` | CTA kind (e.g. `binary_yes_no`) |
| `send_as` | `vera` or `merchant_on_behalf` |
| `suppression_key` | Idempotency / dedupe hint from trigger |
| `rationale` | Short internal reason for the template path |

---

## Judge simulator (optional)

1. Run the bot (`uvicorn` or the deployed URL).
2. At the top of **`judge_simulator.py`**, set **`BOT_URL`**, **`LLM_PROVIDER`**, **`LLM_API_KEY`**, **`LLM_MODEL`** as needed.
3. `python judge_simulator.py`

---

## API surface

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/v1/healthz` | Liveness + context counts |
| GET | `/v1/metadata` | Team / model / version / contact |
| POST | `/v1/context` | Push category / merchant / customer / trigger (idempotent by version) |
| POST | `/v1/tick` | Up to 20 actions for `available_triggers` |
| POST | `/v1/reply` | `send` / `wait` / `end` after merchant or customer message |

---

## Module map

| Module | Role |
|--------|------|
| `bot.py` | FastAPI app |
| `composer.py` | LLM + validation + `compose_message` |
| `templates.py` | Deterministic fallbacks |
| `conversation.py` | Reply routing |
| `state.py` | In-memory contexts & cache |
| `llm_provider.py` | Groq and other backends |
| `signals.py` | Facts pack, validation helpers |
| `playbooks.py` | Kind → playbook |
| `semantic_compat.py` | Trigger × category firewall |
| `hydration.py` | Null-safe copy, dates, category buckets |

---

## License / challenge

Synthetic dataset for the magicpin challenge only—not for real merchant outreach.
