FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
COPY stubs/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY stubs/source_api.py stubs/vendor_api.py ./
RUN useradd --create-home stubuser && mkdir -p /data && chown stubuser /data
USER stubuser
# docker-compose.yml chooses which stub this container runs.
CMD ["uvicorn", "source_api:app", "--host", "0.0.0.0", "--port", "8001"]
