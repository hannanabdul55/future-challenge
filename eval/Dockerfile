FROM python:3.12-slim
WORKDIR /hub
COPY pyproject.toml README.md ./
COPY fc ./fc
RUN pip install --no-cache-dir .
ENTRYPOINT ["fc"]
