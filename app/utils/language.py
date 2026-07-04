"""Language detection for farmer messages: Yoruba, Pidgin, Hausa, English.

Fast keyword heuristic first; falls back to asking Qwen to classify when unsure.
"""

from __future__ import annotations

from app.models.user import Language
from app.utils.logger import get_logger

log = get_logger("yieldra.language")

# Distinctive keywords per language (lowercased, accents stripped where typical).
_KEYWORDS: dict[str, set[str]] = {
    "yoruba": {
        "kini", "mo", "le", "se", "fun", "arun", "iresi", "mi", "bawo", "owo",
        "oko", "ile", "epo", "omo", " se", "ko", "ni", "wa", "pe",
    },
    "hausa": {
        "gonar", "nawa", "tana", "da", "matsala", "yaya", "ina", "kana", "wannan",
        "shuka", "ruwa", "noma", "kwari", "taki", "lafiya",
    },
    "pidgin": {
        "dey", "wahala", "don", "abeg", "wetin", "comot", "sabi", "naa", "make",
        "una", "go", "fit", "no", "yarn", "wey", "wan",
    },
}


def detect_language(text: str) -> str:
    """Return 'yoruba' | 'pidgin' | 'hausa' | 'english' from keyword heuristics."""
    if not text:
        return Language.english.value
    tokens = {t.strip(".,!?;:").lower() for t in text.split()}
    scores = {lang: len(tokens & kw) for lang, kw in _KEYWORDS.items()}
    best = max(scores, key=scores.get)
    if scores[best] >= 1:
        log.info("detect_language heuristic=%s scores=%s", best, scores)
        return best
    return Language.english.value


async def detect_language_llm(text: str) -> str:
    """LLM fallback classifier (qwen-turbo). Use when the heuristic is ambiguous."""
    from app.agents.base import BaseAgent
    from app.config import settings

    agent = BaseAgent(
        model=settings.model_report,  # qwen-turbo: cheap + fast
        system_prompt=(
            "Classify the language of the message as exactly one word: "
            "yoruba, pidgin, hausa, or english. Reply with only that word."
        ),
    )
    try:
        out = (await agent.run([{"role": "user", "content": text}])).strip().lower()
        for lang in ("yoruba", "pidgin", "hausa", "english"):
            if lang in out:
                return lang
    except Exception as exc:  # noqa: BLE001
        log.warning("detect_language_llm failed: %s", exc)
    return Language.english.value
