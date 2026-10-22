"""M3 ML name detector: finds patient names with a model trained by the C1 owner.

How it works:
1. Split the prompt into word tokens and remember where each one sits.
2. Describe each token with two kinds of features:
   - the token's own letters (TF-IDF over character 2 to 4-grams, case kept, so capitals count);
   - its surroundings: the word before and after, the shape of those words (Capitalised,
     initial, lowercase) and the character right after it (a dot after "R" means an initial).
     Shapes matter because names never repeat between train and test, but shapes do.
3. A Logistic Regression model gives each token a probability of being part of a name.
4. Tokens at or above the threshold (0.65) that sit next to each other, separated only by
   spaces or initial dots, become one name span.

The model file is checked against the SHA-256 in models/manifest.json before it is loaded,
because joblib files can run code when opened. If anything is wrong, None is returned and the
caller falls back to the titled-name heuristic.
"""

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import joblib
from scipy.sparse import hstack

from c1.detection.entity import Entity

logger = logging.getLogger("c1.detection.ml_names")

DEFAULT_THRESHOLD = 0.65

# A word made of letters, optionally joined by hyphens: "Perera", "Abdul-Cader"
WORD = re.compile(r"[A-Za-z]+(?:-[A-Za-z]+)*")


@dataclass
class Token:
    """One word in the prompt and where it sits."""

    text: str
    start: int
    end: int


# Split text into word tokens with their positions
def tokenize(text: str) -> list[Token]:
    return [Token(match.group(), match.start(), match.end()) for match in WORD.finditer(text)]


# Describe a word's shape: "Xx" Capitalised, "X" an initial, "XX" all capitals, "x" lowercase
def word_shape(word: str) -> str:
    if len(word) == 1 and word.isupper():
        return "X"
    if word.isupper():
        return "XX"
    if word[0].isupper():
        return "Xx"
    return "x"


# Describe one neighbour by the word itself and by its shape, e.g. "prev:mr prevshape:Xx"
def describe_neighbour(side: str, tokens: list[Token], index: int) -> str:
    if index < 0 or index >= len(tokens):
        return f"{side}:<edge> {side}shape:<edge>"
    word = tokens[index].text
    return f"{side}:{word.lower()} {side}shape:{word_shape(word)}"


# Name the character right after a token: "dot" after an initial such as "R.", "space", ...
def character_after(text: str, token: Token) -> str:
    if token.end >= len(text):
        return "after:edge"
    names = {".": "dot", ",": "comma", "'": "apostrophe", ":": "colon"}
    character = text[token.end]
    return "after:space" if character.isspace() else f"after:{names.get(character, 'other')}"


# Describe what surrounds token number `index`: both neighbours and the character after it
def context_of(text: str, tokens: list[Token], index: int) -> str:
    before = describe_neighbour("prev", tokens, index - 1)
    after = describe_neighbour("next", tokens, index + 1)
    return f"{before} {after} {character_after(text, tokens[index])}"


# Turn tokens into the feature matrix the model expects (same code for training and serving)
def token_features(model: dict, text: str, tokens: list[Token]):
    words = [token.text for token in tokens]
    contexts = [context_of(text, tokens, index) for index in range(len(tokens))]
    word_part = model["token_vectorizer"].transform(words)
    context_part = model["context_vectorizer"].transform(contexts)
    return hstack([word_part, context_part]).tocsr()


# Probability that each token is part of a patient name
def name_probabilities(model: dict, text: str, tokens: list[Token]) -> list[float]:
    if not tokens:
        return []
    features = token_features(model, text, tokens)
    name_column = list(model["classifier"].classes_).index(1)
    return [float(row[name_column]) for row in model["classifier"].predict_proba(features)]


# True when only spaces or initial dots separate two tokens ("R.L. Gamage"), so they join up
def only_spaces_between(text: str, left: Token, right: Token) -> bool:
    return text[left.end : right.start].strip(" .") == ""


# Join neighbouring name tokens into PATIENT_NAME entities; confidence is the weakest token's
def group_into_names(text: str, tokens: list[Token], scores: list[float], threshold: float):
    groups = []
    for token, score in zip(tokens, scores, strict=True):
        if score < threshold:
            continue
        if groups and only_spaces_between(text, groups[-1][-1][0], token):
            groups[-1].append((token, score))
        else:
            groups.append([(token, score)])
    return [make_name_entity(text, group) for group in groups]


# Build one PATIENT_NAME entity from a group of (token, score) pairs
def make_name_entity(text: str, group: list) -> Entity:
    start, end = group[0][0].start, group[-1][0].end
    confidence = round(min(score for _, score in group), 2)
    return Entity("PATIENT_NAME", start, end, text[start:end], confidence, "ml")


# Find patient names with the trained model
def detect_ml_names(text: str, model: dict, threshold: float = DEFAULT_THRESHOLD) -> list[Entity]:
    tokens = tokenize(text)
    scores = name_probabilities(model, text, tokens)
    return group_into_names(text, tokens, scores, threshold)


# SHA-256 of a file, used to prove the model file has not been swapped
def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# The SHA-256 that models/manifest.json expects for this model file, or None
def expected_sha256(manifest_path: Path, model_path: Path) -> str | None:
    if not manifest_path.exists():
        return None
    manifest = json.loads(manifest_path.read_text())
    return manifest.get("files", {}).get(model_path.name, {}).get("sha256")


# Load the model only if the file exists and its hash matches the manifest; otherwise None
def load_name_model(model_path: Path, manifest_path: Path) -> dict | None:
    if not model_path.exists():
        logger.warning("name model missing, using heuristic fallback")
        return None
    if file_sha256(model_path) != expected_sha256(manifest_path, model_path):
        logger.warning("name model hash does not match manifest, using heuristic fallback")
        return None
    try:
        return joblib.load(model_path)
    except Exception:  # a broken file must never stop C1; the fallback takes over
        logger.warning("name model could not be loaded, using heuristic fallback")
        return None
