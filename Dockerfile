FROM python:3.12-slim

WORKDIR /srv

COPY requirements.txt download_models.py ./
RUN pip install --no-cache-dir -r requirements.txt && python download_models.py /models

COPY app ./app

EXPOSE 8099
CMD ["python", "-m", "app"]
