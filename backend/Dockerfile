FROM python:3.11-slim

WORKDIR /app

# System deps:
# - tesseract-ocr for OCR
# - build-essential/g++/python3-dev for fasttext (C++17 extension)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    build-essential \
    g++ \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Install python deps (increase timeout/retries)
COPY backend/requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && \
    pip install --default-timeout=200 --retries=10 -r requirements.txt

# Copy backend + data
COPY backend /app/backend
COPY data /app/data

ENV PYTHONPATH=/app/backend
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]