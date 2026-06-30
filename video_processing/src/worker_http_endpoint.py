from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn

from search_engine import SearchEngine
from db_setup import connect_to_database
from config import Config
from dotenv import load_dotenv

from transformers import BlipProcessor, BlipForQuestionAnswering
import torch
from PIL import Image

from vqa_engine import VQAEngine

app = FastAPI(title="Python Embedding Worker")

search_engine = None
vqa_engine = None


@app.on_event("startup")
def startup_event():
    global search_engine, vqa_engine
    load_dotenv()

    conn = connect_to_database()
    if conn:
        search_engine = SearchEngine(Config(), conn)
    else:
        print("CRITICAL: Could not connect to DB at startup.")

    vqa_engine = VQAEngine(Config())
    print("AI Worker is ready to receive requests from Node.js!")


class SearchRequest(BaseModel):
    prompt: str


class VQARequest(BaseModel):
    image_path: str
    question: str


@app.post("/api/search")
def do_search(request: SearchRequest):
    if not search_engine:
        raise HTTPException(status_code=500, detail="Search engine not initialized")

    raw_results = search_engine.search(request.prompt)
    enriched = search_engine.enrich_results(raw_results)

    return {"results": enriched}


@app.post("/api/vqa")
def do_vqa(request: VQARequest):
    if not vqa_engine:
        raise HTTPException(status_code=500, detail="VQA engine not initialized")

    try:
        answer = vqa_engine.answer_question(request.image_path, request.question)
        return {"answer": answer}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def start():
    conf = Config()
    uvicorn.run(app, host=conf.search_engine_url, port=conf.search_engine_port)

if __name__ == "__main__":
    start()