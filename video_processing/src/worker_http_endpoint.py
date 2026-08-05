from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import custom_logger
from config import Config
from db.connection import connect_to_database
from db.index_ops import apply_serve_tuning
from retrieval.engine import SearchEngine

search_engine = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global search_engine
    from dotenv import load_dotenv
    load_dotenv()
    logger = custom_logger.get_logger("worker")

    conn = connect_to_database()
    if conn:
        # hnsw.ef_search must be raised before any search runs: pgvector returns at most ef_search
        # rows from an index scan, so at the default of 40 the oversample step asks for 1000
        # candidates, quietly receives 40, and the rerank has almost nothing to work with.
        apply_serve_tuning(conn)
        search_engine = SearchEngine(conn)
    else:
        logger.error("Could not connect to the database at startup.")
    yield


app = FastAPI(title="CLIMB search worker", lifespan=lifespan)


class SearchRequest(BaseModel):
    prompt: str
    exclude: list = []
    top_k: int = 48
    collection: str | None = None
    weights: dict | None = None   # per-retriever RRF weights, for tuning without a restart
    depth: int | None = None


@app.get("/api/health")
def health():
    ready = search_engine is not None and search_engine.ready
    return {
        "status": "ok" if ready else "degraded",
        "search_engine_ready": ready,
        "device": search_engine.device if search_engine else None,
    }


@app.post("/api/search")
def do_search(request: SearchRequest):
    if not search_engine or not search_engine.ready:
        raise HTTPException(status_code=500, detail="Search engine not initialized")

    result = search_engine.search(
        request.prompt, exclude=request.exclude, top_k=request.top_k,
        collection=request.collection, weights=request.weights, depth=request.depth,
    )
    return {
        "results": result.results,
        "signals": result.signals_used,
        "timings_ms": result.timings,
    }


def start():
    conf = Config()
    uvicorn.run(app, host=conf.search_engine_url, port=conf.search_engine_port)


if __name__ == "__main__":
    start()
