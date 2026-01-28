from src.parser import ConfigParser
import sys

def test_parser():
    file_path = "configs.txt"
    print(f"Reading configs from {file_path}...")
    try:
        configs = ConfigParser.parse_file(file_path)
        print(f"Successfully parsed {len(configs)} configs.")
        
        vmess_count = sum(1 for c in configs if c.protocol == "vmess")
        vless_count = sum(1 for c in configs if c.protocol == "vless")
        ss_count = sum(1 for c in configs if c.protocol == "ss")
        trojan_count = sum(1 for c in configs if c.protocol == "trojan")
        
        print(f"Stats:")
        print(f"  VMess : {vmess_count}")
        print(f"  VLess : {vless_count}")
        print(f"  SS    : {ss_count}")
        print(f"  Trojan: {trojan_count}")
        
        if configs:
            print("\nSample Config 1:")
            print(configs[0])
            print("\nSample Config 20:")
            if len(configs) > 20: 
                print(configs[20])

    except Exception as e:
        print(f"Failed to parse file: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_parser()
