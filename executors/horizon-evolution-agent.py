#!/usr/bin/env python3
"""Dedicated Horizon executor: horizon-evolution-agent."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from maestro_exec.runner import main
if __name__=="__main__":
    raise SystemExit(main(["--agent","horizon-evolution-agent",*sys.argv[1:]]))
