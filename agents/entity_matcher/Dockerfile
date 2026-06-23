FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the entity_matcher package into /app/entity_matcher/
# so it can be imported as `entity_matcher.app` by uvicorn.
COPY . ./entity_matcher/

CMD ["uvicorn", "entity_matcher.app:app", "--host", "0.0.0.0", "--port", "8080"]
