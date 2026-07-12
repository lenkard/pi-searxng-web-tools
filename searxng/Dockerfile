FROM searxng/searxng:latest

COPY engines/google.py /usr/local/searxng/searx/engines/google.py
COPY engines/google_cse.py /usr/local/searxng/searx/engines/google_cse.py
RUN rm -f /usr/local/searxng/searx/engines/__pycache__/google*.pyc
