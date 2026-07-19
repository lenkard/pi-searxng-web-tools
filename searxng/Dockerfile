FROM searxng/searxng:latest@sha256:800a5e3757df04f59e6bd6c622f15de2edbfa47f5591c5b82d4a8ff3b938a845

COPY engines/google.py /usr/local/searxng/searx/engines/google.py
COPY engines/google_cse.py /usr/local/searxng/searx/engines/google_cse.py
COPY engines/zai.py /usr/local/searxng/searx/engines/zai.py
COPY engines/serper.py /usr/local/searxng/searx/engines/serper.py
RUN rm -f /usr/local/searxng/searx/engines/__pycache__/google*.pyc /usr/local/searxng/searx/engines/__pycache__/zai*.pyc /usr/local/searxng/searx/engines/__pycache__/serper*.pyc
