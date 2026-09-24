"""Génère le logo MSM : trois cubes isométriques (serveurs) en traits.

Usage, depuis la racine du dépôt : `python docs/branding/generate.py .`
Produit les SVG de `docs/branding/`, le favicon et le composant React `MsmLogo`.

Coordonnées isométriques : axe x -> (0.866, 0.5), axe y -> (-0.866, 0.5), z vers le haut.
Cube gauche (0,1,0), cube droit (1,0,0), cube du haut (0,0,1) : ils se juxtaposent
exactement, sans chevauchement, comme l'icône « Boxes » d'origine.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

K = math.sqrt(3) / 2
EMERALD = "#10B981"
INK = "#020617"
TEXT = "#E2E8F0"
MUTED = "#94A3B8"


def f(v: float) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".")


class Mark:
    def __init__(self, s: float, ox: float, oy: float, *, detailed: bool) -> None:
        self.s, self.ox, self.oy, self.detailed = s, ox, oy, detailed

    def pt(self, x: float, y: float) -> tuple[float, float]:
        return (self.ox + x * self.s, self.oy + y * self.s)

    def cube(self, cx: float, cy: float) -> dict[str, tuple[float, float]]:
        p = self.pt
        return {
            "T": p(cx, cy - 1),
            "UR": p(cx + K, cy - 0.5),
            "LR": p(cx + K, cy + 0.5),
            "B": p(cx, cy + 1),
            "LL": p(cx - K, cy + 0.5),
            "UL": p(cx - K, cy - 0.5),
            "C": p(cx, cy),
        }

    def outline(self) -> str:
        parts = []
        for cx, cy in ((-K, 0.5), (K, 0.5), (0, -1)):
            c = self.cube(cx, cy)
            hexagon = " L".join(f"{f(c[k][0])} {f(c[k][1])}" for k in ("T", "UR", "LR", "B", "LL", "UL"))
            parts.append(f"M{hexagon}Z")
            parts.append(
                f"M{f(c['UL'][0])} {f(c['UL'][1])}L{f(c['C'][0])} {f(c['C'][1])}"
                f"L{f(c['UR'][0])} {f(c['UR'][1])}M{f(c['C'][0])} {f(c['C'][1])}"
                f"L{f(c['B'][0])} {f(c['B'][1])}"
            )
        return "".join(parts)

    @staticmethod
    def _face(origin, du, dv):
        """Point (u, v) d'une face : u le long de l'arête horizontale, v vers le bas."""

        def at(u: float, v: float) -> tuple[float, float]:
            return (origin[0] + u * du[0] + v * dv[0], origin[1] + u * du[1] + v * dv[1])

        return at

    def slots(self) -> tuple[str, list[tuple[float, float]]]:
        """Fentes de serveur (traits) et voyants (points) sur une face de chaque cube."""
        s = self.s
        rows = (0.28, 0.5, 0.72) if self.detailed else (0.33, 0.67)
        leds = (0.28, 0.5) if self.detailed else (0.33,)
        lines: list[str] = []
        dots: list[tuple[float, float]] = []

        def left_face(cx: float, cy: float) -> None:
            c = self.cube(cx, cy)
            at = self._face(c["UL"], (K * s, 0.5 * s), (0, s))
            for v in rows:
                a, b = at(0.15, v), at(0.6, v)
                lines.append(f"M{f(a[0])} {f(a[1])}L{f(b[0])} {f(b[1])}")
            for v in leds:
                dots.append(at(0.8, v))

        def right_face(cx: float, cy: float) -> None:
            c = self.cube(cx, cy)
            at = self._face(c["C"], (K * s, -0.5 * s), (0, s))
            for v in rows:
                a, b = at(0.4, v), at(0.85, v)
                lines.append(f"M{f(a[0])} {f(a[1])}L{f(b[0])} {f(b[1])}")
            for v in leds:
                dots.append(at(0.2, v))

        left_face(0, -1)
        left_face(-K, 0.5)
        right_face(K, 0.5)
        return "".join(lines), dots

    def svg_body(self, stroke: str, width: float, led: str | None = None) -> str:
        lines, dots = self.slots()
        r = width * 0.6
        body = (
            f'<g fill="none" stroke="{stroke}" stroke-width="{f(width)}" '
            f'stroke-linecap="round" stroke-linejoin="round">'
            f'<path d="{self.outline()}"/><path d="{lines}"/></g>'
        )
        body += (
            f'<g fill="{led or stroke}">'
            + "".join(f'<circle cx="{f(x)}" cy="{f(y)}" r="{f(r)}"/>' for x, y in dots)
            + "</g>"
        )
        return body


def mark_in_box(size: float, margin: float, *, detailed: bool) -> Mark:
    # Emprise : x de -2K à 2K (3,46 s), y de -2 à 1,5 (3,5 s).
    s = (size - 2 * margin) / 3.5
    oy = margin + 2 * s
    return Mark(s, size / 2, oy, detailed=detailed)


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
    print("wrote", path)


def main(repo: Path) -> None:
    header = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">'

    # Marque seule, transparente, détaillée (docs, README).
    m = mark_in_box(512, 24, detailed=True)
    write(
        repo / "docs/branding/msm-mark.svg",
        header.format(w=512, h=512) + m.svg_body(EMERALD, m.s * 0.13) + "</svg>\n",
    )

    # Icône d'application : carré arrondi sombre.
    m = mark_in_box(512, 84, detailed=True)
    icon = (
        header.format(w=512, h=512)
        + f'<rect width="512" height="512" rx="112" fill="{INK}"/>'
        + m.svg_body(EMERALD, m.s * 0.13)
        + "</svg>\n"
    )
    write(repo / "docs/branding/msm-icon.svg", icon)

    # Favicon : même icône, simplifiée et aux traits plus épais pour 16-32 px.
    m = mark_in_box(64, 9, detailed=False)
    write(
        repo / "frontend/public/favicon.svg",
        header.format(w=64, h=64)
        + f'<rect width="64" height="64" rx="14" fill="{INK}"/>'
        + m.svg_body(EMERALD, m.s * 0.15)
        + "</svg>\n",
    )

    # Logo horizontal : marque + « MSM » + sous-titre, en deux variantes de texte
    # (clair sur fond sombre, sombre sur fond clair — le README suit le thème GitHub).
    for suffix, text, muted in (("", TEXT, MUTED), ("-light", "#0F172A", "#475569")):
        m = mark_in_box(240, 16, detailed=True)
        lockup = (
            header.format(w=900, h=240)
            + m.svg_body(EMERALD, m.s * 0.13)
            + "<g font-family=\"Inter, 'Segoe UI', Helvetica, Arial, sans-serif\">"
            + f'<text x="262" y="142" font-size="124" font-weight="700" fill="{text}" '
            + 'letter-spacing="2">MSM</text>'
            + f'<text x="266" y="192" font-size="31" font-weight="500" fill="{muted}" '
            + 'letter-spacing="3">Minecraft Server Manager</text>'
            + "</g></svg>\n"
        )
        write(repo / f"docs/branding/msm-logo{suffix}.svg", lockup)

    # Composant React : petite taille, couleur héritée (currentColor).
    m = mark_in_box(24, 0.9, detailed=False)
    lines, dots = m.slots()
    circles = "\n".join(f'        <circle cx="{f(x)}" cy="{f(y)}" r="{f(m.s * 0.15 * 0.62)}" />' for x, y in dots)
    tsx = f"""import type {{ SVGProps }} from 'react'

/**
 * Logo MSM : trois serveurs en cubes isométriques.
 *
 * Généré par `docs/branding/generate.py` — ne pas éditer à la main. La couleur
 * suit `currentColor`, comme les icônes Lucide qu'il côtoie.
 */
export function MsmLogo(props: SVGProps<SVGSVGElement>) {{
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={{{f(m.s * 0.15)}}}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {{...props}}
    >
      <path d="{m.outline()}" />
      <path d="{lines}" />
      <g fill="currentColor" stroke="none">
{circles}
      </g>
    </svg>
  )
}}
"""
    write(repo / "frontend/src/components/brand/MsmLogo.tsx", tsx)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
