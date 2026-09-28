FROM python:3.13-slim-bookworm
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["sh", "-c", "python migrate.py && gunicorn -w 1 --threads 4 -b 0.0.0.0:5090 app:app"]
