FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
RUN useradd -m app
WORKDIR /app
COPY pyproject.toml requirements.txt ./
COPY src ./src
COPY frontend ./frontend
RUN pip install --no-cache-dir -r requirements.txt && pip install --no-cache-dir -e .
USER app
ENV CODEINTEL_DATA_DIR=/data
EXPOSE 8000 8501
CMD ["uvicorn", "codeintel.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
