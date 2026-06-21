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


class VQAEngine:
    def __init__(self):
        print("Loading BLIP VQA Model...")
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.processor = BlipProcessor.from_pretrained("Salesforce/blip-vqa-base")
        self.model = BlipForQuestionAnswering.from_pretrained("Salesforce/blip-vqa-base").to(self.device)

    def answer(self, image_path: str, question: str) -> str:
        raw_image = Image.open(image_path).convert('RGB')
        inputs = self.processor(raw_image, question, return_tensors="pt").to(self.device)
        out = self.model.generate(**inputs)
        return self.processor.decode(out[0], skip_special_tokens=True)



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

    vqa_engine = VQAEngine()
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
        answer = vqa_engine.answer(request.image_path, request.question)
        return {"answer": answer}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def start():
    uvicorn.run(app, host="0.0.0.0", port=5000)

if __name__ == "__main__":
    start()