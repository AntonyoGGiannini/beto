"""Relatório de cobertura da coleta: quais casas foram coletadas, quais falharam."""

from __future__ import annotations

from beto.scrapers.base import ScrapeResult


def _status(result: ScrapeResult) -> str:
    if not result.ok:
        return "FALHOU"
    return "OK" if result.quotes else "SEM DADOS"


def format_coverage_report(results: list[ScrapeResult], *, competition_label: str) -> str:
    headers = ("casa", "status", "eventos", "quotes", "tempo", "detalhe")
    rows: list[tuple[str, ...]] = []
    for r in sorted(results, key=lambda x: (x.ok, x.house), reverse=True):
        detail = r.error or r.note or "-"
        rows.append(
            (
                r.house,
                _status(r),
                str(r.n_events),
                str(len(r.quotes)),
                f"{r.duration_s:.1f}s",
                detail,
            )
        )

    widths = [
        min(max(len(headers[i]), *(len(row[i]) for row in rows)), 72) if rows else len(headers[i])
        for i in range(len(headers))
    ]

    def fmt(row: tuple[str, ...]) -> str:
        return "  ".join(cell[: widths[i]].ljust(widths[i]) for i, cell in enumerate(row))

    collected = [r.house for r in results if r.ok and r.quotes]
    lines = [
        f"RELATÓRIO DE COBERTURA — {competition_label}",
        "mercados: 1X2 · O/U gols · O/U escanteios",
        "",
        fmt(headers),
        fmt(tuple("-" * w for w in widths)),
        *(fmt(row) for row in rows),
        "",
        f"Coletadas: {len(collected)}/{len(results)}"
        + (f" — {', '.join(sorted(collected))}" if collected else ""),
    ]
    if any(not r.ok or not r.quotes for r in results):
        lines.append(
            "Dica: rode `beto collect --debug-dump` e inspecione debug/<casa>/ para "
            "ajustar endpoints/parsers (instruções no docstring de cada adaptador)."
        )
    return "\n".join(lines)
