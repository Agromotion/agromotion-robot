#!/bin/bash

PROJECT_DIR="/home/pi/raspberry"
cd "$PROJECT_DIR"

echo "Iniciando AgroMotion Firmware diretamente..."
sudo /home/pi/venv/bin/python3 firmware.py
