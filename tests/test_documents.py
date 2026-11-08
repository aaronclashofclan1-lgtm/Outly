"""Tests de l'import de documents (aucune interface, aucun réseau)."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from outly.gui import documents


def _pdf_minimal(texte: str) -> bytes:
    """Fabrique un PDF 1.4 minimal valide, avec une police Helvetica."""
    contenu = f"BT /F1 18 Tf 72 720 Td ({texte}) Tj ET".encode("latin-1")
    objets = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(contenu)).encode() + b" >>\nstream\n" + contenu + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    sortie = bytearray(b"%PDF-1.4\n")
    offsets = []
    for numero, objet in enumerate(objets, start=1):
        offsets.append(len(sortie))
        sortie += f"{numero} 0 obj\n".encode() + objet + b"\nendobj\n"
    position_xref = len(sortie)
    sortie += f"xref\n0 {len(objets) + 1}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        sortie += f"{offset:010d} 00000 n \n".encode()
    sortie += (
        f"trailer\n<< /Size {len(objets) + 1} /Root 1 0 R >>\n"
        f"startxref\n{position_xref}\n%%EOF\n"
    ).encode()
    return bytes(sortie)


CONTENU_ODT = """<?xml version="1.0" encoding="UTF-8"?>
<office:document-content
    xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
    xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
    xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0">
  <office:body>
    <office:text>
      <text:h text:outline-level="1">Mon positionnement</text:h>
      <text:p>Je parle<text:s text:c="2"/>de montage vidéo.</text:p>
      <text:p>Deuxième paragraphe.</text:p>
    </office:text>
  </office:body>
</office:document-content>
"""


def test_markdown_lu_tel_quel_et_bom_retire(tmp_path: Path):
    chemin = tmp_path / "brief.md"
    chemin.write_bytes("\ufeff# Titre\n\nUn paragraphe.\n".encode("utf-8"))

    document = documents.extraire(chemin)

    assert document.format == "Markdown"
    assert document.texte == "# Titre\n\nUn paragraphe."
    assert document.avertissements == ()


def test_texte_windows_1252_accepte(tmp_path: Path):
    chemin = tmp_path / "notes.txt"
    chemin.write_bytes(b"Caf\xe9 et cr\xe8me")

    assert documents.extraire(chemin).texte == "Café et crème"


def test_fichier_non_decodable_refuse(tmp_path: Path):
    chemin = tmp_path / "binaire.txt"
    chemin.write_bytes(b"\x81\x81")

    with pytest.raises(documents.ErreurDocument, match="UTF-8"):
        documents.extraire(chemin)


def test_json_reindente(tmp_path: Path):
    chemin = tmp_path / "donnees.json"
    chemin.write_text('{"niche":"montage","formats":["short","long"]}', encoding="utf-8")

    document = documents.extraire(chemin)

    assert document.format == "JSON"
    assert '"niche": "montage"' in document.texte
    assert document.texte.startswith("{")
    assert document.texte.count("\n") >= 3


def test_json_invalide_donne_la_ligne(tmp_path: Path):
    chemin = tmp_path / "casse.json"
    chemin.write_text('{"niche": }', encoding="utf-8")

    with pytest.raises(documents.ErreurDocument, match="ligne 1"):
        documents.extraire(chemin)


def test_html_sans_balises_ni_script(tmp_path: Path):
    chemin = tmp_path / "page.html"
    chemin.write_text(
        "<html><head><title>Ma marque</title><style>p{color:red}</style></head>"
        "<body><h1>Ma marque</h1><p>Montage &amp; motion design.</p>"
        "<script>alert('x')</script><ul><li>Shorts</li><li>Formats longs</li></ul>"
        "</body></html>",
        encoding="utf-8",
    )

    document = documents.extraire(chemin)

    assert document.format == "HTML"
    assert "Ma marque" in document.texte
    assert "Montage & motion design." in document.texte
    assert "- Shorts" in document.texte
    assert "alert" not in document.texte
    assert "color:red" not in document.texte
    assert "<" not in document.texte


def test_odt_extrait_les_paragraphes(tmp_path: Path):
    chemin = tmp_path / "brief.odt"
    with zipfile.ZipFile(chemin, "w") as archive:
        archive.writestr("mimetype", "application/vnd.oasis.opendocument.text")
        archive.writestr("content.xml", CONTENU_ODT)

    document = documents.extraire(chemin)

    assert document.format == "OpenDocument"
    assert document.texte == (
        "Mon positionnement\n\nJe parle de montage vidéo.\n\nDeuxième paragraphe."
    )


def test_odt_corrompu_message_clair(tmp_path: Path):
    chemin = tmp_path / "casse.odt"
    chemin.write_bytes(b"pas un zip")

    with pytest.raises(documents.ErreurDocument, match="archive valide"):
        documents.extraire(chemin)


def test_fodt_xml_plat(tmp_path: Path):
    chemin = tmp_path / "brief.fodt"
    chemin.write_text(CONTENU_ODT, encoding="utf-8")

    document = documents.extraire(chemin)

    assert document.format == "OpenDocument (XML plat)"
    assert document.texte.startswith("Mon positionnement")


def test_pdf_extrait_le_texte(tmp_path: Path):
    pytest.importorskip("pypdf")
    chemin = tmp_path / "brief.pdf"
    chemin.write_bytes(_pdf_minimal("Bonjour PDF"))

    document = documents.extraire(chemin)

    assert document.format == "PDF"
    assert "Bonjour" in document.texte


def test_extension_inconnue_lue_comme_texte_avec_avertissement(tmp_path: Path):
    chemin = tmp_path / "brief.weird"
    chemin.write_text("Une ligne\nUne autre", encoding="utf-8")

    document = documents.extraire(chemin)

    assert document.format == "Texte"
    assert "Extension inconnue" in document.avertissements[0]


def test_fichier_vide_refuse(tmp_path: Path):
    chemin = tmp_path / "vide.md"
    chemin.touch()

    with pytest.raises(documents.ErreurDocument, match="vide"):
        documents.extraire(chemin)


def test_fichier_absent_refuse(tmp_path: Path):
    with pytest.raises(documents.ErreurDocument, match="introuvable"):
        documents.extraire(tmp_path / "absent.pdf")


def test_taille_limitee(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(documents, "TAILLE_MAX", 8)
    chemin = tmp_path / "gros.md"
    chemin.write_text("beaucoup trop long", encoding="utf-8")

    with pytest.raises(documents.ErreurDocument, match="trop volumineux"):
        documents.extraire(chemin)


def test_lignes_candidates_nettoie_le_bruit():
    texte = (
        "# Mes mots-clés\n\n"
        "Voici une phrase de paragraphe bien trop longue pour être un mot-clé et qui "
        "décrit le contexte de ma chaîne en détail.\n\n"
        "- montage vidéo\n"
        "2) motion design\n"
        "• Montage vidéo\n"
        '« DaVinci Resolve »\n'
        "Titre :\n"
    )

    assert documents.lignes_candidates(texte) == [
        "montage vidéo",
        "motion design",
        "DaVinci Resolve",
    ]


def test_positionnement_long_borne_pour_le_llm(tmp_path: Path):
    from outly.llm.scoring import MAX_POSITIONNEMENT_PROMPT, positionnement_pour_prompt

    chemin = tmp_path / "positioning.md"
    chemin.write_text("x" * 30_000, encoding="utf-8")

    texte = positionnement_pour_prompt(chemin)

    assert len(texte) <= MAX_POSITIONNEMENT_PROMPT + 10
    assert texte.endswith("[…]")
    assert len(chemin.read_text(encoding="utf-8")) == 30_000  # le fichier reste intact


def test_positionnement_court_inchange(tmp_path: Path):
    from outly.llm.scoring import positionnement_pour_prompt

    chemin = tmp_path / "positioning.md"
    chemin.write_text("Un brief de trois lignes.\n", encoding="utf-8")

    assert positionnement_pour_prompt(chemin) == "Un brief de trois lignes."


def test_lignes_candidates_sur_du_texte_importe(tmp_path: Path):
    chemin = tmp_path / "idees.md"
    chemin.write_text(
        "# Idées\n- niches\n- étude de cas\n- niches\n", encoding="utf-8"
    )

    texte = documents.extraire(chemin).texte
    assert documents.lignes_candidates(texte) == ["niches", "étude de cas"]
