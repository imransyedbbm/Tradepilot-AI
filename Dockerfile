FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir fastapi==0.115.6 uvicorn==0.32.1 pydantic==2.10.4 pydantic-settings==2.7.0 sqlalchemy==2.0.36 "psycopg[binary]==3.2.3"
COPY trading ./trading
COPY apps ./apps
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/api/v1/health')"
CMD ["uvicorn", "apps.api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
