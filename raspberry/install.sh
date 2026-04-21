#!/bin/bash

################################################################################
# AgroMotion Robot - Unified Installation & Management Script
# Raspberry Pi OS (Debian Bookworm)
################################################################################

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Configuration
WORK_DIR="${WORK_DIR:-/home/pi/raspberry}"
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
VENV_PATH="$WORK_DIR/venv"
LOG_FILE="$WORK_DIR/setup.log"
DOCS_FILE="${DOCS_FILE:-DOCUMENTATION.md}"

################################################################################
# UTILITY FUNCTIONS
################################################################################

log() { echo -e "${BLUE}[INFO]${NC} $1" | tee -a "$LOG_FILE"; }
success() { echo -e "${GREEN}[OK]${NC} $1" | tee -a "$LOG_FILE"; }
error() { echo -e "${RED}[ERROR]${NC} $1" | tee -a "$LOG_FILE"; }
warning() { echo -e "${YELLOW}[WARN]${NC} $1" | tee -a "$LOG_FILE"; }

step() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}========================================${NC}\n"
}

show_help() {
    cat << 'EOF'
AgroMotion Robot - Installation & Management Script

Usage: bash install.sh [OPTIONS]

OPTIONS:
  (no args)           Show interactive menu
  --help              Show this help message
  --firmware camera   Run firmware with Pi camera
  --firmware video    Run firmware with video file
  --check             Run installation verification
  --docs              Show full documentation
  --deps              Install only Python dependencies
  --setup             Run full setup (requires root)

EXAMPLES:
  bash install.sh                 # Interactive menu
  bash install.sh --setup         # Full setup
  sudo bash install.sh --setup    # Full setup as root
  bash install.sh --check         # Verify installation
  bash install.sh --firmware camera  # Run with camera

EOF
}

################################################################################
# PREREQUISITE CHECKS
################################################################################

check_root() {
    if [[ $EUID -ne 0 ]]; then
        error "This operation requires root privileges"
        error "Run with: sudo bash install.sh"
        exit 1
    fi
    success "Running as root"
}

check_os() {
    if [[ ! -f /etc/os-release ]]; then
        error "Cannot determine OS"
        exit 1
    fi

    source /etc/os-release

    if [[ "$ID" != "raspbian" && "$ID" != "debian" && "$ID" != "ubuntu" ]]; then
        warning "This script was designed for Debian-based systems"
        warning "Detected: $ID ($PRETTY_NAME)"
    else
        success "OS: $ID ($PRETTY_NAME)"
    fi
}

check_python() {
    if ! command -v python3 &> /dev/null; then
        error "Python 3 is not installed"
        error "Install with: sudo apt install python3 python3-pip"
        exit 1
    fi

    PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
    success "Python $PYTHON_VERSION found"
}

check_disk_space() {
    AVAILABLE=$(df /home 2>/dev/null | tail -1 | awk '{print $4}' || echo "500000")
    REQUIRED=$((500 * 1024))

    if [[ $AVAILABLE -lt $REQUIRED ]]; then
        warning "Low disk space (available: $(( AVAILABLE / 1024 / 1024 ))GB)"
    else
        success "Disk space OK ($(( AVAILABLE / 1024 / 1024 ))GB available)"
    fi
}

################################################################################
# SYSTEM DEPENDENCIES
################################################################################

install_system_dependencies() {
    step "Installing system dependencies"

    check_root

    log "Updating package lists..."
    apt-get update -qq

    log "Installing required packages..."
    apt-get install -y -qq \
        git curl wget \
        python3-pip python3-venv python3-dev \
        libopenjp2-7 libtiff6 libopus-dev libvpx-dev \
        ffmpeg libcamera-tools libffi-dev libssl-dev \
        make build-essential 2>&1 | grep -v "^Get:\|^Hit:\|^Reading" || true

    success "System dependencies installed"
}

################################################################################
# SETUP DIRECTORIES & FILES
################################################################################

setup_directories() {
    step "Setting up directories"

    mkdir -p "$WORK_DIR/logs"
    mkdir -p "$WORK_DIR/secrets"

    chown -R ${SUDO_USER:-pi}:${SUDO_USER:-pi} "$WORK_DIR" 2>/dev/null || true
    chmod 755 "$WORK_DIR"
    chmod 700 "$WORK_DIR/secrets"

    success "Directories created"
}

copy_firmware_files() {
    step "Copying firmware files"

    FILES=(
        "firmware.py" "config.py" "serial_handler.py" "system_monitor.py"
        "video_streaming.py" "command_handler.py" "control_access_manager.py"
        "telemetry_service.py" "firebase_manager.py" "mediamtx.yml"
    )

    for FILE in "${FILES[@]}"; do
        if [[ -f "$SCRIPT_DIR/$FILE" ]]; then
            cp "$SCRIPT_DIR/$FILE" "$WORK_DIR/"
            log "  [OK] $FILE"
        else
            warning "  [SKIP] $FILE not found"
        fi
    done

    chown -R ${SUDO_USER:-pi}:${SUDO_USER:-pi} "$WORK_DIR" 2>/dev/null || true
    chmod 755 "$WORK_DIR"/*.py 2>/dev/null || true

    success "Firmware files copied"
}

################################################################################
# MEDIAMTX INSTALLATION
################################################################################

install_mediamtx() {
    step "Installing Mediamtx media server"

    if command -v mediamtx &> /dev/null; then
        success "Mediamtx already installed"
        return 0
    fi

    log "Detecting architecture..."
    ARCH=$(uname -m)

    case "$ARCH" in
        armv7l)  M_ARCH="armv7" ;;
        armv6l)  M_ARCH="armv6" ;;
        aarch64) M_ARCH="arm64" ;;
        x86_64)  M_ARCH="amd64" ;;
        *) error "Unsupported architecture: $ARCH"; return 1 ;;
    esac

    # Descobre a última release do mediamtx
    LATEST_TAG=$(curl -s https://api.github.com/repos/bluenviron/mediamtx/releases/latest | grep -Po '"tag_name": "\K.*?(?=")')
    URL="https://github.com/bluenviron/mediamtx/releases/download/${LATEST_TAG}/mediamtx_${LATEST_TAG}_linux_${M_ARCH}.tar.gz"

    log "Downloading Mediamtx for $ARCH..."
    cd /tmp
    wget -q "$URL" -O mediamtx.tar.gz
    tar -xzf mediamtx.tar.gz
    mv mediamtx /usr/local/bin/
    chmod +x /usr/local/bin/mediamtx
    rm -f mediamtx.tar.gz
    cd "$SCRIPT_DIR"

    success "Mediamtx installed"
}

################################################################################
# PYTHON ENVIRONMENT & DEPENDENCIES
################################################################################

setup_python_env() {
    step "Setting up Python virtual environment"

    if [[ ! -d "$VENV_PATH" ]]; then
        log "Creating virtual environment..."
        python3 -m venv "$VENV_PATH"
    fi

    log "Upgrading pip..."
    "$VENV_PATH/bin/pip" install --upgrade pip setuptools wheel -q

    success "Python virtual environment ready"
}

install_python_dependencies() {
    step "Installing Python dependencies"

    if [[ ! -f "$SCRIPT_DIR/requirements.txt" ]]; then
        warning "requirements.txt not found, installing base packages..."
        "$VENV_PATH/bin/pip" install -q \
            psutil==5.9.6 python-dotenv==1.0.0 pyserial==3.5 \
            firebase-admin==6.2.0 aiohttp==3.8.5 av==10.0.0 aiortc==1.5.0
    else
        log "Installing from requirements.txt..."
        "$VENV_PATH/bin/pip" install -r "$SCRIPT_DIR/requirements.txt" -q
    fi

    success "Python dependencies installed"
}

################################################################################
# CONFIGURATION FILES & SERVICES
################################################################################

setup_env_file() {
    step "Setting up environment configuration"

    if [[ -f "$WORK_DIR/.env" ]]; then
        log ".env already exists"
        return 0
    fi

    cat > "$WORK_DIR/.env" << EOF
ROBOT_ID=agromotion-robot-01
FIREBASE_CREDENTIALS_PATH=$WORK_DIR/secrets/firebase-credentials.json
ARDUINO_SERIAL_PORT=/dev/ttyUSB0
ARDUINO_BAUD_RATE=115200
LOG_LEVEL=INFO
EOF
    chown ${SUDO_USER:-pi}:${SUDO_USER:-pi} "$WORK_DIR/.env" || true
    chmod 600 "$WORK_DIR/.env"
    success ".env file created"
}

create_systemd_services() {
    step "Creating systemd services"
    check_root
    USER_VAL=${SUDO_USER:-pi}

    # Service MediaMTX
    cat > /etc/systemd/system/mediamtx.service << EOF
[Unit]
Description=Mediamtx Media Server
After=network.target

[Service]
Type=simple
User=$USER_VAL
WorkingDirectory=$WORK_DIR
ExecStart=/usr/local/bin/mediamtx $WORK_DIR/mediamtx.yml
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF

    # Service Firmware
    cat > /etc/systemd/system/agromotion-firmware.service << EOF
[Unit]
Description=AgroMotion Robot Firmware
After=network.target mediamtx.service

[Service]
Type=simple
User=$USER_VAL
WorkingDirectory=$WORK_DIR
Environment="PATH=$VENV_PATH/bin"
ExecStart=$VENV_PATH/bin/python3 $WORK_DIR/firmware.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    success "Systemd services created"
}

################################################################################
# VERIFICATION & RUN
################################################################################

check_installation() {
    step "Checking installation"
    local MISSING=0

    command -v python3 &> /dev/null && success "Python 3 installed" || { error "Python 3 missing"; MISSING=$((MISSING+1)); }
    [ -d "$VENV_PATH" ] && success "Virtual environment OK" || { warning "Venv missing"; MISSING=$((MISSING+1)); }
    command -v mediamtx &> /dev/null && success "Mediamtx OK" || { warning "Mediamtx missing"; MISSING=$((MISSING+1)); }
    [ -f "$WORK_DIR/firmware.py" ] && success "Firmware files OK" || { warning "Files missing"; MISSING=$((MISSING+1)); }

    echo ""
    if [[ $MISSING -eq 0 ]]; then
        success "All checks passed!"
    else
        warning "$MISSING issues found"
    fi
}

run_firmware() {
    local MODE="${1:-camera}"
    step "Running firmware mode: $MODE"
    cd "$WORK_DIR"
    "$VENV_PATH/bin/python3" firmware.py "$MODE"
}

show_documentation() {
    step "AgroMotion Documentation"
    [ -f "$SCRIPT_DIR/readme.md" ] && less "$SCRIPT_DIR/readme.md" || error "readme.md not found"
}

################################################################################
# INTERACTIVE MENU
################################################################################

show_menu() {
    clear
    echo "________________________________________________________"
    echo " AgroMotion Robot - Installation & Management"
    echo "________________________________________________________"
    echo " 1) Full setup (System + Mediamtx + Venv + Services)"
    echo " 2) Check installation status"
    echo " 3) Show documentation"
    echo " 4) Install Python dependencies only"
    echo " 5) Run firmware (camera)"
    echo " 6) Run firmware (video)"
    echo " 7) Update Firmware Files (Copy from current folder)"
    echo " 8) Exit"
    echo "________________________________________________________"
    read -p "Select option (1-8): " choice

    case $choice in
        1) check_root; check_os; check_python; check_disk_space; 
           install_system_dependencies; setup_directories; install_mediamtx; 
           setup_python_env; install_python_dependencies; copy_firmware_files; 
           setup_env_file; create_systemd_services ;;
        2) check_installation ;;
        3) show_documentation ;;
        4) setup_python_env; install_python_dependencies ;;
        5) run_firmware "camera" ;;
        6) run_firmware "video" ;;
        7) copy_firmware_files ;;
        8) exit 0 ;;
    esac
    read -p "Press Enter to continue..."
    show_menu
}

################################################################################
# MAIN EXECUTION
################################################################################

main() {
    mkdir -p "$WORK_DIR" 2>/dev/null || true
    
    case "${1:-}" in
        --help) show_help ;;
        --setup) install_system_dependencies; setup_directories; install_mediamtx; setup_python_env; install_python_dependencies; copy_firmware_files; setup_env_file; create_systemd_services ;;
        --check) check_installation ;;
        --firmware) run_firmware "${2:-camera}" ;;
        "") show_menu ;;
        *) error "Unknown option: $1"; show_help; exit 1 ;;
    esac
}

main "$@"