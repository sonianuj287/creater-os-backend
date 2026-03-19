FROM python:3.11-slim

# Install FFmpeg from apt (pre-built binary, no compilation needed)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY . .

# Start command (Railway overrides this per service)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]