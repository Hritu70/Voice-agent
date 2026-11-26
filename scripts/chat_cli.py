"""Quick text checkpoint tester. Run from the project root: python -m scripts.chat_cli"""
import json

from app import agent, rag, db


def main():
    db.init_db()
    try:
        print("KB chunks:", rag.ingest())
    except Exception as e:
        print("Knowledge-base indexing failed:", e)
    print("Type 'q' to quit, 'end' to summarize the call.")
    while True:
        try:
            msg = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not msg:
            continue
        if msg.lower() in {"q", "quit", "exit"}:
            break
        if msg.lower() == "end":
            print(json.dumps(agent.end_call("cli"), ensure_ascii=False, indent=2))
            break
        out = agent.reply("cli", msg)
        print("aria>", out["reply"])
        print("src :", out["sources"])
        print("lead:", json.dumps(out["lead"], ensure_ascii=False))


if __name__ == "__main__":
    main()
