# Use official Python image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies (minimal)
RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Copy only requirements first (for better caching)
COPY requirements.txt .

# Install Python dependencies with no cache
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir gunicorn

# Copy rest of app code
COPY . /app

# Environment variables
ENV FLASK_ENV=production \
    GUNICORN_CMD_ARGS="--workers=5 --threads=2 --bind=0.0.0.0:8306 --timeout=30 --access-logfile -"

# Expose port
EXPOSE 8306

# Start server
CMD ["gunicorn", "main:app"]
