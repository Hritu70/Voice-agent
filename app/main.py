from contextlib import asynccontextmanager

from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse, HTMLResponse
from pydantic import BaseModel, Field

from . import agent, config, db, rag


@asynccontextmanager
async def lifespan(app):
    db.init_db()
    if not config.API_KEY:
        print("WARNING: GEMINI_API_KEY is not set. Copy .env.example to .env and add your key.")
    try:
        print(f"KB chunks indexed: {rag.ingest()}")
    except Exception as e:
        print(f"Knowledge-base indexing failed: {e}")
    yield


app = FastAPI(title="Autonomous AI Sales Voice Agent", lifespan=lifespan)


class ChatIn(BaseModel):
    session_id: str = Field(min_length=1, max_length=120)
    message: str = Field(min_length=1, max_length=8000)


class BookIn(BaseModel):
    session_id: str = Field(min_length=1, max_length=120)
    slot_id: int
    customer_name: str = Field(default="", max_length=200)


def _page(name):
    return (config.STATIC / name).read_text(encoding="utf-8")


@app.get("/", response_class=HTMLResponse)
def voice_ui():
    return _page("index.html")


@app.post("/chat")
def chat(body: ChatIn, background_tasks: BackgroundTasks):
    return agent.reply(body.session_id, body.message, background_tasks)


@app.post("/chat/stream")
def chat_stream(body: ChatIn):
    return StreamingResponse(agent.stream_reply(body.session_id, body.message),
                             media_type="text/plain; charset=utf-8")


@app.get("/session/{sid}")
def session(sid: str):
    s = agent.get_session(sid)
    return {"history": s["history"], "lead": s["lead"]}


@app.get("/slots")
def slots():
    return {"available": db.list_slots(True)}


@app.post("/book")
def book(body: BookIn):
    s = agent.get_session(body.session_id)
    name = body.customer_name.strip() or s["lead"].get("name") or "Customer"
    db.upsert_lead(body.session_id, s["lead"])
    booked = db.book_slot(body.slot_id, body.session_id, name)
    if not booked:
        raise HTTPException(status_code=409, detail="Slot is unavailable or already booked")
    s["lead"]["status"] = "booked"
    return {"status": "booked", "appointment": booked, "customer_name": name}


@app.post("/end-call/{sid}")
def end_call(sid: str):
    result = agent.end_call(sid)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.get("/dashboard")
def dashboard():
    return db.dashboard_data()


@app.get("/dashboard-ui", response_class=HTMLResponse)
def dashboard_ui():
    return _page("dashboard.html")
