FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY cori ./cori
COPY scripts ./scripts
COPY config.yaml .

EXPOSE 8000
CMD ["uvicorn", "cori.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
