"""
NSE Fundamental Analysis — Main entry point.

Usage:
    python main.py              — Run NSE100 scraper (default)
    python main.py full         — Run full NSE scraper (all listed equities)
    python main.py cli          — Launch interactive CLI viewer
"""

import sys

from pipeline.ingest import run_full_ingest, run_full_nse_ingest

if __name__ == "__main__":
    # Change default to CLI
    mode = sys.argv[1] if len(sys.argv) > 1 else "cli"

    if mode == "cli":
        from client.cli import main as cli_main
        cli_main()
    elif mode == "scrape":
        if len(sys.argv) < 3:
            print("Usage: python main.py scrape <SYMBOL>")
            sys.exit(1)
        symbol = sys.argv[2].upper()
        from pipeline.ingest import run_single_scrape
        run_single_scrape(symbol)
    elif mode == "status":
        from client.cli import show_status
        show_status()
    elif mode == "compare":
        if len(sys.argv) < 4:
            print("Usage: python main.py compare <SYMBOL1> <SYMBOL2>")
            sys.exit(1)
        s1, s2 = sys.argv[2].upper(), sys.argv[3].upper()
        from client.cli import compare_companies_cli
        compare_companies_cli(s1, s2)
    elif mode == "export":
        # python main.py export <FORMAT> --symbol <SYMBOL>
        if len(sys.argv) < 3:
            print("Usage: python main.py export <CSV|EXCEL> [--symbol <SYMBOL>]")
            sys.exit(1)
        fmt = sys.argv[2].upper()
        sym = sys.argv[4].upper() if "--symbol" in sys.argv else None
        from client.cli import export_data_cli
        export_data_cli(fmt, sym)
    elif mode == "full":
        run_full_nse_ingest()
    elif mode == "nse100":
        run_full_ingest()
    else:
        print(f"Unknown mode: {mode}")
        print("Usage: python main.py [cli|full|nse100|status|scrape <SYMBOL>|compare <S1> <S2>|export <FMT>]")
