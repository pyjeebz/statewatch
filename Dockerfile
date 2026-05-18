# Container image for the statewatch GitHub Action.
FROM python:3.12-slim

WORKDIR /opt/statewatch
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir .

COPY action/entrypoint.py /action/entrypoint.py

ENTRYPOINT ["python", "/action/entrypoint.py"]
