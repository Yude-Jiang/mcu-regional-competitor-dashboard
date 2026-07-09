# Build an image that runs the dashboard app
FROM python:3.11-slim
WORKDIR /app
COPY requirements_cloudrun.txt /app/
RUN pip install --no-cache-dir -r requirements_cloudrun.txt
COPY . /app
CMD ["gunicorn", "-b", "0.0.0.0:8080", "-w", "2", "--log-level", "info", "--error-logfile", "-", "--access-logfile", "-", "app:app"]
