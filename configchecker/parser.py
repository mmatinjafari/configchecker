import base64
import json
import urllib.parse
from typing import List, Optional
from .models import ProxyConfig

class ConfigParser:
    @staticmethod
    def parse_file(file_path: str) -> List[ProxyConfig]:
        configs = []
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    config = ConfigParser.parse_link(line)
                    if config:
                        configs.append(config)
                except Exception as e:
                    print(f"Error parsing line: {line[:50]}... | {e}")
        return configs

    @staticmethod
    def parse_link(link: str) -> Optional[ProxyConfig]:
        if link.startswith("vmess://"):
            return ConfigParser._parse_vmess(link)
        elif link.startswith("vless://"):
            return ConfigParser._parse_vless(link)
        elif link.startswith("ss://"):
            return ConfigParser._parse_ss(link)
        elif link.startswith("trojan://"):
            # Trojan often shares similar URL structure to Vless
            return ConfigParser._parse_trojan(link)
        return None

    @staticmethod
    def _clean_remarks(remark: str) -> str:
        if not remark:
            return ""
        # Recursively unquote until no change
        # Limit iterations to avoid infinite loops in weird cases
        current = remark
        for _ in range(5): 
            decoded = urllib.parse.unquote(current)
            if decoded == current:
                break
            current = decoded
        return current.strip()

    @staticmethod
    def _parse_vmess(link: str) -> ProxyConfig:
        b64_part = link[8:]
        # Add padding if missing
        missing_padding = len(b64_part) % 4
        if missing_padding:
            b64_part += '=' * (4 - missing_padding)
        
        try:
            json_str = base64.b64decode(b64_part).decode('utf-8')
            data = json.loads(json_str)
            
            return ProxyConfig(
                protocol="vmess",
                address=data.get("add", ""),
                port=int(data.get("port", 0)),
                uuid=data.get("id", ""),
                security=data.get("scy", "auto"),
                network=data.get("net", "tcp"),
                path=data.get("path", ""),
                host=data.get("host", ""),
                sni=data.get("sni", ""),
                remarks=ConfigParser._clean_remarks(data.get("ps", "")),
                raw_link=link
            )
        except Exception as e:
            raise ValueError(f"Invalid VMess link: {e}")

    @staticmethod
    def _parse_vless(link: str) -> ProxyConfig:
        # vless://uuid@ip:port?query#hash
        parsed = urllib.parse.urlparse(link)
        if not parsed.hostname:
             raise ValueError("Invalid VLESS link: missing hostname")
             
        user_info = parsed.username
        uuid = user_info if user_info else ""
        
        query_params = urllib.parse.parse_qs(parsed.query)
        
        return ProxyConfig(
            protocol="vless",
            address=parsed.hostname,
            port=parsed.port if parsed.port else 443,
            uuid=uuid,
            security=query_params.get("security", ["none"])[0],
            network=query_params.get("type", ["tcp"])[0],
            path=query_params.get("path", [""])[0],
            host=query_params.get("host", [""])[0],
            sni=query_params.get("sni", [""])[0],
            remarks=ConfigParser._clean_remarks(parsed.fragment),
            raw_link=link
        )

    @staticmethod
    def _parse_ss(link: str) -> ProxyConfig:
        # simplified ss parser
        # ss://base64#remarks
        parsed = urllib.parse.urlparse(link)
        netloc = parsed.netloc
        if "@" in netloc:
             # user:pass@ip:port format (often not base64 encoded fully)
             user_pass_part, host_port = netloc.split("@", 1)
             # Decode user_pass if it looks looked base64 (standard is base64(method:password))
             # But often clients handle various formats.
             # Minimal implementation for now.
             address = host_port.split(":")[0]
             port = int(host_port.split(":")[1]) if ":" in host_port else 8388
             return ProxyConfig(
                protocol="ss",
                address=address,
                port=port,
                password=user_pass_part, # Keeping it raw for now
                remarks=ConfigParser._clean_remarks(parsed.fragment),
                raw_link=link
            )
        else:
             # Legacy base64 format without @ in standard URL part?
             # Or ss://BASE64#remark
             # Implementation can be complex, skipping full implementation for this pass
             return ProxyConfig(
                 protocol="ss",
                 address="unknown",
                 port=0,
                 remarks="Complex SS Link",
                 raw_link=link
             )

    @staticmethod
    def _parse_trojan(link: str) -> ProxyConfig:
        parsed = urllib.parse.urlparse(link)
        query_params = urllib.parse.parse_qs(parsed.query)
        
        return ProxyConfig(
            protocol="trojan",
            address=parsed.hostname,
            port=parsed.port if parsed.port else 443,
            password=parsed.username,
            security=query_params.get("security", ["tls"])[0],
            network=query_params.get("type", ["tcp"])[0],
            sni=query_params.get("sni", [""])[0],
            remarks=ConfigParser._clean_remarks(parsed.fragment),
            raw_link=link
        )
