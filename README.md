# Autonomous AI Sales Voice Agent (MVP)

Features: Gemini chat, history-aware retrieval (query includes recent turns), Chroma RAG, async/background lead extraction, BANT fields, SQLite lead/call/appointment tables, conflict-safe booking, end-call summaries/action items, streaming text endpoint, and browser mic/speech UI. Browser speech uses built-in SpeechRecognition and speechSynthesis (best in Chromium; support varies).

## Run locally
1. Python 3.10–3.13.
2. `python -m venv .venv` then activate it.
3. `pip install -r requirements.txt`
4. `cp .env.example .env` and put your Gemini API key in `.env`.
5. `uvicorn app.main:app --reload`
6. Open http://127.0.0.1:8000/

The app initializes SQLite and seeds available demo booking slots on first startup. RAG's Chroma default embedding function uses a lightweight ONNX embedding model instead of sentence-transformers/PyTorch. The model may download on first indexing.

## API
- `POST /chat` `{session_id, message}`: reply immediately; lead extraction is queued in FastAPI background task.
- `POST /chat/stream` `{session_id, message}`: streams response text. Lead extraction runs after streaming completes.
- `GET /session/{sid}`: in-memory conversation state (not intended for production).
- `GET /slots`: available demo slots.
- `POST /book` `{session_id, slot_id, customer_name?}`: books slot atomically; HTTP 409 if unavailable/already booked.
- `POST /end-call/{sid}`: generates and persists summary/action items/final lead score.
- `GET /dashboard`: returns leads, calls, appointments and available slots.
- `GET /dashboard-ui`: simple dashboard page.

## Notes and known MVP limits
- Conversation sessions are in memory; use Redis for production multi-instance deployment.
- Lead extraction runs in background, so `/chat` response may contain the prior lead state; poll `/session/{sid}` or dashboard shortly after.
- The voice UI uses browser-native speech recognition and speech synthesis. Availability and language support vary by browser/OS; it is not a full duplex streaming voice system.
- Streaming endpoint currently streams text, not audio chunks. The browser UI uses standard `/chat` for speech synthesis.
- RAG query expansion is lightweight history concatenation, not an extra LLM rewrite call.
- Booking is a demo workflow; authentication, timezone handling, notifications and external CRM integration are not production-ready.
- Keep `.env` private. Do not commit API keys.

## Demo path
Start the app, open the UI, ask about plans, answer qualification questions, request a demo, click Show demo slots, and book via the `/book` endpoint (Swagger at `/docs`). Then click End call & summarize. The dashboard JSON is at `/dashboard`.

## Architecture
Browser UI (mic/speech) -> FastAPI -> Gemini response -> Browser TTS. Backend also performs history-aware Chroma retrieval and background lead extraction -> SQLite. Booking endpoint atomically updates slot and appointment. End-call endpoint creates summary, action items and score.
