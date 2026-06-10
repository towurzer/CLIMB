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
video_processing/
	src/
	    config.py               # Settings
	    main.py                 # Manages the whole pipeline 
	    model.py                # model
	    model_trainer.py        # trains the model
	    utils.py                # utility functions
	---
	dataset/                    # Image source files (local only)
	logs/                       # Log File/s
	model/                      # trained model
	output/                     # Saved plots, inferred data and evaluation results (local only)

results/                    # Results to display in README
```

## Getting Started - User
// TODO


## Getting Started - Developer

### 1. Video Processing 

In order to get started you will first need to process the videos. Extract the keyframes, encode them and compress them down to decrease loading time in the frontend.
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

| Flag | Long option | Description                                                                     |
|------|-------------|---------------------------------------------------------------------------------|
| -c   | --compress  | Compress the dataset videos using FFmpeg to allow for efficient video retrieval |
| -h   | --help      | Shows how to use the CLIMB-CLI and exits                                        |  

If you want tho get an overview about all possible configurations you can also run

```bash:
python main.py --help
```

### 1.2 Data Preprocessing

Download and extract your Dataset (i.e. from: "https://www2.itec.aau.at/owncloud/index.php/s/AcA1pvZIpDrOom5").
Save it to ```/dataset/V3C1_200``` also extract the scenes and put them under ```/dataset/V3C1_200/scenes_v3c1_200```.
If you would like to choose a different Dataset / Folder structure edit the respective parameters in
```/src/config.py```

In order to allow for efficient browser based retrieval the vide sizes must be small. To compress the videos run

```bash:
python main.py --compress
```

This will initiate a FFmpeg based compression of all the videos which will by default be stored under
```/dataset/web_ready```.
Please be sure that you have FFmpeg installed under your system as CLIMB will spawn a child-process executing FFmpeg.
To download FFmpeg visit: https://ffmpeg.org/download.html

## Results:

//TODO