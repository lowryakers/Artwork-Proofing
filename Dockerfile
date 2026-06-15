FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    tesseract-ocr \
    tesseract-ocr-eng \
    libzbar0 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Replace standard Tesseract English model with tessdata_best (~94MB) for
# significantly better accuracy on small label text and complex layouts
RUN TESSDATA=$(find /usr/share/tesseract-ocr -name tessdata -type d | head -1) \
    && curl -fsSL \
       https://github.com/tesseract-ocr/tessdata_best/raw/main/eng.traineddata \
       -o "${TESSDATA}/eng.traineddata" \
    || echo "WARNING: tessdata_best download failed — using standard model"

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app files
COPY . .

# Ensure uploads directory exists
RUN mkdir -p uploads

EXPOSE 8080
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "4", "--timeout", "120", "--access-logfile", "-"]
