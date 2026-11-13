FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-pol tesseract-ocr-nld tesseract-ocr-eng && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
RUN useradd --create-home --uid 10001 invoice && mkdir -p /data/files && chown -R invoice:invoice /data
COPY app ./app
USER invoice
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DOCUMENT_STORAGE=/data/files
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
