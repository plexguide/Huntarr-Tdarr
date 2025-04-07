# Use an official Python runtime as a parent image
FROM python:3.9-slim

# Set environment variables to prevent Python from writing pyc files and buffering stdout/stderr
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Install system dependencies
RUN apt-get update && apt-get install -y \
    docker.io \
    && rm -rf /var/lib/apt/lists/*

# Set the working directory in the container
WORKDIR /app

# Copy the current directory contents into the container at /app
COPY huntarr.py /app/
COPY requirements.txt /app/

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Create a requirements file if it doesn't exist
RUN touch requirements.txt && \
    echo "requests" >> requirements.txt && \
    pip install --no-cache-dir -r requirements.txt

# Make sure the script is executable
RUN chmod +x /app/huntarr.py

# Run the script when the container launches
CMD ["python3", "/app/huntarr.py"]