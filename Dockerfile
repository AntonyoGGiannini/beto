FROM python:3.11-slim

# Dependências de sistema mínimas (curl para healthcheck; as do Chromium vêm via playwright install --with-deps)
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# uv para gerenciar deps
RUN pip install --no-cache-dir uv

WORKDIR /app

# Camadas de cache: dependências antes do código-fonte
COPY pyproject.toml uv.lock ./
RUN uv sync --extra ui --no-dev

# Chromium + todas as libs de sistema que ele precisa
RUN uv run playwright install chromium --with-deps

# Código-fonte
COPY src/ src/
COPY .streamlit/ .streamlit/

# Railway/Render/Fly.io expõem a porta via $PORT; fallback 8501
ENV PORT=8501 \
    PYTHONUNBUFFERED=1

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s \
    CMD curl -f http://localhost:${PORT}/_stcore/health || exit 1

CMD ["uv", "run", "beto", "ui"]
