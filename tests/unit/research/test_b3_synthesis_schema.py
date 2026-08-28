from aic.research.synthesize import CLAIM_CATEGORIES, MaterialClaimDraft


def test_material_claim_category_is_exact_model_facing_enum() -> None:
    schema = MaterialClaimDraft.model_json_schema(mode="validation")
    category = schema["properties"]["category"]
    assert tuple(category["enum"]) == CLAIM_CATEGORIES
