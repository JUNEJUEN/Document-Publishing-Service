FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# Requirements go to /tmp: /workspace is replaced by the bind mount.
COPY api/requirements.txt /tmp/api-requirements.txt
COPY stubs/requirements.txt /tmp/stubs-requirements.txt
COPY docker/tests-requirements.txt /tmp/tests-requirements.txt
RUN pip install --no-cache-dir -r /tmp/api-requirements.txt -r /tmp/stubs-requirements.txt -r /tmp/tests-requirements.txt
WORKDIR /workspace/api
ENTRYPOINT ["pytest"]
