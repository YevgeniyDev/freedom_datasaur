from fastapi import FastAPI

app = FastAPI(title="F.I.R.E. Challenge API")

@app.get("/health")
def health():
    return {"status": "ok"}