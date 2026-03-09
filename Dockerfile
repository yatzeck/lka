FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY hotel_agent.py .
COPY wp_front_ajax_client.py .

RUN python hotel_agent.py download-files

CMD ["python", "hotel_agent.py", "start"]
