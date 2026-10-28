from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import custom_logger
from config import Config
from db.connection import connect_to_database
from db.index_ops import apply_serve_tuning
from db.migrate import migration_status
from retrieval.engine import SearchEngine

search_engine = None
# Migrations the database has not had. The engine queries what they create, so searching a
# database that is behind fails on SQL nobody can read; this turns it into an instruction.
pending_migrations = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    global search_engine, pending_migrations
    from dotenv import load_dotenv
    load_dotenv()
    logger = custom_logger.get_logger("worker")

    conn = connect_to_database()
    if conn:
        # hnsw.ef_search must be raised before any search runs: pgvector returns at most ef_search
        # rows from an index scan, so at the default of 40 the oversample step asks for its
        # ANN_OVERSAMPLE candidates, quietly receives 40, and the rerank has almost nothing to work with.
        apply_serve_tuning(conn)
        pending_migrations = [f"{v} {n}" for v, n, applied in migration_status(conn) if not applied]
        conn.commit()
        if pending_migrations:
            logger.error(f"Database schema is behind ({', '.join(pending_migrations)}): "
                         f"run `python main.py migrate`. Searches are refused until then.")
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
    # Which sources to search: any of visual, ocr, asr, caption. None or empty means all of them.
    # A source left out is not run at all, so switching one off makes the search cheaper as well
    # as quieter. See SOURCE_RETRIEVERS in retrieval/engine.py.
    sources: list | None = None


@app.get("/api/health")
def health():
    ready = search_engine is not None and search_engine.ready and not pending_migrations
    return {
        "status": "ok" if ready else "degraded",
        "search_engine_ready": ready,
        "device": search_engine.device if search_engine else None,
        "pending_migrations": pending_migrations,
    }


def _require_engine():
    if pending_migrations:
        raise HTTPException(status_code=503, detail=f"Database schema is behind "
                                                    f"({', '.join(pending_migrations)}): run "
                                                    f"`python main.py migrate`")
    if not search_engine or not search_engine.ready:
        raise HTTPException(status_code=500, detail="Search engine not initialized")


@app.post("/api/search")
def do_search(request: SearchRequest):
    _require_engine()

    result = search_engine.search(
        request.prompt, exclude=request.exclude, top_k=request.top_k,
        collection=request.collection, weights=request.weights, depth=request.depth,
        sources=request.sources,
    )
    return {
        "results": result.results,
        "signals": result.signals_used,
        "timings_ms": result.timings,
        "temporal": result.temporal, # Null unless the prompt used the `A >> B` sequence syntax.
        # Null unless the prompt had requirements (text:, said:, tag:, video:): each one and how
        # many videos satisfied it.
        "filters": result.filters,
    }


class SimilarRequest(BaseModel):
    keyframe_id: int
    exclude: list = []
    top_k: int = 48
    collection: str | None = None


@app.post("/api/similar")
def do_similar(request: SimilarRequest):
    _require_engine()
    result = search_engine.similar(request.keyframe_id, exclude=request.exclude,
                                   top_k=request.top_k, collection=request.collection)
    return {"results": result.results, "timings_ms": result.timings}


def start():
    conf = Config()
    uvicorn.run(app, host=conf.search_engine_url, port=conf.search_engine_port)


if __name__ == "__main__":
    start()
