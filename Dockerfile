FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY meal_bot.py .
RUN mkdir -p images
CMD ["python", "meal_bot.py", "--check"]
