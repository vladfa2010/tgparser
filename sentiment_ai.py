"""
Sentiment Analysis with rubert-tiny2.
Lazy model loading: model loads on first use, not at import.
In-memory cache: text_hash -> result.
Fallback to lexicon if model fails.
"""
import hashlib
import logging
import time
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# ─── Lazy model state ────────────────────────────────────────
_pipeline = None
_model_name = "cointegrated/rubert-tiny2-cedr-emotion-detection"
_model_loaded_at: Optional[float] = None
_model_error: Optional[str] = None

# In-memory cache: text_hash -> {"label": "positive|negative|neutral", "score": 0.95, "source": "ai"}
_cache: dict = {}
_cache_hits = 0
_cache_misses = 0

# ─── Lexicon fallback (same as web.py) ───────────────────────
_LEX_POS = frozenset({
    "рост", "прибыль", "прибыльный", "прибыльная", "прирост", "повышение", "подъем",
    "подъём", "рали", "ралли", "бык", "бычий", "лонг", "покупка", "покупаем",
    "покупать", "вход", "вошел", "вошёл", "цель", "тейк", "тейк-профит", " профит",
    "доход", "доходность", "доходный", "окупаемость", "плюс", "позитив", "позитивный",
    "оптимизм", "оптимистичный", "сильный", "укрепление", "восстановление", "отскок",
    "прорыв", "breakout", "рванул", "взлетел", "взлет", "растет", "растёт", "расти",
    "зеленый", "зелёный", "зелень", "buy", "long", "bull", "bullish", "profit",
    "growth", "gain", "up", "rise", "rising", "rocket", "moon",
    "рекорд", "максимум", "high", "higher", "strong",
    "перспектива", "потенциал", "увеличение", "расширение", "дивиденд",
})

_LEX_NEG = frozenset({
    "падение", "убыток", "убыточный", "убыточная", "понижение", "снижение", "спад",
    "медведь", "медвежий", "шорт", "продажа", "продаем", "продаж", "продать",
    "выход", "вышел", "стоп", "стоп-лосс", "лосс", "потеря", "потери", "минус",
    "негатив", "негативный", "пессимизм", "пессимистичный", "слабый", "ослабление",
    "обвал", "кризис", "крах", "пузырь", "коррекция", "просадка", "просел",
    "обвалился", "рухнул", "падает", "падать", "красный", "красные",
    "sell", "short", "bear", "bearish", "loss", "losses", "down", "drop", "fall",
    "falling", "crash", "dump", "crisis", "correction", "weak", "underperform",
    "банкротство", "дефолт", "санкции", "штраф", "иск",
    "конфликт", "задержка", "отсрочка", "срыв", "риск", "опасность",
    "угроза", "нестабильность", "волатильность", "ликвидация",
    "маржин-колл", "форс-мажор", "паника", "fud",
})


# ─── Lazy model loader ───────────────────────────────────────
def _load_model() -> bool:
    """Load rubert-tiny2 model. Returns True if loaded successfully."""
    global _pipeline, _model_loaded_at, _model_error

    if _pipeline is not None:
        return True

    try:
        logger.info(f"Loading sentiment model: {_model_name} ...")
        from transformers import pipeline
        start = time.time()
        _pipeline = pipeline(
            "sentiment-analysis",
            model=_model_name,
            tokenizer=_model_name,
            device=-1,  # CPU
            truncation=True,
            max_length=512,
        )
        _model_loaded_at = time.time()
        elapsed = _model_loaded_at - start
        logger.info(f"Sentiment model loaded in {elapsed:.1f}s")
        return True
    except Exception as e:
        _model_error = str(e)
        logger.error(f"Failed to load sentiment model: {e}")
        return False


# ─── Core: AI sentiment ──────────────────────────────────────
def _ai_sentiment(text: str) -> Tuple[str, float, str]:
    """
    Run text through rubert-tiny2 model.
    Returns: (label, score, source)
    label: "positive" | "negative" | "neutral"
    source: "ai" | "lexicon"
    """
    global _cache_misses, _cache_hits

    if not text or len(text.strip()) < 3:
        return "neutral", 0.5, "ai"

    # Check cache
    text_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
    cached = _cache.get(text_hash)
    if cached:
        _cache_hits += 1
        return cached["label"], cached["score"], cached["source"]

    _cache_misses += 1

    # Try AI model
    if _load_model() and _pipeline is not None:
        try:
            # Model returns labels like "positive", "negative", "neutral"
            result = _pipeline(text[:512])[0]
            label = result["label"].lower()
            score = float(result["score"])

            # Map to unified labels
            label_map = {
                "positive": "positive",
                "negative": "negative",
                "neutral": "neutral",
                "joy": "positive",
                "sadness": "negative",
                "anger": "negative",
                "fear": "negative",
                "surprise": "neutral",
            }
            unified_label = label_map.get(label, "neutral")

            # Normalize neutral if score is low
            if score < 0.6:
                unified_label = "neutral"

            _cache[text_hash] = {"label": unified_label, "score": round(score, 4), "source": "ai"}
            return unified_label, score, "ai"
        except Exception as e:
            logger.warning(f"AI sentiment failed, falling back to lexicon: {e}")

    # Fallback: lexicon
    return _lexicon_sentiment(text)


# ─── Fallback: lexicon sentiment ─────────────────────────────
def _lexicon_sentiment(text: str) -> Tuple[str, float, str]:
    """Simple word-count sentiment (fallback)."""
    txt = text.lower()
    pos = sum(1 for w in _LEX_POS if w in txt)
    neg = sum(1 for w in _LEX_NEG if w in txt)

    if pos > neg:
        label = "positive"
        score = min(0.5 + (pos - neg) * 0.1, 0.95)
    elif neg > pos:
        label = "negative"
        score = min(0.5 + (neg - pos) * 0.1, 0.95)
    else:
        label = "neutral"
        score = 0.5

    text_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
    _cache[text_hash] = {"label": label, "score": round(score, 4), "source": "lexicon"}
    return label, score, "lexicon"


# ─── Public API ──────────────────────────────────────────────
def analyze(text: str) -> dict:
    """
    Analyze sentiment of a single text.
    Returns: {"label": "positive|negative|neutral", "score": 0.95, "source": "ai|lexicon"}
    """
    label, score, source = _ai_sentiment(text)
    return {"label": label, "score": round(score, 4), "source": source}


def analyze_batch(texts: list[str]) -> list[dict]:
    """Analyze sentiment of multiple texts."""
    return [analyze(t) for t in texts]


def get_stats() -> dict:
    """Get model and cache statistics."""
    return {
        "model_name": _model_name,
        "model_loaded": _pipeline is not None,
        "model_loaded_at": _model_loaded_at,
        "model_error": _model_error,
        "cache_size": len(_cache),
        "cache_hits": _cache_hits,
        "cache_misses": _cache_misses,
    }
