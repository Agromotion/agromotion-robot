# AgroMotion Robot

Firmware de sistema de controlo remoto para robot autónomo com streaming de vídeo, telemetria em tempo real e integração Firebase.

## Comece Aqui

Para começar a instalação, execute:

```bash
cd raspberry
bash install.sh
```

Isto irá abrir um menu interativo com as seguintes opções:

1. **Setup Completo** - Instala tudo (requer root)
2. **Verificar Instalação** - Verifica o estado da instalação
3. **Documentação** - Mostra este guia completo
4. **Instalar Dependências** - Só instala as dependências Python
5. **Executar Firmware** - Inicia o firmware
6. **Sair**

## Documentação

Para ver a documentação completa, execute:

```bash
bash install.sh --docs
```

Ou abra o ficheiro [DOCUMENTATION.md](DOCUMENTATION.md)

## Requisitos Rápidos

- Raspberry Pi 4
- Raspbian/Debian/Ubuntu
- Conexão Internet
- Python 3.7+

## Estrutura do Projeto

```
agromotion-robot/
├── README.md              # Este ficheiro
├── DOCUMENTATION.md       # Documentação completa
├── arduino/               # Código Arduino
│   └── ARDUINO_FIRMWARE.ino
└── raspberry/             # Código Raspberry Pi
    ├── install.sh         # Script de instalação (unificado)
    ├── requirements.txt
    ├── firmware.py
    ├── config.py
    └── [outros ficheiros Python...]
```

## Suporte

Consulte [DOCUMENTATION.md](DOCUMENTATION.md) para instrções detalhadas, resolução de problemas e referência completa.
