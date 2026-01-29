#!/bin/bash
# Xray Installation Script for ConfigChecker
# This script downloads and installs the Xray binary for real delay testing

set -e

echo "🔧 Xray Installation Script for ConfigChecker"
echo "=============================================="

# Detect platform
OS=$(uname -s | tr '[:upper:]' '[:lower:]')
ARCH=$(uname -m)

echo "📍 Detected: OS=$OS, ARCH=$ARCH"

# Determine download URL
if [ "$OS" = "darwin" ]; then
    if [ "$ARCH" = "arm64" ]; then
        URL="https://github.com/XTLS/Xray-core/releases/download/v1.8.4/Xray-macos-arm64-v8a.zip"
    else
        URL="https://github.com/XTLS/Xray-core/releases/download/v1.8.4/Xray-macos-64.zip"
    fi
elif [ "$OS" = "linux" ]; then
    if [ "$ARCH" = "x86_64" ]; then
        URL="https://github.com/XTLS/Xray-core/releases/download/v1.8.4/Xray-linux-64.zip"
    elif [ "$ARCH" = "aarch64" ] || [ "$ARCH" = "arm64" ]; then
        URL="https://github.com/XTLS/Xray-core/releases/download/v1.8.4/Xray-linux-arm64-v8a.zip"
    else
        echo "❌ Unsupported Linux architecture: $ARCH"
        exit 1
    fi
else
    echo "❌ Unsupported OS: $OS"
    exit 1
fi

# Find the configchecker package location
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Try to find configchecker bin directory
if [ -d "$SCRIPT_DIR/configchecker/bin" ]; then
    BIN_DIR="$SCRIPT_DIR/configchecker/bin"
elif command -v configchecker &> /dev/null; then
    # Find via pipx/installed package
    PKG_PATH=$(python3 -c "import configchecker; import os; print(os.path.dirname(configchecker.__file__))" 2>/dev/null)
    if [ -n "$PKG_PATH" ]; then
        BIN_DIR="$PKG_PATH/bin"
    else
        echo "❌ Could not find configchecker package location"
        exit 1
    fi
else
    # Default to current directory
    BIN_DIR="./configchecker/bin"
fi

echo "📁 Installing to: $BIN_DIR"
mkdir -p "$BIN_DIR"

# Download
echo "⬇️  Downloading Xray from: $URL"
TEMP_ZIP="$BIN_DIR/xray.zip"
curl -L -o "$TEMP_ZIP" "$URL"

# Extract
echo "📦 Extracting..."
unzip -o "$TEMP_ZIP" -d "$BIN_DIR"
rm "$TEMP_ZIP"

# Set permissions
chmod +x "$BIN_DIR/xray"

echo ""
echo "✅ Xray installed successfully!"
echo "📍 Location: $BIN_DIR/xray"
echo ""
echo "You can now run: configchecker --mode realtime"
