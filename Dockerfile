# Use official Python image as base image
FROM docker.io/python:3.11

# Set working directory
WORKDIR /app

# Install system dependencies and clean up apt cache
RUN apt-get update && apt-get install -y --no-install-recommends && \
    git && \
    apt-get clean && rm -rf /var/lib/apt/lists/*
    

# Copy application code into the container
COPY . /app

# Upgrade pip and install dependencies
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install gunicorn

# Set environment variables
ENV FLASK_ENV=production \
    GUNICORN_CMD_ARGS="--workers=5 --threads=2 --bind=0.0.0.0:8301 --timeout=30 --access-logfile -"

# Expose application port
EXPOSE 8301

# Start Gunicorn server
CMD ["gunicorn", "main:app"]
