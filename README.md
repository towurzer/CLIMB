# CLIMB

Content Localization and Intelligent Multimedia Retrieval

---

A content-based video retrieval system designed for searching short video segments, focusing on sKnown-Item Search (KIS)
and Visual Question Answering (VQA) tasks inspired by the Video Browser Showdown (VBS).
The system provides an intuitive graphical user interface for interactive video exploration and integrates with the
Distributed Retrieval Evaluation Server (DRES) through its REST API, enabling seamless submission of retrieved video
segments.

## Getting Started - User

// TODO

## Getting Started - Developer

### Project Structure

```text
.env                        # Environment variables and secrets
start_vbs.sh                # Launch helper script
backend/
    openapi.yaml            # API specification
    package.json            # Backend dependencies
    server.js               # Express API server
    controller/             # Route handlers
    models/                 # Database models and queries
    routes/                 # Express routes

dataset/                    # local folder only
    climb_db_backup.dump    # Database dump
    compression.checkpoint  # Compression checkpoint metadata
    keyframes/              # Extracted keyframes
    V3C1_200/               # Source video dataset
    web_ready/              # Compressed videos for web playback

frontend/
    index.html              # Application shell
    package.json            # Frontend dependencies
    vite.config.js          # Vite dev server config
    public/                 # Static assets
    src/
        App.jsx             # Main application component
        App.css             # Styles
        main.jsx            # React entry point
        components/
            SearchBar.jsx   # Search input with history
            ResultsGrid.jsx # Thumbnail grid of results
            VideoPlayer.jsx # Video player with segment loop
            ShotBrowser.jsx # Filmstrip navigation
            VideoBrowser.jsx# Browse all videos
            VqaAnswer.jsx   # VQA text answer input
            TaskTimer.jsx   # 5-minute countdown
            SubmissionLog.jsx # Submission history log

video_processing/
    src/
        config.py              # Settings
        custom_logger.py       # Logging utilities
        dataset_compression.py # Dataset compression helpers
        db_queries.py          # Database queries
        db_setup.py            # Database setup
        embeddings_extraction.py # Feature extraction
        keyframe_extraction.py  # Keyframe extraction
        main.py                # Pipeline entry point
        search_engine.py       # Search and embedding service
        utils.py               # Utility functions
        vqa_engine.py          # VQA inference engine
        worker_http_endpoint.py # Search Engine HTTP interface 
    logs/                     # Log files (local only)
```

### 1. Video Processing

In order to get started you will first need to process the videos. Extract the keyframes, encode them and compress them
down to decrease loading time in the frontend.
To do that go into the video processing part of CLIMB by running

```bash
cd video_processing
```

#### 1.1 Installation

Run

```bash
pip install -r requirements.txt
```

to install neccessary requirements.

All steps can be done by running ```main.py``` with the respective options. In order to have the correct relative paths
please run

```bash:
cd src
```

to step into the src folder.

#### Available arguments

You can control different stages of CLIMB using the following flags:

```bash
python main.py [OPTIONS]
```

| Flag | Long option               | Description                                                                     |
|------|---------------------------|---------------------------------------------------------------------------------|
| -c   | --compress                | Compress the dataset videos using FFmpeg to allow for efficient video retrieval |
| -spc | --showshowPostgresCommand | Create and show the command to create and start the postgres database           |
| -h   | --help                    | Shows how to use the CLIMB-CLI and exits                                        |  

If you want tho get an overview about all possible configurations you can also run

```bash:
python main.py --help
```

#### 1.2 Data Preprocessing

Download and extract your Dataset (i.e. from: "https://www2.itec.aau.at/owncloud/index.php/s/AcA1pvZIpDrOom5").
Save it to ```/dataset/V3C1_200``` also extract the scenes and put them under ```/dataset/V3C1_200/scenes_v3c1_200```.
If you would like to choose a different Dataset / Folder structure edit the respective parameters in
```/video_processing/src/config.py```

In order to allow for efficient browser based retrieval the vide sizes must be small. To compress the videos run

```bash:
python main.py --compress
```

This will initiate a FFmpeg based compression of all the videos which will by default be stored under
```/dataset/web_ready```.
Please be sure that you have FFmpeg installed under your system as CLIMB will spawn a child-process executing FFmpeg.
To download FFmpeg visit: https://ffmpeg.org/download.html

#### 1.3 Keyframe extraction

Next you will need to extract the keyframes from the dataset. Additionally you will need to save the frame rate of every
video to later be able to build the Milisecond payload for the dres server. For that we will need to create simple
postgres-database.
Because every sane people hates it when postgres runs locally on your machine we will spin up a podman container for
that. The parameters
for the database can be found and edited in ```/video_processing/src/config.py```. Sensitive information should be  
stored in a ```.env``` file placed in the root directory of the project ```(CLIMB/)```.
To automatically generate the podman command run

```bash 
python main.py --showPostgresCommand
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

Now that you set up the Database, you can populate it. In order to do so, run

```bash 
python main.py --extractKeyframes
```

This will extract the keyframes out of the videos, save the Screenshots locally to later calculate the embeddings and will also insert them into the postgres climb Database.
If you already got your Database set up (i.e. through a provided) dumb, but deleted the keyframes folder (I don't know why you would do that but still) you can run

```bash 
python main.py --extractKeyframesNoDatabase
```

to still extract the keyframes, without updating the Database. (Please not you will still need an active database connection to do so)


#### 1.4 Video Embedding

In order to later to semantic video retrieval, we will need to encode the Videos (Keyframes to be more specific) into a high Dimensional 
Vector Space (1024dim). By doing so, we can later encode your searches into the same space, and do semantic retrieval by performing nearest neighbour searches 
in this space. The setup is pretty easy. Just run

```bash 
python main.py --extractEmbeddings
```

This will scan your climb database for video shots missing embeddings, extract the their features using SigLIP2, and store the vectors in the db.
For more Information about SigLIP2 see: https://arxiv.org/pdf/2502.14786

#### 1.5 Start the Search Engine

You are all set, now you can finally start the Search Engine which will open up a connection for the backend to connect to, to encode the searches and anser VQA-Questions.
Just run 

```bash
python main.py --startSearchEngine
```

and relax. By default the search engine will run locally on port 5000 but just as everything else, this is configurable in the config file.

Since the console will not be yours anymore I guess, so start up a new one and find your way to the root directory and start if the next Section.


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
It's importnat to note that CLIMB will run completely fine without any caching enabled but you might find that video browsing
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

In order to embed the user searches start the search engine by navigating into the 'video_processing/src' folder and running

```bash:
python main.py --startSearchEngine
```

##### 2.4 Start the backend server itself

Now you are all set.
please open a new console if necessary and step into the backend folder, install all dependencies and start the backend.

```bash
cd backend
npm install
npm start
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

If you are interested in creating your own frontend or are just interested in general, you can find the API Specifications of our backend under `/backend/openapi.yaml`.
In order to properly view it I would recommend using an openapi viewer of your choice. JetBrains products typically have one included, browser based wise I like to use 
"https://editor.swagger.io/", but thats completely up to you 

TODO: CLI Help String
TODO: CLI readme
TODO: Explain how to use frontend.
TODO: database dump 
TODO: example .env

