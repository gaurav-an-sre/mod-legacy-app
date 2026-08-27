FROM python:3.11-slim
WORKDIR /workspace
RUN pip install --no-cache-dir fastapi uvicorn httpx pyyaml jinja2
COPY . /workspace
CMD ["uvicorn", "console.main:app", "--host", "0.0.0.0", "--port", "8000"]
