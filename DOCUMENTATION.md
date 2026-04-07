# AgroMotion Robot - Documentação Completa

Sistema de controlo remoto para robot autónomo com streaming de vídeo, telemetria em tempo real e integração Firebase.

---

## Sumário

- [Instalação Rápida](#instalação-rápida)
- [Hardware](#hardware)
- [Funcionalidades](#funcionalidades)
- [Configuração](#configuração)
- [Comandos Úteis](#comandos-úteis)
- [Resolução de Problemas](#resolução-de-problemas)

---

## Instalação Rápida

### Pré-requisitos
- **Raspberry Pi 4** (hostname: `agromotion_pi`)
- **Sistema**: Raspbian/Debian/Ubuntu
- **Utilizador**: `pi`
- **Conexão Internet**

### Setup Automático (Recomendado)

1. **Clone ou copie os ficheiros para o Raspberry Pi**
   ```bash
   git clone https://github.com/your-repo/agromotion-robot.git
   cd agromotion-robot/raspberry
   ```

2. **Execute o script de instalação**
   ```bash
   sudo bash install.sh
   ```

   O menu irá pedir-lhe:
   - **Opção 1**: Setup completo (primeira vez)
   - **Opção 2**: Verificar instalação
   - **Opção 3**: Ver documentação
   - **Opção 4**: Instalar apenas dependências
   - **Opção 5**: Executar firmware
   - **Opção 6**: Sair

3. **Configure o ambiente**
   ```bash
   nano /home/agromotion/.env
   ```

   Edite:
   - `ROBOT_ID` - ID único do robot
   - `FIREBASE_CREDENTIALS_PATH` - Caminho das credenciais
   - `FIREBASE_DATABASE_URL` - URL da base de dados
   - `ARDUINO_SERIAL_PORT` - Porta serial do Arduino (normalmente `/dev/ttyUSB0`)

4. **Adicione as credenciais Firebase**
   ```bash
   cp seu-firebase-credentials.json /home/agromotion/secrets/firebase-credentials.json
   chmod 600 /home/agromotion/secrets/firebase-credentials.json
   ```

5. **Inicie o firmware**
   - Opção 1: Via systemd: `sudo systemctl start agromotion-firmware.service`
   - Opção 2: Via script: `bash install.sh` (opção 5)

---

## Hardware

### Raspberry Pi (`agromotion_pi`)
- **Hostname**: `agromotion_pi`
- **Utilizador**: `pi`
- **Câmara**: Pi Camera Module V2/V3 (CSI)
- **Conexão**: WiFi ou Ethernet

### Arduino Nano ESP32
- **Conexão**: USB serial (normalmente `/dev/ttyUSB0`)
- **Baud rate**: 115200
- **Funções**:
  - Controlo de 3 motores (FL, FR, REAR)
  - GPS Grove Air530 (UART)
  - **Sensor de bateria** (ADC)
    - Voltage divider em A0
    - Current sensor (ACS712) em A1 (opcional)

### Motores
- **Configuração**: 3 rodas (triângulo)
  - 2 rodas à frente (FL, FR)
  - 1 roda atrás (REAR)
- **Motor controllers**: L298N ou similar

---

## Funcionalidades

### Streaming de Vídeo
- **Servidor**: Mediamtx (WebRTC)
- **Limite**: 4 clientes simultâneos
- **URL**: `wss://agromotion_pi:8555/robot`
- **Latência**: 100-200ms

### Controlo
- **Modo**: Exclusivo (1 utilizador de cada vez)
- **Queue**: Suporta fila de espera
- **Timeout**: 5 minutos de inatividade
- **Comandos**: Joystick → 6 tipos de movimento

### Telemetria
- **Frequência**: 2 Hz (broadcast), 5s (Firebase)
- **Dados**:
  - Sistema: CPU, RAM, Temperatura
  - **Bateria**: Voltagem, percentagem, corrente (via Arduino)
  - GPS: Lat, Lon, Alt, Satélites
  - Robot: Estado, controlador ativo

---

## Configuração

### Estrutura de Ficheiros

```
/home/agromotion/
├── firmware.py              # Orquestrador principal
├── config.py                # Configuração
├── serial_handler.py        # Arduino (inclui bateria)
├── system_monitor.py        # CPU/RAM/Temp
├── video_streaming.py       # Streaming
├── command_handler.py       # Joystick
├── control_access_manager.py
├── telemetry_service.py
├── firebase_manager.py
├── mediamtx.yml
├── .env                     # Configuração (editar)
├── venv/                    # Python virtual env
├── logs/
│   ├── firmware.log
│   └── mediamtx.log
└── secrets/
    └── firebase-credentials.json
```

### Variáveis de Ambiente (.env)

```bash
ROBOT_ID=agromotion-robot-01
ROBOT_NAME=AgroMotion
FIREBASE_CREDENTIALS_PATH=/home/agromotion/secrets/firebase-credentials.json
FIREBASE_DATABASE_URL=https://seu-projeto.firebaseio.com
FIREBASE_PROJECT_ID=seu-projeto
ARDUINO_SERIAL_PORT=/dev/ttyUSB0
ARDUINO_BAUD_RATE=115200
LOG_LEVEL=INFO
DEBUG_MODE=false
```

---

## Comandos Úteis

### Controlo do Firmware

```bash
# Iniciar
sudo systemctl start agromotion-firmware.service

# Parar
sudo systemctl stop agromotion-firmware.service

# Reiniciar
sudo systemctl restart agromotion-firmware.service

# Ver status
sudo systemctl status agromotion-firmware.service

# Ver logs
tail -f /home/agromotion/logs/firmware.log

# Ativar auto-start
sudo systemctl enable agromotion-firmware.service

# Desativar auto-start
sudo systemctl disable agromotion-firmware.service
```

### Menu Interativo

```bash
# Menu principal com todas as opções
bash install.sh

# Executar com modo específico
bash install.sh --firmware camera  # Usar câmara
bash install.sh --firmware video   # Usar ficheiro de vídeo
```

---

## Resolução de Problemas

### Problema: ModuleNotFoundError: No module named 'psutil'

**Causa**: Dependências Python não estão instaladas

**Solução Rápida** (30 segundos):
```bash
cd agromotion-robot/raspberry
sudo bash install.sh
# Escolha opção 4 (Instalar apenas dependências)
# Ou opção 5 (Executar firmware - instala automaticamente)
```

### Problema: Arduino não encontrado

**Verificar porta serial**:
```bash
ls /dev/ttyUSB*
# Ou
ls /dev/ttyACM*
```

**Atualizar .env**:
```bash
nano /home/agromotion/.env
# Altere ARDUINO_SERIAL_PORT para a porta encontrada
```

### Problema: Câmara não funciona

**Verificar câmara**:
```bash
libcamera-hello
```

**Se não está instalada**:
```bash
sudo bash install.sh
# Escolha opção 1 (Setup completo)
```

### Problema: Streaming não carrega

**Verificar mediamtx**:
```bash
sudo systemctl status mediamtx.service
tail -f /home/agromotion/logs/mediamtx.log
```

**Reiniciar mediamtx**:
```bash
sudo systemctl restart mediamtx.service
```

### Problema: Firebase não conecta

**Verificar credenciais**:
```bash
ls -la /home/agromotion/secrets/firebase-credentials.json
chmod 600 /home/agromotion/secrets/firebase-credentials.json
```

**Verificar variáveis de ambiente**:
```bash
cat /home/agromotion/.env | grep FIREBASE
```

**Ver logs detalhados**:
```bash
tail -f /home/agromotion/logs/firmware.log | grep -i firebase
```

---

## Novos Utilizadores

1. Comece por executar: `sudo bash install.sh`
2. Escolha opção 1 para setup completo
3. Siga as instruções do script instalador
4. Para mais detalhes, aceda ao menu com opção 3 (esta documentação)

---

## Suporte

Para mais informações ou reportar problemas, consulte o repositório do projeto:
https://github.com/Agromotion
