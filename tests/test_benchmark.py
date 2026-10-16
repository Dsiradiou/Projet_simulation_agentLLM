from llm.benchmark import SYSTEM_PROMPT, build_prompt


def test_prompt_is_deterministic_and_uses_config_values() -> None:
    first = build_prompt(7, "MARQUE_X", "INGREDIENT_Y", "INDICE_Z")
    assert first == build_prompt(7, "MARQUE_X", "INGREDIENT_Y", "INDICE_Z")
    assert first != build_prompt(8, "MARQUE_X", "INGREDIENT_Y", "INDICE_Z")
    assert "MARQUE_X" in first and "INGREDIENT_Y" in first and "INDICE_Z" in first


def test_prompt_size_matches_architecture_assumption() -> None:
    # Le document d'architecture suppose environ 600 tokens en entrée ; en français, on compte
    # environ 1,6 token par mot. Les mesures réelles (prompt_eval_count) sont dans docs/mesures.
    words = len((SYSTEM_PROMPT + build_prompt(1, "M", "I", "P")).split())
    assert 300 <= words <= 450
