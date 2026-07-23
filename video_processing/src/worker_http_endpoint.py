from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn

from search_engine import SearchEngine
from db_setup import connect_to_database
from config import Config
from dotenv import load_dotenv

app = FastAPI(title="Python Embedding Worker")

search_engine = None


@app.on_event("startup")
def startup_event():
    global search_engine
    load_dotenv()

    conn = connect_to_database()
    if conn:
        search_engine = SearchEngine(Config(), conn)
    else:
        print("CRITICAL: Could not connect to DB at startup.")

    print("AI Worker is ready to receive requests from Node.js!")


class SearchRequest(BaseModel):
    prompt: str
    exclude: list = []
    top_k: int = 48


@app.post("/api/search")
def do_search(request: SearchRequest):
    if not search_engine:
        raise HTTPException(status_code=500, detail="Search engine not initialized")

    raw_results = search_engine.search(request.prompt, request.exclude, request.top_k)
    enriched = search_engine.enrich_results(raw_results)

    return {"results": enriched}


def start():
    conf = Config()
    uvicorn.run(app, host=conf.search_engine_url, port=conf.search_engine_port)


if __name__ == "__main__":
    start()
