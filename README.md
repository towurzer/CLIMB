## How to start

### 1. Backend (terminal n 1)

for windows

```bash
cd backend
pip install -r requirements.txt
python -m uvicorn app:app --reload --port 8000
```

for macos

```bash
cd backend
pip install -r requirements.txt
python3 -m uvicorn app:app --reload --port 8000
```

Backend will run on **http://localhost:8000**

### 2. Frontend (terminal n 2)

```bash
cd frontend
npm install
npm run dev
```

there will be a link to **http://localhost:3000** - there is the website
