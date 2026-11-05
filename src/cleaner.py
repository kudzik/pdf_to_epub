import os
import re
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from src.artifact_state import atomic_json, sha256
from typing import Optional

from src.write_control import book_write_lock


# Mapa popularnych ułamków typograficznych Unicode na ich reprezentację ASCII
UNICODE_FRACTIONS: dict[str, str] = {
    "½": "1/2",
    "¼": "1/4",
    "¾": "3/4",
    "⅓": "1/3",
    "⅔": "2/3",
    "⅛": "1/8",
    "⅜": "3/8",
    "⅝": "5/8",
    "⅞": "7/8",
}


def fix_hyphenation(text: str) -> str:
    """
    Usuwa łączniki dzielenia wyrazów na końcach wierszy (de-hyphenation)
    oraz soft-hyphens (\\xad / \\u00ad), łącząc podzielone słowa.
    """
    # 1. Usuwanie soft hyphens z opcjonalnymi spacjami (np. "cul\xad tural" -> "cultural")
    text = re.sub(
        r"(\b[a-zA-ZąćęłńóśźżĄĆĘŁŃÓŚŹŻ]+)[\xad\u00ad]\s*([a-zA-ZąćęłńóśźżĄĆĘŁŃÓŚŹŻ]+\b)",
        r"\1\2",
        text,
    )

    # 2. Usuwanie łącznika na końcu linii, gdy następna linia zaczyna się z małej litery (podział słowa)
    text = re.sub(
        r"(\b[a-zA-ZąćęłńóśźżĄĆĘŁŃÓŚŹŻ]{2,})-\s*\n\s*([a-zà-żąćęłńóśźż]{2,}\b)",
        r"\1\2",
        text,
    )

    # 3. Przypadki typu "Ameri - can" lub "back - ground" (łącznik rozdzielony spacjami wewnątrz słowa)
    text = re.sub(
        r"(\b[a-zA-ZąćęłńóśźżĄĆĘŁŃÓŚŹŻ]{3,})\s+-\s+([a-zà-żąćęłńóśźż]{2,}\b)",
        r"\1\2",
        text,
    )

    return text


def restore_polish_and_ocr_artifacts(text: str) -> str:
    """
    Rekonstruuje polskie znaki diakrytyczne oraz zniekształcenia OCR powstałe
    w wyniku odczytu starego skanu przez oprogramowanie pozbawione tablicy kodowej UTF-8.
    """
    # --- 1. Ułamki z uszkodzonymi symbolami walut i nawiasów (¥2, «, !, Vz, '/z, '/a) ---
    text = re.sub(r"¥\s*2\b", "1/2", text)
    text = re.sub(r"¥\s*z\b", "1/2", text)
    text = re.sub(r"¥\s*3\b", "1/3", text)
    text = re.sub(r"¥\s*4\b", "1/4", text)
    text = re.sub(r"(\d+)\s+¥2\b", r"\1 1/2", text)
    text = re.sub(r"(\d+)-(\d+)\s*¥2\b", r"\1-\2 1/2", text)
    text = re.sub(r"(\*|\'|/)\s*/\s*«", "1/4", text)
    text = re.sub(r"(\d+)\s*/\s*«", r"\1/4", text)
    text = re.sub(r"»\s*/\s*2", "1/2", text)
    text = re.sub(r"»\s*/\s*«", "1/4", text)
    text = re.sub(r">\s*/\s*2", "1/2", text)
    text = re.sub(r"\b1!4\b", "1 1/4", text)
    text = re.sub(r"\b1!2\b", "1 1/2", text)
    text = re.sub(r"\b1\'/£\b", "1 1/2", text)
    text = re.sub(r"1\s*\'/£", "1 1/2", text)

    # Typowe ułamki OCR skanowane jako litery: Vz, '/z, '/a, '/2
    text = re.sub(r"\bVz\b", "1/2", text)
    text = re.sub(r"\bVa\b", "1/2", text)
    text = re.sub(r"'/z\b", "1/2", text)
    text = re.sub(r"'/a\b", "1/2", text)
    text = re.sub(r"'/2\b", "1/2", text)

    # Błędy OCR typu 1-114 lb. -> 1-1 1/4 lb., 114 t. -> 1/4 t.
    units_patt = r"(?:c\.|cup|cups|tsp|tbsp|teaspoon|tablespoon|lb\.|lbs\.|oz\.|inch|inches|hr|hrs|hour|hours)"
    text = re.sub(rf"(\b\d+)-114\s+({units_patt})", r"\1-1 1/4 \2", text, flags=re.IGNORECASE)
    text = re.sub(rf"\b114\s+({units_patt})", r"1/4 \1", text, flags=re.IGNORECASE)

    # Ułamki przed jednostkami (np. 14 c. -> 1/4 c., 34 c. -> 3/4 c., 12 c. -> 1/2 c.)
    text = re.sub(rf"(\b\d+\s+(?:tablespoons?|tbsp)\s+plus\s+)12\s+({units_patt})", r"\g<1>1 1/2 \2", text, flags=re.IGNORECASE)
    text = re.sub(rf"(plus\s+)12\s+({units_patt})", r"\g<1>1 1/2 \2", text, flags=re.IGNORECASE)
    text = re.sub(rf"\b14\s+({units_patt})", r"1/4 \1", text, flags=re.IGNORECASE)
    text = re.sub(rf"\b34\s+({units_patt})", r"3/4 \1", text, flags=re.IGNORECASE)
    text = re.sub(rf"\b12\s+({units_patt})", r"1/2 \1", text, flags=re.IGNORECASE)

    # --- 2. Akcenty kulinarne (francuskie/europejskie) podmienione na £, 6 lub de ---
    text = re.sub(r"\b[sS]aut£", "sauté", text)
    text = re.sub(r"\b[cC]anap£", "canapé", text)
    text = re.sub(r"\b[pP]ur£e", "purée", text)
    text = re.sub(r"\b[pP]ur6e", "purée", text)
    text = re.sub(r"\b[pP]urde\b", "purée", text)
    text = re.sub(r"\b[cC]r£me", "crème", text)
    text = re.sub(r"\b[eE]ntr£e", "entrée", text)
    text = re.sub(r"\b[eE]nt£ee", "entrée", text)
    text = re.sub(r"\b[fF]lamb£", "flambé", text)
    text = re.sub(r"\b[pP][aâAÂ]t£", "pâté", text)
    text = re.sub(r"\bp&t£", "pâté", text)
    text = re.sub(r"\bpfit£", "pâté", text)
    text = re.sub(r"\bgruy£re\b", "gruyère", text)

    # --- 3. Rekonstrukcja polskich znaków diakrytycznych (ą, ę, ó, ś, ł, ż, ź, ń) ---
    # Słowa ze znaczkiem ^ zamiast ogonka ą / ę
    text = re.sub(r"\bgol\^bki\b", "gołąbki", text, flags=re.IGNORECASE)
    text = re.sub(r"\bgoi\^bki\b", "gołąbki", text, flags=re.IGNORECASE)
    text = re.sub(r"\bgolgbki\b", "gołąbki", text, flags=re.IGNORECASE)
    text = re.sub(r"\bmi\^sem\b", "mięsem", text, flags=re.IGNORECASE)
    text = re.sub(r"\bmigsem\b", "mięsem", text, flags=re.IGNORECASE)
    text = re.sub(r"\bzaj\^c\b", "zając", text, flags=re.IGNORECASE)
    text = re.sub(r"\bw\^trob", "wątrób", text, flags=re.IGNORECASE)
    text = re.sub(r"\bpstr\^g", "pstrąg", text, flags=re.IGNORECASE)
    text = re.sub(r"\bjarz\^bek\b", "jarząbek", text, flags=re.IGNORECASE)
    text = re.sub(r"\bciel\^c", "cielęc", text, flags=re.IGNORECASE)
    text = re.sub(r"\bSl\^sk", "Śląsk", text, flags=re.IGNORECASE)
    text = re.sub(r"\bw\^dzon", "wędzon", text, flags=re.IGNORECASE)
    text = re.sub(r"\boszcz\^dn", "oszczędn", text, flags=re.IGNORECASE)
    text = re.sub(r"\bnapr\^dc", "naprędc", text, flags=re.IGNORECASE)
    text = re.sub(r"\bk\^piel\b", "kąpiel", text, flags=re.IGNORECASE)
    text = re.sub(r"\bp\^cak\b", "pęcak", text, flags=re.IGNORECASE)
    text = re.sub(r"\bmi\^tow", "miętow", text, flags=re.IGNORECASE)
    text = re.sub(r"\bLeSZCZ7Aski\b", "Leszczyński", text)
    text = re.sub(r"\bpfczki\b", "pączki", text, flags=re.IGNORECASE)
    text = re.sub(r"\bP£CZKI\b", "PĄCZKI", text)
    text = re.sub(r"\bp\^czki\b", "pączki", text, flags=re.IGNORECASE)
    text = re.sub(r"\bogdrk", "ogórk", text, flags=re.IGNORECASE)
    text = re.sub(r"\bog6rk", "ogórk", text, flags=re.IGNORECASE)
    text = re.sub(r"\bposlne jedzenie\b", "postne jedzenie", text, flags=re.IGNORECASE)
    text = re.sub(r"\bffastday fare\)", "(fastday fare)", text, flags=re.IGNORECASE)
    text = re.sub(r"\bkwaszon\?\s*kapustgi\b", "kwaszoną kapustą", text, flags=re.IGNORECASE)
    text = re.sub(r"\bkielbas\?\b", "kiełbasą", text, flags=re.IGNORECASE)
    text = re.sub(r"\bszynk\$\b", "szynką", text, flags=re.IGNORECASE)
    text = re.sub(r"\bprzepidrk", "przepiórk", text, flags=re.IGNORECASE)
    text = re.sub(r"\bbit\$\s*£mietan\?", "bitą śmietaną", text, flags=re.IGNORECASE)
    text = re.sub(r"\bbitg\s+smietanq\b", "bitą śmietaną", text, flags=re.IGNORECASE)
    text = re.sub(r"\bze\s+smietang\b", "ze śmietaną", text, flags=re.IGNORECASE)
    text = re.sub(r"\bze\s+smietank[?^Q]\b", "ze śmietanką", text, flags=re.IGNORECASE)
    text = re.sub(r"\bze\s+smietan[?^]\b", "ze śmietaną", text, flags=re.IGNORECASE)
    text = re.sub(r"\bze\s+Smietan\^\s+i\s+cebul\?", "ze śmietaną i cebulą", text)
    text = re.sub(r"\bcebula\s+w\s+smietanie\b", "cebula w śmietanie", text, flags=re.IGNORECASE)
    text = re.sub(r"\bwgtrobka\s+cielfca\b", "wątróbka cielęca", text, flags=re.IGNORECASE)
    text = re.sub(r"\bs\^kacz\b", "sękacz", text, flags=re.IGNORECASE)
    text = re.sub(r"\bsgkacz\b", "sękacz", text, flags=re.IGNORECASE)
    text = re.sub(r"\bswigte\b", "święte", text, flags=re.IGNORECASE)

    # Rzeczowniki rodzaju żeńskiego w narzędniku z końcówką ^ (kapust^, cebulk^, smietank^, fasol^)
    text = re.sub(r"\b([a-zA-ZąćęłńóśźżĄĆĘŁŃÓŚŹŻ]{2,}[kltzns])\^(\b|\s|[.,;])", r"\1ą\2", text)

    # Polskie słowa ze znakiem £ zamiast ś/Ś lub ę
    text = re.sub(r"(\s|^)£wiez", r"\1śwież", text, flags=re.IGNORECASE)
    text = re.sub(r"(\s|^)£liwk", r"\1śliwk", text, flags=re.IGNORECASE)
    text = re.sub(r"(\s|^)£mietan", r"\1śmietan", text, flags=re.IGNORECASE)
    text = re.sub(r"(\s|^)£l\?sk", r"\1śląsk", text, flags=re.IGNORECASE)
    text = re.sub(r"\bkapu£niak", "kapuśniak", text, flags=re.IGNORECASE)
    text = re.sub(r"\bkapu£cie\b", "kapuście", text, flags=re.IGNORECASE)
    text = re.sub(r"\bma£le\b", "maśle", text, flags=re.IGNORECASE)
    text = re.sub(r"\bnale£nik", "naleśnik", text, flags=re.IGNORECASE)
    text = re.sub(r"\bkluski\s+£l\?skie", "kluski śląskie", text, flags=re.IGNORECASE)
    text = re.sub(r"\bg£s\b", "gęś", text, flags=re.IGNORECASE)
    text = re.sub(r"\bg£si\b", "gęsi", text, flags=re.IGNORECASE)
    text = re.sub(r"\bkurcz£(?=\s|[.,;:]|\b|$)", "kurczę", text, flags=re.IGNORECASE)
    text = re.sub(r"\bstrz£piasty\b", "strzępiasty", text, flags=re.IGNORECASE)
    text = re.sub(r"\bogórkow\b", "ogórków", text, flags=re.IGNORECASE)
    text = re.sub(r"\bpamula\b", "pamuła", text, flags=re.IGNORECASE)
    text = re.sub(r"\b0LLOSTKA¥EO EOOTDOM\b", "", text)

    # Polskie słowa z cyfrą 6 zamiast ó
    text = re.sub(r"\bgrzyb6w\b", "grzybów", text, flags=re.IGNORECASE)
    text = re.sub(r"\br6zn", "różn", text, flags=re.IGNORECASE)
    text = re.sub(r"\bdrobi6w\b", "drobiów", text, flags=re.IGNORECASE)
    text = re.sub(r"\bsmak6w\b", "smaków", text, flags=re.IGNORECASE)
    text = re.sub(r"\bsos6w\b", "sosów", text, flags=re.IGNORECASE)
    text = re.sub(r"\bmakowc6w\b", "makowców", text, flags=re.IGNORECASE)
    text = re.sub(r"\boz6r\b", "ozór", text, flags=re.IGNORECASE)

    # --- 4. Angielskie substytucje OCR i zagubione znaki ---
    text = re.sub(r"\bha\^been\b", "has been", text)
    text = re.sub(r"\bb°ynthe\b", "by the", text)
    text = re.sub(r"\b°Ut\s*\^°U\b", "But you", text)
    text = re.sub(r"\b°Ut\b", "But", text)
    text = re.sub(r"\b°f\b", "of", text)
    text = re.sub(r"\b°n\b", "on", text)
    text = re.sub(r"\b(\d+)\s*°f\b", r"\1°F", text)
    text = re.sub(r"\bP°\^-loin\b", "Pork loin", text)
    text = re.sub(r"\bbiros\b", "bigos", text, flags=re.IGNORECASE)
    text = re.sub(r"\btiiose\b", "those", text, flags=re.IGNORECASE)
    text = re.sub(r"\btiiat\b", "that", text, flags=re.IGNORECASE)
    text = re.sub(r"\btiien\b", "then", text, flags=re.IGNORECASE)
    text = re.sub(r"\btiling\b", "thing", text, flags=re.IGNORECASE)
    text = re.sub(r"\bpastlies\b", "pastries", text, flags=re.IGNORECASE)

    # Zamiana błędnego "die" na "the" w kontekście kulinarno-angielskim
    text = re.sub(
        r"\b(in|on|of|with|to|cook|add|heat|stir|pour|serve|spread|place|into|from|at|during|under|as)\s+die\s+([a-z]+)",
        r"\1 the \2",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bdie\s+(oven|pan|pot|meat|sauce|soup|mixture|bowl|bottom|top|middle|same|real|author|book|kitchen|table|heat|water|dough|batter|filling|skillet|dish|platter|cabbage|onions|potatoes)\b",
        r"the \1",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\bdie\s+(US|Surgeon|United States|globe|world)\b", r"the \1", text)
    text = re.sub(r"\b(On|Throughout)\s+die\b", r"\1 the", text)

    # Zamiana błędnego "tire" na "the"
    text = re.sub(
        r"\btire\s+(American|more|traditional|other|best|same|first|above|season|master)\b",
        r"the \1",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\b(on|in|of)\s+tire\b", r"\1 the", text, flags=re.IGNORECASE)

    # --- 5. Poprawki zniekształceń polskich znaków diakrytycznych i waluty ---
    text = re.sub(r"\bČ", "Ć", text)
    text = re.sub(r"\bč", "ć", text)
    text = re.sub(r"\bztotych\b", "złotych", text, flags=re.IGNORECASE)
    text = re.sub(r"\bztoty\b", "złoty", text, flags=re.IGNORECASE)
    text = re.sub(r"\bztotys\b", "złotych", text, flags=re.IGNORECASE)

    # --- 6. Korekta częstych zniekształceń OCR w słowach angielskich i kulinarnych ---
    text = re.sub(r"\blitte\b", "little", text)
    text = re.sub(r"\bstrenghtens\b", "strengthens", text)
    text = re.sub(r"\bhealthul\b", "healthful", text)
    text = re.sub(r"\bregimnens\b", "regimens", text)
    text = re.sub(r"\bItis\b", "It is", text)
    text = re.sub(r"\bmagnesium pils\b", "magnesium pills", text, flags=re.IGNORECASE)
    text = re.sub(r"\ba axy coating hat cts\b", "a waxy coating that acts", text, flags=re.IGNORECASE)
    text = re.sub(r"\bhat cts\b", "that acts", text, flags=re.IGNORECASE)
    text = re.sub(r"\bdiat\b", "that", text, flags=re.IGNORECASE)
    text = re.sub(r"\bdiis\b", "this", text, flags=re.IGNORECASE)
    text = re.sub(r"\bwidi\b", "with", text, flags=re.IGNORECASE)

    # Znane zbitki szumu skanera ze starych wydań
    text = re.sub(r"\boaos orn nn n ouns nin o srirn nsst, rs e ins dns\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\boot ont S os\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bsures sr i s se tesd\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*or doe cae ct ccrs\b", "", text, flags=re.IGNORECASE)

    # --- 7. Usuwanie szumu skanera (kwadraciki i artefakty graficzne) ---
    text = re.sub(r"[■▲◄►▪]", "", text)

    return text


def normalize_culinary_terms(text: str) -> str:
    """
    Normalizuje ułamki typograficzne i jednostki miar w przepisach kulinarnych.
    """
    # 1. Zamiana ułamków Unicode na ASCII (np. ½ -> 1/2)
    for uni_frac, ascii_frac in UNICODE_FRACTIONS.items():
        text = text.replace(uni_frac, ascii_frac)

    # 2. Usuwanie spacji wokół kreski ułamkowej (np. "1 / 2" -> "1/2")
    text = re.sub(r"(\b\d+)\s*/\s*(\d+\b)", r"\1/\2", text)

    units_pattern = (
        r"(?:tsp|tbsp|cup|cups|teaspoon|teaspoons|tablespoon|tablespoons|oz|ounce|ounces|"
        r"lb|lbs|pound|pounds|gram|grams|g|kg|ml|l|pinch|pinches|inch|inches|quart|quarts|qt|qts)\b"
    )

    # 3. Naprawa ułamków typograficznych z apostrofem/cudzysłowem (np. 1'/2-inch -> 1 1/2-inch, '/3-inch -> 1/3-inch)
    text = re.sub(r"(\b\d+)\s*['‘\"]\s*/\s*([2348])(?=[-a-zA-Z\s])", r"\1 1/\2", text)
    text = re.sub(r"(?:^|(?<=[\s\(\[]))['‘\"]\s*/\s*([2348])(?=[-a-zA-Z\s])", r"1/\1", text)
    text = re.sub(r"(?:^|(?<=[\s\(\[]))['‘\"]([2348])\s+(" + units_pattern + ")", r"1/\1 \2", text, flags=re.IGNORECASE)
    text = re.sub(r"\bV2\s+(" + units_pattern + ")", r"1/2 \1", text, flags=re.IGNORECASE)
    text = re.sub(r"\bVz\s+(" + units_pattern + ")", r"1/2 \1", text, flags=re.IGNORECASE)

    # 4. Naprawa ułamków sklejonych bez kreski w nawiasach i przed jednostkami (np. "plus 12 teaspoons" -> "plus 1 1/2 teaspoons")
    text = re.sub(
        rf"(plus\s+)12\s+({units_pattern})",
        r"\g<1>1 1/2 \2",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        rf"(\b\d+\s+(?:tablespoons?|tbsp)\s+plus\s+)12\s+({units_pattern})",
        r"\g<1>1 1/2 \2",
        text,
        flags=re.IGNORECASE,
    )

    # 5. Naprawa ułamków sklejonych lub uszkodzonych przed jednostkami kulinarnymi:
    text = re.sub(r"\b1/4\s+ounces\s+\(2\s+tablespoons\s+plus\s+1/2\s+teaspoons\)", "1 1/4 ounces (2 tablespoons plus 1 1/2 teaspoons)", text)
    text = re.sub(r"\bplus\s+1/2\s+teaspoons\b", "plus 1 1/2 teaspoons", text)

    # 6. Naprawa błędów OCR w ułamkach przed jednostkami kulinarnymi:
    # np. "1 2 tsp" -> "1/2 tsp", "1 4 cup" -> "1/4 cup"
    text = re.sub(
        rf"(\b[1-9])\s+([2348])\s+({units_pattern})",
        r"\1/\2 \3",
        text,
        flags=re.IGNORECASE,
    )

    return text


def clean_markdown_headings(text: str) -> str:
    """
    Czyści strukturę nagłówków Markdown:
    - Rozdziela obrazy sklejone w tej samej linii z nagłówkiem (## TYTUŁ![Image](...)).
    - Demotuje fałszywe nagłówki proceduralne (## Preheat the oven to 425°F.).
    - Oddziela tekst definicji wklejony w nagłówek (## GALANGAL (...) This member...).
    - Usuwa szumowe znaczniki na końcach nagłówków (____-SE, itp.).
    """
    # 1. Obrazy w tej samej linii co nagłówek
    text = re.sub(r"^(#{1,6}\s+[^!\n]+)(!\[[^\]]*\]\([^)]+\))", r"\1\n\n\2", text, flags=re.MULTILINE)

    # 2. Usuwanie śmieciowych ogonów na końcach nagłówków
    text = re.sub(r"^(#{1,6}\s+.*?)\s+[-_\\~]{3,}.*?$", r"\1", text, flags=re.MULTILINE)

    # 3. Demotowanie kroków wykonawczych i zdań proceduralnych oznaczonych błędnie jako ##
    text = re.sub(r"^##\s+(Preheat\s+(?:the\s+)?oven\b.*)$", r"\1", text, flags=re.MULTILINE)

    # 4. Demotowanie pojedynczych składników kulinarnych oznaczonych błędnie jako nagłówek ##
    units_heading = (
        r"(?:tsp|tbsp|cup|cups|teaspoon|teaspoons|tablespoon|tablespoons|oz|ounce|ounces|"
        r"lb|lbs|pound|pounds|gram|grams|g|kg|ml|l|pinch|pinches|quart|quarts|qt|qts)\b"
    )
    text = re.sub(
        rf"^##\s+(\d+(?:/\d+)?(?:\s+\d+/\d+)?\s+{units_heading}.*)$",
        r"- \1",
        text,
        flags=re.MULTILINE | re.IGNORECASE,
    )

    # 5. Oddzielanie definicji słownikowych/składnikowych sklejonych z nagłówkiem
    text = re.sub(
        r"^(##\s+[A-Z\s\(\),-]{3,45})\s+([A-Z][a-z]{3,}\s+.*)$",
        r"\1\n\n\2",
        text,
        flags=re.MULTILINE,
    )

    # 6. Usuwanie znaczników przejścia strony błędnie oznaczonych jako nagłówki (np. '## >> continued')
    text = re.sub(r"^##\s+(?:>>|\?>|\d+\?|>|\?)\s*continued\b.*$", "", text, flags=re.MULTILINE | re.IGNORECASE)

    # 7. Usuwanie śmieciowych nagłówków ze znakami szumowymi (np. '## ae ie Sie lt x @')
    text = re.sub(r"^##\s+(?:ae\s+ie\s+Sie\s+lt\b.*|fa\s+'a\s+AT\s+ee\b.*|<b\.\s+a\s+-.*|cory\s+ey\b.*)$", "", text, flags=re.MULTILINE | re.IGNORECASE)

    # 8. Usuwanie symboli szumowych (©, ®, \_., itp.) na końcach poprawnych nagłówków
    text = re.sub(r"^(##\s+[A-Z\s\(\),\"'-]{3,60})\s+[©®\\_.]+\s*$", r"\1", text, flags=re.MULTILINE)

    # 9. Normalizacja typowych literówek OCR w standardowych sekcjach książek
    text = re.sub(r"^##\s+INGREDIENTS\s+GEOSSARY\b", "## INGREDIENTS GLOSSARY", text, flags=re.MULTILINE | re.IGNORECASE)
    text = re.sub(r"^##\s+GRATITUDE\s+ANDSRESReaeik\b", "## GRATITUDE AND RESPECT", text, flags=re.MULTILINE | re.IGNORECASE)
    text = re.sub(r"^##\s+EQUIPMENT\s+AND\s+PECTINIQUES\b", "## EQUIPMENT AND TECHNIQUES", text, flags=re.MULTILINE | re.IGNORECASE)

    return text


def format_recipe_ingredients(text: str) -> str:
    """
    Wykrywa i rozdziela sklejone w jednej linii składniki przepisów kulinarnych
    (np. '2 quarts water 1 1/4 ounces salt 1 head cabbage' -> osobne pozycje).
    Działa linia po linii, nie ingerując w treść procedur ani nie tworząc pustych elementów.
    """
    # 1. Łączenie rozbitych wierszy składników (ilość na końcu wiersza, nazwa produktu w następnym)
    patt_wrap = re.compile(
        r"(\b\d+(?:/\d+)?(?:\s+\d+/\d+)?\s+(?:quarts?|qt|cups?|tablespoons?|tbsp|teaspoons?|tsp|ounces?|oz|pounds?|lb|lbs|heads?|cloves?|pieces?|slices?|stalks?|bunch(?:es)?|sprigs?|pinch(?:es)?|dash(?:es)?)\s*(?:\([^)]+\))?)\s*\n+\s*([a-z][a-z\s,-]{2,30}\b)",
        re.IGNORECASE,
    )
    text = patt_wrap.sub(r"\1 \2", text)

    units_pattern = (
        r"(?:quarts?|qt|qts|cups?|c\.|tablespoons?|tbsp|teaspoons?|tsp|ounces?|oz|"
        r"pounds?|lb|lbs|heads?|cloves?|pieces?|slices?|stalks?|bunch(?:es)?|"
        r"sprigs?|pinch(?:es)?|dash(?:es)?|can|cans|package|packages|pkg)\b"
    )
    target_pattern = re.compile(rf"^(?:\d+\s+\d+/\d+|\d+/\d+|\d+)\s+{units_pattern}", re.IGNORECASE)

    lines = text.splitlines()
    result_lines = []

    for line in lines:
        stripped = line.strip()

        # Pomijaj nagłówki, linie tabel, linie pustego tekstu i linie z obrazami
        if not stripped or stripped.startswith(("#", "|", "!", "---")):
            result_lines.append(line)
            continue

        words = stripped.split()
        buffer = ""
        in_paren = 0
        items = []

        for i, w in enumerate(words):
            in_paren += w.count("(") - w.count(")")
            remainder = " ".join(words[i:i + 4])
            if (
                in_paren <= 0
                and buffer
                and target_pattern.match(remainder)
                and not (i > 0 and words[i - 1].isdigit())
            ):
                items.append(buffer.strip())
                buffer = w
            else:
                buffer += " " + w if buffer else w

        if buffer:
            items.append(buffer.strip())

        if len(items) > 1:
            for it in items:
                clean_it = it.strip()
                if clean_it:
                    result_lines.append("- " + clean_it if not clean_it.startswith(("-", "*", "•")) else clean_it)
        elif (
            len(items) == 1
            and result_lines
            and result_lines[-1].startswith("- ")
            and not stripped.endswith(".")
            and len(words) <= 15
        ):
            clean_it = items[0].strip()
            result_lines.append("- " + clean_it if not clean_it.startswith(("-", "*", "•")) else clean_it)
        else:
            result_lines.append(line)

    return "\n".join(result_lines)


def remove_scan_noise(text: str) -> str:
    """
    Usuwa linie będące artefaktami skanera (losowe znaczki, plamki, puste linie)
    oraz poszarpane ciągi literowe z marginesów.
    """
    lines = text.splitlines()
    cleaned_lines = []

    for line in lines:
        stripped = line.strip()

        # Puste linie z samym myślnikiem lub gwiazdką (osierocone punkty)
        if stripped in ["-", "*", "•"]:
            continue

        # Zachowaj linie puste, separatory Markdown, tabele oraz elementy list
        if not stripped or stripped.startswith(("---", "|", "#", "-", "*", "•")):
            cleaned_lines.append(line)
            continue

        # Puste/śmieciowe nagłówki typu '## -', '## "', '## .'
        if re.match(r"^#{1,6}\s*[-~\"'·•*`^.]+\s*$", stripped):
            continue

        # Znane stemple biblioteczne lub artefakty naklejek
        if "SAN RAFAEL PUBLIC LIBRARY" in stripped or "srpublic" in stripped:
            continue

        # 1. Znaczniki typowe dla szumu OCR (kwadraciki, daszki, tyldy, itp.)
        if any(c in "■·▪♦▲▼◄►`~^<>\\" for c in stripped) and len(stripped) < 35:
            continue

        # Krótkie linie z izolowanymi znaczkami interpunkcyjnymi lub kreskami
        if len(stripped) <= 3 and not stripped.isalnum() and not stripped.startswith(("-", "*", "#")):
            continue

        if stripped in ["|", "| |", "/", "\\", "—", "_", ".", "..", "..."]:
            continue

        if stripped.startswith("|") and len(stripped) < 15 and stripped.count("|") == 1:
            continue

        alnum_count = sum(1 for c in stripped if c.isalnum())

        # 2. Bardzo krótkie linie z małą liczbą liter/cyfr i symbolami (np. "b *", "·", "1 \.")
        if alnum_count <= 2 and len(stripped) < 20 and not stripped.isdigit():
            continue

        # 3. Krótkie linie z przewagą znaków interpunkcyjnych lub dziwnych zakończeń (np. "FlraS -*")
        non_alnum_ratio = (len(stripped) - alnum_count) / len(stripped)
        if len(stripped) < 25 and non_alnum_ratio >= 0.35:
            continue

        # 4. Izolowane śmieciowe ciągi sylab OCR ze skanów i rysunków (np. "fea", "Wil", "ee gc.", "Sel oS)", "a ce")
        valid_culinary_terms = {
            "dough", "filling", "brine", "finish", "garnish", "garnishes", "meats", "prawns",
            "rice", "noodles", "sauce", "sambal", "curry", "birds", "vegetables", "appetizers",
            "masala", "ferment", "optional", "serves", "makes", "yield", "cook", "bake",
            "stir", "heat", "cool", "drain", "serve", "salt", "oil", "pan", "pot", "wok",
            "page", "see", "to", "for", "in", "and", "with", "or", "a", "the", "of", "at", "on", "by",
            "sponge", "crust", "batter", "stock", "glaze", "rub", "syrup", "salad", "cake", "puree",
            "about", "servings", "cup", "cups", "tablespoon", "tablespoons", "teaspoon", "teaspoons"
        }
        words = [re.sub(r"[^a-zA-Z0-9]", "", w.lower()) for w in stripped.split()]
        words = [w for w in words if w]
        if 0 < len(stripped) <= 16 and len(words) <= 3 and not stripped.endswith((".", "!", "?")):
            if not any(w in valid_culinary_terms for w in words):
                noise_syllables = {"fea", "wil", "cea", "sey", "aie", "ata", "aoe", "mat", "eis", "ene", "ier", "pur", "qut", "fe", "wa", "ay", "vil", "scl", "eee", "snoino"}
                if any(w in noise_syllables for w in words) or (all(len(w) <= 3 for w in words) and any(c in "()[]'\"`~_\\/|0-9" for c in stripped)):
                    continue

        cleaned_lines.append(line)

    return "\n".join(cleaned_lines)


def clean_running_headers(text: str) -> str:
    """
    Wykrywa i usuwa powtarzające się nagłówki żywej paginy stron
    (np. 'Foreword 9', '9 Foreword', wielokrotne '## POLISH HERITAGE COOKERY').
    """
    # 1. Usuwanie linii typu "Foreword 12" lub "12 Foreword"
    text = re.sub(r"^\s*(?:Foreword\s+\d+|\d+\s+Foreword)\s*$", "", text, flags=re.MULTILINE | re.IGNORECASE)

    # 2. Usuwanie numerów stron będących osobnymi liniami (np. sam numer "14" na początku/końcu strony)
    text = re.sub(r"^\s*\d{1,4}\s*$", "", text, flags=re.MULTILINE)

    # 3. Deduplikacja powtarzających się nagłówków H2, które występują wielokrotnie jako żywa pagina
    lines = text.splitlines()
    header_counts = Counter(line.strip() for line in lines if line.strip().startswith("## "))

    seen_headers = set()
    cleaned_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## ") and header_counts[stripped] > 3:
            if stripped in seen_headers:
                continue
            seen_headers.add(stripped)
        cleaned_lines.append(line)

    return "\n".join(cleaned_lines)


def normalize_typography(text: str) -> str:
    """
    Normalizuje encje HTML, spacje i interpunkcję.
    """
    # 1. Encje HTML
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")

    # 2. Usuwanie wielokrotnych spacji w wierszach
    text = re.sub(r"(?<=\S)[ \t]{2,}(?=\S)", " ", text)

    # 3. Usuwanie spacji przed znakami interpunkcyjnymi (wyłącznie spacje poziome, nie nowe linie!)
    text = re.sub(r"[ \t]+([,.:;?!])", r"\1", text)

    # 4. Redukcja wielokrotnych pustych linii do maksymalnie dwóch
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip() + "\n"


def clean_markdown_text(text: str) -> str:
    """
    Kompleksowy proces czyszczenia i normalizacji tekstu Markdown ze skanów.
    """
    text = fix_hyphenation(text)
    text = restore_polish_and_ocr_artifacts(text)
    text = normalize_culinary_terms(text)
    text = clean_markdown_headings(text)
    text = format_recipe_ingredients(text)
    text = remove_scan_noise(text)
    text = clean_running_headers(text)
    text = normalize_typography(text)
    return text


def clean_markdown_file(
    input_path: Path,
    output_path: Optional[Path] = None,
) -> Path:
    """
    Wczytuje plik Markdown, czyści go i zapisuje do pliku wyjściowego
    (lub nadpisuje plik wejściowy, jeśli output_path nie został podany).
    Wykorzystuje blokadę zapisu oraz atomowy zapis pliku tymczasowego.
    """
    if not input_path.exists():
        raise FileNotFoundError(f"Plik {input_path} nie istnieje.")

    dest = output_path or input_path
    dest.parent.mkdir(parents=True, exist_ok=True)

    with book_write_lock(dest, owner="cli:clean"):
        raw_text = input_path.read_text(encoding="utf-8")
        cleaned_text = clean_markdown_text(raw_text)

        temp_file = dest.with_name(f"{dest.name}.{uuid.uuid4().hex}.tmp")
        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                f.write(cleaned_text)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_file, dest)

            status_file = dest.parent.parent / f"clean_status_{dest.stem}.json"
            atomic_json(status_file, {
                "cleaned": True,
                "book": dest.stem,
                "markdown_sha256": sha256(dest),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        finally:
            if temp_file.exists():
                try:
                    temp_file.unlink()
                except OSError:
                    pass

    return dest
