# Use official Python image
FROM python:3.11

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && \
    apt-get install -y git && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Copy app code
COPY . /app

# Install Python dependencies
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install gunicorn

# Environment variables
ENV FLASK_ENV=production \
    GUNICORN_CMD_ARGS="--workers=5 --threads=2 --bind=0.0.0.0:8301 --timeout=30 --access-logfile -"

# Expose port
EXPOSE 8301

# Start server
CMD ["gunicorn", "main:app"]
