# CLIMB

Content Localization and Intelligent Multimedia Retrieval

---

A content-based video retrieval system designed for searching short video segments, focusing on sKnown-Item Search (KIS)
and Visual Question Answering (VQA) tasks inspired by the Video Browser Showdown (VBS).
The system provides an intuitive graphical user interface for interactive video exploration and integrates with the
Distributed Retrieval Evaluation Server (DRES) through its REST API, enabling seamless submission of retrieved video
segments.

## Project Structure

```text
dataset/                    # Image source files (local only)
results/                    # Results to display in README
video_processing/
	src/
	    config.py               # Settings
	    main.py                 # Manages the whole pipeline 
	    model.py                # model
	    model_trainer.py        # trains the model
	    utils.py                # utility functions
	---
	logs/                       # Log File/s
	model/                      # trained model
	output/                     # Saved plots, inferred data and evaluation results (local only)
---
frontend/
    src/
        App.jsx                 # Main application component
        App.css                 # Styles
        main.jsx                # React entry point
        components/
            SearchBar.jsx       # Search input with history
            ResultsGrid.jsx     # Thumbnail grid of results
            VideoPlayer.jsx     # Video player with segment loop
            ShotBrowser.jsx     # Filmstrip navigation
            VideoBrowser.jsx    # Browse all videos
            VqaAnswer.jsx       # VQA text answer input
            TaskTimer.jsx       # 5-minute countdown
            SubmissionLog.jsx   # Submission history log
---
backend/
    server.js                   # TODO Server

```

## Getting Started - User

// TODO

## Getting Started - Developer

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

### 1.2 Data Preprocessing

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

### 1.3 Keyframe extraction

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

### 2. Backend

To get the backend working you need to do 3 things.

- Spin up the DB-Container
- Start the AI-Embedding Endpoint
- Start the backend server itself

First of all I hope you followed step 1 and properly setup everything.
If so please return to the root directory in order to align the relative paths.

#### 2.1 Spin up the DB-Container

As previously stated, to start the container run

```bash 
podman start climb
```

#### 2.2 Start the AI-Embedding Endpoint

To start the AI-Embedding Endpoint you need to go into the video_processing folder and run the main script with
appropriate flags.

```bash
cd video_processing/src
python main.py -start
```

##### 2.3 Start the backend server itself

Well your console will not be yours anymore I guess, so start up a new one and find your way to the root directory.

Now please step into the backend folder, install all dependencies and start it.

```bash
cd backend
npm install
npm start
```

### 3. Frontend

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:3000` in your browser.