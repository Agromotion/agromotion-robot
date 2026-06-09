#!/bin/bash

# Define o caminho para a pasta do projeto
PROJECT_DIR="/home/pi/raspberry"
cd "$PROJECT_DIR"

# Ativa o ambiente virtual e corre o firmware
# O modo "camera" no install.sh corresponde a correr o firmware.py normalmente
echo "🚀 Iniciando AgroMotion Firmware diretamente..."
sudo ./venv/bin/python3 firmware.py
