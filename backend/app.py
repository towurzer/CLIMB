
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import os
import json
import random
#fastAPI is a framework for connecting to the server
app = FastAPI(title="Video Retrieval API")

# Allow React dev server to connect - this is for safety
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)



# just for now for mock results - randomly select 20 random values as a percentage to some pictures and acting like it is a video
def generate_mock_results(query: str, count: int = 20):
    rng = random.Random(hash(query))
    results = []
    for i in range(count):
        video_id = f"v{rng.randint(1, 200):05d}"
        shot_id = rng.randint(0, 50)
        fps = rng.choice([24, 25, 29.97, 30])
        start_frame = shot_id * rng.randint(60, 300)
        end_frame = start_frame + rng.randint(60, 300)
        results.append({
            "video_id": video_id,
            "shot_id": shot_id,
            "score": round(rng.uniform(0.3, 0.95), 4),
            "start_frame": start_frame,
            "end_frame": end_frame,
            "fps": fps,
            "start_time_ms": int(start_frame / fps * 1000),
            "end_time_ms": int(end_frame / fps * 1000),
            # Placeholder thumbnail - each combination gets a unique image
            "thumbnail_url": f"https://picsum.photos/seed/{query[:10]}_{i}/320/180",
        })
    # Sort by score descending
    results.sort(key=lambda x: x["score"], reverse=True)
    return results



@app.get("/api/search")
def search(q: str = Query(..., description="Text query for search")):
    """Search for video segments matching the text query."""
    results = generate_mock_results(q)
    return {"query": q, "count": len(results), "results": results}


@app.get("/api/videos")
def list_videos():
    """List all videos in the dataset."""
    # TODO: Replace with real video list from metadata.json
    rng = random.Random(42)
    videos = []
    for i in range(1, 201):
        video_id = f"{i:05d}"
        num_shots = rng.randint(8, 45)
        fps = rng.choice([24, 25, 29.97, 30])
        duration = rng.uniform(60, 600)
        videos.append({
            "video_id": video_id,
            "fps": fps,
            "duration_sec": round(duration, 1),
            "num_shots": num_shots,
            "thumbnail_url": f"https://picsum.photos/seed/vid{video_id}/320/180",
        })
    return {"count": len(videos), "videos": videos}


@app.get("/api/video/{video_id}")
def get_video_info(video_id: str):
    """Get metadata for a specific video."""
    return {
        "video_id": video_id,
        "fps": 25,
        "duration_sec": random.uniform(60, 600),
        "width": 1280,
        "height": 720,
        # In production, this will be a real file path
        "video_url": f"/videos/{video_id}.mp4",
    }


@app.get("/api/video/{video_id}/shots")
def get_video_shots(video_id: str):
    """Get all shots for a specific video (for browsing)."""
    shots = []
    current_frame = 0
    fps = 25
    for i in range(random.randint(10, 40)):
        length = random.randint(60, 300)
        shots.append({
            "shot_id": i,
            "start_frame": current_frame,
            "end_frame": current_frame + length,
            "fps": fps,
            "thumbnail_url": f"https://picsum.photos/seed/{video_id}_{i}/320/180",
        })
        current_frame += length
    return {"video_id": video_id, "shots": shots}


@app.get("/api/similar/{video_id}/{shot_id}")
def find_similar(video_id: str, shot_id: int):
    """Find shots visually similar to the given shot."""
    # TODO: Replace with real similarity search using embeddings
    results = generate_mock_results(f"similar to {video_id}")
    return {"source_video": video_id, "source_shot": shot_id, "results": results}


class DresSubmission(BaseModel):
    video_id: str
    start_time_ms: int
    end_time_ms: int


class VqaSubmission(BaseModel):
    text_answer: str
    video_id: str | None = None
    start_time_ms: int | None = None
    end_time_ms: int | None = None


@app.post("/api/dres/submit")
def submit_to_dres(submission: DresSubmission):
    """
    Submit a result to the DRES server.
    In production, this will forward the submission to the real DRES API.
    For now, it just returns a mock response.
    """
    # TODO: Replace with actual DRES API call
    print(f"[MOCK DRES] Submitting: {submission}")
    return {
        "status": "mock_success",
        "message": f"Would submit {submission.video_id} "
                   f"({submission.start_time_ms}-{submission.end_time_ms}ms) to DRES",
    }


@app.post("/api/dres/submit-vqa")
def submit_vqa_to_dres(submission: VqaSubmission):
    """
    Submit a VQA text answer to the DRES server.
    Optionally includes a video segment reference.
    """
    # TODO: Replace with actual DRES API call
    print(f"[MOCK DRES VQA] Answer: '{submission.text_answer}', "
          f"Video: {submission.video_id}")
    return {
        "status": "mock_success",
        "message": f"Would submit VQA answer '{submission.text_answer}' to DRES",
    }


@app.get("/api/health")
def health():
    """Health check endpoint."""
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)