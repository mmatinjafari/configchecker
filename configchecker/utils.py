import socket
import subprocess
import sys

def get_local_ip():
    """
    Attempts to find the physical LAN IP to bypass VPN interfaces.
    """
    # 1. Try macOS specific command for Wi-Fi (en0)
    if sys.platform == "darwin":
        try:
            result = subprocess.run(["ipconfig", "getifaddr", "en0"], capture_output=True, text=True)
            ip = result.stdout.strip()
            if ip:
                return ip
        except Exception:
            pass

    # 2. Fallback: Socket connect (Standard method), but filter out VPN/Bogus ranges
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Doesn't actually connect, just calculates route
        s.connect(('8.8.8.8', 80))
        IP = s.getsockname()[0]
        
        # If we got a reserved IP often used by VPNs (Class E or Carrier Grade NAT), fallback
        if IP.startswith("240.") or IP.startswith("100."):
             # Try to find another IP from gethostbyname_ex
             hostname = socket.gethostname()
             try:
                 _, _, ip_list = socket.gethostbyname_ex(hostname)
                 for addr in ip_list:
                     # Pick first one that looks like 192.168 or 10.x or 172.x 
                     # and is NOT the VPN one we just found
                     if not addr.startswith("127.") and not addr.startswith("240.") and addr != IP:
                         return addr
             except:
                 pass
                 
    except Exception:
        IP = "127.0.0.1"
    finally:
        s.close()
    return IP
