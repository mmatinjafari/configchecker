# 🚀 Stability Checker

A high-performance, asynchronous tool designed to verify the connectivity and stability of V2Ray (VMess/VLess), Trojan, and SSH/Shadowsocks configurations.

Unlike simple ping tools, **Stability Checker** monitors connections over a duration (e.g., 60 seconds) to detect **Packet Loss** and **Jitter**, helping you find truly stable servers.

## ✨ Features

- **⚡️ High Concurrency**: Capable of testing hundreds of configurations simultaneously (default 500 threads).
- **📊 Stability Metrics**:
  - **Packet Loss %**: Percentage of failed connection attempts.
  - **Avg Latency**: Mean TCP handshake time.
  - **Jitter**: Standard deviation of latency (lower is better).
- **🔗 Protocol Support**: Automatically parses and tests:
  - `vmess://` (Base64 decoded)
  - `vless://`
  - `trojan://`
  - `ss://` (Shadowsocks)
- **🧠 Smart Reporting**:
  - Recursively decodes URL-encoded remarks/names.
  - Highlights the **Top 5 Best Configs** at the end of the run.
- **🛠 CLI Wrapper**: Includes a `stablecheck` script that handles virtual environments automatically.

## 📦 Installation & Setup

1.  **Clone/Open the project**:
    ```bash
    cd /path/to/stable-checker
    ```

2.  **Install Dependencies**:
    The project includes a wrapper script that manages the environment. First, ensure you have Python 3 installed.
    
    If you haven't set up the virtual environment yet:
    ```bash
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
    ```

3.  **Setup Global Command (Optional)**:
    Add the convenience script to your path or create an alias:
    ```bash
    # Add alias to your .zshrc
    echo "alias stablecheck='$(pwd)/stablecheck'" >> ~/.zshrc
    source ~/.zshrc
    ```

## 🚀 Usage

### 1. Stability Mode (Recommended)
Tests all configs in `configs.txt` for a specific duration to measure stability.

```bash
# Run for 60 seconds (Standard Test)
stablecheck --mode stable --duration 60

# Run for 2 minutes
stablecheck --mode stable --duration 120
```

### 2. Quick Mode
Performs a single "one-shot" connectivity check on all configs. Good for quickly filtering out dead servers.

```bash
stablecheck --mode quick
```

### 3. Realtime Mode
Launches a **live dashboard** that continuously monitors all configs with real-time updates, showing stability scores, latencies, and a **QR code** for the best config (for easy mobile import).

```bash
stablecheck --mode realtime
```

**Keyboard Navigation:**
| Key | Action |
|-----|--------|
| `↑` / `k` | Move selection up |
| `↓` / `j` | Move selection down |
| `Esc` | Return to auto-selection mode |
| `q` | Quit |


### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--file` | `configs.txt` | Path to your configuration file containing links. |
| `--mode` | `quick` | `quick` (one-shot), `stable` (duration-based), or `realtime` (live monitor). |
| `--duration` | `30` | Duration of the stability test in seconds (stable mode only). |
| `--concurrency` | `50` | Number of concurrent checks. |
| `--bind-ip` | Auto | Local IP to bind to (useful for bypassing VPN loops). |
| `--no-bind` | - | Disable auto-detection of local IP binding. |

## 📋 Input File Format (`configs.txt`)
The tool expects a file named `configs.txt` in the root directory. Paste your config links line-by-line:

```text
vless://uuid@ip:port?security=tls&...#Example1
vmess://eyJhZG...
trojan://password@ip:port...
ss://...
```

## 🏆 Output Example

```text
🏆 TOP 5 STABLE CONFIGS 🏆
========================================================================================================================
1. DE-Server-1
   Shape: Loss=0.0%, Latency=180.5ms, Jitter=12.4ms
   Link: vless://...
------------------------------------------------------------------------------------------------------------------------
2. US-Backup-2
   Shape: Loss=0.0%, Latency=210.2ms, Jitter=45.1ms
   Link: vless://...
```
