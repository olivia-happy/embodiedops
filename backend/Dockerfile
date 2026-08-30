FROM python:3.11-slim

WORKDIR /app/backend
COPY pyproject.toml ./

RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    python -c "import pathlib,tomllib; data=tomllib.loads(pathlib.Path('pyproject.toml').read_text()); print('\\n'.join([*data['build-system']['requires'], *data['project']['dependencies']]))" \
      > /tmp/build-requirements.txt \
    && pip install --timeout 120 --retries 5 -r /tmp/build-requirements.txt \
    && rm /tmp/build-requirements.txt

COPY signalforge ./signalforge
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    pip install --no-deps --no-build-isolation .

COPY tests ./tests

EXPOSE 8000
CMD ["uvicorn", "signalforge.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
