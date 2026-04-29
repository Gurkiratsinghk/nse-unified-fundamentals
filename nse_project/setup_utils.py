"""
Dependency setup utilities for lazy-loading heavy scraper packages.
"""

import sys
import subprocess
from loguru import logger
from rich.prompt import Confirm
from rich.console import Console

console = Console()

def ensure_dependencies(features: list[str]) -> bool:
    """
    Ensure specific heavy dependencies are installed before running features.
    
    Args:
        features: List of feature names, e.g., ["scraper"]
    """
    missing = []
    
    if "scraper" in features:
        try:
            import seleniumbase
            import curl_cffi
            import pyvirtualdisplay
        except ImportError as e:
            logger.warning(f"Missing scraper dependency: {e.name}")
            missing.append(e.name)
            
    if not missing:
        return True
        
    console.print(f"[bold yellow]The following heavy dependencies are required: {', '.join(missing)}[/]")
    
    # Prompt the user
    if not Confirm.ask("Would you like to install them now via pip?", default=True):
        console.print("[red]Dependencies missing. Cannot proceed with this feature.[/]")
        return False
        
    console.print("[cyan]Installing dependencies... This may take a moment.[/]")
    
    try:
        # Install from requirements to ensure correct versions
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"],
            capture_output=True,
            text=True
        )
        
        if result.returncode == 0:
            console.print("[bold green]Pip install successful.[/]")
            return True
        else:
            console.print("[bold red]Pip install failed.[/]")
            logger.error(f"Pip error output: {result.stderr}")
            return False
            
    except Exception as e:
        console.print(f"[bold red]Failed to run pip: {e}[/]")
        return False
