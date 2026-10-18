#!/usr/bin/env python3
"""
Memoria Multi-Agente: Decision Log + Locks + Handoff Protocol.
Garantiza que la siguiente sesión —u otro agente— retome el estado real.
"""
from __future__ import annotations
import json, os, subprocess, sys
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any


MEMORY_DIR = Path("MEMORY")
DECISION_LOG = MEMORY_DIR / "decision-log.md"
HYPOTHESES_LOG = MEMORY_DIR / "hypotheses.md"
LOCKS_FILE = MEMORY_DIR / "locks.md"
STATE_JSON = Path("STATE.json")


@dataclass
class LockEntry:
    agent: str
    zone: str
    files: List[str]
    since: str
    until: Optional[str] = None
    purpose: str = ""


@dataclass
class Hypothesis:
    """Idea en cuarentena - solo sale con oos_pass=true"""
    id: str
    description: str
    blocker: str
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    status: str = "quarantined"  # quarantined | testing | promoted | rejected


class MemoryManager:
    """Gestor de memoria compartida entre agentes."""
    
    def __init__(self, agent_id: str = "agent"):
        self.agent_id = agent_id
        MEMORY_DIR.mkdir(exist_ok=True)
        self._init_files()
    
    def _init_files(self):
        if not DECISION_LOG.exists():
            DECISION_LOG.write_text(
                "# Decision Log\n\nAppend-only ADRs. Una entrada por tarea completada.\n\n",
                encoding="utf-8"
            )
        if not HYPOTHESES_LOG.exists():
            HYPOTHESES_LOG.write_text(
                "# Hypotheses (Cuarentena)\n\nIdeas no validadas. Solo salen con `oos_pass=true`.\n\n",
                encoding="utf-8"
            )
        if not LOCKS_FILE.exists():
            LOCKS_FILE.write_text(
                "# Locks de Coordinación\n\n| agente | zona | archivos | desde | hasta | propósito |\n"
                "|--------|------|----------|-------|-------|-----------|\n",
                encoding="utf-8"
            )
    
    # ========== DECISION LOG ==========
    
    def log_decision(self, 
                    task: str,
                    files_changed: List[str],
                    decision: str,
                    evidence: str,
                    metrics_changed: Dict[str, str] = None,
                    rejected: str = "",
                    pending: str = "",
                    status: str = "OK",
                    verify_exit_code: int = 0,
                    tests_passed: int = 0,
                    tests_failed: int = 0) -> None:
        """
        Escribe entrada append-only en decision-log.md.
        Obligatorio al terminar CUALQUIER tarea.
        """
        if len(files_changed) > 3:
            print(f"[WARN] {len(files_changed)} archivos (>3 límite AGENTS.md §8)")
        
        git_sha_before = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], 
            capture_output=True, text=True
        ).stdout.strip()
        
        entry = f"""## {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} · {task}
- **agente**: {self.agent_id}
- **commit**: {git_sha_before}
- **archivos** (≤3): {', '.join(files_changed) if files_changed else 'ninguno'}
- **decisión**: {decision}
- **evidencia**: {evidence}
- **cifras tocadas**: {json.dumps(metrics_changed) if metrics_changed else 'ninguna'}
- **rechazado**: {rejected or 'ninguno'}
- **pendiente**: {pending or 'ninguno'}
- **status**: {status}
- **verify**: exit {verify_exit_code} · {tests_passed} passed · {tests_failed} failed

"""
        with DECISION_LOG.open("a", encoding="utf-8") as f:
            f.write(entry)
        
        print(f"[memory] Logged decision: {task} (status={status})")
    
    def get_recent_decisions(self, n: int = 5) -> List[str]:
        """Lee últimas N entradas del decision log."""
        content = DECISION_LOG.read_text(encoding="utf-8")
        entries = content.split("## ")[1:]  # Skip header
        return ["## " + e for e in entries[-n:]]
    
    # ========== HYPOTHESES ==========
    
    def add_hypothesis(self, hypothesis_id: str, description: str, blocker: str) -> None:
        """Añade hipótesis a cuarentena."""
        h = Hypothesis(id=hypothesis_id, description=description, blocker=blocker)
        
        entry = f"""- [ ] **{h.id}**: {h.description}
      - Bloqueante: {h.blocker}
      - Creado: {h.created_at}
      - Status: {h.status}

"""
        with HYPOTHESES_LOG.open("a", encoding="utf-8") as f:
            f.write(entry)
        print(f"[memory] Hypothesis quarantined: {hypothesis_id}")
    
    def promote_hypothesis(self, hypothesis_id: str, oos_evidence: str) -> None:
        """Promueve hipótesis (requiere oos_pass=true)."""
        content = HYPOTHESES_LOG.read_text(encoding="utf-8")
        # Simple replacement - en producción usar parser más robusto
        content = content.replace(f"- [ ] **{hypothesis_id}**:", f"- [x] **{hypothesis_id}** (PROMOTED):")
        content += f"\n  - Promovido: {datetime.now(timezone.utc).isoformat()}\n  - Evidencia OOS: {oos_evidence}\n\n"
        HYPOTHESES_LOG.write_text(content, encoding="utf-8")
        print(f"[memory] Hypothesis promoted: {hypothesis_id}")
    
    # ========== LOCKS ==========
    
    def acquire_lock(self, zone: str, files: List[str], purpose: str, 
                     until: Optional[str] = None) -> bool:
        """
        Reclama lock ANTES de editar. Falla si ya está reclamado.
        """
        locks = self._read_locks()
        
        # Verificar conflictos
        for lock in locks:
            for f in files:
                if f in lock.files and lock.agent != self.agent_id:
                    print(f"[memory] CONFLICT: {lock.agent} holds {f} in {lock.zone}")
                    return False
        
        # Añadir nuestro lock
        new_lock = LockEntry(
            agent=self.agent_id,
            zone=zone,
            files=files,
            since=datetime.now(timezone.utc).isoformat(),
            until=until,
            purpose=purpose
        )
        locks.append(new_lock)
        self._write_locks(locks)
        print(f"[memory] Lock acquired: {zone} - {files}")
        return True
    
    def release_locks(self, files: List[str] = None) -> None:
        """Libera locks del agente actual."""
        locks = self._read_locks()
        if files:
            # Liberar solo archivos específicos
            for lock in locks:
                if lock.agent == self.agent_id:
                    lock.files = [f for f in lock.files if f not in files]
        else:
            # Liberar todos los locks del agente
            locks = [l for l in locks if l.agent != self.agent_id]
        self._write_locks(locks)
        print(f"[memory] Locks released")
    
    def _read_locks(self) -> List[LockEntry]:
        content = LOCKS_FILE.read_text(encoding="utf-8")
        locks = []
        for line in content.split("\n"):
            if line.startswith("|") and not line.startswith("| agente") and not line.startswith("|---"):
                parts = [p.strip() for p in line.split("|")[1:-1]]
                if len(parts) >= 6:
                    locks.append(LockEntry(
                        agent=parts[0], zone=parts[1], 
                        files=[f.strip() for f in parts[2].split(",")],
                        since=parts[3], until=parts[4] if parts[4] else None,
                        purpose=parts[5]
                    ))
        return locks
    
    def _write_locks(self, locks: List[LockEntry]) -> None:
        lines = ["# Locks de Coordinación\n", 
                 "| agente | zona | archivos | desde | hasta | propósito |",
                 "|--------|------|----------|-------|-------|-----------|"]
        for l in locks:
            lines.append(f"| {l.agent} | {l.zone} | {', '.join(l.files)} | {l.since} | {l.until or ''} | {l.purpose} |")
        LOCKS_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    
    def get_active_locks(self) -> List[LockEntry]:
        return self._read_locks()
    
    # ========== HANDOFF ==========
    
    def handoff(self, next_action: str, no_attempt: List[str] = None) -> str:
        """
        Genera bloque de handoff estándar para siguiente sesión/agente.
        """
        # Verificar verify.ps1
        verify_result = subprocess.run(
            ["pwsh", "scripts/verify.ps1"], 
            capture_output=True, text=True, timeout=300
        )
        
        # Regenerar STATE.json
        state_result = subprocess.run(
            [sys.executable, "scripts/build_state.py"], 
            capture_output=True, text=True
        )
        
        # Git diff
        diff_result = subprocess.run(
            ["git", "diff", "--stat"], 
            capture_output=True, text=True
        )
        
        handoff = f"""── HANDOFF ────────────────────────────────────
estado           : {'OK' if verify_result.returncode == 0 else 'BROKEN'}
verify           : exit {verify_result.returncode} · salida ver arriba
STATE.json       : {'regenerado' if state_result.returncode == 0 else 'FALLO'} · {state_result.stdout.strip() if state_result.returncode == 0 else state_result.stderr.strip()}
archivos tocados : {diff_result.stdout.strip() or 'ninguno'}
cifras cambiadas : ver decision-log
edge demostrado  : NO (oos_pass=false)
siguiente paso obvio: {next_action}
NO intentar sin más datos: {', '.join(no_attempt) if no_attempt else 'ninguno'}
───────────────────────────────────────────────"""
        
        print(handoff)
        return handoff


def verify_session_complete() -> bool:
    """
    Checklist bloqueante antes de cerrar sesión.
    Returns True si todo OK.
    """
    checks = [
        ("verify.ps1 exit 0", lambda: subprocess.run(["pwsh", "scripts/verify.ps1"]).returncode == 0),
        ("build_state.py OK", lambda: subprocess.run([sys.executable, "scripts/build_state.py"]).returncode == 0),
        ("agent_gate.py 0 bloqueos", lambda: subprocess.run([sys.executable, "scripts/agent_gate.py"]).returncode == 0),
        ("≤3 archivos", lambda: len(subprocess.run(["git", "diff", "--name-only"], capture_output=True, text=True).stdout.strip().split("\n")) <= 3),
    ]
    
    all_ok = True
    for name, check in checks:
        try:
            ok = check()
            print(f"  {'✓' if ok else '✗'} {name}")
            if not ok:
                all_ok = False
        except Exception as e:
            print(f"  ✗ {name}: {e}")
            all_ok = False
    
    return all_ok


if __name__ == "__main__":
    # Demo
    mm = MemoryManager("demo_agent")
    
    # Lock
    mm.acquire_lock("cockpit", ["cockpit/server.py", "cockpit/cycle.py"], "extender cockpit", "2026-10-16T12:00")
    
    # Log decision
    mm.log_decision(
        task="extender cockpit con /api/state",
        files_changed=["cockpit/server.py"],
        decision="Añadido endpoint /api/state que expone STATE.json sin romper compatibilidad",
        evidence="pwsh scripts/verify.ps1 exit 0; git diff cockpit/server.py",
        metrics_changed={},
        pending="testear con agente paralelo en detector/costs.py",
        status="OK",
        verify_exit_code=0, tests_passed=398, tests_failed=0
    )
    
    # Hypothesis
    mm.add_hypothesis(
        "Rama-C-high-firstMC",
        "firstMC alto + firstLiq alto → mejor WR (36.84%) pero EV −16.77%",
        "resolución 72s > ventana decisión 60s; esperar sampler 12s + 3 semanas datos"
    )
    
    # Handoff
    mm.handoff(
        next_action="testear causal guard en master_signal.py",
        no_attempt=["cambiar umbrales sin oos_pass", "promover reglas con N<30"]
    )
    
    # Liberar
    mm.release_locks()