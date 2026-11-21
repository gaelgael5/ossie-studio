FROM python:3.12-slim

# git est requis dans l'image du backend : il descend et versionne les sources
# et les espaces de travail (volumes mappés).
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /usr/local/bin/uv

# Utilisateur non privilégié : le backend ne tourne jamais en root (il ne doit
# pas contourner un problème de droits sur un volume en passant root).
RUN useradd --create-home --uid 1000 app
WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev

USER app
EXPOSE 8000
CMD ["uv", "run", "--no-dev", "uvicorn", "ossie_studio.app:create_app", \
     "--factory", "--host", "0.0.0.0", "--port", "8000"]
