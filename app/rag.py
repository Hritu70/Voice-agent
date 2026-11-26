import threading

import chromadb

from . import config

# Chroma's built-in ONNX embedding function avoids the heavyweight sentence-transformers/PyTorch stack.
# The client/collection are created lazily so importing this module never fails or touches disk.
_col = None
_lock = threading.Lock()


def _collection():
    global _col
    if _col is None:
        with _lock:
            if _col is None:
                config.CHROMA_DIR.mkdir(parents=True, exist_ok=True)
                client = chromadb.PersistentClient(path=str(config.CHROMA_DIR))
                _col = client.get_or_create_collection("kb_docs", metadata={"hnsw:space": "cosine"})
    return _col


def _chunks(text, size=600):
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    out, cur = [], ""
    for p in paras:
        if cur and len(cur) + len(p) > size:
            out.append(cur)
            cur = p
        else:
            cur = f"{cur}\n\n{p}" if cur else p
    if cur:
        out.append(cur)
    return out


def ingest():
    col = _collection()
    ids, docs, metas = [], [], []
    for f in sorted(config.KB_DIR.glob("*.md")):
        for i, chunk in enumerate(_chunks(f.read_text(encoding="utf-8"))):
            ids.append(f"{f.stem}-{i}")
            docs.append(chunk)
            metas.append({"source": f.name})
    if ids:
        col.upsert(ids=ids, documents=docs, metadatas=metas)
    # Remove chunks left over from files that were deleted or got shorter.
    existing = set(col.get(include=[])["ids"])
    stale = list(existing - set(ids))
    if stale:
        col.delete(ids=stale)
    return len(ids)


def retrieve(query):
    col = _collection()
    n = col.count()
    if n == 0:
        return []
    r = col.query(query_texts=[query], n_results=min(config.TOP_K, n))
    docs = (r.get("documents") or [[]])[0] or []
    metas = (r.get("metadatas") or [[]])[0] or []
    dists = (r.get("distances") or [[]])[0] or []
    return [{"text": d, "source": (m or {}).get("source", "unknown"), "dist": dist}
            for d, m, dist in zip(docs, metas, dists) if dist is not None and dist <= config.MAX_DIST]
