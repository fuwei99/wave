FROM python:3.11-slim

WORKDIR /app

# Copy requirements (if any specific ones, otherwise we install manually)
# Assuming we need fastapi, uvicorn, requests, boto3, python-dotenv
RUN pip install --no-cache-dir fastapi uvicorn requests boto3 python-dotenv

# Copy the application code
COPY . /app/wavespeed2api

# Set environment variables
ENV PYTHONPATH=/app
ENV PORT=8001

# Expose the port
EXPOSE 8001

# Run the application
CMD ["python", "wavespeed2api/start.py"]