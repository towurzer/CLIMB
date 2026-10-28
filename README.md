# CLIMB

A contest winning Content Localization system and Intelligent Multimedia Browser.

<img src="readme_images/competition_scores.png" alt="competition_scores" width="500"/>

<i>Figure 1: Competition scores. We are Group 10.</i>

---

A content-based video retrieval system designed for searching short video segments, focusing on Known-Item Search (KIS)
and Visual Question Answering (VQA) tasks, as well as Ad-Hoc Video search (AVS), inspired by the Video Browser Showdown (VBS).
The system provides an intuitive graphical user interface for interactive video exploration and integrates with the
Distributed Retrieval Evaluation Server (DRES) through its REST API, enabling seamless submission of retrieved video
segments.

## Getting Started - User

### Run CLIMB for the first time

In order to run CLIMB, you will need to make sure that the following things are fulfilled:

- You will need the processed media in `dataset/media/` , the keyframes under `media/kf/` and `media/thumbs/`. Set
  `CLIMB_MEDIA_DIR` if you keep them on an external drive instead
- You will need the source collections mounted somewhere and `CLIMB_SOURCE_ROOT` pointing at them. The videos are
  played straight from there, there is no separate web copy any more
- (To see how to produce all of that, see the "Video Processing" section under "Getting Started - Developer")
- A filled database: a dump of one, `dataset/climb_db.dump` from whoever ran the pipeline (`pg_dump -Fc`, see 1.0),
  or your own run of the pipeline ("Getting Started - Developer"). 

Then run `setup_climb.sh` (found in the root directory), with podman or docker installed:

```bash:
./setup_climb.sh
```

The first time it copies `.env.example` to `.env` and stops, so you can set `POSTGRES_PASSWORD` and
`CLIMB_SOURCE_ROOT` (see "Environment Variables"). Run it again and it creates `video_processing/.venv` (with
Python 3.13 if there is one, see 1.1) and installs the requirements; where `nvidia-smi` finds a GPU, that includes
torch for CUDA 12.6 and `paddlepaddle-gpu` (1.0). It creates and starts the `climb` and `climb_caching` containers,
restores `dataset/climb_db.dump` into the database if there is one and the database is still empty, and migrates
the schema. Running it again later only starts what exists and applies new migrations.

Once you are set up, simply run `start_climb.sh` (also found in the root directory)

```bash:
./start_climb.sh
```

### Restart CLIMB

If you already setup climb you can always restart it using the shell scripts

```bash:
./start_climb.sh
```

If we don't support your terminal emulator, and you don't want to have all processes running in the same one, you can

```bash:
./start_climb_using_conda.sh
```

but for that you will need to use conda, otherwise you are fine with plain python.

To setup the exact same conda environment used in this shell script, move to the video_processing folder and run
```bash:
conda env create -n climb -f conda_environment.yml
```

## How To use CLIMB

Using CLIMB is as simple as climbing a ladder ;)

When opening up the climb website, you will see a pretty empty screen. Don't be scared it's all how it's supposed to be:

<img src="readme_images/bootup.png" alt="bootup.png" width="500"/>

The header holds everything that is not a search: Search / Browse, the KIS / AVS toggle and the "Login to DRES" button.
At its very right end there is a small circle telling you whether CLIMB's own services are up. The frontend re-checks
every 5 seconds and the colour means:

- green: backend and embedding service both answer, everything works
- green with a yellow blink: everything works **except** the collaboration. The shared session service cannot be
  reached, so your teammates' submissions stop showing up. Searching, browsing and submitting to DRES are completely
  unaffected, which is why this is not orange.
- orange: the backend is up but the embedding service is not, so browsing, playback and submitting still work while text
  search does not. Usually you forgot to start the search engine (`python main.py serve`) or it could not
  reach the database
- red: the backend cannot be reached at all, nothing will work until it is back

Hovering the circle tells you which piece is missing and why.

But lets get some competition points. To maximize them I can recommend 2 things:

- sabotage your opponents
- connect to DRES in order to be able to submit frames to the server.
  In order to connect to dres, click the "Login to DRES" button in the top right of the header. A popup opens where you
  fill in the server URL, the evaluation name (e.g. IVADL26), your username and your password. Enter submits the form,
  Esc or a click outside closes it, and the popup closes by itself once you are connected.

Once connected the popup shows which evaluation you actually landed on, with its name on top and its id underneath. That
matters because DRES falls back to the first available evaluation if the name you asked for does not exist, so the name
alone does not tell you where your submissions are going. The header button is your status light: a grey dot and "Login
to DRES" while disconnected, a green dot and "DRES · &lt;evaluation name&gt;" once you are in. Click it again any time to
reopen the popup and reconnect.

### Searching

Type what you are looking for and hit Enter. Plain words search everything. Hover the search box and it lists the
rest: `text:` for text on screen, `said:` for speech, `tag:`, `video:` / `-video:`, and `A >> B` for one thing after
another, all explained in [1.9](#19-how-searching-actually-works). Clicking into the box shows your recent searches, and
Ctrl+K gets you back into it from anywhere.

Under the box sit the dataset dropdown and the sources. At VBS the task tells you which collection it is from, so pick
it, and the other two cannot put look-alikes on your page. The tickboxes decide what gets searched at all: Visual (the
picture), Caption, OCR, ASR, Info (the uploader's title, tags and description), and for LHE a second picture model,
PeskaVLP. Untick OCR when the subtitles keep winning over the picture. Picking a dataset sets two of them for you: Info
is only on for V3C, the only collection with uploader info, and ASR is off for MVK, because nobody talks underwater
(Whisper hears things anyway). Switching to AVS unticks OCR and Info as well, see "Turning a signal off".

<img src="readme_images/search.png" alt="search.png" width="500"/>

Results are **one card per keyframe**. This used to be one card per scene, on the theory that seven near-identical
frames of the same shot were a waste of a page. Turns out that was throwing away the most useful thing on the screen:
if the ten best keyframes are all the same shot, that shot is almost certainly the answer, and collapsing them to a
single card hides exactly that. In an AVS session a submitted scene drops out of the results anyway, so the repetition
costs you a page slot precisely once.

Each card shows its rank, its score and, underneath, the signals that found it: `OCR (3) Visual (41)` means a sign
matched at rank 3 and the picture agreed, distantly, at 41. ⟲ on the card finds similar keyframes, ✕ throws the video
out (for MVK the whole dive) and searches again. A sequence (`A >> B`) puts the hits of the later stages in a strip
under the card, each with how long after the previous one it came. The line above the results counts them, says which
dataset you searched and, once you use `text:`, `said:` or `tag:`, how many videos each of them left, so an empty page
tells you which one emptied it.

Scrolling through search results is cheap now. The first page runs the actual search and everything after it is
served from cache, so page four arrives in about 15ms instead of running a fourth search and throwing three pages of
it away.

### The sidebar

If you click on a card it opens up a sidebar, from top to bottom:

- what you clicked: video, scene, score, where the scene starts and ends, and the keyframe's time
- a small part of the video you clicked on. "Loop segment" keeps it on repeat. On an MVK clip, `◀ 0001` and `0002 ▶`
  step to the clips before and after it in the same dive
- the time that gets submitted, From and To, filled in from the keyframe. Type over it if you know better
  (`(h:)mm:ss.cc`); a time it cannot read greys out the submit button rather than sending rubbish
- "Submit to DRES", which asks "Yes, submit!" before anything goes out
- "Exclude video from search" and "Find similar", the same as ✕ and ⟲ on the card. Find similar forgets your query and
  looks for keyframes that look like this one
- a small band of all keyframes of the video (for MVK, of the whole dive). "Browse in Fullscreen" opens it in Browse,
  on the keyframe you are on
- the "VQA-Answer" field, where you can enter a textual answer for a Visual Question Answering Task. Two submit
  buttons let you choose what goes to DRES: "Submit image + text" sends your answer together with the selected scene,
  "Submit text only" sends just the answer for tasks where DRES expects no media item. Both ask for confirmation first,
  double-clicking a button confirms it.
- the submission log, everything you sent and what DRES said about it

The arrow keys go through the results, Esc deselects.

<img src="readme_images/similarity_search.png" alt="similarity_search.png" width="500"/>

Every submission shows a result box with the DRES status code, a plain language summary and the verdict,
e.g. 200 · Accepted & graded · CORRECT. Green means a correct verdict, red a wrong or rejected one, and amber
(202 · Accepted - awaiting verdict) means DRES took the submission but hasn't judged it yet.

### Browsing

If you don't want to search for something specific, but want to just browse through all the videos select the browsing
tab on the top right. It has the same dataset dropdown as the search (one setting for both) and a filter by video id.
We will dynamically load more videos on scrolling and cache the Videos loaded for faster retrieval. An MVK dive is one
card, `Ambon_Apr2012 · 3 clips · 21 shots`.

<img src="readme_images/browsing.png" alt="browsing.png" width="500"/>

Click a video and you get every keyframe it has, with the scene number on each and its time underneath (for MVK the
whole dive, clip by clip). Click a keyframe and it opens in the sidebar, the same as a search result.

<img src="readme_images/select_video.png" alt="select_video.png" width="500"/>

### AVS

Every submission shows a result box with the DRES status code, a plain language summary and the verdict, 
e.g. 200 · Accepted & graded · CORRECT. Green means a correct verdict, red a wrong or rejected one, and amber 
(202 · Accepted - awaiting verdict) means DRES took the submission but hasn't judged it yet.

Working together starts with **Join Team** in the top bar, next to "Login to DRES". Pick your team from the list (each
one says how long it has left, a team nobody used for a week is gone) or type a new name, give the password and your
username, Connect. A name nobody has taken yet creates the team with that password. The button turns green once you are
in. Your username is what your teammates see on your hints, and who the session records as having submitted a scene.

In a team you can send each other **hints**: "Send to team" under the player sends the keyframe you are on (untick it
to send only the video, for MVK the whole dive). It is made for the keyboard: after the click, Enter sends; Tab opens a
note, and Tab, Enter sends it with the note. Your teammates get a large red popup at the bottom of the screen, "Anna:
have a look at 00191 · 00:12.40", and a red frame around the whole window. Both stay until you deal with it, because
someone deep in their own search has to decide to ignore a hint, not miss it. Clicking the popup opens that video in
the fullscreen keyframe browser, scrolled to the keyframe and with it selected (or at the top with nothing selected,
when only the video was sent); the × ignores it. Every hint also lands in the inbox next to the team button.

For Ad-hoc Video Search (AVS) tasks, switch the KIS/AVS toggle in the top bar (next to Search/Browse) to AVS. That
also unticks OCR and Info, which do not help on AVS topics (see "Turning a signal off"); tick them back by hand if a
task is about on-screen text.
AVS is collaborative: in a team everybody is in the team's session, and its 4-letter code is shown so the people next
to you can check they see the same one. New session starts a fresh one for the whole team (the next AVS task), after a
confirmation, since it un-hides everything for everybody. Without a team, New session gives you a session of your own.
Each shot you submit is sent as its own submission and is then hidden from everyone in the session's search results, 
so no one on the team submits the same scene twice. Videos where you already got a correct hit are dimmed and marked 
"covered", since extra shots from the same video barely raise the AVS score. For MVK that means the whole dive: a hit
in one clip covers every clip of the same recording. Sessions are deleted after 2 hours of
inactivity, which is meant to comfortably outlive a task block rather than punish you for thinking.

Sessions are shared through a small separate service (see [2.6](#26-avs-collaboration-across-machines)), so
collaboration works even when everyone runs their own CLIMB on their own laptop - which at a competition you very much
want to, because that keeps thumbnails and video on localhost instead of on the venue wifi. If that service ever
becomes unreachable you get a "collab offline" badge next to the session code and the header light starts blinking
yellow: you keep the session and every scene already known to be taken, you just stop hearing about new ones until it
is back. Nothing about searching or submitting changes. If instead the session genuinely expired you get a
"Session XXXX expired" banner - a different message on purpose, because one of them means "wait a moment" and the other
means "start a new session".

Well and that's it.

## Environment Variables

In order for CLIMB to work correctly you will need a `.env` file in the root directory: copy `.env.example`, which
`setup_climb.sh` does for you the first time, and change the password. Its service lines:

```text:
POSTGRES_DB_NAME=CLIMB_DB
POSTGRES_PASSWORD=password
SEARCH_ENGINE_URL=localhost
SEARCH_ENGINE_PORT=5000
REDIS_URL=redis://localhost:6379
VIDEOS_CACHE_TTL_SECONDS=30
DB_PORT=5432
DB_HOST=localhost
BACKEND_URL=localhost
BACKEND_PORT=8000
FRONTEND_PORT=3000
ALLOWED_ORIGIN_REGEX=^https?:\/\/(?:[a-zA-Z0-9-]+\.)*q1studios\.at(?::\d+)?$
```
If you want teams and AVS collaboration across machines you need two more. Leave them out and both still work, they
are just private to your own CLIMB. `CLIMB_USER` is only the fallback name for submissions made outside a team:

```text:
AVS_SESSION_SERVICE_URL=https://example.org
AVS_SESSION_TOKEN=supersecrettoken
CLIMB_USER=AlexHonold
```

And one for where the source collections live (the videos, their scene files and info JSONs). The pipeline reads
from it and the backend plays videos out of it, see "The source collections" below:

```text:
CLIMB_SOURCE_ROOT=/run/media/you/Data
```

That is the service wiring, and it is deliberately not the full list. The pipeline reads about thirty more
`CLIMB_*` knobs, media and work directories, decode workers, batch sizes per model, OCR backend, caption model,
the RRF weights, the temporal query defaults. **`config.py` is the reference for those**; this file documents
the ones you need to get started rather than pretending to be complete.

---

## Getting Started - Developer

### Project Structure

```text
.env                        # Environment variables and secrets
datasets.json               # where each collection's videos / scenes / info live under CLIMB_SOURCE_ROOT
start_climb.sh                # Launch ClIMB
setup_climb.sh                # Setup CLIMB
start_climb_using_conda.sh    # Launch CLIMB using Conda
backend/
    openapi.yaml            # API specification
    package.json            # Backend dependencies
    server.js               # Express API server
    mock-dres-server.js     # a fake DRES to test submissions against (port 8080)
    avsSessionClient.js     # talks to the AVS session service, and mirrors it so search never waits
    teamClient.js           # this stack's team membership, held like the DRES login
    serviceUrls.js          # resolves the backend / search engine / avs service base urls from env
    datasets.js             # finds a source video to play, from datasets.json
    controller/             # Route handlers
    models/                 # Database models and queries
    routes/                 # Express routes

climb-avs-service/          # teams and AVS sessions, deployed once for everyone
    server.js               # 10 routes wired to the controllers. That is the entire service
    sessions.controller.js  # the AVS session routes
    teams.controller.js     # the team routes: list, connect, snapshot, new session, hint
    auth.js                 # bearer token + rate limit, the whole door
    sessionStore.js         # the sessions themselves, in memory; the backend requires this too for solo mode
    teamStore.js            # the teams: password hash, current session, hints; idle out after a week
    Containerfile           # one dependency, no DB, no dataset, no media, no DRES credentials
    package.json

dataset/                    # local folder only
    media/                  # everything we keep (point CLIMB_MEDIA_DIR at your SSD instead)
        kf/                 # 384px keyframes
        thumbs/             # 160px keyframes for the result grid
    work/                   # everything we throw away again (CLIMB_WORK_DIR)
        raw/                # downloaded source videos
        cand/               # candidate frames, before selection picks from them
        audio/              # extracted audio, waiting for Whisper

frontend/
    index.html              # Application shell
    package.json            # Frontend dependencies
    vite.config.js          # Vite dev server config
    public/                 # Static assets
    src/
        App.jsx             # Main application component
        App.css             # Styles
        main.jsx            # React entry point
        dresSubmission.js   # formatting of DRES submission results
        timecode.js         # timecode <-> ms <-> frame conversion
        sceneKey.js         # one canonical scene identity for grid, browser and AVS
        useRecording.js     # all clips of the selected video's recording, prev / next clip
        useTeam.js          # the team connection, its poll, which hints are new
        hints.js            # how a hint reads: "Anna: have a look at 00191 · 00:12.40"
        components/
            SearchBar.jsx   # Search input with history
            ResultsGrid.jsx # Thumbnail grid of results
            VideoPlayer.jsx # Video player with segment loop and prev / next clip
            ShotBrowser.jsx # Filmstrip of every keyframe in the video's recording
            KeyframeTime.jsx # editable keyframe time fields
            VideoBrowser.jsx# Browse all videos
            VqaAnswer.jsx   # VQA answer input + DRES submit buttons
            SubmissionLog.jsx # Submission history log
            AvsSessionBar.jsx # the AVS session: code, counts, New session
            TeamLoginModal.jsx # Join Team: create or join, username
            TeamInbox.jsx   # every hint of the team
            HintToast.jsx   # a teammate's hint: red popup and screen frame until dealt with
            SendHint.jsx    # send the selected video to the team
            DresLoginModal.jsx # DRES login popup
            BackendStatusDot.jsx # backend liveness circle in the header

video_processing/
    requirements.txt           # python dependencies        
    pytest.ini                 # test config; `-m "not integration"` is the fast run
    tests/                     # 157 tests: pure logic + HTTP integration + DRES/AVS
    migrations/                # numbered .sql schema migrations, applied in order
        001_core_schema.sql    # videos / scenes / keyframes / text tables / ingest_jobs
        002_vector_function_costs.sql # teaches the query planner that vector math isn't free
        003_videos_damaged_flag.sql   # marks videos whose bitstream only partly decodes
        004_model_aware_embeddings.sql # lets more than one embedding model coexist
        005_text_embeddings.sql        # transcript vectors
        006_caption_embeddings.sql     # caption vectors
    src/
        config.py              # Settings
        custom_logger.py       # Logging utilities
        db_setup.py            # Prints the podman command, nothing more
        main.py                # CLI entry point (subcommands)
        utils.py               # Utility functions
        worker_http_endpoint.py # Search Engine HTTP interface
        retrieval/
            query_parser.py    # search box string -> structured query
            retrievers.py      # the signals, plus the Scope every one of them filters by
            temporal.py        # `A >> B` chains: linking stages across time
            fusion.py          # reciprocal rank fusion
            engine.py          # ties them together
        db/
            connection.py      # DB connections + a commit/rollback context manager
            migrate.py         # migration runner (with checksums, so nobody edits history)
            index_ops.py       # builds/drops the expensive ANN + GIN indexes
        pipeline/
            runner.py          # enqueue / fetch / purge / status -- the batch lifecycle
            probe.py           # ffprobe metadata (fps, duration, dimensions, audio)
            device.py          # picks cuda / mps / cpu
            embed.py           # SigLIP2 vectors
            ocr.py             # on-screen text
            caption.py         # VLM shot descriptions
            asr.py             # Whisper transcripts
            text_embed.py      # transcript + caption embeddings
            datasets.py        # finds the collections under CLIMB_SOURCE_ROOT, reads folders or .tgz alike
            shot_boundaries.py # parses scene files -> scenes
            decode.py          # the one-pass FFmpeg stage
            keyframe_selection.py # thins candidate frames down to 2-32 per shot
            paths.py           # where everything lives on disk, derived not stored
    logs/                     # Log files (local only)
   
readme_images/              # Images displayed in readme
```

### 1. Video Processing

In order to get started you will first need to process the videos: cut them into scenes, pick keyframes, and run every
model over them.
To do that go into the video processing part of CLIMB by running

```bash
cd video_processing
```

#### 1.0 The whole collection on a GPU server, the short version

If you just want to embed the whole collection on a GPU server, it gets easier. But first of all, thank you :) 
Everything below is explained in the sections that follow. This is just the order.

1. **Install.** Python 3.13 (PaddleOCR has no wheel for 3.14, and OCR falls back to a worse engine with a warning),
   FFmpeg with the `libopus` encoder, and an NVIDIA driver whose `nvidia-smi` header says `CUDA Version: 12.6` or higher.
   `./setup_climb.sh` installs everything else ("Getting Started - User"). By hand, from `video_processing`:

   ```bash
   pip install -r requirements.txt torch==2.12.1+cu126 torchvision==0.27.1+cu126 \
       --extra-index-url https://download.pytorch.org/whl/cu126
   pip install paddlepaddle-gpu==3.3.1 --timeout 120 -i https://www.paddlepaddle.org.cn/packages/stable/cu126/
   pip install nvidia-cudnn-cu12==9.10.2.21 nvidia-cusparselt-cu12==0.7.1 nvidia-nccl-cu12==2.29.3 \
       nvidia-nvshmem-cu12==3.4.5
   ```

   The third line is not optional. paddlepaddle-gpu pins older NCCL, cuDNN and cuSPARSELt than torch and installs
   them over torch's: with Paddle's NCCL, `import torch` fails (`undefined symbol: ncclGetLsaMultimemDevicePointer`),
   and with Paddle's cuDNN, torch refuses its first convolution. The line puts torch's back. cuDNN 9 and NCCL 2 run
   code built for an older minor version, so Paddle runs on them as well, and pip listing Paddle's pins as conflicts
   afterwards is expected.

   CUDA 12.6 is where the three GPU libraries meet. faster-whisper's ctranslate2 exists for CUDA 12 only (it opens
   `libcublas.so.12`); Paddle's GPU runtime is not on PyPI and comes per CUDA version; and torch's own PyPI wheel is
   CUDA 13.0, which wants driver 580 and leaves Whisper without its cuBLAS. torch for cu126 brings CUDA 12's cuBLAS
   and cuDNN as pip packages, the ASR stage loads cuBLAS from there, and Paddle for cu126 runs on the same libraries.
   `main.py check` FAILs, with the line that fixes it, when torch cannot use the GPU or Whisper finds no CUDA 12
   cuBLAS, and warns about a Paddle built for another CUDA, a card under 12 GB or one without bf16.
2. **Database.** `python main.py schema` prints the podman command (docker takes the same arguments), and
   `setup_climb.sh` runs it for you. Keep the `--shm-size=8g` in it, without it the index build at the end crawls.
3. **`.env`** in the repo root, copied from `.env.example`: the `POSTGRES_*` / `DB_*` lines from "Environment
   Variables", plus

   ```text
   CLIMB_SOURCE_ROOT=/where/the/collections/are   # VBS/videos/..., V3Cmeta/..., MVKmeta/..., LHEmeta/...
   CLIMB_MEDIA_DIR=/big/disk/climb/media           # keyframes we keep, ~33 MB per hour of video
   CLIMB_WORK_DIR=/big/disk/climb/work             # scratch, a few GB at a time
   ```
4. **Trial run first, on a throwaway database.** Some things only show up on a new GPU: bf16, Paddle and torch
   sharing CUDA libraries in one venv, the caption and ASR models at their full size. Finding out after a day of GPU
   time is the expensive way, so put two videos per collection through every stage first. That should be quick and makes
   sure there are no suprises after hours of GPU-Time. 
   From `video_processing/src`:

   ```bash
   podman exec climb createdb -U postgres climb_trial
   (
     # In a subshell, so none of this leaks into the real run.
     export POSTGRES_DB_NAME=climb_trial CLIMB_MEDIA_DIR=/big/disk/climb-trial/media \
            CLIMB_WORK_DIR=/big/disk/climb-trial/work
     python main.py migrate && python main.py check
     for c in V3C MVK LHE; do
       python main.py run --collection $c --limit 2 \
         --stages fetch,ingest-shots,decode,select,purge,embed,ocr,caption,asr,embed-text
     done
   )
   ```

   Every stage logs `Running on cuda (...)`; `cpu` there means the GPU was not found. While the caption model loads,
   transformers 5.12 prints a few `[ERROR] ... is part of Qwen3VLCausalLMOutputWithPast.__init__'s signature, but not
   documented` lines: that is its own docstring check, not a failure. Then look at what came out:

   ```bash
   podman exec climb psql -U postgres climb_trial -c "SELECT caption FROM keyframe_caption LIMIT 10;"
   podman exec climb psql -U postgres climb_trial -c "SELECT ocr_text FROM keyframe_text WHERE ocr_text <> '' LIMIT 10;"
   podman exec climb psql -U postgres climb_trial -c "SELECT text, hallucination FROM transcript_segment WHERE text <> '' LIMIT 20;"
   ```

   Captions should describe the frame, OCR should be readable text, transcripts real speech, with
   `hallucination = t` only on the invented lines (MVK is filmed underwater, so all of its lines are). Empty
   captions, garbled or empty OCR, or every line flagged mean something is off. When it all looks right, throw the
   trial away: `podman exec climb dropdb -U postgres climb_trial` and `rm -rf /big/disk/climb-trial`.
5. **Check, then run.** From `video_processing/src`:

   ```bash
   python main.py migrate
   python main.py check          # fix every FAIL before going on
   python main.py run            # ingest + decode/select/purge in batches, CPU only
   python main.py embed          # SigLIP2 large, plus PeskaVLP for LHE (1.7)
   python main.py ocr
   python main.py caption
   python main.py asr
   python main.py embed-text
   python main.py index build
   ```

   Every stage resumes where it stopped, so a crash or a Ctrl-C costs nothing but the batch in flight.
6. **More than one GPU.** One process uses one GPU. Start one per GPU, each with its own slice:
   `CUDA_VISIBLE_DEVICES=0 python main.py embed --shard 0 --shards 2` and
   `CUDA_VISIBLE_DEVICES=1 python main.py embed --shard 1 --shards 2`, same for the other model stages.
7. **Hand back** the media directory and a dump of the database:
   `podman exec climb pg_dump -U postgres -Fc <POSTGRES_DB_NAME> > climb.dump`.

---
You want to know more about the embedding Pipeline? I'm glad you asked:
#### 1.1 Installation

Run

```bash
pip install -r requirements.txt
```

to install neccessary requirements. That gives torch's PyPI wheel, which runs on any CPU; on a machine with an
NVIDIA GPU install torch for CUDA 12.6 and `paddlepaddle-gpu` as in 1.0 instead, or let `setup_climb.sh` do it.
`requirements.txt` leaves torch's CUDA libraries to torch, so that either one brings its own.

Two things that are easy to lose an evening to:

- **`torchvision` is not optional.** Captioning needs it, and without it both VLMs fail with
  `Unrecognized image processor`, which sends you hunting through the model repo instead of the venv. It is pinned to
  `0.27.1` because that is the version that matches `torch==2.12.1`; a bare `pip install torchvision` will happily
  upgrade torch to 2.13.0 behind your back.
- **PaddleOCR needs Python ≤ 3.13 and paddleocr 3.x.** There is no `paddlepaddle` wheel for 3.14 at all, so on a
  3.14 venv OCR falls back to `rapidocr-onnxruntime`, which runs the older PP-OCRv3 models, on the CPU only. It is not
  quiet about it: `main.py check` warns, and so does the OCR stage when it starts, with the error paddle failed on.
  `CLIMB_OCR_BACKEND` forces the issue either way (`auto` / `paddle` / `rapidocr`); the interface is the same, and
  pinning `rapidocr` is how you say you meant it.
  paddleocr 3 renamed its arguments and rejects the 2.x ones with `Unknown argument: use_gpu`, so a 2.x install is a
  FAIL in `main.py check` rather than a surprise at the start of the OCR stage. paddleocr wants `numpy<2.4` and
  `PyYAML==6.0.2`, so below 3.14 `requirements.txt` pins those lower. It also brings `opencv-contrib-python==4.10.0.84`
  while rapidocr brings `opencv-python`, and both install `cv2` over each other, so `opencv-python` is pinned to the
  same 4.10 there.

In case you prefer using a conda environment run
```bash:
conda env create -n climb -f conda_environment.yml
```
from `video_processing`. It is Python 3.13 plus the same `requirements.txt`, so the two cannot drift apart.

All steps can be done by running ```main.py``` with the respective options. In order to have the correct relative paths
please run

```bash:
cd src
```

to step into the src folder.

#### Available commands

CLIMB's pipeline is driven by subcommands:

```bash
python main.py <command> [options]
```

| Command        | What it does                                                                    |
|----------------|---------------------------------------------------------------------------------|
| `schema`       | Print the podman command for the database                                       |
| `migrate`      | Apply pending schema migrations. Run this first, always                         |
| `check`        | Check everything a run needs, before spending any compute on it                 |
| `enqueue`      | Load a manifest of videos into the job queue (only if you have to download)     |
| `fetch`        | Download queued videos                                                          |
| `ingest-shots` | Probe the videos, load their TransNet scenes and (V3C) info                     |
| `decode`       | One FFmpeg pass: candidate frames + audio                                       |
| `select`       | Pick 2-32 keyframes per scene and write the images                              |
| `purge`        | Delete transient files whose stage has finished                                 |
| `embed`        | SigLIP2 vectors (GPU)                                                           |
| `ocr`          | Read on-screen text (GPU)                                                       |
| `caption`      | Describe each shot (GPU)                                                        |
| `asr`          | Transcribe the audio with Whisper (GPU)                                         |
| `embed-text`   | Encode transcripts, captions and video info for meaning-based search (GPU)      |
| `index`        | `build`, `drop` or `status` for the search indexes                              |
| `run`          | Several stages in one go, always in pipeline order                              |
| `status`       | What is done and what is left                                                   |
| `serve`        | Start the search engine the backend talks to                                    |

Every command takes `--collection` (`V3C`, `MVK` or `LHE`; leave it out for all of them) and `--limit`. The GPU ones
also take `--shard N --shards M`. `--limit` counts what the command works on: videos, except for `embed`, `ocr` and
`caption`, which count keyframes. In `run` it counts videos and only limits the stages that take videos in (`fetch`
to `select`); the later stages finish what those produced. So in an example `run --limit 17` selects 17 videos, creates
310 keyframes and then all of 17 of them.  
**`python main.py <command> --help`** explains any of them.

---
In order to run the whole batch in one line:

```bash
python main.py run
```

which is `fetch,ingest-shots,decode,select,purge` , pick your own with `--stages`. Order does not matter,
they get sorted into pipeline order regardless, so you cannot accidentally ask it to select keyframes from a
video it has not decoded yet.

When `decode` and `select` are both in there, `run` does not decode everything and then select everything. It goes
round in batches: decode 200 videos, select them, purge their candidate frames, next 200. Candidate frames are about
130 MB per hour of video, so decoding the whole of V3C before selecting any of it would park ~350 GB of JPEGs in
`work/` for no reason. `CLIMB_PROCESS_BATCH` (env) sets the batch size. A video that fails is skipped for the rest of
that run (the log says why) and retried by the next one, so one broken file cannot stall everything behind it.

Everything is resumable. Every stage works out what is still outstanding by asking the database, so a run that
dies halfway can simply be run again, and a run with nothing left to do takes about five seconds.

```bash:
python main.py --help
```

#### Before you run anything: `check`

```bash
python main.py check
```

goes through everything a run needs and tells you what is wrong *before* anyone spends a GPU-day on it: FFmpeg and
its encoders, the Python packages, the GPU, whether the models are cached or at least downloadable, the source
collections (and whether each collection's scene files actually belong to its videos, by comparing a few against the
real frame count), the info files, the database and its schema, and whether the output directories are writable,
big enough, and not accidentally inside the sources. `FAIL` means a stage would crash or quietly produce rubbish,
`WARN` means it will run, just slower or without something. It exits non-zero on any `FAIL`, so you can put it in
front of a script.

#### 1.2 The source collections

The videos, their scene files and the V3C info JSONs are read from wherever they are mounted, and never written to.
Point `CLIMB_SOURCE_ROOT` at the root of it. The layout under it is in `datasets.json` at the repo root and looks like
this:

```text
VBS/videos/v3c/00001.mp4
VBS/videos/mvk/Ambon_Apr2012_0001.mp4
VBS/videos/lhe/LHE01.mp4
V3Cmeta/scenesV3C.tgz           or V3Cmeta/scenesV3C/scenes/00001.mp4.scenes.txt
V3Cmeta/info/00001.json         or V3Cmeta/infoV3C.tgz
MVKmeta/scenesMVK12.tgz         or MVKmeta/scenes/...
LHEmeta/scenesLHE.tgz           or LHEmeta/scenes/...
```

The scene files are fine either way, unpacked or still in their `.tgz`. The archive is actually preferred: on the HDD
it reads all 28,452 V3C scene files in under a second, where the unpacked folder takes about 6 ms *per file*. For the
info files it is the other way round, see 1.4.1. If you have one of these somewhere else entirely, `CLIMB_V3C_VIDEOS`, `CLIMB_MVK_SCENES`,
`CLIMB_LHE_INFO` and friends override a single entry (absolute, or relative to `CLIMB_SOURCE_ROOT`).

`MVKmeta` contains two scene archives, `scenesMVK12.tgz` and `mvk12scenes.tgz`. They are the same 3,190 files byte for
byte, so it does not matter which one you have.

Please be sure that you have FFmpeg installed under your system as CLIMB will spawn a whole lot of child-processes
executing FFmpeg. To download FFmpeg visit: https://ffmpeg.org/download.html

Video compression used to live here as its own step, and then inside decode. It is gone: the source videos are already
480x270 H.264 (thanks), so the frontend just plays them, and compressing something that small a second time was a bit silly.
There is no flag left for it either: the backend plays the file straight out of `CLIMB_SOURCE_ROOT`.

#### 1.2.1 Videos that are not mounted: the job queue

If the collections are mounted (see 1.2), skip `enqueue` and `fetch`, there is nothing to download. `purge` and
`status` further down are for everyone.

If they are not mounted, the videos come down a batch at a time, get processed, and go away again. That loop is what
`enqueue` / `fetch` / `purge` are for, and `ingest_jobs` in the database is the queue that tells `fetch` what is next:

```bash 
python main.py enqueue --collection V3C --manifest videos.txt   # a list of source URIs, one per line
python main.py fetch                           # pulls the next batch down
...                                            # decode, select, and so on
python main.py purge                           # throws away what is no longer needed
```

How the videos are actually reachable is your business, not the pipeline's , set `CLIMB_FETCH_COMMAND` to
whatever works (`rsync -a --partial {source} {dest}` by default, but scp, curl or cp are all fine).

`purge` is careful rather than clever. It does not wait for the whole pipeline to finish before freeing
anything, because the working set is what your SSD is spent on:

- the **downloaded video** goes once keyframe selection has run for it. Selection still needs it for scenes too short
  to have a candidate frame, after that nothing reads it. Only downloads in `work/raw` are ever deleted, never
  anything under `CLIMB_SOURCE_ROOT`
- the **candidate frames** go once every scene in that video has a keyframe
- the **audio** goes once the video has been transcribed, or if it never had any

Each of those is checked against what is actually in the database and on disk, so a decode that died halfway
cannot talk purge into deleting the only copy of the source.

```bash 
python main.py status
```

tells you where everything stands: how many videos are at which stage, how many scenes, keyframes, embeddings,
captions and transcripts exist, and how much transient junk is still sitting on disk.

#### 1.3 The Database

Everything downstream needs a database: the frame rate of every video (so we can build the millisecond payload for the
DRES server), the scenes, the keyframes and eventually a couple of million embeddings.
Because every sane people hates it when postgres runs locally on your machine we will spin up a podman container for
that. The parameters
for the database can be found and edited in ```/video_processing/src/config.py```. Sensitive information should be  
stored in a ```.env``` file placed in the root directory of the project ```(CLIMB/)```.
To automatically generate the podman command run

```bash 
python main.py schema
```

In order to create a Podman Container running Postgres
just run the command in you shell. This will automatically fetch the postgres image, build the container and start it in
the background.
To stop the container just run

```bash 
podman stop climb
```

To restart the container run

```bash 
podman start climb
```

Always start the container before running any video_processing / frontend or backend otherwise CLIMB won't function
properly.

(Note: Other usefull commands include ```podman ps``` to see all running containers and ```podman logs climb``` to see
the logs if you stumble upon undesired behaviour. For more details however I will recommend their excellent
documentation found under https://docs.podman.io/en/latest/)

**A word of warning if you have been here before:** we moved from ```ankane/pgvector``` (Postgres 16, pgvector 0.5.1) to
```pgvector/pgvector:pg17``` (Postgres 17, pgvector 0.8.x), because 0.5.1 has neither ```halfvec``` nor binary
quantization and we need both to survive with the full V3C dataset. Postgres does not do major version upgrades by politely
reading the old files, so an existing ```postgres_data``` volume and the old ```climb_db.dump``` are **not** reusable.
Use a fresh volume name and rebuild. Sorry.

##### 1.3.1 Create the schema

The tables live in ```/video_processing/migrations/``` as numbered ```.sql``` files, and get applied in order by

```bash 
python main.py migrate
```

This is safe to run as often as you like, it only applies what is missing. It also refuses to run if somebody edited a
migration that was already applied, because two machines quietly disagreeing about what the schema is, is a fun
afternoon nobody asked for. Need to change something? Add a new migration.

You no longer need to create the ```vector``` extension by hand, migration 001 does it for you (along with ```pg_trgm```
and ```unaccent```).

#### 1.4 Scenes

Every collection comes with *scene files*: where each scene starts and ends, found by TransNet. They are the backbone of
the whole index, so they go in before anything else touches a video.

We use these, not V3C's official *master shot boundaries*. The TransNet scenes are longer, they did the job at the uni
competition, and V3C has 1.97 million of them against 4.14 million master shots, which is a lot of keyframes we do not
have to pick, store, embed and caption.

```bash 
python main.py ingest-shots
```

This walks every collection, runs ```ffprobe``` on every video (frame rate, duration, dimensions, whether there is an
audio track at all) and writes one ```scenes``` row per scene. Frame rates come from ffprobe rather than OpenCV, because
OpenCV cheerfully returns 0 or NaN for a decent number of V3C files and the old code's answer to that was "eh, must be 30
fps" , which then quietly poisons every timestamp we ever send to DRES for that video. The 200-video set alone has ten
different frame rates in it, including such beauties as 23.000689 and 29.97003.

The parser handles both the ```<video_id>.mp4.scenes.txt``` scene files and the official V3C boundary TSVs. It works
out which columns hold the frame numbers by looking at what is actually in them instead of trusting a fixed column
order, so it does not care whether NIST puts the frames before or after the timecodes, and it falls back to converting
timecodes with the probed frame rate if a file has no frame columns at all. One corrupt line gets skipped with a warning
rather than taking the whole video down with it.

##### 1.4.1 Video info

V3C also comes with an info file per video: title, what the uploader wrote about it, tags and categories. Nobody
describes their own video better than the person who filmed it (well, usually), so `ingest-shots` loads those too, into
`video_info`, and `embed-text` turns title + tags + description into one vector per video.

There are two versions of these files and **they are not the same data**. The extracted `V3Cmeta/info/` folder is a
Vimeo scrape with tags and categories. `V3Cmeta/infoV3C.tgz` is a yt-dlp dump with no tags in any of its 28,450 files,
and where a video has no description it holds Vimeo's page filler instead ("This is ... on Vimeo, the home for high
quality videos and the people who love them."). Both are read, the filler is thrown away, and the folder wins. If only
the archive is there you get a warning, because otherwise you would just quietly have no tags.

Some tags arrive with a `?` where a non-ASCII character used to be (`vid?o`, `v?lo`). That damage is in the source
files, not something we did.

#### 1.5 Decoding

Now the actual work. Run

```bash 
python main.py decode
```

and go do something else for a while. For each video this spawns exactly **one** FFmpeg process which decodes it once
and produces two things at the same time:

- candidate frames under ```work/cand/``` , one every half second, which keyframe selection will later thin down
- a 16 kHz mono Opus track under ```work/audio/``` , food for Whisper

The old code did this in two passes: ```--compress``` decoded the whole video to transcode it, then
```--extractKeyframes``` decoded it *again*, seeking to each keyframe individually with OpenCV. Seeking to an arbitrary
frame in H.264 means decoding forward from the previous I-frame, so sampling once a second re-decodes most of the video
over and over , and when OpenCV's seek missed (which on V3C it does), the fallback rewound to the start of the shot and
walked forward frame by frame. On the 39-minute shot in video 00191 that is about 56,000 sequential reads to get one
picture. It worked. It just did not need to.

A few things worth knowing:

- **It will not upscale.** Candidates are capped at 384 lines, so the 480x270 sources stay at their own size instead
  of being blown up for no reason.
- **Candidate frames are JPEG, not WebP.** They get deleted the moment selection has run, so WebP's nicer compression
  buys nothing, and it is not free: 3.1s per video versus 0.1s. The keyframes we actually *keep* are WebP.
- **Tuning knobs**, both environment variables: ```CLIMB_DECODE_WORKERS``` (default 6) and ```CLIMB_FFMPEG_THREADS```
  (default 2). Set ```CLIMB_MEDIA_DIR``` and ```CLIMB_WORK_DIR``` to put the output on your external SSD rather than
  filling up your laptop.
- **No GPU here.** There used to be an NVENC path for the web copy. With no web copy there is nothing to encode, and
  decoding 270p on the CPU is not what anyone is waiting on.

- **Broken videos get indexed anyway.** V3C1_200 contains two videos (00016 and 00024) whose H.264 streams are damaged
  , the container promises 23,120 and 47,289 frames, the bitstream delivers about 84% of that and a great deal of
  shouting about NAL units. Throwing away five sixths of a video because the last sixth is rubbish would be a shame, so
  those get indexed from whatever decodes and flagged as ```damaged``` in the database, which is why their keyframe
  coverage looks thin later.
- **A short extraction that is *not* explained by a broken file fails the video instead.** That combination means
  something went wrong outside the video , a full disk, most likely , and is worth retrying. Ask me how I know.

#### 1.6 Keyframe selection

The decode step left a pile of candidate frames, two per second, which is far more than anyone needs. Thinning them down
is its own step:

```bash 
python main.py select
```

Each scene gets **2 to 32 keyframes, picked for how different they look from each other** rather than by the
clock. The rule:

1. **How many.** `k = ceil(shot_seconds / 4)`, clamped to between 2 and 32. A 3-second shot gets 2, a two-minute one
   gets 32. The old pipeline took one frame per second no matter what, which is how a single 39-minute shot ended up
   with 2,329 keyframes that all look the same.
2. **Throw out the rubbish, but not the title cards.** Frames that are nearly black, nearly white, or nearly flat are
   dropped. A frame only counts as rubbish if it *also* has basically no edges in it, 
   because white-on-black credits score exactly like a fade on brightness alone, and those are
   some of the most useful frames we have (dense readable text is a gift for OCR and for text-based tasks). If *every*
   frame in a shot is genuinely blank, the least boring one is kept anyway, because a scene with no keyframe at all is
   worse.
3. **Describe each frame cheaply.** A colour histogram, a coarse structural hash and an edge-density number.
4. **Pick the spread.** Start from the most typical frame of the shot, then repeatedly add whichever remaining frame is
   least like everything picked so far. You end up covering the shot instead of collecting near-duplicates of its first
   second.
5. **Very short shots** (under half a second , about 5% of them, and no candidate frame happens to land inside) simply
   get their middle frame pulled straight out of the video.

Each chosen frame is written twice: 384px for the detail panel and as model input, 160px for the results grid. Both
WebP, roughly 12 KB and 6 KB. For the 200-video set that is **35,558 keyframes instead of the old 99,661**, every one of
the 14,345 scenes has at least one, and no scene has more than its share, fewer files, better coverage, and no more
scrolling past twenty identical pictures of the same shot.

#### 1.7 The four GPU stages

Four stages read the keyframes and audio and turn them into something searchable. All four want a GPU, all four can be
stopped and restarted at any point, and all four figure out what is left to do by asking the database rather than by
keeping a list.

```bash 
python main.py embed         # SigLIP2 vectors, the backbone of semantic search
python main.py ocr           # OCR: signs, chyrons, scoreboards, title cards
python main.py caption       # a sentence describing each shot
python main.py asr           # Whisper transcripts of the speech
```

**Embeddings** encode each keyframe into a 1024-dimensional vector with SigLIP2 large, so a search query can be encoded
into the same space and answered with a nearest-neighbour lookup. This is the one that matters most.

Large is the model on every machine, because every query goes through the model's text
tower. The bigger SigLIP2 models are not worth their embed time here. From the SigLIP2 paper (Table 1), text-to-image
recall@1:

| Model  | COCO | Flickr | Compute per keyframe |
|--------|------|--------|----------------------|
| large  | 55.3 | 85.0   | 0.36 TFLOP           |
| so400m | 56.0 | 85.3   | 0.49 TFLOP           |
| giant  | 56.1 | 86.0   | 1.34 TFLOP           |

One point at rank 1 in a gallery of 5,000 images, while what counts at VBS is the target landing on the first pages
out of millions. And a second model is a second pass over every keyframe: so400m on top of large is about 2.4 times
the compute of large alone.

Keyframes are read ahead: `CLIMB_EMBED_LOADERS` threads (4) decode the next batches while the model works on this one,
and the image processor resizes and normalises them on the model's device. Done one after the other, that took ~4.7 ms
per keyframe on the laptop and capped the stage at about 160 keyframes/s whatever the GPU. Decoding alone runs at
~1,300 keyframes/s in four threads, the processor gives the same pixel values on decoded frames as on PIL images, and
what is left on the main thread, turning vectors into text and inserting them, is about 1 ms per keyframe. How fast it
is on the server's GPU is still to be measured, on the trial run.

**If there is GPU time to spare**, a second model for the machines with a GPU is one line in `.env`:

```text
CLIMB_EMBED_MODEL=google/siglip2-so400m-patch16-384
```

`embed` then embeds with large first and so400m after it, and search picks by hardware: so400m on a GPU, large on CPU.
A model only counts once it has a vector for every keyframe, so a so400m pass that is still running, or was stopped,
changes nothing: every machine keeps searching with large (with a warning in the log). `main.py check` says which model
this machine searches with, and `embed --model <name>` runs one of them on its own. Dropping it again is deleting the
line; to get the space back, `DELETE FROM keyframe_embedding WHERE model_id = 3;` (3 is so400m, see
`migrations/010_so400m.sql`), before `index build`, or it builds an index over them too.

**LHE gets a second model.** SigLIP2 learned from web images, and a laparoscopic hysterectomy looks nothing like the
web. For LHE a second model searches next to it:

| Collection | Model | What it learned from | License |
|------------|-------|----------------------|---------|
| LHE | PeskaVLP (`camma/peskavlp`, ResNet-50 + Bio_ClinicalBERT) | surgical video lectures and their narration | CC BY-NC-SA 4.0, research only |

It ranks the same keyframes in its own space, and its list fuses with SigLIP2's like any other signal
(`CLIMB_W_DOMAIN`, 1.0, measured below), with its own badge. It runs only when the dataset dropdown names LHE (its
vectors exist for LHE alone, so in an all-datasets search it would only add hits from there), and it has its own
tickbox, second after Visual and only there while LHE is picked, so SigLIP2 and PeskaVLP can each be searched alone.
`embed` embeds it for LHE only, about 150,000 keyframes, 1% of V3C's. `CLIMB_COLLECTION_MODELS=` (empty) switches it
off.

PeskaVLP sees the whole keyframe, squashed to 224x224. It was trained on the middle 224 pixels of the frame at
640x360, which is a third of the width, and on GynSurg the whole frame found more (below; migration 015 re-embeds
from it). On the laptop's CPU its BERT adds next to nothing to a search.

PeskaVLP is rebuilt from its weights (`pipeline/peskavlp.py`, 495 MB from the University of Strasbourg on first use)
rather than installed: CAMMA's package pins transformers 4.30.2, numpy < 2 and mmengine 0.7.0, which would uninstall
half of this pipeline on the way in. The weights load strictly, and the text vectors match their code to a cosine of
1.0 (`tests/test_domain_models.py`).

**Measured for LHE** on GynSurg's labelled frames (the same group's gynaecological laparoscopy, 1,248 instrument and
725 anatomy frames scaled to the 270 lines of the VBS copies, two phrasings per class, a frame relevant when its mask
holds the class). Average precision ranks every frame; the first 48 are what the search would show, which fuses each
model's 200 best (`CLIMB_RETRIEVER_DEPTH`):

| | SigLIP2 | PeskaVLP alone | both, `CLIMB_W_DOMAIN` 0.5 | both, 1.0 | both, 2.0 |
|---|---|---|---|---|---|
| 7 instrument classes, average precision / first 48 | 0.334 / 0.42 | 0.409 / 0.55 | 0.391 / 0.45 | 0.414 / 0.53 | **0.426 / 0.56** |
| 3 anatomy classes | **0.535** / 0.57 | 0.504 / 0.55 | 0.528 / **0.58** | 0.520 / 0.57 | 0.514 / 0.56 |

At the default 1.0, 54% of the first page is relevant against SigLIP2's 46%; 2.0 is not significantly better, and 0.5
is significantly worse. PeskaVLP is the one that knows the sealer-divider (0.78 of the first page against 0.28), the
suturing instruments (0.91 against 0.67), the hook and the ovary; SigLIP2 knows the fallopian tube (0.42 against 0.05)
and the scissors, and fused at 1.0 the two find the bipolar forceps better than either alone (0.83). With the centre
crop PeskaVLP was trained on, the same table read 0.337 / 0.46 and 0.478 / 0.49 for PeskaVLP alone and 51% fused:
the whole frame wins on frames cut to LHE's 16:9 at 480x270 too (0.432 against 0.377 average precision), where three
crops across the frame, averaged, did not.

To repeat it (no database needed), download GynSurg's two segmentation sets:
[instruments](https://ftp.itec.aau.at/datasets/GynSurge/Semantic_Segmentation_Dataset/GynSurg_Instrument_Dataset.zip),
2.4 GB, and [anatomy](https://ftp.itec.aau.at/datasets/GynSurge/Semantic_Segmentation_Dataset/GynSurg_Anatomy_Dataset.zip),
345 MB. They are for research use only and to be cited (Nasirihaghighi et al., ACM MM 2025). Unpack both into one
folder and run `python main.py eval-gynsurg --data <that folder>`; the queries and mask values are in
`evaluation/gynsurg.py`. The first run embeds the 1,973 frames, which takes 78 minutes of SigLIP2 and 2 of PeskaVLP
on the laptop's CPU, so it fits a lunch break. The vectors stay in `CLIMB_WORK_DIR/gynsurg-eval`, so trying other
queries or weights afterwards takes half a minute.

**MVK had one too, until it was measured.** BioCLIP 2.5 Huge (`imageomics/bioclip-2.5-vith14`, 200M+ images of living
things) searched next to SigLIP2 for the dives, the same way PeskaVLP does for LHE. MSC (Truong et al., ACM MM 2025)
describes 92 of MVK's own clips: a summary written by a person for 81 of them, and Gemini, GPT and Qwen captions for
each of their 449 segments (cut at scene changes, median 4 s). The measurement ranks 867 frames, one every 4 s of each
segment, at the VBS copies' 480x270. A summary looks for its clip and a caption for its segment, and either counts as
found when a frame of it is on the first page (48). The species are the 15 that at least three summaries name
("a grouper", "a grouper swimming"), scored like GynSurg, a frame relevant when its clip's summary names the species:

| | SigLIP2 | BioCLIP alone | both, `CLIMB_W_DOMAIN` 0.5 | both, 1.0 | both, 2.0 |
|---|---|---|---|---|---|
| 81 summaries, found on the first page / mean reciprocal rank | **0.93 / 0.44** | 0.64 / 0.20 | 0.89 / 0.38 | 0.88 / 0.36 | 0.83 / 0.34 |
| 1,054 captions | 0.63 / 0.20 | 0.46 / 0.10 | **0.67** / 0.20 | 0.64 / 0.19 | 0.58 / 0.16 |
| 15 species, average precision / first 48 | 0.278 / **0.25** | 0.218 / 0.21 | **0.286** / 0.24 | 0.276 / **0.25** | 0.262 / 0.24 |

BioCLIP alone is behind SigLIP2 on all three, and fused at the default 1.0 it cost the summaries 5 points of the
first page while adding 1 to the captions and nothing to the species. It knew its sponges (0.40 average precision
against SigLIP2's 0.28) and not much else: groupers 0.05 against 0.34. For 0.3 s more per MVK search and about 1.4 GB
more of the worker's memory, it was taken out again; migration 014 drops its vectors. A random ranking would find the
summary's clip on the first page 36% of the time and the caption's segment 10%: the pool is small, so every number
here is higher than it would be on the whole index, and it is the comparison that carries over.

Its smaller predecessor, BioCLIP 2 (ViT-L/14), did worse still (0.179 average precision on the species) and went the
same way. `eval-msc` prints a 95% paired bootstrap interval for every difference to SigLIP2 alone, and neither BioCLIP
gains anywhere without losing significantly somewhere else. The one significant gain came from the other side:
SigLIP2 so400m, the opt-in GPU model, is no better than large on its own, but the two fused lift the captions by
5 points of the first page (+3.0 to +7.1) and the species by 0.016 average precision, with the summaries unchanged.
CLIMB searches with one SigLIP2 per machine, so for now that is a number, not a feature.

To repeat it, MSC needs a login, which the form at https://msc.hkustvgd.com gives. From https://hkust-vgd.ust.hk/MSC/
download `videos_480/` and `information/annotations/` (0.8 GB; the 25 GB of PNG frames next to them are not needed)
into one folder, laid out as on the server. They are for non-commercial research only and to be cited. Then
`python main.py eval-msc --data <that folder>` ranks with SigLIP2, and with MVK's own model next to it if
`CLIMB_COLLECTION_MODELS` gives MVK one again; `--model <name>` measures any other model the pipeline can load
instead. BioCLIP's columns come from the version that still had its loader. The first run embeds the 867 frames,
about half an hour of SigLIP2 on the laptop's CPU (so400m: 41 minutes), and the vectors stay in
`CLIMB_WORK_DIR/msc-eval`, named by model.

For more Information about SigLIP2 see: https://arxiv.org/pdf/2502.14786

**OCR, captions and transcripts** exist because embeddings are vague where text is exact. If a hint says the shop sign
reads "BOULANGERIE", no amount of vector similarity will beat simply having read the sign. Transcripts run per video and
are matched to scenes by overlapping timestamps.

OCR reads **every** keyframe, captions only the **first of each shot**. That asymmetry is deliberate. Keyframes are
picked for how different they look, so skipping all but the first throws away exactly the frames where the text is most
likely to have changed , rolling credits, a news banner swapping mid-shot, a scoreboard ticking over, subtitles, a pan
along a row of shopfronts. Miss those and nothing later can recover them; no clever ranking reads a sign nobody looked
at. Captions do not have that problem , a sentence describing a shot reads much the same whichever frame of it you
pick , and they are the pricier stage, so they stay at one per shot.

**A trap worth knowing about:** small VLMs like SmolVLM default to splitting each image into tiles sized for a
full-resolution photo. Our keyframes are 480x270, so that means 13 crops of 512x512 per frame , every one of them an
*upscale* of a piece of an already-small picture, for detail that was never in there. It costs 5.3x
(17.57 s/shot versus 3.34) and buys nothing; the untiled captions were if anything slightly better, since the untiled
one mentioned on-screen text the tiled one missed. `CLIMB_CAPTION_IMAGE_SPLITTING` defaults to off for exactly this
reason. The Qwen VLMs do not tile and are unaffected.

Which model you point at it is `CLIMB_CAPTION_MODEL`. Measured on 12 CPU cores over the full 200-video set:
**SmolVLM-256M at 1.9-2.1 s/shot, Qwen2-VL-2B at ~13 s/shot.** SmolVLM is roughly 6x cheaper despite being the one
you would expect to be worse , but only after the tiling fix above. Before it, the 256M model was the *more*
expensive of the two despite having eight times fewer parameters, which is a fun thing to discover at 2am.

Those were CPU numbers for picking something the laptop could live with. The default is now **Qwen3-VL-4B**, chosen
for the GPU server: captions are written once and only their text is searched, so a bigger model costs server time
and nothing at search time. 4B rather than 8B because on Qwen's own benchmarks 2B to 4B gains 7-11 points on general
image understanding (MMStar 58.3 to 69.8, RealWorldQA 63.9 to 70.9) and 4B to 8B about one (70.9, 71.5), for twice
the GPU time per caption.

**Text embeddings** are the fourth-and-a-half stage:

```bash 
python main.py embed-text
```

Run it after transcription and captioning. Word matching alone does not get you far on either: people rarely phrase a
search the way somebody happened to say it out loud, and they certainly do not phrase it the way a model happened to
describe a picture. Encoding both with a multilingual text model fixes that , on paraphrased speech it finds the right
segment first 5 times out of 6, and an English query will happily land on a German sentence.

Captions need this even more than transcripts do. Out of five test searches, four shared **not a single content word**
with the caption that described exactly what they were looking for: search "a cyclist standing outside a shop", and the
caption says "A man in a red jacket stands beside a bicycle outside a bakery". Match those by keyword and you get
nothing; match them by meaning and it comes first every time. It is also the only way captions earn their keep , ask for
"a man carrying bread walks past a parked bicycle" and it correctly prefers the man carrying bread over the man merely
standing next to a bicycle, which is the sort of distinction the image embeddings cheerfully ignore.

The OCR text is deliberately **not** encoded this way. We tried it and measured it, and on short shop signs it could not
tell a bakery from a pharmacy , with two words to go on, the model has nothing to work with. OCR is better served by
plain word matching, because what OCR is uniquely good at is exact strings: if you typed "Dupont" and a sign says
"Dupont", that is not a hint, that is an answer. Trigram matching covers the misreadings ("B0ULANGERIE" still scores
0.73 against the right sign), and searching for text you have actually seen is what the `text:"..."` prefix is for.

If you find yourself with time to burn before the competition, you can go back and fill in the rest:

```bash 
python main.py caption --all-keyframes
```

Every stage only ever does work that is still outstanding, so this picks up exactly the keyframes the narrower run
skipped and leaves the existing captions alone. Same flag works for `ocr`, though that one already covers
everything by default. The permanent equivalents are `CLIMB_OCR_SCOPE` and `CLIMB_CAPTION_SCOPE`, either `all` or
`shot`.

##### Running them on more than one machine

Each stage takes `--shard N --shards M`, which partitions the work by row id. Point four GPUs at the same
database with `--shard 0..3 --shards 4` and they will divide the work exactly and never collide, no locking, no
coordination, no queue server. Verified: four shards over 1,149 pending keyframes split 287/288/287/287.

#### 1.8 Build the search indexes

Embeddings in a table are not a search engine, they are a very expensive list. To make them findable in single-digit
milliseconds instead of "go get a coffee" run

```bash 
python main.py index build
```

This builds the HNSW vector indexes , one per model per vector table, so **three** families of them.
Three things worth knowing:

- Do it **after** loading, not before. Maintaining an HNSW index while you shovel millions of rows into it is several
  times slower than building it once at the end.
- Do it on a machine with some RAM to spare (we ask for 8GB of ```maintenance_work_mem```). Building the index and
  *reading* the index are very different appetites , the plan is that whoever has the beefy machine builds it, and
  everybody else gets the finished database.
- Give the Postgres container a real ```--shm-size``` (8g if you are asking for 8GB of ```maintenance_work_mem```). A
  parallel index build wants a shared memory segment that big, the pgvector image ships with 63MB of ```/dev/shm```,
  and the error you get is ```No space left on device``` pointing at a disk with plenty of space on it. The build
  falls back to a serial one and tells you so rather than dying, but serial is the slow way to spend an evening.

The vector index is built over *binary quantized* embeddings and then reranks the top candidates against the full
precision vectors. Roughly: 1024 dimensions squashed to 1024 bits is about 5 GB of index instead of 28 GB, which is the
difference between fitting in a laptop's RAM and very much not.

How much oversampling is worth it, measured over the 35,558 keyframes of the course subset against exhaustive exact
search, averaged over the 90 TRECVID AVS 2019-21 topics on a laptop CPU:

| `CLIMB_ANN_OVERSAMPLE` | mean recall@20 | queries with all 20 | median latency |
|------------------------|----------------|---------------------|----------------|
| 500                    | 0.918          | 42/90               | 10 ms          |
| 1000                   | 0.956          | 57/90               | 17 ms          |
| **2000 (default)**     | **0.983**      | 70/90               | 33 ms          |
| 4000                   | 0.994          | 82/90               | 63 ms          |
| exact (whole corpus)   | 1.000          | 90/90               | 73 ms          |

The ceiling is the binary quantization, not the index , the ANN stage ranks by hamming distance on 1-bit vectors, so
the true cosine neighbours are not all inside its top-N. Even 4000 candidates leave 8 of 90 queries one or two short.
Exact search reads every vector, so its 73 ms grow with the corpus, while the ANN reads only its candidates.

The default is 2000 because on a whole search it barely shows: 442 ms at 1000, 469 ms at 2000, on a laptop CPU, and
368 ms of either is just encoding your query. 4000 doubles the ANN again for the last 1%, which is a decision you
should make on purpose.

Captions and transcripts run the exact same oversample-then-rerank, which is the point of it living in one function.
Captions share the knob, transcripts have their own candidate count (`ASR_SEGMENT_LIMIT`, 500 segments). Recall@20 was
measured at 96.2% for captions and 98.8% for transcripts, next to the visual retriever's 95.6% at 1000 above. Do not
read that as free: a fixed candidate count is a shrinking slice of a growing collection (2000 is 14% of 14k captions,
5.6% of 35k keyframes and 0.016% of 12.4M), so all of these drift down as it grows, and for captions and transcripts it is
a real (small) step down from the exhaustive scan those two used to do. Being exact is easy when you are small.

It has to be a *binary quantized* index for these two as well, and not only for the 5x size saving. `embedding` is a
dimensionless `halfvec` column so one column can carry every model, and an index on the typmod cast
`embedding::halfvec(768)` is one Postgres will happily build and then never once match against the identical
expression in a query. `binary_quantize()` is a real function call, so the expressions match. Ask how long that took
to work out sometime.

#### 1.9 How searching actually works

Four different things look for your query at once, a fifth for LHE, and two more judge whole videos. Their answers all
get merged.

- **The picture itself.** Your words get turned into a vector and matched against every keyframe.
  This is the workhorse and handles anything visual.
- **The picture again, for LHE.** PeskaVLP learned from surgery videos and ranks the same keyframes in its own space
  (1.7). It only runs when the dataset dropdown says LHE.
- **Text on screen.** Signs, chyrons, scoreboards, credits. Matched as words, not meaning, because
  what OCR is uniquely good at is *exact* strings , nothing else in the system can find "Dupont".
- **Shot descriptions.** Matched by meaning, so "a cyclist outside a shop" finds "A man in a red
  jacket stands beside a bicycle outside a bakery" despite sharing no words with it.
- **What people said.** Also by meaning, and mapped back to whichever scenes were on screen at the
  time.
- **What the uploader wrote, and their tags** (V3C only, from the info files, 1.4.1). Info matches your words
  against the video's title, description and tags by meaning, Tags looks for your words among its tags. Neither knows
  which frame you mean, so neither adds any: they lift the frames the others found, by how well the whole video
  matches. Both sit under the Info tickbox, see below.

Each returns its own ranked list of scenes (Info and Tags one of videos), and the lists are merged by **where** each
one placed a scene rather than by its score , the numbers are not comparable (image similarity sits around 0.05,
text similarity around 0.85, keyword rank is unbounded), but "this one came third" always is.

A scene found by two signals beats a scene found by one, and an exact text match is weighted very
heavily indeed. If you typed a word and a sign says that word, the argument is over.

Each result card says which signals found it, in the order of who did the work , `[OCR (3)] [Visual (41)]` means a
sign matched at rank 3 and the picture agreed, distantly, at 41. That ordering is by *contribution*, not by rank,
because the weights are not equal: OCR at rank 20 is doing more for a scene than the visual retriever at rank 5, and
sorting the badges by rank would cheerfully credit the wrong one. Sequence hits show no badges.

##### Turning a signal off

Under the search box are five tickboxes , Visual, Caption, OCR, ASR, Info , and in KIS the first four start ticked. Untick one and
it is not searched at all, which is both quieter and faster, since the retriever never runs and its query never gets
embedded. LHE gets a sixth, second in line: its own model, PeskaVLP (see 1.7), ticked whenever LHE is picked.

**Info** is the odd one out. It matches your words against what the uploader wrote about the whole video (title, tags,
description, see 1.4.1), so it cannot say *which frame*. Instead of adding frames it re-orders the ones the other
signals found: a frame gets a bonus for how well its video's description matches, the same weight / (60 + rank) as any
other signal (`CLIMB_W_INFO`, 1.0 for now; on AVS topics it did not help, see below). Only V3C has info files, so in an
all-datasets search a V3C frame could earn a bonus an MVK frame never can. That is why Info follows the dataset
dropdown: picking V3C ticks it, picking anything else (including "All datasets", the start) unticks it. After that the
tickbox is yours to flip until the next dataset or KIS/AVS switch.

**AVS** starts with OCR and Info unticked as well. AVS topics describe what is in the shot ("a person paddling a kayak
in the water"), not what is written on it or what the uploader called the video. On the 86 TRECVID AVS 2019-21 topics
with a relevant shot in the course subset, OCR alone found not a single relevant shot, and leaving OCR and Info out of
the fusion raised condensed AP from 0.317 to 0.335 and the relevant shots on the first page from 4.2 to 5.1. Switching
between KIS and AVS sets them the way a dataset switch does, so whatever you flip by hand stays flipped until the next
switch of either. An AVS task that does want on-screen text gets OCR back with one tick, or with `text:"..."`, which
runs regardless (see below).

The Info tickbox also covers **Tags**: the words of your query against the uploader's tags, no `tag:` needed. "a bmx
rider doing flatland tricks" lifts 00001, tagged `bmx` and `flatland`. Single words and runs of up to four are tried,
since a third of the tags are several words ("fish eye", "of monsters and men"), and both sides are compared as keys:
lowercase, no accents, nothing but letters and digits (migration 012), so `fish-eye`, `Fish Eye` and `fisheye` are one
tag and "velo" finds `vélo`. A rare tag counts for more than a common one (`flatland` in one video against `travel` in
twelve, ln of how many videos over how many have it). Where the info files lost a character (`v?lo`), the `?` matches
any one, but only when enough survived: a Korean tag that arrived as `?????` used to let its video through
`tag:canon`, and would have matched every five-letter word. `tag:` uses the same keys now. Like Info it is a
video-level prior with its own weight (`CLIMB_W_TAGS`, 1.0, unmeasured) and badge, and like Info it lifts every frame
of the video it likes, so a well-tagged video can take most of a page.

**ASR** follows the dropdown the same way, the other way round: picking MVK unticks it, everything else ticks it.
MVK is filmed underwater, nobody talks, and Whisper fills the regulator noise with things it has heard a lot of:
"Thank you.", "Продолжение следует..." ("to be continued"), "We'll see you next time." Every one of the 36 segments it
found in 78 MVK clips was invented, and they still matched queries: "fish on a coral reef" ranked a clip third on the
strength of a transcript that said "You".

Those segments are now caught when they are transcribed (`flag_hallucinations` in `pipeline/asr.py`), and a caught
segment is neither embedded nor searched. The tell is not the words but the silence around them: "Thank you." in the
middle of a conversation is somebody saying thank you, "Thank you." with no other speech within 30 seconds is Whisper
filling regulator noise. So a segment is dropped when it is one of the phrases Whisper is known to make up (the list
is `ASR_HALLUCINATION_PHRASES` in `config.py`) and isolated, when it is isolated and Whisper was unsure of it (mean
log probability below -0.8), when it has no letter or digit in it ("..."), or when it is a repetition loop
(compression ratio above 2.4, Whisper's own cut-off). On a probe over 61 MVK clips and 14 V3C videos that dropped all
26 invented MVK segments and 8 of 1,097 V3C ones, all of them lone "You" / "Music" or "...". Whisper's
`no_speech_prob`, the obvious candidate, was 0.0 for every single segment, which is why it is stored but not used.

The values stay in the database next to the flag, so a changed threshold or phrase is `python main.py asr
--reclassify` (seconds) rather than another transcription (days), followed by `embed-text` if anything came back.
The tickbox still starts unticked for MVK: the full collection is 3,190 clips, and the probe saw 61.

This exists because the badges made a problem impossible to ignore. OCR is weighted 4x on the reasoning that an exact
match on a proper noun is close to proof , which it is, for a proper noun. But when your query is lowercase the
lexical pass falls back to see every content word, so `a snowboarder doing a backflip` goes hunting for scenes whose
subtitles say "backflip", and eight of those sit above the first actual snowboarder. Unticking OCR is how you say "not
this time" for one query, instead of retuning the weights for every query forever.

The tickboxes do **not** gag `text:...` or `said:...`. Those only run because you typed them, and an is an
instruction rather than a preference , so an explicit phrase search still works with OCR unticked, which beats
returning nothing, because nothing cannot be submitted.

##### Telling it exactly where to look

| You type | What happens                                              |
|----------|-----------------------------------------------------------|
| `a red car on a bridge` | everything searches                                       |
| `text:BOULANGERIE` | the video must show that text, as a phrase (typos tolerated) |
| `said: after the earthquake` | the video must have someone say that (`spoken:` works too) |
| `tag:bmx` / `tag:"short film"` | the video's uploader tagged it that (V3C only)  |
| `video:00191` | only this video; for MVK, the whole recording (`video:Ambon_Apr2012` or any of its clips) |
| `-video:00191` | leave that video out (repeatable; for MVK, the whole recording) |
| `a dog >> a man on a bike` | one thing, then the other, in the same video              |
| `a dog >>(d120) a man on a bike` | the same, but within 120 seconds                          |

**They combine, at video level.** `said:"hello darling" text:"and they kissed"` finds the videos where *both* hold,
each possibly in a different scene, because the speech and the caption on screen rarely line up to the second. Every
`text:`, `said:` and `tag:` is a requirement and you can use as many as you like, `text:BANK text:EXIT` included (the
second one used to be dropped without a word). Plain words never exclude anything, they only decide the order within
whatever the requirements left over. Each requirement is applied inside what the previous ones left, and the results
header shows the funnel, `text:"they kissed" → 12 videos · said:"hello darling" → 3 videos`, so an empty page tells you
*which* requirement emptied it instead of looking like a broken search.

When the requirements leave only a handful of videos (300 or fewer, `CLIMB_EXACT_SCOPE_MAX_VIDEOS`), the picture,
caption and speech searches rank by exact distance over just those videos instead of going through the index, because
the index answers for the whole collection and `video:00191 a red car` would otherwise keep whichever of the global top
1000 happened to be in 00191, which is usually none of them. A query that is *only* requirements, say `video:00191` or
`tag:bmx`, lists the scenes of what is left in order, up to the 500 results a search fetches (`SEARCH_RESULT_DEPTH`).
That is the first 500 scenes of a very long video (00193 has 840); Browse shows all of them.

The dataset dropdown next to the sources does what it says: search only V3C, MVK or LHE. At VBS the task tells you which
collection it is from, and there is no point in letting the other two put look-alikes on the page. Browse has the same
dropdown, sharing the same setting, so switching to MVK there lists only the MVK clips.

The quotes are optional , the colon is what makes it an operator, and a space after it is fine. Unquoted, the phrase
runs to the end of the stage or to the next operator, so `text:BANK said:we are live` is two phrases and
`said: after the earthquake` is one. Quote it when you want plain words on the same stage: `text:"Dupont" a man walks
past` searches for both, where `text:Dupont a man walks past` is one long and rather optimistic phrase. This used to
fail silently , typing `said: after the earthquake` searched for the *word* "said" on signs, at four times the weight
of the picture, and cheerfully returned something.

The x on a result card does the same thing without typing: it drops that video and re-runs the search on the spot. On an
MVK clip it drops the whole recording, because the other clips of the same dive look exactly like the one you just
threw away. It
appends its list to the box as `--exclude: 00083, 00140, 00004` so you can see what you have thrown away, and it sends
that list as its own parameter, which is the one that counts.

##### Searching for things that happen in order

Sometimes you do not want to search for a shot, you need a *sequence* , the interview, then the building. `>>` chains
stages: `A >> B >> C` is three stages and two independent windows, and each stage is a full query in its own right, so
`text:"Boulangerie" >> a dog runs past` is perfectly legal. The window rides with the separator that precedes the
stage: `(d120)` and `(120)` are both 120 seconds, `(d500ms)` is half a second, and leaving it off uses the default.

The splitting is quote-aware, so `text:"a >> b"` stays one phrase rather than becoming a sequence. An empty stage is
not a stage either , `A >>` is just a search for A, not a sequence missing its second half.

MVK recordings count as one video here too: an MVK dive is cut into clips (median 27 seconds, a few up to 5
minutes), so `a turtle >> a diver` links across clip boundaries on the recording's own clock, and the result still
points at the right clip for DRES.

Interesting to know is: 

- **Query vectors are cached** (`CLIMB_QUERY_EMBED_CACHE`, 512 entries). Nobody types a sequence once , you type it,
  look, change the last word, look again. Refining one stage of three now re-embeds one stage of three instead of
  cheerfully redoing all of it.
- **Stages are embedded before any of them runs**, so on a GPU all of them go through the tower in one pass. On CPU
  they deliberately do *not* , a batch of three measured 903 ms against 816 ms for three separate calls, because the
  forward pass already owns every core and batching just makes it longer. `batch_embeddings` decides, not you.
- **Nothing is enriched until the chains exist.** A stage runs 1000 deep and maybe 40 hits survive into a chain, so
  fetching filmstrips for the other 2960 was pure tribute. Linking only ever wanted four columns.

##### MVK dives in the preview

Browse lists a dive as one card (`Ambon_Apr2012 · 3 clips · 21 shots`); the filter still finds it by a clip's id.
The filmstrip under the player and "Browse in Fullscreen" show the whole dive, not just the clip you clicked: every
clip in order, each one starting at an orange mark with its number (`0002`), and the fullscreen view says how far into
the dive each clip starts. Clicking a keyframe in another clip selects *that* clip, which is what gets submitted, since
DRES judges clips, not dives.

Next to "Loop segment" sit `◀ 0001` and `0002 ▶`. Forward lands on the next clip's first keyframe, back on the previous
clip's last, so stepping reads as continuing the dive. The player never switches clips on its own. With the loop off
it plays to the end of the clip and then offers "Next clip 0002 ▶", because a player that wandered into the next clip
by itself would be showing you one clip while Submit sends another. With the loop on, a keyframe in the last five
seconds of a clip now loops instead of running into the end and stopping.

#### 1.10 Start the Search Engine

You are all set, now you can finally start the Search Engine which will open up a connection for the backend to connect
to, in order to encode the searches.
Just run

```bash
python main.py serve
```

and relax. By default the search engine will run locally on port 5000 but just as everything else, this is configurable
in the config file.

Since the console will not be yours anymore I guess, so start up a new one and find your way to the root directory and
start if the next Section.

#### 1.11 Tests

There are tests now. I know, I was surprised too!

```bash
cd video_processing
pytest -m "not integration"   # 90 pure-logic tests, ~2 s, no database needed
pytest                        # all 157, including the 67 that need a live DB and an indexed corpus
```

The pure ones cover the query parser (`>>` splitting outside quotes, the `(d120)` / `(500ms)` gap syntax, dropped
empty stages, global `-video:` exclusions, the requirement operators), how requirements narrow at video level, the
temporal DP, the RRF arithmetic, both OCR backend adapters, the dataset layout and info file parsing, and the batched
`run` loop. The integration ones drive the worker and the backend over HTTP, check that the RRF signals actually
contribute, check `video:`, `tag:`, combined `text:` + `said:`, MVK recordings and the dataset restriction against
whatever corpus is loaded, run the temporal cases end to end, `EXPLAIN` the ANN query to confirm the planner really picks HNSW rather than quietly
falling back to a sequential scan, and exercise the whole DRES/AVS submission path against the mock server below.

If the integration ones skip rather than run, that means the services are not up , they need the database, the worker
on 5000, the backend on 8000 and the mock DRES on 8080.

### 2. Backend

To get the backend working you need to do 3 to 4 things.

- Spin up the DB-Container
- Optionally create and spin up the caching container
- Start the AI-Embedding Endpoint
- Start the backend server itself

First of all I hope you followed step 1 and properly setup everything.
If so please return to the root directory in order to align the relative paths.

#### 2.1 Spin up the DB-Container

As previously stated, to start the container run

```bash 
podman start climb
```

#### 2.2 Caching

In order to reduce load times during video browsing we added some paging and caching using Redis.
It's importnat to note that CLIMB will run completely fine without any caching enabled but you might find that video
browsing
takes longer to load. If you want to activate it just create a new podman container

```bash 
podman run --name climb_caching -p 6379:6379 -d docker.io/library/redis:7
```

and spin it up everytime you need some performance boost.

```bash:
podman start climb_caching
```

Redis is configurable via the following parameters in your root environment file:

```text:
REDIS_URL=<url>:<port>
VIDEOS_CACHE_TTL_SECONDS=<time>
```

##### 2.3 Start the search endpoint

In order to embed the user searches start the search engine by navigating into the 'video_processing/src' folder and
running

```bash:
python main.py serve
```

##### 2.4 Start the backend server itself

Now you are all set.
please open a new console if necessary and step into the backend folder, install all dependencies and start the backend.

```bash
cd backend
npm install
npm start
```

##### 2.5 Testing submissions without DRES

Submitting to a server that does not exist yet is hard to practice, so there is a fake one:

```bash
node backend/mock-dres-server.js     # port 8080, log in with admin / password
```

It implements the v2 client API CLIMB actually calls , login, `evaluation/list`, `evaluation/currentTask`,
`submit/{evaluationId}` , and is deliberately strict in the same places DRES is: unknown session → 401, unknown
evaluation → 404, and **a submission naming a `mediaItemName` without a `mediaItemCollectionName` → 400**, which is
the rule the whole thing exists to catch. It also warns if an answer set ever carries more than one answer, because
DRES grades only the first one and silently ignores the rest.

Test hooks live under `/mock/*`: `POST /mock/verdict` sets what the next submission comes back as, `POST /mock/task`
sets the current task, `GET /mock/submissions` shows everything received (with a recorded `shape` per submission, so
the "N separate POSTs, one answer each" rule that AVS depends on is actually assertable), and `POST /mock/reset`
clears it all again.

##### 2.6 AVS collaboration across machines

At a competition every teammate runs their own full CLIMB. 

The things that genuinely have to be shared are tiny: the teams (name, a scrypt hash of the password, the current
session, the last 200 hints) and the AVS session bookkeeping (a few hundred scene records keyed by a 4-letter code). No
database, no dataset, no media, no DRES credentials. So it lives on its own in `climb-avs-service/`, deployed **once**
for everyone:

```bash
cd climb-avs-service
podman build -t climb_avs .
podman run --name climb_avs -d \
    -e AVS_SESSION_TOKEN="$(openssl rand -hex 24)" \
    -p 20359:8080 climb_avs
```

The service always listens on 8080 inside the container; the host side is yours to pick, and only clients care which
one you picked. Every teammate then sets `AVS_SESSION_SERVICE_URL` (plus `AVS_SESSION_PORT=20359` if they give a bare
host rather than a full URL) and the same `AVS_SESSION_TOKEN`, and gets on with it.

Joining a team hands the backend a token for it, and the backend holds the membership the way it holds the DRES login,
so a page reload keeps you in. The service-wide `AVS_SESSION_TOKEN` stays what keeps strangers out of the service; the
team password only separates teams from each other. Everything is in memory: a restart of the service empties it, and
every member sees "IVADL expired or was reset. Join it again." and joins again, which recreates the team.

Three rules this is built around:

- **Search never touches the network.** Every backend keeps a local mirror of the session and filters against that,
  synchronously. A slow or missing session service cannot slow down a search, and an unknown session just means no
  filtering - showing a scene twice costs one duplicate submission, failing a search costs you the query.
- **Submitting never depends on it.** The DRES POST goes out first and is what scores; recording the scene into the
  session happens afterwards, is not awaited, and cannot fail into your submission.
- **404 and 503 are different answers.** 404 means the session (or team) is really gone. 503 means we could not
  reach the service and have nothing to say, so the team keeps its exclusion list, its session and its hints, and only
  the indicator changes. Collapsing those two would throw away good state over a few seconds of bad wifi.

Leave `AVS_SESSION_SERVICE_URL` unset and none of this applies: the store runs in-process exactly as it used to, which
is what you want when developing on your own. The backend says which mode it is in on startup, so you never have to
guess whether you are actually sharing a session:

```text
AVS sessions: shared via https://example.org
AVS sessions: in-process (set AVS_SESSION_SERVICE_URL to collaborate across machines)
```

### 3. Frontend

Starting the frontend is even easier.
All you need to do is to open a new terminal, navigate to the frontend directory, install all dependencies and run it.

```bash
cd frontend
npm install
npm run dev
```

To now see the User interface open the url provided in the terminal. By default it will be `http://localhost:3000`.

#### 3.1 Backend API

If you are interested in creating your own frontend or are just interested in general, you can find the API
Specifications of our backend under `/backend/openapi.yaml`.
In order to properly view it I would recommend using an openapi viewer of your choice. JetBrains products typically have
one included, browser based wise I like to use
"https://editor.swagger.io/", but that's completely up to you.

<div style="text-align: center;"><u><b><i>THE END</i></b></u></div>

---
