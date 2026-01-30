import asyncio
import time
import statistics
import sys
import select
from collections import deque
from typing import List, Dict, Deque
from rich.live import Live
from rich.table import Table
from rich.layout import Layout
from rich.console import Console
from .models import ProxyConfig
from .checker import ProxyChecker

# Keyboard input handler for navigation
try:
    import termios
    import tty
    HAS_TERMIOS = True
except ImportError:
    HAS_TERMIOS = False

class KeyboardHandler:
    """Non-blocking keyboard input handler for Unix terminals."""
    
    def __init__(self):
        self.old_settings = None
        self.enabled = False
        
    def enable_raw(self):
        """Enable raw mode for immediate key detection."""
        if not HAS_TERMIOS:
            return False
        try:
            self.old_settings = termios.tcgetattr(sys.stdin)
            tty.setcbreak(sys.stdin.fileno())
            self.enabled = True
            return True
        except Exception:
            return False
    
    def restore(self):
        """Restore terminal to normal mode."""
        if self.old_settings and HAS_TERMIOS:
            try:
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, self.old_settings)
            except Exception:
                pass
        self.enabled = False
    
    def get_key(self, timeout=0.05):
        """
        Non-blocking key read.
        Returns: 'up', 'down', 'esc', 'enter', or None
        """
        if not self.enabled or not HAS_TERMIOS:
            return None
        try:
            if select.select([sys.stdin], [], [], timeout)[0]:
                ch = sys.stdin.read(1)
                
                if ch == '\x1b':  # Escape sequence
                    # Read more characters for arrow keys
                    if select.select([sys.stdin], [], [], 0.01)[0]:
                        ch2 = sys.stdin.read(1)
                        if ch2 == '[':
                            if select.select([sys.stdin], [], [], 0.01)[0]:
                                ch3 = sys.stdin.read(1)
                                if ch3 == 'A':
                                    return 'up'
                                elif ch3 == 'B':
                                    return 'down'
                    return 'esc'  # Plain Escape
                
                elif ch == 'k' or ch == 'K':
                    return 'up'
                elif ch == 'j' or ch == 'J':
                    return 'down'
                elif ch == '\r' or ch == '\n':
                    return 'enter'
                elif ch == 'q' or ch == 'Q':
                    return 'quit'
        except Exception:
            pass
        return None

class RollingStats:
    def __init__(self, config: ProxyConfig, maxlen=100):
        self.config = config
        self.history = deque(maxlen=maxlen) # Stores (is_up, latency)
        self.last_jitter = 0.0
        self.smoothed_score = None
        self.last_success_time = time.time() # Start assuming alive to give it a chance
    
    def add(self, is_up, latency):
        self.history.append((is_up, latency))
        if is_up:
            self.last_success_time = time.time()
        
    def get_metrics(self):
        if not self.history:
            return 0.0, 0.0, 0.0, 0 # loss, lat, jitter, count
            
        total = len(self.history)
        failures = sum(1 for up, _ in self.history if not up)
        loss = (failures / total) * 100
        
        valid_latencies = [lat for up, lat in self.history if up]
        
        # Check for DEAD status (10 mins without success)
        # Note: We handle throttling in the pinger loop, but here we can return a flag if needed.
        # For metrics display, we treat it as 100% loss essentially.
        
        if len(valid_latencies) < 2:
            return loss, 0.0, 0.0, total
            
        avg_lat = statistics.mean(valid_latencies)
        
        # RFC-style Jitter: Mean deviation (average of absolute differences between consecutive latencies)
        # This is more robust for network jitter than standard deviation
        diffs = [abs(valid_latencies[i] - valid_latencies[i-1]) for i in range(1, len(valid_latencies))]
        jitter = statistics.mean(diffs) if diffs else 0.0
        
        return loss, avg_lat, jitter, total
        
    def get_score(self):
        loss, lat, jitter, count = self.get_metrics()
        
        # Mark as effectively dead for sorting if long inactivity
        time_since_success = time.time() - self.last_success_time
        if time_since_success > 600:
             loss = 100.0 # Force 100% loss view for sorting
             return 9999999 + time_since_success, loss, lat, jitter, count # Push to bottom
        
        # Raw score
        raw_score = (loss * 10000) + (jitter * 5) + (lat * 0.5)
        
        if count < 2:
            return 999999 + raw_score, loss, lat, jitter, count
            
        # Exponential Smoothing for Rank Stability
        # New Score = 0.1 * Raw + 0.9 * Old
        if self.smoothed_score is None:
             self.smoothed_score = raw_score
        else:
             self.smoothed_score = (0.05 * raw_score) + (0.95 * self.smoothed_score)
             
        return self.smoothed_score, loss, lat, jitter, count

from rich.layout import Layout
from rich.panel import Panel
from rich.text import Text
from rich.align import Align
from rich.console import Group, Console
from rich.style import Style
import io

def generate_qr_ascii(data: str, console_width: int = None) -> tuple:
    """
    Generate QR code for terminal with strict width checking.
    - Uses "half-block" rendering (standard for best aspect ratio).
    - Checks if QR fits in console_width.
    Returns: (qr_text, qr_width, "success") or (None, required_width, "error") if too narrow.
    """
    try:
        import segno
        qr = segno.make(data, error='L', boost_error=False)
        matrix = qr.matrix
        
        modules = len(matrix[0]) if matrix else 0
        
        # Standard Border = 2
        border = 2
        required_width = modules + (border * 2)
        
        # Guard: Check Width
        if console_width and required_width > console_width:
             # Try reducing border to 1
             border = 1
             required_width = modules + (border * 2)
             
             if required_width > console_width:
                 return (None, required_width, "error")

        # Build QR text (Half-Block Renderer)
        lines = []
        width = modules
        border_line = " " * required_width
        
        for _ in range(border):
            lines.append(border_line)
        
        # Process matrix 2 rows at a time
        for y in range(0, len(matrix), 2):
            line = " " * border
            for x in range(width):
                top = matrix[y][x] if y < len(matrix) else False
                bottom = matrix[y + 1][x] if y + 1 < len(matrix) else False
                
                if top and bottom:
                    line += "█"  # Both dark
                elif top:
                    line += "▀"  # Only top dark
                elif bottom:
                    line += "▄"  # Only bottom dark
                else:
                    line += " "  # Both light
            line += " " * border
            lines.append(line)
        
        for _ in range(border):
            lines.append(border_line)
            
        return ("\n".join(lines), required_width, "success")
    except Exception as e:
        return (f"(QR error: {e})", 0, "error")

def generate_fullscreen_qr(data: str, console) -> None:
    """Display QR code fullscreen with dark background for better scanning."""
    try:
        import segno
        qr = segno.make(data, error='L', boost_error=False)
        
        # Get terminal size
        term_width = console.width
        term_height = console.height
        
        # Generate QR
        output = io.StringIO()
        qr.terminal(out=output, compact=True, border=2)
        qr_text = output.getvalue()
        
        qr_lines = qr_text.strip().split('\n')
        qr_height = len(qr_lines)
        qr_width = len(qr_lines[0]) if qr_lines else 0
        
        # Calculate padding for centering
        top_padding = max(0, (term_height - qr_height - 6) // 2)
        left_padding = max(0, (term_width - qr_width) // 2)
        
        # Clear screen and show fullscreen QR
        console.clear()
        
        # Top padding
        for _ in range(top_padding):
            console.print("")
        
        # Title
        console.print(Align.center(Text("📱 SCAN THIS QR CODE", style="bold yellow on black")))
        console.print(Align.center(Text("Press any key to return...", style="dim white on black")))
        console.print("")
        
        # QR code centered with white on black for contrast
        for line in qr_lines:
            padded_line = " " * left_padding + line
            console.print(Text(padded_line, style="white on black"))
        
        console.print("")
        console.print(Align.center(Text("━" * 40, style="dim")))
        
    except Exception as e:
        console.print(f"[red]QR Error: {e}[/red]")


async def start_monitor(configs: List[ProxyConfig], concurrency: int = 50, bind_addr: str = None):
    from .verifier import XrayVerifier # Lazy import to avoid circular dependency if any
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
    
    console = Console()
    
    # ═══════════════════════════════════════════════════════════════════════
    # PHASE 1: Real Delay Verification (Tests all configs with actual proxy)
    # ═══════════════════════════════════════════════════════════════════════
    console.print("\n[bold cyan]═══ PHASE 1: Testing Real Delay for All Configs ═══[/bold cyan]")
    console.print("[dim]This verifies configs actually work through proxy...[/dim]\n")
    
    # Check if Xray needs to be downloaded (show message to user)
    import os
    xray_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin", "xray")
    if not os.path.exists(xray_path):
        console.print("[yellow]⬇️  Downloading Xray core (first time only)...[/yellow]")
    
    verified_configs = []
    real_delays = {}  # config.raw_link -> latency
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TextColumn("[cyan]{task.fields[status]}[/cyan]"),
        console=console
    ) as progress:
        task = progress.add_task("Verifying configs...", total=len(configs), status="Starting...")
        
        def update_progress(completed, total, name, valid, latency):
            status = f"✓ {latency:.0f}ms" if valid else "✗ Failed"
            progress.update(task, completed=completed, status=f"{name[:20]}... {status}")
        
        results = await XrayVerifier.verify_all_configs(
            configs, 
            concurrency=10,  # Higher concurrency for faster testing
            progress_callback=update_progress
        )
        
        # Handle Xray unavailable or results
        if results is None:
            # Xray not available - tell user how to install
            console.print("\n[bold yellow]⚠ Xray not available - for installing and real delay, run:[/bold yellow]")
            console.print("[bold white]   curl -sSL https://raw.githubusercontent.com/mmatinjafari/configchecker/master/install-xray.sh | bash[/bold white]\n")
            console.print("[dim]Continuing with TCP-only monitoring...[/dim]")
            verified_configs = configs
            real_delays = {}
        elif results:
            for config, latency in results:
                verified_configs.append(config)
                real_delays[config.raw_link] = latency
            console.print(f"\n[bold green]✓ Phase 1 Complete: {len(verified_configs)}/{len(configs)} configs verified[/bold green]")
        else:
            console.print(f"\n[bold green]✓ Phase 1 Complete: 0/{len(configs)} configs verified[/bold green]")
    
    if not verified_configs:
        console.print("[bold red]No working configs found! Exiting.[/bold red]")
        return
    
    # Show top 5 by real delay (only if we have real delays)
    if real_delays:
        console.print("\n[bold]Top 5 by Real Delay:[/bold]")
        for i, cfg in enumerate(verified_configs[:5], 1):
            lat = real_delays.get(cfg.raw_link, 0)
            console.print(f"  {i}. {cfg.remarks[:40]} - [cyan]{lat:.0f}ms[/cyan]")
    
    console.print("\n[bold cyan]═══ PHASE 2: Real Delay Monitoring ═══[/bold cyan]\n")
    await asyncio.sleep(2)  # Brief pause before Phase 2
    
    # ═══════════════════════════════════════════════════════════════════════
    # PHASE 2: Real Delay Monitoring (Top 30 configs only)
    # ═══════════════════════════════════════════════════════════════════════
    
    # Limit to top 30 configs for monitoring
    MAX_MONITORED = 30
    monitored_configs = verified_configs[:MAX_MONITORED]
    console.print(f"[dim]Monitoring top {len(monitored_configs)} configs with real delay checks...[/dim]\n")
    
    stats_map = {c.raw_link: RollingStats(c) for c in monitored_configs}
    # Pre-populate with real delay data from Phase 1
    for cfg in monitored_configs:
        stats_map[cfg.raw_link].add(True, real_delays.get(cfg.raw_link, 100))
    
    sem = asyncio.Semaphore(5)  # Lower concurrency for Xray (resource intensive)
    
    running = True
    trigger_rescan = False  # Flag to trigger full rescan

    async def real_delay_pinger(config: ProxyConfig):
        """Real delay monitoring using Xray verification."""
        stat = stats_map[config.raw_link]
        try:
            while running:
                try:
                    async with sem:
                        is_valid, latency = await XrayVerifier.verify_config(config, timeout=5)
                    
                    stat.add(is_valid, latency if is_valid else 0)
                    
                except Exception as e:
                    stat.add(False, 0)
                
                await asyncio.sleep(5)  # 5-second polling interval
        except asyncio.CancelledError:
            pass

    pinger_tasks = [asyncio.create_task(real_delay_pinger(c)) for c in monitored_configs]
    
    monitor_start_time = time.time()
    recommended_config: ProxyConfig = None
    verification_status = ""
    failed_verifications = set()  # Track configs that failed verification (by raw_link)
    last_verification_time = 0  # Cooldown between verification attempts 
    
    # Navigation state
    selected_index = 0  # Currently selected row in the table
    selected_config = None  # Track the actual selected config (not just index)
    manual_mode = False  # True when user is manually navigating
    cached_snapshots = []  # Cache snapshots for navigation

    def generate_dashboard(rec_config, verify_status, sel_config=None, is_manual=False):
        snapshots = []
        
        # DEBUG: Log what we're seeing in stats
        debug_samples = []
        for stat in stats_map.values():
            score, loss, lat, jitter, count = stat.get_score()
            snapshots.append((score, loss, lat, jitter, stat.config, count))
            if len(debug_samples) < 5:  # Log first 5 for debug
                debug_samples.append(f"{stat.config.remarks[:20]}: hist={len(stat.history)} score={score:.1f} count={count}")
        
        # Write debug
        with open("debug_dashboard.log", "a") as f:
            f.write(f"DASHBOARD: total_stats={len(snapshots)} samples={debug_samples}\n")

        snapshots.sort(key=lambda x: x[0])
        
        # Find the index of the selected config after sorting
        sel_idx = 0
        if is_manual and sel_config:
            for i, (_, _, _, _, config, _) in enumerate(snapshots):
                if config.raw_link == sel_config.raw_link:
                    sel_idx = i
                    break

        # --- Network Health Logic ---
        # Warming up configs have score > 900000
        # Valid established configs have score < 900000
        
        established_stats = [s for s in snapshots if s[0] < 900000 and s[1] < 100]
        warmup_stats = [s for s in snapshots if s[0] >= 900000 and s[1] < 100 and s[5] > 0]
        
        network_status = "CRITICAL"
        style = "bold red"
        details = "Most configs are unreachable"
        
        target_stats = established_stats[:5]
        is_warmup = False
        
        if not target_stats:
            if warmup_stats:
                target_stats = warmup_stats[:5]
                is_warmup = True
            else:
                 # Truly critical, nothing is up
                 pass
        
        if target_stats:
             avg_loss = sum(s[1] for s in target_stats) / len(target_stats)
             avg_jitter = sum(s[3] for s in target_stats) / len(target_stats)
             
             if is_warmup:
                 network_status = "CALCULATING"
                 style = "bold blue"
                 # Progress Bar Logic
                 # Avg samples of top 5
                 avg_count = sum(s[5] for s in target_stats) / len(target_stats)
                 
                 # Debug: Log anomaly
                 if avg_count == 0:
                     with open("debug_pinger.log", "a") as f:
                        f.write(f"ANOMALY: avg_count=0. target_stats size={len(target_stats)}\n")
                        for idx, s in enumerate(target_stats):
                            f.write(f"  Item {idx}: count={s[5]} score={s[0]} loss={s[1]} tuple_len={len(s)}\n")
                 
                 progress = min(avg_count / 2.0, 1.0)
                 bar_len = 20
                 filled = int(progress * bar_len)
                 bar = "█" * filled + "░" * (bar_len - filled)
                 
                 # TEMPORARY DEBUG ON SCREEN
                 counts_str = ",".join(str(s[5]) for s in target_stats)
                 details = f"{bar} {int(progress*100)}% (Samps: {avg_count:.2f}/2.0) [DBG: {counts_str}]"
             else:
                 if avg_loss < 10:
                    if avg_jitter < 50:
                        network_status = "EXCELLENT"
                        style = "bold green"
                        details = "Network is stable and low jitter"
                    elif avg_jitter < 200:
                        network_status = "GOOD"
                        style = "bold green"
                        details = "Usable, slight jitter detected"
                    else:
                        network_status = "UNSTABLE"
                        style = "bold yellow"
                        details = "High jitter detected (Packet variance)"
                 elif avg_loss < 50:
                    network_status = "DEGRADED"
                    style = "bold yellow"
                    details = "Significant packet loss detected"

        header_panel = Panel(
            Align.center(
                Group(
                    Text(f" {network_status} ", style=Style(bgcolor=style.split()[-1], color="black", bold=True)),
                    Text(details, style="dim white")
                )
            ),
            title=f"📡 Network Health Monitor [{bind_addr or 'System Route'}]",
            border_style="blue"
        )
        
        # --- Footer Logic (Display Only) ---
        elapsed = time.time() - monitor_start_time
        footer_content = None
        footer_style = "blue"
        
        # Determine which config to display: selected (manual) or recommended (auto)
        display_config = None
        if is_manual and sel_idx < len(snapshots):
            display_config = snapshots[sel_idx][4]  # config is at index 4
        else:
            display_config = rec_config
        
        # Navigation hint
        nav_hint = "  [dim]↑↓ Navigate | r: Rescan | Esc: Auto[/dim]" if is_manual else "  [dim]↑↓ Browse | r: Rescan all[/dim]"
        
        if elapsed < 60:
            footer_content = Text(f"⏳ Analyzing stability... Best config will appear in {60 - int(elapsed)}s", style="dim white")
        elif verify_status:
             footer_content = Text(f"{verify_status}", style="bold yellow")
        elif display_config:
            # Config info (selected or recommended)
            mode_indicator = "👆 Selected" if is_manual else "🏆 Best"
            footer_content = Group(
                Text(f"{mode_indicator}: {display_config.remarks[:55]}", style="bold cyan"),
                Text(f"{display_config.protocol.upper()} → {display_config.address}:{display_config.port}", style="cyan"),
                Text(""),
                Text(display_config.raw_link, style="dim white", overflow="fold"),
            )
            footer_style = "cyan" if is_manual else "green"
        else:
            footer_content = Text("No verified stable configs found yet...", style="red")

        footer_title = "👆 Selected Config" if is_manual else "🏆 Sticky Best Config (Verified)"
        footer_panel = Panel(
            Align.center(footer_content),
            title=footer_title,
            subtitle=nav_hint if display_config or is_manual else None,
            border_style=footer_style
        )

        table = Table(expand=True, border_style="dim white")
        table.add_column("", justify="center", width=3)  # Selection indicator
        table.add_column("Rank", justify="right", width=6)
        table.add_column("Score", justify="right", width=12)
        table.add_column("Loss %", justify="right", width=10)
        table.add_column("Latency", justify="right", width=12)
        table.add_column("Jitter", justify="right", width=12)
        table.add_column("Protocol", justify="left", width=10)
        table.add_column("Remarks", justify="left", ratio=1, no_wrap=True, overflow="ellipsis") 

        count = 0
        # RESPONSIVE: Calculate rows based on terminal height
        term_height = console.height if console.height else 40
        
        # Reserve space for: header(3) + footer(8) + QR(~20 if shown)
        # Small screens (<35 rows): hide QR, show more table
        # Large screens (>=35 rows): show QR, fewer table rows
        show_qr = term_height >= 35 and display_config
        
        if show_qr:
            # Leave room for QR (~20 lines)
            max_rows = max(5, term_height - 33)
        else:
            # No QR, use more space for table
            max_rows = max(5, term_height - 15)
        
        max_rows = min(max_rows, 15)  # Cap at 15 rows
        
        # Cache snapshots for navigation
        nonlocal cached_snapshots
        cached_snapshots = snapshots
        
        for i, (score, loss, lat, jitter, config, cnt) in enumerate(snapshots): 
             if i >= max_rows: break
             
             # Selection indicator
             selector = "▶" if is_manual and i == sel_idx else ""
             
             row_style = ""
             if is_manual and i == sel_idx:
                row_style = "bold reverse cyan"
             elif config == rec_config:
                row_style = "bold green"
             elif loss >= 100:
                row_style = "dim red"
            
             loss_str = f"{loss:.1f}%"
             if loss == 100 and score > 9000000:
                 loss_str = "DEAD" 
            
             table.add_row(
                selector,
                f"#{i+1}", 
                f"{score:.1f}", 
                loss_str, 
                f"{lat:.0f}ms", 
                f"{jitter:.0f}ms", 
                config.protocol, 
                config.remarks,
                style=row_style
             )
             count += 1
        
        # Create QR panel only if screen is big enough
        qr_panel = None
        if show_qr:
            # Calculate available width for QR (approx 1/3 of screen)
            available_width = console.width // 3 if console.width else 40
            qr_text, qr_width, status = generate_qr_ascii(display_config.raw_link, available_width)
            
            if status == "success":
                # QR fits - create panel with fixed styling
                qr_panel = Panel(
                    Align.center(Text(qr_text, style="black on white", no_wrap=True, overflow="ignore")),
                    title=f"📱 Scan",
                    border_style="yellow",
                    padding=(0, 1)
                )
            else:
                # QR Overflow or Error
                qr_panel = Panel(
                    Align.center(
                        Group(
                            Text("⚠️  Terminal too narrow", style="bold red"),
                            Text("Resize window to view QR", style="dim white"),
                            Text("or copy link below", style="dim white")
                        )
                    ),
                    title="📱",
                    border_style="dim yellow",
                    padding=(1, 1)
                )
        
        # Layout: header, table, then footer+QR side by side
        if qr_panel:
            from rich.table import Table as RichTable
            # Create side-by-side layout: footer on left, QR on right
            bottom_layout = RichTable.grid(expand=True)
            bottom_layout.add_column("footer", ratio=2)
            bottom_layout.add_column("qr", ratio=1)
            bottom_layout.add_row(footer_panel, Align.center(qr_panel, vertical="middle"))
            return Group(header_panel, table, bottom_layout)
        else:
            return Group(header_panel, table, footer_panel)

    # Initialize keyboard handler
    kb = KeyboardHandler()
    kb_enabled = kb.enable_raw()
    
    try:
        # Initial Render
        with Live(generate_dashboard(None, "", selected_config, manual_mode), refresh_per_second=4, screen=True, auto_refresh=True) as live:
            while running:
                elapsed = time.time() - monitor_start_time
                
                # --- Keyboard Input Handling ---
                if kb_enabled:
                    key = kb.get_key(timeout=0.05)
                    if key == 'up':
                        manual_mode = True
                        # Move up in cached snapshots
                        if cached_snapshots:
                            # Find current index of selected config
                            curr_idx = 0
                            if selected_config:
                                for i, s in enumerate(cached_snapshots):
                                    if s[4].raw_link == selected_config.raw_link:
                                        curr_idx = i
                                        break
                            new_idx = max(0, curr_idx - 1)
                            selected_config = cached_snapshots[new_idx][4]
                    elif key == 'down':
                        manual_mode = True
                        # Move down in cached snapshots
                        if cached_snapshots:
                            curr_idx = 0
                            if selected_config:
                                for i, s in enumerate(cached_snapshots):
                                    if s[4].raw_link == selected_config.raw_link:
                                        curr_idx = i
                                        break
                            max_idx = len(cached_snapshots) - 1
                            new_idx = min(max_idx, curr_idx + 1)
                            selected_config = cached_snapshots[new_idx][4]
                    elif key == 'esc':
                        manual_mode = False
                        selected_config = None
                    elif key == 'r' or key == 'R':
                        # Trigger full rescan (exit to restart Phase 1)
                        trigger_rescan = True
                        running = False
                        break
                    elif key == 'quit':
                        running = False
                        break
                
                # --- Auto-select Best Config (based on real delay rankings) ---
                current_snapshots = []
                for stat in stats_map.values():
                    current_snapshots.append((*stat.get_score(), stat.config))
                current_snapshots.sort(key=lambda x: x[0])
                
                # Auto-select best: top config with low packet loss
                # Tuple: (score, loss, lat, jitter, count, config) - config is at index 5
                if not manual_mode and current_snapshots:
                    best_candidates = [s for s in current_snapshots if s[1] < 50]  # < 50% loss
                    if best_candidates:
                        recommended_config = best_candidates[0][5]  # index 5 is config
                    elif current_snapshots:
                        recommended_config = current_snapshots[0][5]  # Fallback to top

                # Update UI
                live.update(generate_dashboard(recommended_config, verification_status, selected_config, manual_mode))
                await asyncio.sleep(0.2)  # Refresh rate
                
    except asyncio.CancelledError:
        pass
    except KeyboardInterrupt:
        pass
    finally:
        running = False
        kb.restore()  # Restore terminal
        for t in pinger_tasks:
            t.cancel()
        await asyncio.gather(*pinger_tasks, return_exceptions=True)

