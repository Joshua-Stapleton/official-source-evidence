FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY autonomous_data_api/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY autonomous_data_api ./autonomous_data_api

EXPOSE 8765

CMD ["uvicorn", "autonomous_data_api.app:app", "--host", "0.0.0.0", "--port", "8765"]
