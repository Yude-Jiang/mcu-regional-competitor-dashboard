# Build an image that runs the dashboard app
FROM python:3.11-slim
WORKDIR /app
COPY requirements_cloudrun.txt /app/
RUN pip install --no-cache-dir -r requirements_cloudrun.txt
COPY . /app
CMD ["python", "app.py"]
