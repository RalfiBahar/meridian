"""Experiment runner: discover, execute, and persist named experiments.

Experiments live under `experiments/<name>/run.py` at the repository root.
Each run script is executed in its own subprocess; its stdout is captured and
stored in the `experiments` table.  A `manifest.yaml` alongside `run.py` may
declare parameter defaults (superseded by `--params` on the CLI).

The runner:
1. Resolves the experiment root directory.
2. Reads `manifest.yaml` (if present) for defaults.
3. Merges caller-supplied params.
4. Computes SHA-256 of `run.py` for provenance.
5. Inserts a `pending` row into `experiments`.
6. Spawns `python run.py` with params as JSON on stdin.
7. Updates the row to `completed` or `failed` with captured stdout/metrics.

Experiments may print a final JSON object to stdout on the last line;
the runner picks that up as the `metrics` payload.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class ExperimentResult:
    """Outcome of a single experiment run."""

    id: UUID
    name: str
    code_sha: str
    params: dict[str, Any]
    metrics: dict[str, Any]
    stdout: str
    status: str  # "completed" or "failed"
    started_at: datetime
    completed_at: datetime
    returncode: int

    @property
    def success(self) -> bool:
        return self.status == "completed"

    def summary(self) -> str:
        lines = [
            f"Experiment:  {self.name}",
            f"ID:          {self.id}",
            f"Status:      {self.status}",
            f"Code SHA:    {self.code_sha[:12]}",
            f"Duration:    {(self.completed_at - self.started_at).total_seconds():.1f}s",
        ]
        if self.metrics:
            lines.append("Metrics:")
            for k, v in self.metrics.items():
                lines.append(f"  {k}: {v}")
        if self.stdout.strip():
            lines.append("\nOutput:")
            lines.extend(f"  {ln}" for ln in self.stdout.splitlines()[-20:])
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _experiments_root() -> Path:
    """Return the `experiments/` directory next to this repo's root."""
    here = Path(__file__).resolve()
    # Walk up to find the repo root (contains pyproject.toml).
    for parent in here.parents:
        if (parent / "pyproject.toml").exists():
            return parent / "experiments"
    return Path.cwd() / "experiments"


def discover_experiments() -> list[str]:
    """Return names of all experiments that have a `run.py` script."""
    root = _experiments_root()
    if not root.is_dir():
        return []
    return sorted(d.name for d in root.iterdir() if d.is_dir() and (d / "run.py").exists())


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _load_manifest(exp_dir: Path) -> dict[str, Any]:
    manifest_path = exp_dir / "manifest.yaml"
    if not manifest_path.exists():
        return {}
    try:
        import yaml  # optional dependency

        with manifest_path.open() as f:
            return dict(yaml.safe_load(f) or {})
    except ModuleNotFoundError:
        return {}
    except Exception:
        return {}


def _extract_metrics(stdout: str) -> dict[str, Any]:
    """Pick the last line if it parses as JSON; otherwise return {}."""
    lines = stdout.strip().splitlines()
    if not lines:
        return {}
    last = lines[-1].strip()
    if last.startswith("{") and last.endswith("}"):
        try:
            return dict(json.loads(last))
        except json.JSONDecodeError:
            pass
    return {}


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


async def run_experiment(
    pool: Any,
    name: str,
    *,
    params: dict[str, Any] | None = None,
    data_window: dict[str, Any] | None = None,
    notes: str | None = None,
    run_timeout: float = 300.0,
) -> ExperimentResult:
    """Run the named experiment and persist the result.

    Raises `FileNotFoundError` if the experiment script doesn't exist.
    Raises `TimeoutError` if the script exceeds `run_timeout` seconds.
    """
    root = _experiments_root()
    exp_dir = root / name
    run_script = exp_dir / "run.py"

    if not run_script.exists():
        raise FileNotFoundError(f"Experiment script not found: {run_script}")

    manifest = _load_manifest(exp_dir)
    merged_params: dict[str, Any] = {**manifest.get("params", {}), **(params or {})}
    merged_window: dict[str, Any] = data_window or {}
    code_sha = _sha256_file(run_script)

    # Insert pending row.
    exp_id = await _insert_pending(
        pool,
        name=name,
        code_sha=code_sha,
        params=merged_params,
        data_window=merged_window,
        notes=notes,
    )
    started_at = datetime.now(tz=UTC)

    # Execute subprocess.
    try:
        proc = await asyncio.wait_for(
            asyncio.create_subprocess_exec(
                sys.executable,
                str(run_script),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(exp_dir),
                env={**os.environ, "EXPERIMENT_PARAMS": json.dumps(merged_params)},
            ),
            timeout=run_timeout,
        )
        stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=run_timeout)
        stdout = stdout_bytes.decode(errors="replace")
        returncode = proc.returncode or 0
        status = "completed" if returncode == 0 else "failed"
    except TimeoutError:
        stdout = "(timed out)"
        returncode = -1
        status = "failed"

    completed_at = datetime.now(tz=UTC)
    metrics = _extract_metrics(stdout) if status == "completed" else {}

    await _update_row(
        pool, exp_id, status=status, stdout=stdout, metrics=metrics, completed_at=completed_at
    )

    return ExperimentResult(
        id=exp_id,
        name=name,
        code_sha=code_sha,
        params=merged_params,
        metrics=metrics,
        stdout=stdout,
        status=status,
        started_at=started_at,
        completed_at=completed_at,
        returncode=returncode,
    )


async def list_experiments(
    pool: Any, *, name: str | None = None, limit: int = 20
) -> list[dict[str, Any]]:
    """Return recent experiment rows from the DB."""
    async with pool.acquire() as conn:
        if name:
            rows = await conn.fetch(
                """
                SELECT id, name, code_sha, params, metrics, status, created_at, completed_at
                FROM experiments
                WHERE name = $1
                ORDER BY created_at DESC
                LIMIT $2
                """,
                name,
                limit,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT id, name, code_sha, params, metrics, status, created_at, completed_at
                FROM experiments
                ORDER BY created_at DESC
                LIMIT $1
                """,
                limit,
            )
    out: list[dict[str, Any]] = []
    for r in rows:
        row = dict(r)
        for key in ("metrics", "params", "data_window"):
            val = row.get(key)
            if isinstance(val, str):
                try:
                    row[key] = json.loads(val)
                except json.JSONDecodeError:
                    row[key] = {}
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


async def _insert_pending(
    pool: Any,
    *,
    name: str,
    code_sha: str,
    params: dict[str, Any],
    data_window: dict[str, Any],
    notes: str | None,
) -> UUID:
    now = datetime.now(tz=UTC)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO experiments (name, code_sha, params, data_window, notes, status, started_at)
            VALUES ($1, $2, $3::jsonb, $4::jsonb, $5, 'running', $6)
            RETURNING id
            """,
            name,
            code_sha,
            json.dumps(params),
            json.dumps(data_window),
            notes,
            now,
        )
    return UUID(str(row["id"]))


async def _update_row(
    pool: Any,
    exp_id: UUID,
    *,
    status: str,
    stdout: str,
    metrics: dict[str, Any],
    completed_at: datetime,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE experiments
            SET status = $2, stdout = $3, metrics = $4::jsonb, completed_at = $5
            WHERE id = $1
            """,
            exp_id,
            status,
            stdout,
            json.dumps(metrics),
            completed_at,
        )
