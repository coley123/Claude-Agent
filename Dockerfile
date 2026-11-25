# Run the colony in a container so agent commands cannot touch your machine.
#   docker build -t colony .
#   docker run --rm -it -e ANTHROPIC_API_KEY -v "$PWD/runs:/app/runs" -v "$PWD/hub:/app/hub" colony run examples/converge.toml
FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
RUN useradd -m agent
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -e ".[dev]" && chown -R agent /app
USER agent
RUN examples/make_demo_repo.sh
ENTRYPOINT ["python", "-m", "colony"]
