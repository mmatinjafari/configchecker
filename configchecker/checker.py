import asyncio
import time
import statistics
from dataclasses import dataclass
from typing import List, Tuple
from .models import ProxyConfig

@dataclass
class StabilityResult:
    config: ProxyConfig
    packet_loss: float
    avg_latency: float
    jitter: float
    total_checks: int
    successful_checks: int

class ProxyChecker:
    @staticmethod
    async def check_tcp_connect(config: ProxyConfig, timeout: int = 5, bind_addr: str = None) -> Tuple[bool, float, str]:
        """
        Checks if the proxy server's IP/Port is reachable via TCP.
        Returns: (is_reachable, latency_ms, error_message)
        """
        start_time = time.time()
        try:
            # If bind_addr is provided, we bind to that local interface to try and bypass system VPN routing
            local_addr = (bind_addr, 0) if bind_addr else None
            
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(config.address, config.port, local_addr=local_addr), 
                timeout=timeout
            )
            latency = (time.time() - start_time) * 1000
            writer.close()
            await writer.wait_closed()
            return True, latency, ""
        except asyncio.TimeoutError:
            return False, 0, "Timeout"
        except Exception as e:
            return False, 0, str(e)

    @staticmethod
    async def check_stability(config: ProxyConfig, duration: int = 60, interval: int = 1, bind_addr: str = None) -> StabilityResult:
        """
        Checks stability over a duration.
        """
        latencies = []
        failures = 0
        checks = 0
        endtime = time.time() + duration
        
        while time.time() < endtime:
            is_up, lat, err = await ProxyChecker.check_tcp_connect(config, timeout=3, bind_addr=bind_addr) # short timeout for stability checks
            checks += 1
            if is_up:
                latencies.append(lat)
            else:
                failures += 1
            
            # Simple sleep for interval, subtract execution time to be more precise?
            # For now simply sleep.
            await asyncio.sleep(interval)

        packet_loss = (failures / checks * 100) if checks > 0 else 0
        avg_latency = statistics.mean(latencies) if latencies else 0
        jitter = statistics.stdev(latencies) if len(latencies) > 1 else 0

        return StabilityResult(
            config=config,
            packet_loss=packet_loss,
            avg_latency=avg_latency,
            jitter=jitter,
            total_checks=checks,
            successful_checks=len(latencies)
        )

    @staticmethod
    async def check_all_stability(configs: List[ProxyConfig], duration: int = 60, concurrency: int = 20, bind_addr: str = None) -> List[StabilityResult]:
        sem = asyncio.Semaphore(concurrency)
        results = []

        async def check_one(config):
            async with sem:
                return await ProxyChecker.check_stability(config, duration=duration, bind_addr=bind_addr)

        tasks = [check_one(c) for c in configs]
        results = await asyncio.gather(*tasks)
        return results
