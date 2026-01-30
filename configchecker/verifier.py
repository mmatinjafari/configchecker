import asyncio
import json
import os
import platform
import shutil
import subprocess
import time
import zipfile
import aiohttp
from rich.console import Console
from .models import ProxyConfig

class XrayVerifier:
    BIN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin")
    XRAY_PATH = os.path.join(BIN_DIR, "xray")
    
    @staticmethod
    def _get_platform_url():
        system = platform.system().lower()
        machine = platform.machine().lower()
        
        if system == "darwin": # macOS
            if machine == "arm64":
                return "https://github.com/XTLS/Xray-core/releases/download/v1.8.4/Xray-macos-arm64-v8a.zip"
            else:
                return "https://github.com/XTLS/Xray-core/releases/download/v1.8.4/Xray-macos-64.zip"
        elif system == "linux":
            if machine == "x86_64" or machine == "amd64":
                return "https://github.com/XTLS/Xray-core/releases/download/v1.8.4/Xray-linux-64.zip"
            elif machine == "aarch64" or machine == "arm64":
                return "https://github.com/XTLS/Xray-core/releases/download/v1.8.4/Xray-linux-arm64-v8a.zip"
        return None

    @staticmethod
    async def ensure_xray():
        if os.path.exists(XrayVerifier.XRAY_PATH):
            return True
        
        console = Console()
        
        try:
            os.makedirs(XrayVerifier.BIN_DIR, exist_ok=True)
            url = XrayVerifier._get_platform_url()
            
            if not url:
                console.print("[red]❌ Unsupported platform for Xray[/red]")
                return False
            
            console.print("[cyan]📥 Downloading Xray core (first run only)...[/cyan]")
            
            zip_path = os.path.join(XrayVerifier.BIN_DIR, "xray.zip")
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        total = int(resp.headers.get('content-length', 0))
                        data = await resp.read()
                        with open(zip_path, 'wb') as f:
                            f.write(data)
                        console.print(f"[green]✓ Downloaded {len(data) // 1024 // 1024}MB[/green]")
                    else:
                        console.print(f"[red]❌ Download failed: HTTP {resp.status}[/red]")
                        return False
            
            console.print("[cyan]📦 Extracting...[/cyan]")
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(XrayVerifier.BIN_DIR)
            
            os.chmod(XrayVerifier.XRAY_PATH, 0o755)
            # Cleanup
            if os.path.exists(zip_path):
                os.remove(zip_path)
            
            console.print("[green]✓ Xray ready![/green]\n")
            return True
        except Exception as e:
            console.print(f"[red]❌ Failed to setup Xray: {e}[/red]")
            return False

    @staticmethod
    async def verify_config(config: ProxyConfig, timeout=5) -> tuple:
        """
        Runs Xray with the given config in a temporary process, 
        tries to proxy a request to a reliable endpoint.
        Returns: (is_valid: bool, latency_ms: float)
        """
        if not await XrayVerifier.ensure_xray():
            return True, 0  # Fallback: If no Xray, assume True (TCP check was OK)
            
        # Create temp config.json
        # Port must be random to avoid conflicts
        import random
        local_port = random.randint(30000, 40000)
        
        xray_config = {
            "log": {"loglevel": "none"},
            "inbounds": [{
                "port": local_port,
                "protocol": "http",
                "settings": {"udp": True}
            }],
            "outbounds": []
        }
        
        outbound = XrayVerifier._generate_outbound(config)
        if not outbound:
            return True, 0  # Cannot verify this protocol, pass it.
            
        xray_config["outbounds"].append(outbound)
        
        config_path = os.path.join(XrayVerifier.BIN_DIR, f"temp_config_{local_port}.json")
        with open(config_path, 'w') as f:
            json.dump(xray_config, f)
            
        process = None
        latency = 0
        try:
            # Start Xray
            process = subprocess.Popen(
                [XrayVerifier.XRAY_PATH, "-c", config_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            
            # Init Wait (fast startup)
            await asyncio.sleep(0.2)  # Reduced wait for speed
            if process.poll() is not None:
                return False, 0  # Crashed on startup
                
            # Try Proxy Request
            # Using aiohttp with proxy
            async with aiohttp.ClientSession() as session:
                try:
                    # Target: Google (Real Delay)
                    start = time.time()
                    async with session.get("http://www.gstatic.com/generate_204", 
                                         proxy=f"http://127.0.0.1:{local_port}",
                                         timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                        latency = (time.time() - start) * 1000
                        if resp.status == 204:
                            return True, latency
                except:
                    return False, 0
        except Exception:
            return False, 0
        finally:
            if process:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except:
                    process.kill()
            if os.path.exists(config_path):
                os.remove(config_path)
                
        return False, 0

    @staticmethod
    async def verify_all_configs(configs, concurrency=5, progress_callback=None):
        """
        Verify all configs with real delay test.
        Returns: list of (config, latency_ms) sorted by latency, or None if Xray unavailable
        """
        # Check if Xray is available first
        xray_available = await XrayVerifier.ensure_xray()
        if not xray_available:
            # Return None to signal that Phase 1 should be skipped
            return None
        
        sem = asyncio.Semaphore(concurrency)
        results = []
        total = len(configs)
        completed = 0
        
        async def verify_one(config):
            nonlocal completed
            async with sem:
                is_valid, latency = await XrayVerifier.verify_config(config, timeout=5)
                completed += 1
                if progress_callback:
                    progress_callback(completed, total, config.remarks[:30], is_valid, latency)
                return (config, is_valid, latency)
        
        tasks = [verify_one(c) for c in configs]
        results = await asyncio.gather(*tasks)
        
        # Filter valid configs and sort by latency
        valid_results = [(c, lat) for c, valid, lat in results if valid and lat > 0]
        valid_results.sort(key=lambda x: x[1])  # Sort by latency ascending
        
        return valid_results

    @staticmethod
    def _generate_outbound(config: ProxyConfig) -> dict:
        stream_settings = {
            "network": config.network,
            "security": config.security if config.security != "auto" else "none",
        }
        
        if config.security == "tls":
             stream_settings["tlsSettings"] = {"serverName": config.sni}
        
        if config.network == "ws":
            stream_settings["wsSettings"] = {"path": config.path, "headers": {"Host": config.host}}
        elif config.network == "grpc":
            stream_settings["grpcSettings"] = {"serviceName": config.path} # path often holds service name in parsing
            
        out = {
            "protocol": config.protocol,
            "settings": {},
            "streamSettings": stream_settings
        }
        
        if config.protocol == "vmess":
            out["settings"] = {
                "vnext": [{
                    "address": config.address,
                    "port": config.port,
                    "users": [{"id": config.uuid}]
                }]
            }
        elif config.protocol == "vless":
            out["settings"] = {
                "vnext": [{
                    "address": config.address,
                    "port": config.port,
                    "users": [{"id": config.uuid, "encryption": "none"}]
                }]
            }
            if config.security == "reality":
                 stream_settings["security"] = "reality"
                 # Reality needs pbk, shortId etc which our simple parser might miss
                 # If parsing was incomplete, config might fail.
                 # For now, basic fallback.
                 pass
                 
        elif config.protocol == "ss":
             out["settings"] = {
                "servers": [{
                    "address": config.address,
                    "port": config.port,
                    "password": config.password or config.uuid, # parser might put pass in uuid slot
                    "method": "chacha20-ietf-poly1305" # Default guess, real one needed
                }]
             }
             
        return out
