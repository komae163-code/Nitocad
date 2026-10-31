"""Assembly planner: catalog, validation, retry loop. No CadQuery or network needed."""
import pytest

from assembly_planner import (
    MAX_PARTS,
    AssemblyPlanError,
    default_catalog,
    plan_assembly,
    planner_system_prompt,
    validate_spec,
)


def good_part(name="main_shaft", **over):
    p = {"name": name, "part_type": "shaft",
         "parameters": {"diameter_mm": 20, "length_mm": 120},
         "position": [0, 0, 0], "rotation": [0, 0, 0]}
    p.update(over)
    return p


class TestCatalog:
    def test_reads_real_template_parameters(self):
        cat = default_catalog()
        assert cat["shaft"]["diameter_mm"] == 10.0
        assert "teeth" in cat["gear"]
        assert "stroke_mm" in cat["crankshaft"]

    def test_excludes_open_ended_types(self):
        cat = default_catalog()
        assert "freeform" not in cat and "assembly" not in cat
        assert len(cat) >= 20

    def test_prompt_lists_every_type_and_parameter(self):
        text = planner_system_prompt()
        for part_type, params in default_catalog().items():
            assert f"- {part_type}:" in text
            for key in params:
                assert key in text


class TestValidateSpec:
    def test_valid_plan_is_normalized(self):
        parts, errors = validate_spec({"parts": [good_part()]})
        assert errors == []
        assert parts[0]["position"] == (0.0, 0.0, 0.0)
        assert parts[0]["parameters"]["diameter_mm"] == 20

    @pytest.mark.parametrize("spec", [None, {}, {"parts": []}, {"parts": "x"}, []])
    def test_rejects_missing_parts(self, spec):
        _, errors = validate_spec(spec)
        assert errors

    def test_rejects_unknown_part_type(self):
        _, errors = validate_spec({"parts": [good_part(part_type="warp_drive")]})
        assert any("not supported" in e for e in errors)

    def test_rejects_typod_parameter_instead_of_silently_defaulting(self):
        _, errors = validate_spec({"parts": [good_part(parameters={"diamater_mm": 20})]})
        assert any("unknown parameter 'diamater_mm'" in e for e in errors)

    @pytest.mark.parametrize("bad", [-5, "20", None, float("nan"), float("inf"), True])
    def test_rejects_bad_numbers(self, bad):
        _, errors = validate_spec({"parts": [good_part(parameters={"length_mm": bad})]})
        assert errors

    def test_rejects_bad_and_duplicate_names(self):
        _, errors = validate_spec({"parts": [good_part("Bad Name")]})
        assert any("snake_case" in e for e in errors)
        _, errors = validate_spec({"parts": [good_part("a"), good_part("a")]})
        assert any("used twice" in e for e in errors)

    def test_rejects_out_of_range_position_and_rotation(self):
        _, errors = validate_spec({"parts": [good_part(position=[0, 0, 99999])]})
        assert any("position" in e for e in errors)
        _, errors = validate_spec({"parts": [good_part(rotation=[0, 0, 720])]})
        assert any("rotation" in e for e in errors)
        _, errors = validate_spec({"parts": [good_part(position=[0, 0])]})
        assert any("position" in e for e in errors)

    def test_no_part_cap_by_default(self):
        assert MAX_PARTS is None
        many = [good_part(f"p{i}") for i in range(300)]
        parts, errors = validate_spec({"parts": many})
        assert not any("Too many parts" in e for e in errors)
        assert len(parts) == 300

    def test_cap_can_still_be_switched_on(self, monkeypatch):
        import assembly_planner
        monkeypatch.setattr(assembly_planner, "MAX_PARTS", 5)
        _, errors = validate_spec({"parts": [good_part(f"p{i}") for i in range(6)]})
        assert any("Too many parts" in e for e in errors)

    def test_string_parameters_checked_against_default_type(self):
        cat = default_catalog()
        assert isinstance(cat["flat_plate"]["hole_pattern"], str)
        _, errors = validate_spec({"parts": [{"name": "plate", "part_type": "flat_plate",
                                              "parameters": {"hole_pattern": 3}}]})
        assert errors


class TestPlanAssembly:
    def test_returns_valid_plan(self):
        parts = plan_assembly("shaft", call_llm=lambda s, u: {"parts": [good_part()]})
        assert parts[0]["name"] == "main_shaft"

    def test_retries_with_exact_failures_fed_back(self):
        seen = []

        def llm(system, user):
            seen.append(user)
            if len(seen) == 1:
                return {"parts": [good_part(parameters={"diamater_mm": 20})]}
            return {"parts": [good_part()]}

        parts = plan_assembly("a shaft", call_llm=llm)
        assert len(seen) == 2 and "diamater_mm" in seen[1] and parts

    def test_raises_after_exhausting_attempts_never_returns_bad_plan(self):
        with pytest.raises(AssemblyPlanError) as exc:
            plan_assembly("x", call_llm=lambda s, u: {"parts": [good_part(part_type="nope")]})
        assert exc.value.errors

    def test_unsupported_request_is_reported(self):
        with pytest.raises(AssemblyPlanError) as exc:
            plan_assembly("a whole airplane", call_llm=lambda s, u: {"parts": [], "unsupported": "needs lofted wings"})
        assert "lofted wings" in str(exc.value)

    def test_llm_exception_is_retried_then_reported(self):
        calls = []

        def llm(s, u):
            calls.append(1)
            raise TimeoutError("slow")

        with pytest.raises(AssemblyPlanError) as exc:
            plan_assembly("x", call_llm=llm)
        assert len(calls) == 2 and "slow" in str(exc.value)
