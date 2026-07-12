FROM searxng/searxng:latest@sha256:aaf56bb5ad737d48a8a375778d42eb4ab1757231e6b1eb7980e71d8dd5bdf0de

COPY engines/google.py /usr/local/searxng/searx/engines/google.py
COPY engines/google_cse.py /usr/local/searxng/searx/engines/google_cse.py
RUN rm -f /usr/local/searxng/searx/engines/__pycache__/google*.pyc
