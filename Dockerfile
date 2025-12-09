# Dockerfile for Sreality Watchdog Bot

# Use an official Python runtime as a parent image
FROM python:3.9-slim

# Set the working directory in the container
WORKDIR /app

# Ensure Python output is not buffered (for real-time Docker logs)
ENV PYTHONUNBUFFERED=1

# Copy the requirements file into the container at /app
COPY requirements.txt .

# Install the packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application's code into the container at /app
COPY . .

# Make sure the data directory exists and is writable
RUN mkdir -p /app/data && chmod -R 755 /app/data

# Define the command to run the bot
CMD ["python", "bot.py"]
