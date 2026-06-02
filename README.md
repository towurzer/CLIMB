# CLIMB
Content Localization and Intelligent Multimedia Retrieval

---

A content-based video retrieval system designed for searching short video segments, focusing on sKnown-Item Search (KIS) and Visual Question Answering (VQA) tasks inspired by the Video Browser Showdown (VBS). 
The system provides an intuitive graphical user interface for interactive video exploration and integrates with the Distributed Retrieval Evaluation Server (DRES) through its REST API, enabling seamless submission of retrieved video segments.


## Project Structure
```text
src/
    config.py               # Settings
    main.py                 # Manages the whole pipeline 
    model.py                # model
    model_trainer.py        # trains the model
    utils.py                # utility functions
---
dataset/                    # Image source files (local only)
model/                      # trained model
output/                     # Saved plots, inferred data and evaluation results (local only)
results/                    # Results to display in README
```

## Getting Started

### 1. Installation

Run
```bash
pip install -r requirements.txt
```
to install neccessary requirements.

### 2. Run the pipeline

To reproduce the results, run the pipeline:

```bash
cd src/
python main.py
```

#### Available arguments

You can control different stages of the pipeline using the following flags:

```bash
python main.py [OPTIONS]
```
//TODO
| Flag | Long option                 | Description                                                                                  |
|------|-----------------------------|----------------------------------------------------------------------------------------------|
|  |||

## Results:
//TODO