# AgroMotion Robot - Firmware Raspberry Pi

Código do firmware do robot para Raspberry Pi.

## Início Rápido

Execute o script de instalação:

```bash
bash install.sh
```

## O que está incluído

- **install.sh** - Script de instalação unificado com menu interativo
- **firmware.py** - Orquestrador principal
- **config.py** - Configuração
- **serial_handler.py** - Comunicação com Arduino
- **system_monitor.py** - Monitorização do sistema
- **video_streaming.py** - Streaming de vídeo
- **command_handler.py** - Processamento de comandos
- **telemetry_service.py** - Telemetria e dados
- **firebase_manager.py** - Integração Firebase
- **requirements.txt** - Dependências Python

## Opções do Script

```bash
bash install.sh                  # Menu interativo
bash install.sh --setup          # Setup completo
bash install.sh --check          # Verificar instalação
bash install.sh --docs           # Ver documentação
bash install.sh --deps           # Instalar só dependências
bash install.sh --firmware camera # Executar com câmara
bash install.sh --firmware video  # Executar com vídeo
```

Para mais detalhes, aceda à [documentação completa](../DOCUMENTATION.md).
