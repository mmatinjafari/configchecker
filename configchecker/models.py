from dataclasses import dataclass
from typing import Optional, Literal

@dataclass
class ProxyConfig:
    protocol: Literal["vmess", "vless", "ss", "trojan", "ssh"]
    address: str
    port: int
    uuid: Optional[str] = None  # for vmess/vless/trojan
    password: Optional[str] = None # for ss/ssh
    security: str = "auto"
    network: str = "tcp"
    path: Optional[str] = None
    host: Optional[str] = None
    sni: Optional[str] = None
    remarks: str = ""
    raw_link: str = ""

    def __str__(self):
        return f"[{self.protocol.upper()}] {self.remarks} ({self.address}:{self.port})"
