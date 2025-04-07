FROM python:3.11-slim

# Install Docker CLI for container management
RUN apt-get update && \
    apt-get install -y \
    apt-transport-https \
    ca-certificates \
    curl \
    gnupg \
    lsb-release && \
    mkdir -p /etc/apt/keyrings && \
    curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null && \
    apt-get update && \
    apt-get install -y docker-ce-cli && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Install jq for JSON parsing
RUN apt-get update && \
    apt-get install -y jq && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy Python requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application
COPY huntarr.py .

# Set execution permissions
RUN chmod +x huntarr.py

# Environment variables with defaults for optional settings
# (Required variables must be provided at runtime)
ENV OFFSET_THRESHOLD="1" \
    TRANSCODE_THRESHOLD="1" \
    WAIT_SECONDS="10" \
    BASIC_CHECK="3" \
    RESTART_DELAY="30"

# Command to run the application
CMD ["python", "huntarr.py"]