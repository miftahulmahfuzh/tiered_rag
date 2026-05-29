FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml requirements.txt ./
COPY src ./src
COPY xlsx ./xlsx
RUN pip install --no-cache-dir -e .
# default command runs tier-1 on 9101; compose overrides per service
CMD ["python", "-m", "tiered_rag.mock_llm", "--tier", "1", "--port", "9101"]
