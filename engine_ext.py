# File: engine_ext.py
# Fork-added: transparent, model-tagged .pt conditioning cache. Kept out of engine.py
# so upstream merges to the core synthesize() path stay clean — engine.py calls two
# thin functions here instead of carrying the persistence logic inline.
#
# A .pt holds conditioning tensors specific to ONE model architecture, so the model
# type is part of the filename and a .pt is only ever loaded for the model that made
# it. Exaggeration is NOT in the name — generate() overrides the baked value.
#
# Import-light on purpose (config is imported lazily) so the logic is unit-testable
# without torch/chatterbox.

import logging
import os

logger = logging.getLogger(__name__)

CONDS_DIRNAME = ".conds"


def persist_enabled() -> bool:
    import config
    return config.config_manager.get_bool("tts_engine.persist_conditionals", True)


def persisted_conds_path(audio_prompt_path: str, model_type: str) -> str:
    """Model-tagged .pt path beside the source audio: <dir>/.conds/<stem>.<model>.pt."""
    directory = os.path.dirname(audio_prompt_path)
    stem = os.path.splitext(os.path.basename(audio_prompt_path))[0]
    return os.path.join(directory, CONDS_DIRNAME, f"{stem}.{model_type}.pt")


def try_load_conds(audio_prompt_path: str, model_type: str, cond_class, map_location):
    """Return a Conditionals object from disk if a fresh, matching .pt exists, else None."""
    if not persist_enabled() or cond_class is None or not model_type:
        return None
    pt = persisted_conds_path(audio_prompt_path, model_type)
    try:
        if not os.path.exists(pt):
            return None
        # Ignore a stale cache if the source audio was replaced after baking.
        if os.path.getmtime(pt) < os.path.getmtime(audio_prompt_path):
            return None
        conds = cond_class.load(pt, map_location=map_location)
        logger.info(f"Loaded persisted conditionals: {pt}")
        return conds
    except Exception as e:
        logger.warning(f"Could not load persisted conditionals '{pt}': {e}")
        return None


def try_save_conds(audio_prompt_path: str, model_type: str, conds) -> None:
    """Persist conditionals to a model-tagged .pt beside the source audio (best effort)."""
    if not persist_enabled() or not model_type or conds is None:
        return
    pt = persisted_conds_path(audio_prompt_path, model_type)
    try:
        os.makedirs(os.path.dirname(pt), exist_ok=True)
        conds.save(pt)
        logger.info(f"Persisted conditionals: {pt}")
    except Exception as e:
        logger.warning(f"Could not persist conditionals '{pt}': {e}")
