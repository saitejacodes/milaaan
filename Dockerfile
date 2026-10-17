# Public web demo. Runs with no API key and no network access beyond the browser.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Hugging Face Spaces runs containers as uid 1000.
RUN useradd --create-home --uid 1000 user
WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY milaan ./milaan
COPY config ./config
COPY .streamlit ./.streamlit
# Editable install: Milaan reads config/ relative to the source tree.
RUN pip install -e ".[dashboard]" && chown -R user:user /app

USER user
ENV HOME=/home/user
EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:7860/_stcore/health')"
CMD ["streamlit", "run", "milaan/webapp.py", "--server.port=7860", "--server.address=0.0.0.0"]
