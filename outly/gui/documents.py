"""Import de documents texte dans « Mon contenu ».

L'utilisateur a souvent son positionnement (ou ses mots-clés) déjà écrit
ailleurs : un brief Markdown, un document OpenDocument, un PDF, une page HTML,
un export JSON. Ce module convertit ces fichiers en texte brut, sans passer par
l'interface, pour que la conversion soit testable seule.

Formats lus :
  * Markdown / texte  : lecture directe ;
  * JSON              : rechargé puis réindenté, pour rester lisible ;
  * HTML              : balises retirées, titres et paragraphes conservés ;
  * OpenDocument      : `.odt` (archive zip) et `.fodt` (XML plat) ;
  * PDF               : via `pypdf`, importé à la demande.

Aucune erreur ne remonte en exception brute : tout passe par `ErreurDocument`,
avec un message en français qui dit quoi faire.
"""

from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree

# Au-delà, on refuse : un document de veille n'a jamais cette taille et charger
# 200 Mo en mémoire figerait l'interface.
TAILLE_MAX = 25 * 1024 * 1024

# Libellés affichés dans la fenêtre d'import et le compte rendu.
FORMATS = {
    ".md": "Markdown",
    ".markdown": "Markdown",
    ".txt": "Texte",
    ".json": "JSON",
    ".html": "HTML",
    ".htm": "HTML",
    ".odt": "OpenDocument",
    ".odf": "OpenDocument",
    ".fodt": "OpenDocument (XML plat)",
    ".pdf": "PDF",
}

TEXTE_NS = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
MOTIF_PUCE = re.compile(r"^(?:[-*•·–—+]|\d{1,2}[.)])\s+")


class ErreurDocument(Exception):
    """Document illisible, vide, protégé ou dans un format non pris en charge."""


@dataclass(frozen=True)
class Document:
    """Texte extrait d'un fichier, avec sa provenance."""

    chemin: Path
    texte: str
    format: str
    avertissements: tuple[str, ...] = ()

    @property
    def nom(self) -> str:
        return self.chemin.name


# ---------------------------------------------------------------------------
# Lecture bas niveau
# ---------------------------------------------------------------------------


def _octets(chemin: Path) -> bytes:
    try:
        taille = chemin.stat().st_size
    except OSError as probleme:
        raise ErreurDocument(f"Fichier introuvable ou illisible : {probleme}") from probleme
    if taille == 0:
        raise ErreurDocument("Le fichier est vide.")
    if taille > TAILLE_MAX:
        raise ErreurDocument(
            f"Fichier trop volumineux ({taille / 1024 / 1024:.1f} Mo, "
            f"limite {TAILLE_MAX / 1024 / 1024:.0f} Mo)."
        )
    try:
        return chemin.read_bytes()
    except OSError as probleme:
        raise ErreurDocument(f"Lecture impossible : {probleme}") from probleme


def _decoder(donnees: bytes, nom: str) -> tuple[str, tuple[str, ...]]:
    """UTF-8 d'abord, puis Windows-1252 (exports bureautiques anciens)."""
    for encodage in ("utf-8-sig", "cp1252"):
        try:
            return donnees.decode(encodage), ()
        except UnicodeDecodeError:
            continue
    raise ErreurDocument(
        f"{nom} n'est ni en UTF-8 ni en Windows-1252 : convertissez-le en texte UTF-8."
    )


def _compacter(texte: str) -> str:
    """Normalise les fins de ligne et supprime les lignes vides en trop."""
    normalise = texte.replace("\r\n", "\n").replace("\r", "\n")
    normalise = "\n".join(ligne.rstrip() for ligne in normalise.splitlines())
    return re.sub(r"\n{3,}", "\n\n", normalise).strip()


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------


class _ExtracteurHtml(HTMLParser):
    """Retire les balises en gardant une ligne par bloc (titre, paragraphe, li)."""

    IGNORES = {"script", "style", "noscript", "head", "template", "svg"}
    BLOCS = {
        "p", "div", "br", "li", "tr", "td", "th", "section", "article", "header",
        "footer", "main", "nav", "aside", "blockquote", "pre", "figure",
        "figcaption", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "table", "hr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.morceaux: list[str] = []
        self._ignores = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self.IGNORES:
            self._ignores += 1
            return
        if self._ignores:
            return
        if tag in self.BLOCS:
            self.morceaux.append("\n")
        if tag == "li":
            self.morceaux.append("- ")

    def handle_startendtag(self, tag: str, attrs) -> None:
        if not self._ignores and tag in self.BLOCS:
            self.morceaux.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.IGNORES:
            self._ignores = max(0, self._ignores - 1)
            return
        if not self._ignores and tag in self.BLOCS:
            self.morceaux.append("\n")

    def handle_data(self, donnees: str) -> None:
        if not self._ignores:
            self.morceaux.append(donnees)

    def texte(self) -> str:
        brut = "".join(self.morceaux)
        lignes = [" ".join(ligne.split()) for ligne in brut.splitlines()]
        return _compacter("\n".join(lignes))


def _html_vers_texte(contenu: str) -> str:
    extracteur = _ExtracteurHtml()
    extracteur.feed(contenu)
    extracteur.close()
    texte = extracteur.texte()
    if not texte:
        raise ErreurDocument("Aucun texte lisible dans ce HTML (page vide ou script seulement).")
    return texte


# ---------------------------------------------------------------------------
# OpenDocument
# ---------------------------------------------------------------------------


def _texte_xml(element: ElementTree.Element) -> str:
    """Somme le texte d'un nœud ODF, en respectant espaces et sauts de ligne."""
    parties = [element.text or ""]
    for enfant in element:
        balise = enfant.tag
        if balise == f"{{{TEXTE_NS}}}s":
            try:
                nombre = int(enfant.get(f"{{{TEXTE_NS}}}c", "1") or 1)
            except ValueError:
                nombre = 1
            parties.append(" " * max(1, nombre))
        elif balise == f"{{{TEXTE_NS}}}tab":
            parties.append("\t")
        elif balise == f"{{{TEXTE_NS}}}line-break":
            parties.append("\n")
        elif balise == f"{{{TEXTE_NS}}}note":
            pass  # notes de bas de page : hors sujet pour un positionnement
        else:
            parties.append(_texte_xml(enfant))
        if enfant.tail:
            parties.append(enfant.tail)
    return "".join(parties)


def _odt_vers_texte(element: ElementTree.Element) -> str:
    paragraphes: list[str] = []
    for noeud in element.iter():
        if noeud.tag not in (f"{{{TEXTE_NS}}}p", f"{{{TEXTE_NS}}}h"):
            continue
        texte = " ".join(_texte_xml(noeud).split())
        if texte:
            paragraphes.append(texte)
    if not paragraphes:
        raise ErreurDocument("Ce document OpenDocument ne contient pas de texte.")
    return _compacter("\n\n".join(paragraphes))


def _lire_odt(chemin: Path, donnees: bytes) -> str:
    try:
        with zipfile.ZipFile(chemin) as archive:
            try:
                info = archive.getinfo("content.xml")
            except KeyError as probleme:
                raise ErreurDocument(
                    "Archive OpenDocument sans content.xml : le fichier est peut-être corrompu."
                ) from probleme
            if info.file_size > TAILLE_MAX:
                raise ErreurDocument("Contenu OpenDocument décompressé trop volumineux.")
            contenu = archive.read(info)
    except zipfile.BadZipFile as probleme:
        raise ErreurDocument(
            "Ce fichier .odt n'est pas une archive valide. Réenregistrez-le dans LibreOffice."
        ) from probleme
    except OSError as probleme:
        raise ErreurDocument(f"Lecture de l'archive impossible : {probleme}") from probleme

    try:
        racine = ElementTree.fromstring(contenu)
    except ElementTree.ParseError as probleme:
        raise ErreurDocument(f"XML OpenDocument invalide : {probleme}") from probleme
    return _odt_vers_texte(racine)


def _lire_fodt(contenu: str) -> str:
    try:
        racine = ElementTree.fromstring(contenu)
    except ElementTree.ParseError as probleme:
        raise ErreurDocument(f"XML OpenDocument invalide : {probleme}") from probleme
    return _odt_vers_texte(racine)


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------


def _pdf_vers_texte(chemin: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as probleme:  # dépendance déclarée, mais venv incomplet possible
        raise ErreurDocument(
            "Lecture des PDF indisponible : installez pypdf avec "
            "« pip install pypdf », ou importez un autre format."
        ) from probleme

    try:
        lecteur = PdfReader(str(chemin))
    except Exception as probleme:  # pypdf lève des types variés selon le PDF
        raise ErreurDocument(f"PDF illisible : {probleme}") from probleme

    if lecteur.is_encrypted:
        try:
            ouvert = lecteur.decrypt("")
        except Exception:  # noqa: BLE001 - message unique, quelle que soit la cause
            ouvert = 0
        if not ouvert:
            raise ErreurDocument(
                "Ce PDF est protégé par un mot de passe : enregistrez-en une copie "
                "sans protection avant de l'importer."
            )

    morceaux: list[str] = []
    for page in lecteur.pages:
        try:
            morceaux.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001 - une page cassée ne doit pas bloquer le reste
            morceaux.append("")

    texte = _compacter("\n\n".join(morceau.strip() for morceau in morceaux if morceau.strip()))
    if len(texte) < 5:
        raise ErreurDocument(
            "Aucun texte extractible : ce PDF est probablement un scan. "
            "Passez-le par un OCR, ou importez le document d'origine."
        )
    return texte


# ---------------------------------------------------------------------------
# Point d'entrée
# ---------------------------------------------------------------------------


def extraire(chemin: str | Path) -> Document:
    """Convertit un fichier en texte. Lève `ErreurDocument` si c'est impossible."""
    chemin = Path(chemin)
    suffixe = chemin.suffix.lower()
    donnees = _octets(chemin)
    avertissements: list[str] = []

    if suffixe == ".pdf":
        return Document(chemin, _pdf_vers_texte(chemin), FORMATS[".pdf"])

    if suffixe in (".odt", ".odf"):
        return Document(chemin, _lire_odt(chemin, donnees), FORMATS[suffixe])

    contenu, notes = _decoder(donnees, chemin.name)
    avertissements.extend(notes)

    if suffixe == ".fodt":
        texte = _lire_fodt(contenu)
        return Document(chemin, texte, FORMATS[".fodt"], tuple(avertissements))

    if suffixe in (".html", ".htm"):
        texte = _html_vers_texte(contenu)
        return Document(chemin, texte, FORMATS[suffixe], tuple(avertissements))

    if suffixe == ".json":
        try:
            donnees_json = json.loads(contenu)
        except json.JSONDecodeError as probleme:
            raise ErreurDocument(
                f"JSON invalide (ligne {probleme.lineno}, colonne {probleme.colno}) : "
                f"{probleme.msg}."
            ) from probleme
        texte = json.dumps(donnees_json, ensure_ascii=False, indent=2)
        return Document(chemin, texte, FORMATS[".json"], tuple(avertissements))

    texte = _compacter(contenu)
    if not texte:
        raise ErreurDocument("Le fichier ne contient que des lignes vides.")
    if suffixe in (".md", ".markdown", ".txt"):
        return Document(chemin, texte, FORMATS[suffixe], tuple(avertissements))

    avertissements.append(
        "Extension inconnue : le fichier a été lu comme du texte brut. "
        "Vérifiez le résultat avant d'enregistrer."
    )
    return Document(chemin, texte, "Texte", tuple(avertissements))


def lignes_candidates(texte: str) -> list[str]:
    """Lignes exploitables comme seeds ou mots-clés, sans bruit de mise en forme.

    Un document importé contient des titres, des phrases et des puces : on ne
    garde que les lignes courtes qui ressemblent à une entrée de liste, en
    retirant la puce ou la numérotation et en dédoublonnant.
    """
    resultat: list[str] = []
    vues: set[str] = set()
    for brute in texte.splitlines():
        ligne = " ".join(brute.split())
        if not ligne or ligne.startswith("#"):
            continue
        ligne = MOTIF_PUCE.sub("", ligne).strip()
        ligne = ligne.strip("\"'«»“”").strip()
        if not 2 <= len(ligne) <= 80:
            continue
        if ligne[-1] in ".:;!?":
            continue
        if ligne.startswith(("<!--", "|", "*Source :")):
            continue
        cle = ligne.casefold()
        if cle in vues:
            continue
        vues.add(cle)
        resultat.append(ligne)
    return resultat
