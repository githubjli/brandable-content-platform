FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

RUN groupadd -r app && useradd -r -g app -d /app app

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy proto and generate bindings at build time
COPY . /app/
COPY ../../proto /proto

RUN python -m grpc_tools.protoc \
    -I /proto \
    -I /app \
    --python_out=generated/ \
    --grpc_python_out=generated/ \
    /proto/live/v1/live_runtime.proto || true

# Fix relative imports in generated files
RUN sed -i 's/^import live_runtime_pb2/from . import live_runtime_pb2/' generated/live_runtime_pb2_grpc.py 2>/dev/null || true

RUN chown -R app:app /app

USER app

ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app

EXPOSE 50053

CMD ["python", "main.py"]
