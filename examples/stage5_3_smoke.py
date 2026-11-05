"""Opt-in three-question local smoke test; never installs, pulls or starts Ollama."""
import argparse
from pathlib import Path

from oem_agent.ollama_local import OllamaLimits
from oem_agent.stage5_agent import run_local_agent, write_agent_report
from oem_agent.stage5_evidence import EvidenceSnapshot

QUESTIONS = (
    "¿Cuántas piezas seleccionadas hay y cuál es el estado general de la evidencia?",
    "Para la pieza 10, informá precios observados y filtrados, anuncios y unidades. ¿La muestra es suficiente?",
    "¿Qué estado de revisión y costo DEMO tiene la pieza 10? No registres ninguna decisión.",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--dossier", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--questions", type=int, choices=(1, 2, 3), default=3)
    args = parser.parse_args()
    snapshot = EvidenceSnapshot.load(args.evidence, args.dossier)
    args.out.mkdir(parents=True, exist_ok=False)
    success = True
    for index, question in enumerate(QUESTIONS[:args.questions], 1):
        try:
            report = run_local_agent(snapshot, question, limits=OllamaLimits())
        except (KeyboardInterrupt, SystemExit) as interrupted:
            if hasattr(interrupted, "agent_report"):
                write_agent_report(args.out / f"question-{index}.json", interrupted.agent_report)
            raise
        write_agent_report(args.out / f"question-{index}.json", report)
        print(f"Question {index}: {report['status']} | model rounds {report['model_rounds']} | tools {report['native_tool_attempts']}")
        success = success and report["complete"]
        if report["model_info"] is None:
            break  # Do not repeat unavailable/unsafe setup or trigger any provisioning.
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
