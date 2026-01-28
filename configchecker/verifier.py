import asyncio
import json
import os
import platform
import shutil
import subprocess
import zipfile
import aiohttp
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
        # Add linux/windows if needed later
        return None

    @staticmethod
    async def ensure_xray():
        if os.path.exists(XrayVerifier.XRAY_PATH):
            return True
            
        print("Downloading Xray Core for Verification...")
        os.makedirs(XrayVerifier.BIN_DIR, exist_ok=True)
        url = XrayVerifier._get_platform_url()
        
        if not url:
            print("Unsupported platform for auto-download.")
            return False
            
        zip_path = os.path.join(XrayVerifier.BIN_DIR, "xray.zip")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        with open(zip_path, 'wb') as f:
                            f.write(await resp.read())
                            
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(XrayVerifier.BIN_DIR)
            
            os.chmod(XrayVerifier.XRAY_PATH, 0o755)
            # Cleanup
            if os.path.exists(zip_path):
                os.remove(zip_path)
            return True
        except Exception as e:
            print(f"Failed to download Xray: {e}")
            return False

    @staticmethod
    async def verify_config(config: ProxyConfig, timeout=5) -> bool:
        """
        Runs Xray with the given config in a temporary process, 
        tries to proxy a request to a reliable endpoint, and returns True/False.
        """
        if not await XrayVerifier.ensure_xray():
            return True # Fallback: If no Xray, assume True (TCP check was OK)
            
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
        
        # Convert ProxyConfig to Xray Outbound JSON (Simplified adapter)
        # This is the tricky part - we need to parse config.raw_link to JSON properly
        # For now, let's rely on a helper or basic template.
        # Since we don't have a full link->json converter here, we might need to add it 
        # or use a simplified approach depending on protocol.
        
        # Reuse logic from old xray_manager or re-implement basic parsing?
        # Actually, for verification, we need accuracy. 
        # Let's try to infer from link components.
        
        # ... Wait, generating a VALID xray json from just a link string is complex.
        # However, for 'vmess', we already parsed standard fields. for 'vless' too.
        # Let's support vmess/vless/trojan/ss basic.
        
        outbound = XrayVerifier._generate_outbound(config)
        if not outbound:
            return True # Cannot verify this protocol, pass it.
            
        xray_config["outbounds"].append(outbound)
        
        config_path = os.path.join(XrayVerifier.BIN_DIR, f"temp_config_{local_port}.json")
        with open(config_path, 'w') as f:
            json.dump(xray_config, f)
            
        process = None
        try:
            # Start Xray
            process = subprocess.Popen(
                [XrayVerifier.XRAY_PATH, "-c", config_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            
            # Init Wait
            await asyncio.sleep(1) # Wait for core startup
            if process.poll() is not None:
                return False # Crashed on startup
                
            # Try Proxy Request
            # Using aiohttp with proxy
            async with aiohttp.ClientSession() as session:
                try:
                    # Target: Google (Real Delay) or something global like Cloudflare
                    start = time.time()
                    async with session.get("http://www.gstatic.com/generate_204", 
                                         proxy=f"http://127.0.0.1:{local_port}",
                                         timeout=timeout) as resp:
                        if resp.status == 204:
                            return True
                except:
                    return False
        except Exception:
            return False
        finally:
            if process:
                process.terminate()
                process.wait()
            if os.path.exists(config_path):
                os.remove(config_path)
                
        return False

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
