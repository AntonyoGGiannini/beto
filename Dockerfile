FROM python:3.11-slim

# Dependências de sistema mínimas (curl para healthcheck; as do Chromium vêm via playwright install --with-deps)
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# uv para gerenciar deps
RUN pip install --no-cache-dir uv

WORKDIR /app

# 1) Só as dependências (camada cacheável). --no-install-project pula a build do
#    pacote `beto`, que exigiria README.md/src ainda não copiados.
COPY pyproject.toml uv.lock ./
RUN uv sync --no-install-project --extra ui --no-dev

# 2) Chromium + libs de sistema na mesma camada de deps (cacheável). Usa o binário
#    do venv direto — `uv run` tentaria sincronizar o projeto (ainda não copiado).
RUN .venv/bin/playwright install chromium --with-deps

# 3) Código-fonte, README (referenciado em pyproject) e config; instala o projeto
COPY README.md ./
COPY src/ src/
COPY .streamlit/ .streamlit/
RUN uv sync --extra ui --no-dev

# Railway/Render/Fly.io expõem a porta via $PORT; fallback 8501
ENV PORT=8501 \
    PYTHONUNBUFFERED=1

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s \
    CMD curl -f http://localhost:${PORT}/_stcore/health || exit 1

# Padrão: interface web. No Railway, o serviço "worker" sobrescreve para `beto run`.
CMD ["uv", "run", "beto", "ui"]
