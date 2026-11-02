
import win32com.client
import time
import os
from collections import Counter
from datetime import datetime
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

console = Console()

IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".heic", ".heif",
    ".tif", ".tiff", ".bmp", ".gif", ".webp",
    ".dng", ".raw", ".cr2", ".cr3", ".nef",
    ".arw", ".orf", ".rw2"
}

VIDEO_EXTENSIONS = {
    ".mov", ".mp4", ".m4v", ".avi", ".3gp"
}

current_path = "Waiting..."
last_update = time.time()


def find_iphone():
    shell = win32com.client.Dispatch("Shell.Application")
    computer = shell.NameSpace(17)

    for device in computer.Items():
        if "iphone" in device.Name.lower():
            for folder in device.GetFolder.Items():
                if folder.Name.lower() == "internal storage":
                    return folder.GetFolder

    return None


def create_display(stats, status):

    table = Table(expand=True)
    table.add_column("Category", style="cyan")
    table.add_column("Count", justify="right")

    table.add_row("Images", f"[bold green]{stats['images']:,}[/]")
    table.add_row("Videos", f"[bold yellow]{stats['videos']:,}[/]")
    table.add_row("All files", f"{stats['files']:,}")
    table.add_row("Folders scanned", f"{stats['folders']:,}")
    table.add_row("Errors", str(stats["errors"]))

    extensions = Table(expand=True)
    extensions.add_column("Extension")
    extensions.add_column("Count", justify="right")

    for ext, count in sorted(stats["extensions"].items()):
        extensions.add_row(ext.upper(), f"{count:,}")

    path_display = Text(current_path, style="bold white")
    path_display.no_wrap = False
    path_display.overflow = "fold"

    return Panel(
        Group(
            Panel(path_display, title="Currently scanning", border_style="yellow"),
            table,
            extensions
        ),
        title="[bold cyan]IPHONE IMAGE SCANNER[/bold cyan]",
        subtitle=f"{status} | {datetime.now():%H:%M:%S}"
    )


def scan_recursive(root, stats, path, live):

    global current_path, last_update

    try:
        current_path = path
        stats["folders"] += 1

        if time.time() - last_update >= 0.2:
            live.update(create_display(stats, "Scanning iPhone"))
            last_update = time.time()

        items = root.Items()

        for item in items:

            try:
                item_path = f"{path}\\{item.Name}"

                if item.IsFolder:
                    scan_recursive(
                        item.GetFolder,
                        stats,
                        item_path,
                        live
                    )

                else:
                    stats["files"] += 1

                    ext = os.path.splitext(item.Name)[1].lower()

                    if ext in IMAGE_EXTENSIONS:
                        stats["images"] += 1
                        stats["extensions"][ext] += 1

                    elif ext in VIDEO_EXTENSIONS:
                        stats["videos"] += 1

            except Exception:
                stats["errors"] += 1

    except Exception:
        stats["errors"] += 1


def main():

    global current_path, last_update

    stats = {
        "images": 0,
        "videos": 0,
        "files": 0,
        "folders": 0,
        "errors": 0,
        "extensions": Counter()
    }

    with Live(
        create_display(stats, "Searching for iPhone"),
        console=console,
        refresh_per_second=10
    ) as live:

        iphone = None

        while iphone is None:
            iphone = find_iphone()

            if iphone is None:
                current_path = "Waiting for iPhone connection"
                live.update(create_display(stats, "Disconnected"))
                time.sleep(2)

        current_path = "Apple iPhone\\Internal Storage"
        last_update = time.time()

        live.update(create_display(stats, "Scanning iPhone"))

        # ONE COMPLETE RECURSIVE SCAN
        scan_recursive(
            iphone,
            stats,
            "Apple iPhone\\Internal Storage",
            live
        )

        current_path = "Scan complete"
        live.update(create_display(stats, "SCAN COMPLETE"))

        # Keep final results visible until Ctrl+C
        while True:
            time.sleep(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[yellow]Scanner stopped.[/]")