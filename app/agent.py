import json
import re
import threading

from google import genai
from google.genai import types

from . import config, rag, db

SESSIONS = {}
_LOCK = threading.RLock()
_client = None
_client_lock = threading.Lock()

FALLBACK_ERROR = "I'm having a technical issue at the moment. Please try again in a moment."
FALLBACK_EMPTY = "Sorry, I couldn't generate a response just now. Could you repeat that?"


class MissingAPIKey(RuntimeError):
    pass


def get_client():
    """Create the Gemini client lazily so the app can start (and show a clear error) without a key."""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                if not config.API_KEY:
                    raise MissingAPIKey("GEMINI_API_KEY is not set. Copy .env.example to .env and add your key.")
                _client = genai.Client(api_key=config.API_KEY)
    return _client


def _gen_config(max_tokens, **kw):
    # Gemini 2.5 models "think" by default and thinking tokens count against max_output_tokens,
    # which often produced empty or truncated replies. Disable thinking on Flash models.
    if config.MODEL.startswith("gemini-2.5-flash"):
        kw["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
    elif config.MODEL.startswith("gemini-3"):
        kw["thinking_config"] = types.ThinkingConfig(thinking_level="low")
        max_tokens = max(max_tokens, 1024)
    else:
        max_tokens = max(max_tokens, 2048)  # leave room for thinking on models where it can't be disabled
    return types.GenerateContentConfig(max_output_tokens=max_tokens, **kw)


def _prompt(name):
    return (config.PROMPTS / name).read_text(encoding="utf-8")


def _fill(template, values):
    """Single-pass placeholder replacement so inserted text (e.g. KB content) is never re-substituted."""
    return re.sub(r"\{(\w+)\}", lambda m: values.get(m.group(1), m.group(0)), template)


def get_session(sid):
    with _LOCK:
        if sid not in SESSIONS:
            lead = {k: None for k in config.LEAD_FIELDS}
            lead.update(score=0, sentiment="neutral")
            SESSIONS[sid] = {"history": [], "lead": lead, "extract_lock": threading.Lock()}
        return SESSIONS[sid]


def _recent_context(history, turns=6):
    return "\n".join(f"{m['role']}: {m['content']}" for m in history[-turns:])


def _retrieval_query(history, current):
    # Lightweight history-aware query expansion: previous user turns + the current one.
    # The current message is already the last history item, so exclude it to avoid duplicating it.
    prior = [m["content"] for m in history[:-1][-4:] if m["role"] == "user"]
    return "\n".join(prior + [current])


def _make_system(s, ctx):
    # Customer-controlled lead values are passed as a clearly delimited data block, not instructions.
    return _fill(_prompt("system.md"), {
        "context": ctx,
        "lead": json.dumps(s["lead"], ensure_ascii=False),
        "fields": ", ".join(config.LEAD_FIELDS),
    })


def _prepare(sid, user_text):
    s = get_session(sid)
    with _LOCK:
        s["history"].append({"role": "user", "content": user_text})
        history = list(s["history"])
    try:
        hits = rag.retrieve(_retrieval_query(history, user_text))
    except Exception as e:
        print(f"[rag] retrieval failed: {e}")
        hits = []
    ctx = "\n\n".join(f"[{h['source']}]\n{h['text']}" for h in hits) or "NO RELEVANT DOCUMENTS FOUND"
    system = _make_system(s, ctx)
    contents = [
        types.Content(role="model" if m["role"] == "assistant" else "user", parts=[types.Part(text=m["content"])])
        for m in history if m["content"]  # Gemini rejects empty parts
    ]
    return s, hits, system, contents


def reply(sid, user_text, background_tasks=None):
    s, hits, system, contents = _prepare(sid, user_text)
    try:
        r = get_client().models.generate_content(
            model=config.MODEL, contents=contents,
            config=_gen_config(300, system_instruction=system))
        answer = (getattr(r, "text", None) or "").strip() or FALLBACK_EMPTY
    except MissingAPIKey as e:
        answer = str(e)
    except Exception as e:
        print(f"[agent] generate_content failed: {e}")
        answer = FALLBACK_ERROR
    with _LOCK:
        s["history"].append({"role": "assistant", "content": answer})
    # Extraction is intentionally moved off the response path. API caller gets the answer first.
    if background_tasks is not None:
        background_tasks.add_task(_extract, sid)
    else:
        threading.Thread(target=_extract, args=(sid,), daemon=True).start()
    return {"reply": answer, "lead": dict(s["lead"]), "sources": [h["source"] for h in hits]}


def stream_reply(sid, user_text):
    s, hits, system, contents = _prepare(sid, user_text)

    def gen():
        parts = []
        try:
            for chunk in get_client().models.generate_content_stream(
                    model=config.MODEL, contents=contents,
                    config=_gen_config(300, system_instruction=system)):
                txt = getattr(chunk, "text", None) or ""
                if txt:
                    parts.append(txt)
                    yield txt
        except MissingAPIKey as e:
            parts = [str(e)]
            yield str(e)
        except Exception as e:
            print(f"[agent] stream failed: {e}")
            if not parts:
                parts = [FALLBACK_ERROR]
                yield FALLBACK_ERROR
            else:
                yield "\n[Agent temporarily unavailable]"
        answer = "".join(parts).strip() or FALLBACK_EMPTY
        with _LOCK:
            s["history"].append({"role": "assistant", "content": answer})
        threading.Thread(target=_extract, args=(sid,), daemon=True).start()

    return gen()


def _parse_json_object(text):
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        return {}
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _clean_score(value, default=0):
    try:
        return max(0, min(100, int(float(value))))
    except (TypeError, ValueError):
        return max(0, min(100, int(default or 0)))


def _extract(sid):
    s = get_session(sid)
    lock = s["extract_lock"]
    if not lock.acquire(blocking=False):
        return
    try:
        with _LOCK:
            transcript = _recent_context(s["history"], turns=10)
            current = dict(s["lead"])
        system = _fill(_prompt("extract.md"), {
            "fields": json.dumps(config.LEAD_FIELDS, ensure_ascii=False),
            "current": json.dumps(current, ensure_ascii=False),
        })
        try:
            r = get_client().models.generate_content(
                model=config.MODEL, contents=transcript,
                config=_gen_config(400, system_instruction=system, response_mime_type="application/json"))
            data = _parse_json_object(getattr(r, "text", None) or "")
            if not data:
                return
            with _LOCK:
                updated = dict(s["lead"])
                for k, v in data.items():
                    if k in config.LEAD_FIELDS and v not in (None, ""):
                        # SQLite can't store lists/dicts; keep everything as plain text.
                        if isinstance(v, list):
                            v = ", ".join(str(x) for x in v)
                        updated[k] = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
                updated["score"] = _clean_score(data.get("score"), updated.get("score"))
                sentiment = data.get("sentiment", updated.get("sentiment"))
                updated["sentiment"] = sentiment if sentiment in ("positive", "neutral", "negative") else "neutral"
                s["lead"] = updated
            db.upsert_lead(sid, updated)
        except Exception as e:
            print(f"[agent] lead extraction failed: {e}")
    finally:
        lock.release()


def end_call(sid):
    s = get_session(sid)
    with _LOCK:
        transcript = "\n".join(f"{m['role']}: {m['content']}" for m in s["history"])
    if not transcript.strip():
        return {"error": "No conversation to summarize"}
    prompt = ("Summarize this sales call. Return JSON with keys summary (string), action_items (array of strings), "
              "final_lead_score (integer 0-100). Use only transcript evidence.\n\nTRANSCRIPT:\n" + transcript)
    current_score = s["lead"].get("score", 0)
    try:
        r = get_client().models.generate_content(
            model=config.MODEL, contents=prompt,
            config=_gen_config(800, response_mime_type="application/json"))
        data = _parse_json_object(getattr(r, "text", None) or "")
    except Exception as e:
        print(f"[agent] summary failed: {e}")
        data = {}
    if not data:
        data = {"summary": "Call ended. Summary generation failed.",
                "action_items": ["Review transcript manually"], "final_lead_score": current_score}
    score = _clean_score(data.get("final_lead_score"), current_score)
    items = data.get("action_items") or []
    if not isinstance(items, list):
        items = [str(items)]
    items = [str(i) for i in items]
    summary = str(data.get("summary") or "No summary available.")
    with _LOCK:
        s["lead"]["score"] = score
        lead = dict(s["lead"])
    db.save_call(sid, transcript, summary, items, lead)
    return {"summary": summary, "action_items": items, "final_lead_score": score, "lead": lead}
