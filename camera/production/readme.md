
# Raspberry Pi - Guia de Instalação de stream da câmara
## 1 - Configuração do Hardware
Garante que a câmara está conectada corretamente e ativa o suporte no sistema:
  

    sudo raspi-config
    
    # Interface Options -> Camera -> Enable
    
    # Reinicia o Pi
    
    sudo reboot

## 2 - Dependências do Sistema
O Raspberry Pi necessita de bibliotecas específicas para processamento de vídeo e compilação dos módulos WebRTC. Corre isto no terminal:

    sudo apt update && sudo apt upgrade -y
    sudo apt install -y python3-pip python3-venv python3-opencv \
    libavdevice-dev libavfilter-dev libavformat-dev libavcodec-dev \
    libswresample-dev libswscale-dev libavutil-dev pkg-config \
    libopus-dev libvpx-dev
    
## 3 - Preparação do Projeto
Cria uma pasta para o projeto e configura um ambiente virtual para manter o sistema limpo:

    mkdir agromotion-robot && cd agromotion-robot
    python3 -m venv venv
    source venv/bin/activate
    
## 4 - Instalação de pacotes Python
Com o ambiente virtual ativo, instala os requisitos necessários:

    pip install --upgrade pip
    pip install aiortc aioice firebase-admin python-dotenv psutil av opencv-python

## 5 - Configuração de Credenciais
### 5.1  - Firebase
Coloca o teu ficheiro de chave privada (`serviceAccountKey.json`) numa pasta chamada `secrets/`.

### 5.2 - Variáveis de Ambiente
Cria um ficheiro `.env` na raiz do projeto:

    ROBOT_ID=robot_01
    FIREBASE_CERT_PATH=secrets/serviceAccountKey.json

## 6 - Como Executar
Executa o script principal que utiliza a câmara física do Pi:
    python agromotion_camera_stream.py
    
## 7 - Como criar um Serviço que executa ao Iniciar o Sistema
### 7.1 - Criar o ficheiro do serviço
No terminal do Raspberry Pi, executa o seguinte comando para criar o ficheiro de configuração:

    sudo nano /etc/systemd/system/agromotion_camera.service
    
### 7.2 - Colar a Configuração
Copia e cola o conteúdo abaixo. **Atenção:** Substitui `/home/pi/agromotion-robot` pelo caminho real onde guardaste a pasta do projeto.

    [Unit]
    Description=Agromotion Camera Robot Core Service
    After=network.target
    
    [Service]
    # Garante que o serviço corre dentro da pasta do projeto
    WorkingDirectory=/home/pi/agromotion-robot
    # Usa o Python do ambiente virtual (venv)
    ExecStart=/home/pi/agromotion-robot/venv/bin/python agromotion_camera_stream.py
    Restart=always
    RestartSec=5
    User=pi
    # Necessário para aceder à câmara e hardware
    Group=video
    SupplementaryGroups=dialout
    
    [Install]
    WantedBy=multi-user.target

### 7.3 - Ativar e iniciar o Serviço
Agora, diz ao sistema para carregar o novo serviço e iniciá-lo:

    # Recarregar as definições do sistema
    sudo systemctl daemon-reload
    
    # Ativar para arrancar no boot
    sudo systemctl enable agromotion_camera.service
    
    # Iniciar agora
    sudo systemctl start agromotion_camera.service

## Comandos úteis
Como o script agora corre "em background", podes usar estes comandos para ver o que está a acontecer:
**Ver logs em tempo real:** `sudo journalctl -u agromotion_camera.service -f`
**Verificar o estado:** `sudo systemctl status agromotion_camera.service`
**Parar o robô:** `sudo systemctl stop agromotion_camera.service`
**Reiniciar (após mudares o código):** `sudo systemctl restart agromotion_camera.service`
