FROM omnicast:1.0.0

WORKDIR /app

COPY src/ ./src/

ENV PYTHONUNBUFFERED=1
ENV HOST=0.0.0.0
ENV PORT=8081

EXPOSE 8081

CMD ["python", "-m", "uvicorn", "src.app:app", "--host", "0.0.0.0", "--port", "8081"]
