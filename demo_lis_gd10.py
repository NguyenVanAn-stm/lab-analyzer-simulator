"""Chay query + run live (LIS GD10) va in log tieng Viet de doc cho demo."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from lab_analyzer_simulator.config import load_settings
from lab_analyzer_simulator.simulation.engine import SimulationEngine


def _line(title: str, value: str) -> None:
    print(f"  {title:<22} {value}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Demo log ro rang: may gia -> Connector 5101 -> LIS")
    parser.add_argument("barcode", help="Ma ong STM...")
    parser.add_argument("--profile", default="chemistry-normal")
    parser.add_argument("--query-only", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent
    settings = load_settings(root / "config.lis-gd10.json")
    engine = SimulationEngine(root, settings)

    print("")
    print("=" * 60)
    print("  MAY XN GIA (SIM-01)  ->  Connector :5101  ->  LIS")
    print("=" * 60)
    print("")
    print("[1] HOI LENH (ASTM query / worklist-claims)")
    print(f"    Barcode quet: {args.barcode}")

    try:
        worklist = engine.query(args.barcode, "normal")
    except Exception as error:
        print("")
        print("  KET QUA: LOI - LIS khong tra lenh cho ong nay.")
        print(f"  Chi tiet: {error}")
        print("")
        print("  Kiem tra: da Tiep nhan mau tren LIS? Barcode dung? Ong chua co ket qua?")
        print("  Connector: docker logs lis-gd10-connector-1 --tail 15")
        return 1

    wl = engine._worklist_dict(worklist)
    print("")
    print("  KET QUA: CO LENH")
    _line("Ong mau:", wl["sample_identifier"] or args.barcode)
    _line("Benh nhan:", wl.get("patient_name") or "(LIS khong gui ten)")
    _line("Ma BN (P):", wl.get("patient_id") or "-")
    tests = wl.get("ordered_tests") or []
    if tests:
        codes = ", ".join(f"{t['code']}" + (f" ({t['name']})" if t.get("name") else "") for t in tests)
        _line("Xet nghiem:", codes)
    else:
        _line("Xet nghiem:", "(trong)")

    if args.query_only:
        print("")
        print("[2] Bo qua gui ket qua (--query-only). Tren UI: Run selected profile.")
        return 0

    print("")
    print("[2] GUI KET QUA (ASTM result -> LIS)")
    print(f"    Profile gia: {args.profile}")

    try:
        run = engine.run(args.profile, args.barcode, "normal")
    except Exception as error:
        print(f"  LOI gui ket qua: {error}")
        return 1

    # Tim dong R gan nhat trong messages
    result_value = "?"
    for msg in reversed(engine.messages):
        if msg.get("kind") == "result" and "^^^" in msg.get("message", ""):
            for part in msg["message"].split("\r"):
                if part.startswith("R|"):
                    fields = part.split("|")
                    if len(fields) > 3:
                        result_value = fields[3]
                    break
            break

    print("")
    print("  KET QUA: DA GUI LIS")
    _line("Trang thai may:", str(run.state))
    _line("So XN gui:", str(run.result_count))
    _line("Gia tri (demo):", result_value)
    print("")
    print("[3] TREN LIS")
    print("    - Mo chi dinh: xem LDH so bo")
    print("    - lis.bacsi -> Kiem duyet ket qua -> Duyet")
    print("    - Canh bao 'bon mat' neu may khong gui performedBy (binh thuong voi simulator)")
    print("")
    print("  Log Connector (tieng Viet):")
    print("    docker logs lis-gd10-connector-1 2>&1 | Select-String SIM-01 | Select-Object -Last 8")
    print("")
    return 0


if __name__ == "__main__":
    sys.exit(main())
