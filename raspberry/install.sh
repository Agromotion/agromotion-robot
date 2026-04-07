#!/bin/bash

################################################################################
# AgroMotion Robot - Unified Installation & Management Script
#
# Usage: bash install.sh [--help|--firmware|--check|--docs]
#
# This script replaces: setup.sh, START_HERE.sh, install-deps.sh,
#                       check-installation.sh, run.sh, and other scripts
################################################################################

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Configuration
WORK_DIR="${WORK_DIR:-/home/agromotion}"
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
        warning "This script was designed for Debian-based systems (Raspbian/Ubuntu)"
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
        libopenjp2-7 libtiff5 libopus-dev libvpx-dev \
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
        armv7l)
            MEDIAMTX_URL="https://github.com/bluenviron/mediamtx/releases/download/v1.4.2/mediamtx_v1.4.2_linux_armv7.tar.gz"
            ;;
        armv6l)
            MEDIAMTX_URL="https://github.com/bluenviron/mediamtx/releases/download/v1.4.2/mediamtx_v1.4.2_linux_armv6.tar.gz"
            ;;
        aarch64)
            MEDIAMTX_URL="https://github.com/bluenviron/mediamtx/releases/download/v1.4.2/mediamtx_v1.4.2_linux_arm64.tar.gz"
            ;;
        x86_64)
            MEDIAMTX_URL="https://github.com/bluenviron/mediamtx/releases/download/v1.4.2/mediamtx_v1.4.2_linux_amd64.tar.gz"
            ;;
        *)
            error "Unsupported architecture: $ARCH"
            return 1
            ;;
    esac

    log "Downloading Mediamtx for $ARCH..."
    cd /tmp || return 1
    wget -q "$MEDIAMTX_URL" -O mediamtx.tar.gz || return 1
    tar -xzf mediamtx.tar.gz || return 1
    mv mediamtx /usr/local/bin/ || return 1
    chmod +x /usr/local/bin/mediamtx
    rm -f mediamtx.tar.gz

    success "Mediamtx installed"
}

################################################################################
# PYTHON ENVIRONMENT & DEPENDENCIES
################################################################################

setup_python_env() {
    step "Setting up Python virtual environment"

    if [[ -d "$VENV_PATH" ]]; then
        log "Virtual environment already exists"
        success "Virtual environment ready"
        return 0
    fi

    log "Creating virtual environment..."
    python3 -m venv "$VENV_PATH"

    log "Upgrading pip..."
    "$VENV_PATH/bin/pip" install --upgrade pip setuptools wheel -q

    success "Python virtual environment created"
}

install_python_dependencies() {
    step "Installing Python dependencies"

    if [[ ! -f "$SCRIPT_DIR/requirements.txt" ]]; then
        warning "requirements.txt not found, using default packages..."
        "$VENV_PATH/bin/pip" install -q \
            psutil==5.9.6 \
            python-dotenv==1.0.0 \
            pyserial==3.5 \
            firebase-admin==6.2.0 \
            aiohttp==3.8.5 \
            av==10.0.0 \
            aiortc==1.5.0
    else
        log "Installing from requirements.txt..."
        "$VENV_PATH/bin/pip" install -r "$SCRIPT_DIR/requirements.txt" -q
    fi

    success "Python dependencies installed"
}

################################################################################
# CONFIGURATION FILES
################################################################################

setup_env_file() {
    step "Setting up environment configuration"

    if [[ -f "$WORK_DIR/.env" ]]; then
        log ".env already exists"
        success ".env already configured"
        return 0
    fi

    log "Creating .env file..."
    cat > "$WORK_DIR/.env" << 'EOF'
ROBOT_ID=agromotion-robot-01
ROBOT_NAME=AgroMotion
FIREBASE_CREDENTIALS_PATH=/home/agromotion/secrets/firebase-credentials.json
FIREBASE_DATABASE_URL=https://your-project.firebaseio.com
FIREBASE_PROJECT_ID=your-firebase-project
ARDUINO_SERIAL_PORT=/dev/ttyUSB0
ARDUINO_BAUD_RATE=115200
LOG_LEVEL=INFO
DEBUG_MODE=false
EOF

    chown ${SUDO_USER:-pi}:${SUDO_USER:-pi} "$WORK_DIR/.env" 2>/dev/null || true
    chmod 600 "$WORK_DIR/.env"

    success ".env file created (edit with: nano $WORK_DIR/.env)"
}

copy_firmware_files() {
    step "Copying firmware files"

    FILES=(
        "firmware.py"
        "config.py"
        "serial_handler.py"
        "system_monitor.py"
        "video_streaming.py"
        "command_handler.py"
        "control_access_manager.py"
        "telemetry_service.py"
        "firebase_manager.py"
        "mediamtx.yml"
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
# SYSTEMD SERVICES
################################################################################

create_systemd_services() {
    step "Creating systemd services"

    log "Creating mediamtx.service..."
    cat > /etc/systemd/system/mediamtx.service << EOF
[Unit]
Description=Mediamtx Media Server
After=network.target

[Service]
Type=simple
User=${SUDO_USER:-pi}
WorkingDirectory=$WORK_DIR
ExecStart=/usr/local/bin/mediamtx $WORK_DIR/mediamtx.yml
Restart=on-failure
RestartSec=5
StandardOutput=append:$WORK_DIR/logs/mediamtx.log
StandardError=append:$WORK_DIR/logs/mediamtx.log

[Install]
WantedBy=multi-user.target
EOF

    log "Creating agromotion-firmware.service..."
    cat > /etc/systemd/system/agromotion-firmware.service << EOF
[Unit]
Description=AgroMotion Robot Firmware
After=network.target mediamtx.service

[Service]
Type=simple
User=${SUDO_USER:-pi}
WorkingDirectory=$WORK_DIR
Environment="PATH=$VENV_PATH/bin"
ExecStart=$VENV_PATH/bin/python3 $WORK_DIR/firmware.py
Restart=on-failure
RestartSec=10
StandardOutput=append:$WORK_DIR/logs/firmware.log
StandardError=append:$WORK_DIR/logs/firmware.log

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload

    success "Systemd services created"
}

################################################################################
# VERIFICATION
################################################################################

check_installation() {
    step "Checking installation"

    local MISSING=0

    # Python 3
    if command -v python3 &> /dev/null; then
        success "Python 3 installed"
    else
        error "Python 3 not found"
        MISSING=$((MISSING + 1))
    fi

    # Virtual environment
    if [[ -d "$VENV_PATH" ]]; then
        success "Virtual environment exists"
    else
        warning "Virtual environment not found"
        MISSING=$((MISSING + 1))
    fi

    # Python modules
    if [[ -d "$VENV_PATH" ]]; then
        if "$VENV_PATH/bin/python3" -c "import psutil, serial" 2>/dev/null; then
            success "Python modules installed"
        else
            warning "Some Python modules missing"
            MISSING=$((MISSING + 1))
        fi
    fi

    # Mediamtx
    if command -v mediamtx &> /dev/null; then
        success "Mediamtx installed"
    else
        warning "Mediamtx not found"
        MISSING=$((MISSING + 1))
    fi

    # Firmware files
    if [[ -f "$WORK_DIR/firmware.py" ]]; then
        success "Firmware files exist"
    else
        warning "Firmware files not found in $WORK_DIR"
        MISSING=$((MISSING + 1))
    fi

    # Configuration
    if [[ -f "$WORK_DIR/.env" ]]; then
        success ".env configuration exists"
    else
        warning ".env configuration not found"
        MISSING=$((MISSING + 1))
    fi

    echo ""
    if [[ $MISSING -eq 0 ]]; then
        success "All checks passed!"
        return 0
    else
        warning "$MISSING issues found"
        return 1
    fi
}

################################################################################
# FIRMWARE EXECUTION
################################################################################

run_firmware() {
    local MODE="${1:-camera}"

    step "Running firmware"

    # Check if not in venv
    if [[ -z "$VIRTUAL_ENV" && -d "$VENV_PATH" ]]; then
        log "Activating virtual environment..."
        source "$VENV_PATH/bin/activate"
    fi

    # Check Python dependencies
    if ! python3 -c "import psutil" 2>/dev/null; then
        warning "Dependencies not installed, installing now..."
        install_python_dependencies
        source "$VENV_PATH/bin/activate"
    fi

    # Check .env
    if [[ ! -f "$WORK_DIR/.env" ]]; then
        log "Creating .env configuration..."
        setup_env_file
    fi

    log "Starting firmware with mode: $MODE"
    cd "$WORK_DIR" || exit 1
    python3 firmware.py "$MODE"
}

################################################################################
# DOCUMENTATION
################################################################################

show_documentation() {
    step "AgroMotion Robot Documentation"

    # Look for documentation in various places
    local DOC_PATH=""
    
    if [[ -f "$SCRIPT_DIR/../DOCUMENTATION.md" ]]; then
        DOC_PATH="$SCRIPT_DIR/../DOCUMENTATION.md"
    elif [[ -f "$SCRIPT_DIR/DOCUMENTATION.md" ]]; then
        DOC_PATH="$SCRIPT_DIR/DOCUMENTATION.md"
    elif [[ -f "./DOCUMENTATION.md" ]]; then
        DOC_PATH="./DOCUMENTATION.md"
    elif [[ -f "$SCRIPT_DIR/readme.md" ]]; then
        DOC_PATH="$SCRIPT_DIR/readme.md"
    else
        error "Documentation file not found"
        return 1
    fi

    log "Documentation file: $DOC_PATH"
    log "Press 'q' to exit the documentation viewer\n"
    
    if command -v less &> /dev/null; then
        less "$DOC_PATH"
    else
        cat "$DOC_PATH"
    fi
}

################################################################################
# INTERACTIVE MENU
################################################################################

show_menu() {
    clear

    cat << 'EOF'

________________________________________________________
 AgroMotion Robot - Installation & Management
________________________________________________________

What would you like to do?

 1) Full setup (install everything - requires root)
 2) Check installation status
 3) Show documentation
 4) Install Python dependencies only
 5) Run firmware
 6) Exit

________________________________________________________

EOF

    read -p "Select option (1-6): " choice

    case $choice in
        1)
            check_root
            install_system_dependencies
            setup_directories
            install_mediamtx
            setup_python_env
            install_python_dependencies
            copy_firmware_files
            setup_env_file
            create_systemd_services
            log "Next: edit .env with your configuration"
            log "Then: sudo systemctl start agromotion-firmware.service"
            ;;
        2)
            check_installation
            ;;
        3)
            show_documentation
            ;;
        4)
            setup_python_env
            install_python_dependencies
            ;;
        5)
            read -p "Enter firmware mode (camera/video) [camera]: " mode
            mode=${mode:-camera}
            run_firmware "$mode"
            ;;
        6)
            echo "Goodbye!"
            exit 0
            ;;
        *)
            error "Invalid option"
            sleep 1
            show_menu
            ;;
    esac

    read -p "Press Enter to continue..."
    show_menu
}

################################################################################
# MAIN EXECUTION
################################################################################

main() {
    # Create log directory if needed
    mkdir -p "$WORK_DIR" 2>/dev/null || true
    > "$LOG_FILE"

    # Parse command line arguments
    case "${1:-}" in
        --help)
            show_help
            exit 0
            ;;
        --setup)
            check_root
            install_system_dependencies
            setup_directories
            install_mediamtx
            setup_python_env
            install_python_dependencies
            copy_firmware_files
            setup_env_file
            create_systemd_services
            check_installation
            ;;
        --check)
            check_installation
            ;;
        --docs)
            show_documentation
            ;;
        --deps)
            setup_python_env
            install_python_dependencies
            ;;
        --firmware)
            if [[ -z "${2:-}" ]]; then
                error "Usage: bash install.sh --firmware [camera|video]"
                exit 1
            fi
            run_firmware "$2"
            ;;
        "")
            # No arguments - show interactive menu
            show_menu
            ;;
        *)
            error "Unknown option: $1"
            show_help
            exit 1
            ;;
    esac
}

# Run
main "$@"
